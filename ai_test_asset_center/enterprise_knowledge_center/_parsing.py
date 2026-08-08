"""Source classification and parsing: OpenAPI, Postman, markdown, SQL, permissions."""
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import io
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable
from ._utils import _clean_markup_text, _contains_markdown_api_sections, _decode_docx, _decode_pdf, _dedupe_by_id, _detected_source_format, _hash_bytes, _json_or_none, _lexicon_dict, _lexicon_list, _looks_like_field_dictionary, _looks_like_uiux_spec, _norm, _normalize_state_token, _now, _parser_receipt, _redact_text, _safe_slug, _short_hash, _tokens  # noqa: F401

logger = logging.getLogger(__name__)

try:
    import docx2txt
except ImportError:
    docx2txt = None

from ._common import *  # noqa: F401,F403
from ._utils import *  # noqa: F401,F403
from ._utils import _csv_rows  # noqa: F401

__all__ = [
    "_canonical_entity_name", "_classify_source", "_classify_source_multi", "_csv_rows", "_doc_bool",
    "_entity_inventory_rows", "_field_dictionary_entries",
    "_field_dictionary_tables", "_flatten_json_field_names", "_infer_field_rows_from_markdown",
    "_inline_qualified_field_rows", "_is_entity_inventory_table", "_is_field_definition_table",
    "_merge_table_identities", "_permission_crosstab_entries", "_section_table_label",
    "_json_blocks", "_json_schema_tables", "_markdown_api_operations", "_markdown_table_blocks",
    "_markdown_table_rows", "_negative_permission_clause", "_openapi_operations", "_parse_source",
    "_permission_action_aliases", "_permission_action_values", "_permission_decision",
    "_permission_entries", "_permission_field", "_permission_resource_aliases",
    "_permission_scope", "_pick_first", "_postman_operations", "_risk_type_from_text",
    "_roles_from_text", "_rule_type_from_text", "_rules_from_text", "_sql_tables",
    "_state_machines_from_text", "_ticket_rows", "_typed_validation_constraint",
    "_uiux_specs_from_text",
]


def _doc_bool(value: Any) -> bool:
    normalized = _norm(value)
    if not normalized:
        return False
    if normalized in {"yes", "true", "required", "必填", "是", "y"}:
        return True
    if normalized in {"no", "false", "nullable", "optional", "否", "非必填", "n"}:
        return False
    return False


def _classify_source(name: str, text: str, explicit: str | None = None) -> str:
    """Classify a knowledge source document by file name and content preview.

    Rules are evaluated in priority order; the first match wins.
    Returns the PRIMARY source_type (backward compatible).
    Use _classify_source_multi for all matching labels.
    """
    labels = _classify_source_multi(name, text, explicit)
    return labels[0] if labels else "collaboration_document"


def _classify_source_multi(name: str, text: str, explicit: str | None = None) -> list[str]:
    """Classify a source into MULTIPLE labels (R1 fix).

    Returns all matching source_types in priority order.
    The first element is the primary label (same as _classify_source).
    """
    explicit = str(explicit or "").strip().lower()
    name_low = _norm(name)
    low = _norm(f"{name} {text[:5000]}")
    data = _json_or_none(text)
    suffix = Path(name).suffix.lower()

    def _has(*tokens: str) -> bool:
        return any(t in name_low for t in tokens)

    def _has_in_text(*tokens: str) -> bool:
        return any(t in low for t in tokens)

    rules: list[tuple[bool, str]] = [
        # (condition, source_type)
        (suffix in SOURCE_CODE_SUFFIXES, "source_code"),
        (suffix == ".har" or (suffix == ".json" and isinstance(data, dict) and "log" in data), "har"),
        (suffix == ".log" or (suffix == ".txt" and _has("log", "日志", "access", "error")), "application_log"),
        (suffix == ".svg" or "<svg" in str(text or "").lower(), "uiux_svg"),
        (
            _has("business_rule", "business-rule", "domain_rule", "domain-rule", "业务规则", "业务约束")
            or (
                not _has("prd", "mrd")
                and any(
                    _norm(marker) in low
                    for marker in _lexicon_list("business_rule_document_markers")
                )
            ),
            "business_rules",
        ),
        (_has("permission", "permissions", "matrix", "权限矩阵", "rbac", "acl"), "permission_matrix"),
        (_has("historical_bug", "historical-bug", "bugs", "bug", "defect", "缺陷"), "historical_bug"),
        (_has("ticket", "issue", "jira", "zentao", "工单"), "ticket"),
        (_has("postman",), "postman"),
        (_has("confluence",), "confluence_document"),
        (_has("feishu", "lark", "飞书"), "feishu_document"),
        (isinstance(data, dict) and isinstance(data.get("paths"), dict) and (data.get("openapi") or data.get("swagger")), "openapi"),
        (isinstance(data, dict) and isinstance(data.get("item"), list) and (data.get("info") or {}).get("schema", "").lower().find("postman") >= 0, "postman"),
        (name.lower().endswith(".sql") or "create table" in low or "alter table" in low, "database_schema"),
        (_looks_like_field_dictionary(name, text, data), "db_field_dictionary"),
        ("mrd" in name_low or bool(re.search(r"\bMRD\b", name, flags=re.I)) or "市场需求" in low, "mrd"),
        ("prd" in name_low or bool(re.search(r"\bPRD\b", name, flags=re.I)) or "产品需求" in low or "需求说明" in low, "prd"),
        ("postman" in low and ("collection" in low or '"item"' in low), "postman"),
        (_contains_markdown_api_sections(text) or (suffix in {".md", ".txt", ".rst"} and _has("api", "接口") and _has_in_text("请求参数", "响应参数", "response", "request", "curl", "header")), "markdown_api"),
        (_looks_like_uiux_spec(name, text), "uiux_spec"),
        (_has_in_text("openapi", "swagger", "api contract"), "openapi"),
        (_has_in_text("权限矩阵", "permission matrix", "role matrix", "rbac", "acl"), "permission_matrix"),
        (_has_in_text("历史缺陷", "historical bug", "defect list", "bug list", "缺陷列表"), "historical_bug"),
        (_has_in_text("jira", "禅道", "工单", "ticket", "incident"), "ticket"),
        (_has_in_text("confluence",), "confluence_document"),
        (_has_in_text("飞书", "feishu", "lark"), "feishu_document"),
    ]
    matched: list[str] = []
    if explicit in SOURCE_TYPES:
        # An explicit source_type from the ingest caller is authoritative
        # (structured formats the content rules cannot infer, e.g. a UI/UX
        # requirements JSON); automatic labels still join as secondary tags.
        matched.append(explicit)
    for condition, source_type in rules:
        if condition and source_type not in matched:
            matched.append(source_type)
    if not matched:
        matched.append("collaboration_document")
    # Additional: if text contains 2D tables with field-definition headers,
    # add db_field_dictionary as secondary label
    if "db_field_dictionary" not in matched and "database_schema" not in matched:
        if _text_contains_field_definition_tables(text):
            matched.append("db_field_dictionary")
    return matched


def _text_contains_field_definition_tables(text: str) -> bool:
    """Check if text contains markdown tables that look like field definitions."""
    from ._format_normalizer import extract_tables_from_markdown
    tables = extract_tables_from_markdown(text)
    return any(_is_field_definition_table(t.get("headers") or []) for t in tables)


# ── Phase 2: Generic field-definition table recognizer ──
# Industry-neutral header semantic groups.
_NAME_HEADERS = {
    "field", "field_name", "fieldname", "column", "column_name", "columnname",
    "name", "attribute", "property", "key",
    "字段", "字段名", "列名", "属性", "属性名", "名称",
}
_TYPE_HEADERS = {
    "type", "data_type", "datatype", "field_type", "fieldtype",
    "description", "desc", "comment", "remark", "note",
    "constraint", "constraints", "required", "nullable", "default",
    "类型", "字段类型", "数据类型", "说明", "描述", "备注", "约束", "必填", "是否必填", "默认值",
}


def _is_field_definition_table(headers: list[str]) -> bool:
    """Determine if a table's headers indicate it's a field definition table.

    Requires at least one 'name-type' header AND at least one 'type/desc-type' header.
    Industry-neutral: only uses generic field/column/type/description vocabulary.
    """
    if not headers:
        return False
    norm_headers = {_norm(h) for h in headers if h}
    has_name = bool(norm_headers & _NAME_HEADERS)
    has_type_or_desc = bool(norm_headers & _TYPE_HEADERS)
    return has_name and has_type_or_desc


# ── Entity inventory tables ──
# A table whose rows enumerate entities rather than fields, e.g. "| 表 | 说明 |".
_ENTITY_NAME_HEADERS = {
    "table", "table_name", "tablename", "entity", "entity_name", "entityname",
    "object", "object_name", "model", "collection", "schema",
    "表", "表名", "数据表", "实体", "实体名", "对象", "模型", "集合",
}
_DESCRIPTION_HEADERS = {
    "description", "desc", "comment", "remark", "note", "meaning", "purpose",
    "说明", "描述", "备注", "含义", "用途",
}


# ── Source-declared identity constraints ──
# A field is an identity field when the source marks it as a primary key or a
# unique key. Both make the value address exactly one row, which is what proves
# two observations reached the same resource. Recognition is by constraint
# vocabulary, never by field name, so no industry term is assumed.
_IDENTITY_CONSTRAINT_HEADERS = {
    "constraint", "constraints", "key", "keys", "index", "indexes",
    "约束", "键", "主键", "索引",
}
_IDENTITY_CONSTRAINT_RE = re.compile(
    r"(?i)(?:\bpk\b|\bprimary\s+key\b|\bunique\b|主键|唯一)"
)
# Constraint lists outside a table, e.g. "products: UNIQUE(sku), INDEX(org)".
_IDENTITY_CONSTRAINT_CALL_RE = re.compile(
    r"(?i)(?:primary\s+key|unique|主键|唯一)\s*\(([^)]*)\)"
)


def _declares_identity(constraint_text: str) -> bool:
    text = str(constraint_text or "")
    if not text.strip():
        return False
    # "FK -> work_orders.id, UNIQUE" declares identity; a bare FK does not.
    return bool(_IDENTITY_CONSTRAINT_RE.search(text))


def _identity_columns_from_constraint_calls(text: str) -> list[str]:
    """Column names named inside PRIMARY KEY(...) / UNIQUE(...) declarations."""
    found: list[str] = []
    for match in _IDENTITY_CONSTRAINT_CALL_RE.finditer(str(text or "")):
        for raw in match.group(1).split(","):
            column = raw.strip().strip('`"[]')
            if column and column not in found:
                found.append(column)
    return found


def _is_entity_inventory_table(headers: list[str]) -> bool:
    """Determine if a table enumerates entities (one row per entity).

    A field-definition table always wins: when a field-name header is present the
    rows describe fields, not entities.
    """
    if not headers:
        return False
    norm_headers = {_norm(h) for h in headers if h}
    if norm_headers & _NAME_HEADERS:
        return False
    return bool(norm_headers & _ENTITY_NAME_HEADERS) and bool(norm_headers & _DESCRIPTION_HEADERS)


# ── Entity identity canonicalization ──
# The same entity is often declared under two labels: a data dictionary heading
# carries a human gloss ("Product (产品)") while an OpenAPI schema uses the bare
# identifier ("Product"). Without canonicalization one entity becomes two
# identities and its fields are split across them.
_ENTITY_GLOSS_RE = re.compile(r"^(?P<base>[^()（）]+?)\s*[（(](?P<gloss>[^()（）]{1,40})[）)]\s*$")
_HEADING_ORDINAL_RE = re.compile(r"^\s*(?:\d+(?:\.\d+)+|\d+\s*[\.、)])\s*(?=\S)")


def _canonical_entity_name(raw: str) -> tuple[str, str]:
    """Split a declared entity label into (canonical_name, alias).

    Format-level normalization only — strips a heading ordinal and a trailing
    parenthetical gloss. The identifier itself is never rewritten.
    """
    label = str(raw or "").strip()
    if not label:
        return "", ""
    label = _HEADING_ORDINAL_RE.sub("", label).strip()
    match = _ENTITY_GLOSS_RE.match(label)
    if not match:
        return label, ""
    base = match.group("base").strip()
    gloss = match.group("gloss").strip()
    if not base:
        return label, ""
    return base, gloss


def _section_table_label(section: str) -> str:
    """Reduce a section heading to the entity label it declares."""
    label = str(section or "").strip()
    if not label or _norm(label) == "document":
        return ""
    match = re.search(r"(?i)(?:table|表|数据表)\s*[:：]\s*([A-Za-z0-9_\-\u4e00-\u9fff]+)", label)
    return match.group(1) if match else label


def _entity_inventory_rows(text: str, source_id: str = "") -> list[dict[str, Any]]:
    """Extract entities declared by an inventory table (one row per entity)."""
    from ._format_normalizer import extract_tables_from_markdown

    rows: list[dict[str, Any]] = []
    for table in extract_tables_from_markdown(text):
        headers = table.get("headers") or []
        if not _is_entity_inventory_table(headers):
            continue
        for row in table.get("rows") or []:
            raw_name = _pick_first(row, tuple(sorted(_ENTITY_NAME_HEADERS)))
            name, alias = _canonical_entity_name(raw_name)
            if not name:
                continue
            description = _pick_first(row, tuple(sorted(_DESCRIPTION_HEADERS)))
            rows.append({
                "table_id": f"table:{name}",
                "source_id": source_id,
                "name": name,
                "aliases": [alias] if alias else [],
                "columns": [],
                "foreign_keys": [],
                "field_dictionary": [],
                "description": _redact_text(description, 320),
                "derivation": "entity_inventory_table",
                "tokens": sorted(_tokens(f"{name} {alias} {description}")),
            })
    return rows


# Inline qualified field declarations, i.e. a backticked `<entity>.<field>` token
# followed by its description, as used in prose and bullet lists.
_QUALIFIED_FIELD_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_]{0,62})\.([A-Za-z_][A-Za-z0-9_]{0,62})`")


def _inline_qualified_field_rows(
    text: str, source_id: str, declared_entities: Iterable[str]
) -> list[dict[str, Any]]:
    """Extract fields written inline as `table.field` in prose or bullet lists.

    Fail-closed by construction: a qualified reference is accepted only when its
    qualifier resolves to an entity already declared in the same source. This
    keeps unrelated dotted tokens (hostnames, filenames, module paths) out.
    """
    known: dict[str, str] = {}
    for name in declared_entities:
        label = str(name or "").strip()
        if label:
            known.setdefault(label.lower(), label)
    if not known:
        return []

    rows: list[dict[str, Any]] = []
    for line in str(text or "").splitlines():
        for match in _QUALIFIED_FIELD_RE.finditer(line):
            table_name = known.get(match.group(1).lower())
            if not table_name:
                continue
            field_name = match.group(2)
            trailing = line[match.end():].strip()
            description = trailing.lstrip("：:->—-").strip().rstrip("；;。.,，")
            rows.append({
                "field_id": f"field:{source_id}:{_short_hash({'table': table_name, 'field': field_name})}",
                "source_id": source_id,
                "table": table_name,
                "table_id": f"table:{table_name}",
                "field": field_name,
                "field_path": f"{table_name}.{field_name}",
                "type": "",
                "required": False,
                "description": _redact_text(description, 320),
                "derivation": "inline_qualified_reference",
                "tokens": sorted(_tokens(f"{table_name} {field_name} {description}")),
            })
    return rows


def _openapi_operations(openapi: dict[str, Any], source_id: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, methods in (openapi.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            method_u = str(method).upper()
            if method_u not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} or not isinstance(operation, dict):
                continue
            parameters = operation.get("parameters") or []
            parameter_names = [str(row.get("name")) for row in parameters if isinstance(row, dict) and row.get("name")]
            tags = [str(x) for x in operation.get("tags") or []]
            # Keep summary and description separate so Chinese prose can attach as
            # interface spans. Display ``summary`` still coalesces for inventory.
            summary_text = str(operation.get("summary") or "")
            description_text = str(operation.get("description") or "")
            summary = summary_text or description_text
            operation_id = str(operation.get("operationId") or f"{method.lower()}_{str(path).strip('/').replace('/', '_').replace('{', '').replace('}', '') or 'root'}")
            interface_id = f"api:{method_u}:{path}"
            rows.append({
                "interface_id": interface_id,
                "source_id": source_id,
                "source_kind": "openapi",
                "method": method_u,
                "path": str(path),
                "operation_id": operation_id,
                "summary": summary,
                "description": description_text,
                "openapi_summary": summary_text,
                "openapi_description": description_text,
                "tags": tags,
                "parameters": parameter_names,
                "tokens": sorted(_tokens(f"{path} {operation_id} {summary} {' '.join(tags)} {' '.join(parameter_names)}")),
            })
    return rows


def _postman_operations(payload: Any, source_id: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    def walk(items: Any) -> None:
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("item"), list):
                walk(item.get("item"))
            request = item.get("request") if isinstance(item.get("request"), dict) else {}
            if not request:
                continue
            method = str(request.get("method") or "GET").upper()
            url = request.get("url") or ""
            if isinstance(url, dict):
                path_values = url.get("path")
                if isinstance(path_values, list):
                    path = "/" + "/".join(str(x) for x in path_values)
                else:
                    path = str(url.get("raw") or "")
            else:
                path = str(url)
            path = re.sub(r"^https?://[^/]+", "", path) or "/"
            name = str(item.get("name") or "Postman request")
            interface_id = f"postman:{method}:{path}"
            rows.append({
                "interface_id": interface_id,
                "source_id": source_id,
                "source_kind": "postman",
                "method": method,
                "path": path,
                "operation_id": _safe_slug(name, 64),
                "summary": name,
                "tags": ["postman"],
                "parameters": [],
                "tokens": sorted(_tokens(f"{path} {name}")),
            })
    root_items = payload.get("item") if isinstance(payload, dict) else []
    walk(root_items)
    return rows


def _json_blocks(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for match in re.finditer(r"```json\s*(.*?)```", text or "", re.I | re.S):
        body = str(match.group(1) or "").strip()
        try:
            value = json.loads(body)
        except Exception:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _flatten_json_field_names(value: Any, prefix: str = "", depth: int = 0) -> list[str]:
    if depth > 3:
        return []
    if isinstance(value, dict):
        fields: list[str] = []
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            fields.append(path)
            fields.extend(_flatten_json_field_names(child, path, depth + 1))
        return fields
    if isinstance(value, list) and value:
        return _flatten_json_field_names(value[0], f"{prefix}[]".rstrip("."), depth + 1)
    return []


def _markdown_table_blocks(text: str) -> list[list[dict[str, str]]]:
    blocks: list[list[dict[str, str]]] = []
    current: list[str] = []
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            current.append(stripped)
            continue
        if len(current) >= 2:
            blocks.append(current[:])
        current = []
    if len(current) >= 2:
        blocks.append(current[:])
    tables: list[list[dict[str, str]]] = []
    for block in blocks:
        headers = [part.strip() for part in block[0].strip("|").split("|")]
        if not headers or not any(headers):
            continue
        rows: list[dict[str, str]] = []
        for line in block[2:] if len(block) >= 2 and re.fullmatch(r"[\|\-\:\s]+", block[1]) else block[1:]:
            values = [part.strip() for part in line.strip("|").split("|")]
            if len(values) != len(headers):
                continue
            rows.append({str(headers[idx]): values[idx] for idx in range(len(headers))})
        if rows:
            tables.append(rows)
    return tables


def _pick_first(item: dict[str, Any], keys: Iterable[str]) -> str:
    norm_map = {_norm(key): key for key in item}
    for key in keys:
        actual = norm_map.get(_norm(key))
        if not actual or actual not in item:
            continue
        value = item.get(actual)
        if value is None:
            continue
        # Preserve explicit false/true booleans. ``value or ""`` would drop False
        # and erase required=false / nullable declarations from field excerpts.
        if isinstance(value, bool):
            return "true" if value else "false"
        text = str(value).strip()
        if text:
            return text
    return ""


def _infer_field_rows_from_markdown(text: str, source_id: str = "") -> list[dict[str, Any]]:
    """Extract field rows from Markdown pipe tables.

    Section attribution is positional: each table block belongs to the heading in
    effect at its own position. A row-level table column still wins over it.
    """
    from ._format_normalizer import extract_tables_from_markdown

    rows: list[dict[str, Any]] = []
    for table in extract_tables_from_markdown(text):
        section_table, section_alias = _canonical_entity_name(
            _section_table_label(str(table.get("source_locator") or ""))
        )
        for row in table.get("rows") or []:
            explicit_table = _pick_first(row, ("table", "table_name", "table name", "表", "数据表"))
            explicit_name, explicit_alias = _canonical_entity_name(explicit_table)
            table_name = explicit_name or section_table
            # The human gloss dropped by canonicalization is still source evidence.
            table_alias = explicit_alias if explicit_name else section_alias
            field_name = _pick_first(row, ("field", "field_name", "field name", "column", "column_name", "字段", "列名", "属性"))
            if not field_name:
                continue
            field_type = _pick_first(row, ("type", "data_type", "datatype", "字段类型", "类型"))
            description = _pick_first(row, ("description", "desc", "comment", "说明", "描述", "备注"))
            required = _pick_first(row, ("required", "nullable", "必填", "是否必填"))
            constraint = _pick_first(row, tuple(sorted(_IDENTITY_CONSTRAINT_HEADERS)))
            rows.append({
                "field_id": f"field:{source_id}:{_short_hash({'table': table_name or 'default', 'field': field_name})}",
                "source_id": source_id,
                "table": table_name or "default",
                "table_id": f"table:{table_name or 'default'}",
                "field": field_name,
                "field_path": field_name,
                "type": field_type,
                "required": _doc_bool(required),
                "constraint": _redact_text(constraint, 160),
                "identity": _declares_identity(constraint),
                "description": _redact_text(description, 320),
                "table_alias": table_alias,
                "tokens": sorted(_tokens(f"{table_name} {table_alias} {field_name} {field_type} {description}")),
            })
    return rows


def _field_dictionary_entries(text: str, payload: Any, source_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key in ("fields", "items", "columns", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend([item for item in value if isinstance(item, dict)])
        tables = payload.get("tables")
        if isinstance(tables, list):
            for table in tables:
                if not isinstance(table, dict):
                    continue
                table_name = str(table.get("name") or table.get("table") or "")
                for field in table.get("fields") or table.get("columns") or []:
                    if isinstance(field, dict):
                        item = dict(field)
                        item.setdefault("table", table_name)
                        candidates.append(item)
    elif isinstance(payload, list):
        candidates.extend([item for item in payload if isinstance(item, dict)])
    candidates.extend(_csv_rows(text))
    for item in candidates:
        table_name = _pick_first(item, ("table", "table_name", "tableName", "表", "数据表"))
        field_name = _pick_first(item, ("field", "field_name", "fieldName", "column", "column_name", "字段", "列名", "name"))
        if not field_name:
            continue
        field_type = _pick_first(item, ("type", "data_type", "dataType", "字段类型", "类型"))
        description = _pick_first(item, ("description", "desc", "comment", "说明", "描述", "remark", "备注"))
        required = _pick_first(item, ("required", "nullable", "必填", "is_required"))
        constraint = _pick_first(item, tuple(sorted(_IDENTITY_CONSTRAINT_HEADERS)))
        required_value = _doc_bool(required)
        foreign_key_raw = _pick_first(item, ("foreign_key", "foreignKey", "fk", "外键", "外键约束"))
        foreign_key_value = _doc_bool(foreign_key_raw)
        evidence_bits: list[str] = []
        if table_name:
            evidence_bits.append(f"table={table_name}")
        evidence_bits.append(f"field={field_name}")
        if required is not None and str(required).strip() != "":
            evidence_bits.append(
                f"required={'true' if required_value else 'false'}"
            )
        if foreign_key_raw is not None and str(foreign_key_raw).strip() != "":
            evidence_bits.append(
                f"foreign_key={'true' if foreign_key_value else 'false'}"
            )
        if field_type:
            evidence_bits.append(f"type={field_type}")
        normalized_evidence = _redact_text("; ".join(evidence_bits), 320)
        rows.append({
            "field_id": f"field:{source_id}:{_short_hash({'table': table_name or 'default', 'field': field_name})}",
            "source_id": source_id,
            "table": table_name or "default",
            "table_id": f"table:{table_name or 'default'}",
            "field": field_name,
            "field_path": field_name,
            "type": field_type,
            "required": required_value,
            "foreign_key": foreign_key_value,
            "constraint": _redact_text(constraint, 160),
            "identity": _declares_identity(constraint) or _doc_bool(
                _pick_first(item, ("primary_key", "primaryKey", "unique", "is_unique", "主键", "唯一"))
            ) is True,
            "description": _redact_text(description, 320),
            "normalized_evidence": normalized_evidence,
            "evidence_kind": "NORMALIZED_STRUCTURED_DECLARATION",
            "evidence_derivation": "normalized_field_dictionary_projection",
            "tokens": sorted(_tokens(f"{table_name} {field_name} {field_type} {description}")),
        })
    rows.extend(_infer_field_rows_from_markdown(text, source_id))
    return _dedupe_by_id(rows, "field_id")


def _field_dictionary_tables(entries: list[dict[str, Any]], source_id: str = "") -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in entries:
        if isinstance(row, dict):
            grouped[str(row.get("table") or "default")].append(row)
    tables: list[dict[str, Any]] = []
    for table_name, items in grouped.items():
        columns = sorted({str(item.get("field") or "") for item in items if str(item.get("field") or "")})
        aliases = sorted({str(item.get("table_alias") or "") for item in items} - {""})
        identity_fields = sorted({
            str(item.get("field") or "")
            for item in items
            if item.get("identity") is True and str(item.get("field") or "")
        })
        tables.append({
            "table_id": f"table:{table_name}",
            "source_id": source_id,
            "name": table_name,
            "aliases": aliases,
            "columns": columns,
            "identity_fields": identity_fields,
            "foreign_keys": [],
            "field_dictionary": items,
            "derivation": "field_dictionary_grouping",
            "tokens": sorted(_tokens(f"{table_name} {' '.join(columns)} {' '.join(str(item.get('description') or '') for item in items[:12])}")),
        })
    return tables


def _constraint_list_identity_fields(text: str) -> dict[str, list[str]]:
    """Identity columns from constraint lists that sit outside any table.

    Source documents commonly summarize keys separately from the field
    definitions, one entity per line, e.g. "products: UNIQUE(sku), INDEX(org)".
    Only PRIMARY KEY / UNIQUE declarations are identity; INDEX is not.
    """
    found: dict[str, list[str]] = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip().lstrip("-*• \t")
        label, sep, remainder = line.partition(":")
        if not sep or not label.strip():
            continue
        columns = _identity_columns_from_constraint_calls(remainder)
        if not columns:
            continue
        table_name, _alias = _canonical_entity_name(label)
        if not table_name:
            continue
        bucket = found.setdefault(table_name, [])
        for column in columns:
            if column not in bucket:
                bucket.append(column)
    return found


def _apply_constraint_list_identities(
    tables: list[dict[str, Any]],
    text: str,
) -> list[dict[str, Any]]:
    """Attach constraint-list identity columns to the tables they name.

    A constraint list only qualifies columns of an entity the source already
    declared; it never creates an entity on its own.
    """
    declared = _constraint_list_identity_fields(text)
    if not declared:
        return tables
    for table in tables:
        if not isinstance(table, dict):
            continue
        canonical, _alias = _canonical_entity_name(str(table.get("name") or ""))
        columns = declared.get(canonical)
        if not columns:
            continue
        known = {str(c) for c in table.get("columns") or []}
        table["identity_fields"] = sorted(
            {str(c) for c in table.get("identity_fields") or [] if str(c)}
            # A constraint list can name a column the field table never defined;
            # that is a source gap, not an identity field we can bind.
            | {c for c in columns if c in known}
        )
    return tables


def _merge_table_identities(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold table declarations that resolve to the same canonical identity.

    Once entity names are canonicalized the same entity is routinely declared more
    than once — an inventory table, a data dictionary section and an OpenAPI schema
    may each contribute part of it. Deduplicating by identity would silently drop
    the later declarations along with their columns, so each one is merged in and
    its origin retained in `source_refs` / `derivations`.
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in tables:
        if not isinstance(row, dict):
            continue
        table_id = str(row.get("table_id") or "")
        if not table_id:
            continue
        derivation = str(row.get("derivation") or "")
        source_id = str(row.get("source_id") or "")
        existing = merged.get(table_id)
        if existing is None:
            record = dict(row)
            record["aliases"] = sorted({str(a) for a in row.get("aliases") or [] if str(a)})
            record["source_refs"] = sorted({source_id} - {""})
            record["derivations"] = sorted({derivation} - {""})
            record["declaration_count"] = 1
            merged[table_id] = record
            order.append(table_id)
            continue
        existing["columns"] = sorted(
            {str(c) for c in existing.get("columns") or []}
            | {str(c) for c in row.get("columns") or []}
        )
        existing["field_dictionary"] = _dedupe_by_id(
            [*(existing.get("field_dictionary") or []), *(row.get("field_dictionary") or [])],
            "field_id",
        )
        seen_fk = {json.dumps(fk, sort_keys=True, default=str) for fk in existing.get("foreign_keys") or []}
        for fk in row.get("foreign_keys") or []:
            key = json.dumps(fk, sort_keys=True, default=str)
            if key not in seen_fk:
                seen_fk.add(key)
                existing.setdefault("foreign_keys", []).append(fk)
        existing["identity_fields"] = sorted(
            {str(c) for c in existing.get("identity_fields") or [] if str(c)}
            | {str(c) for c in row.get("identity_fields") or [] if str(c)}
        )
        existing["aliases"] = sorted(
            {str(a) for a in existing.get("aliases") or [] if str(a)}
            | {str(a) for a in row.get("aliases") or [] if str(a)}
        )
        existing["source_refs"] = sorted({*(existing.get("source_refs") or []), source_id} - {""})
        existing["derivations"] = sorted({*(existing.get("derivations") or []), derivation} - {""})
        existing["declaration_count"] = int(existing.get("declaration_count") or 1) + 1
        if not str(existing.get("description") or "") and row.get("description"):
            existing["description"] = row.get("description")
        existing["tokens"] = sorted(
            {str(t) for t in existing.get("tokens") or []}
            | {str(t) for t in row.get("tokens") or []}
        )
    return [merged[table_id] for table_id in order]


_SOURCE_CODE_HTTP_METHODS = frozenset(
    {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
)
_SOURCE_CODE_METHOD_CALL_RE = re.compile(
    r"(?ix)\b(?:app|router|server|api|blueprint|bp|routes?)\s*\.\s*"
    r"(?P<method>get|post|put|patch|delete|head|options)\s*\(\s*"
    r"['\"](?P<path>/[^'\"\r\n]+)['\"]"
)
_SOURCE_CODE_ROUTE_DECORATOR_RE = re.compile(
    r"(?ix)\b(?:app|router|blueprint|bp)\s*\.\s*"
    r"(?P<decorator>route|api_route)\s*\(\s*"
    r"['\"](?P<path>/[^'\"\r\n]+)['\"](?P<tail>[^)\r\n]*)\)"
)
_SOURCE_CODE_MAPPING_RE = re.compile(
    r"(?ix)(?:@|\[)\s*(?P<annotation>"
    r"getmapping|postmapping|putmapping|patchmapping|deletemapping|"
    r"requestmapping|httpget|httppost|httpput|httppatch|httpdelete)"
    r"\s*(?:\((?P<args>[^)\r\n]*)\))?\s*\]?"
)
_SOURCE_CODE_ROUTE_OBJECT_RE = re.compile(
    r"(?ix)\b(?:app|router|server|api|fastify)\s*\.\s*route\s*\(\s*\{"
    r"(?P<body>[^}\r\n]*)\}\s*\)"
)
_SOURCE_CODE_QUOTED_PATH_RE = re.compile(r"['\"](?P<path>/[^'\"\r\n]+)['\"]")
_SOURCE_CODE_METHOD_LITERAL_RE = re.compile(
    r"['\"](?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)['\"]",
    flags=re.IGNORECASE,
)
_SOURCE_CODE_ROUTE_SIGNAL_RE = re.compile(
    r"(?ix)(?:\.\s*(?:get|post|put|patch|delete|head|options|route|api_route)\s*\(|"
    r"@\s*(?:getmapping|postmapping|putmapping|patchmapping|deletemapping|requestmapping)\b|"
    r"\[\s*http(?:get|post|put|patch|delete)\b)"
)


def _source_code_path_parameters(path: str) -> list[str]:
    parameters: list[str] = []
    for match in re.finditer(
        r":(?P<colon>[A-Za-z_][A-Za-z0-9_]*)|"
        r"\{(?P<brace>[^{}]+)\}|"
        r"<(?P<angle>[A-Za-z_][A-Za-z0-9_]*)>",
        path,
    ):
        value = next(
            (
                match.group(name)
                for name in ("colon", "brace", "angle")
                if match.group(name)
            ),
            "",
        )
        value = value.strip()
        if value and value not in parameters:
            parameters.append(value)
    return parameters


def _source_code_operations(
    text: str,
    source_id: str = "",
    filename: str = "",
) -> list[dict[str, Any]]:
    """Extract only literal server-route declarations from implementation sources.

    This is a source-evidence adapter, not a code execution engine. It records an
    exact method/path and locator, while deliberately leaving request bodies,
    response schemas, middleware effects, and computed paths unresolved.
    """

    candidates: list[tuple[str, str, int, str]] = []
    for line_number, line in enumerate(str(text or "").splitlines(), start=1):
        if str(line).lstrip().startswith(("//", "#", "/*", "*", "<!--")):
            continue
        for match in _SOURCE_CODE_METHOD_CALL_RE.finditer(line):
            candidates.append(
                (
                    str(match.group("method") or "").upper(),
                    str(match.group("path") or ""),
                    line_number,
                    line,
                )
            )
        for match in _SOURCE_CODE_ROUTE_DECORATOR_RE.finditer(line):
            methods = [
                str(row.group("method") or "").upper()
                for row in _SOURCE_CODE_METHOD_LITERAL_RE.finditer(
                    str(match.group("tail") or "")
                )
            ]
            for method in methods:
                candidates.append(
                    (method, str(match.group("path") or ""), line_number, line)
                )
        for match in _SOURCE_CODE_MAPPING_RE.finditer(line):
            annotation = str(match.group("annotation") or "").lower()
            args = str(match.group("args") or "")
            path_match = _SOURCE_CODE_QUOTED_PATH_RE.search(args)
            if not path_match:
                continue
            path = str(path_match.group("path") or "")
            if annotation == "requestmapping":
                methods = []
                methods.extend(
                    str(row.group("method") or "").upper()
                    for row in re.finditer(
                        r"(?ix)RequestMethod\s*\.\s*"
                        r"(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)",
                        args,
                    )
                )
                methods.extend(
                    str(row.group("method") or "").upper()
                    for row in _SOURCE_CODE_METHOD_LITERAL_RE.finditer(args)
                )
            else:
                methods = [
                    {
                        "getmapping": "GET",
                        "postmapping": "POST",
                        "putmapping": "PUT",
                        "patchmapping": "PATCH",
                        "deletemapping": "DELETE",
                        "httpget": "GET",
                        "httppost": "POST",
                        "httpput": "PUT",
                        "httppatch": "PATCH",
                        "httpdelete": "DELETE",
                    }.get(annotation, "")
                ]
            for method in methods:
                candidates.append((method, path, line_number, line))
        for match in _SOURCE_CODE_ROUTE_OBJECT_RE.finditer(line):
            body = str(match.group("body") or "")
            path_match = re.search(
                r"(?ix)(?:url|path)\s*:\s*['\"](?P<path>/[^'\"\r\n]+)['\"]",
                body,
            )
            if not path_match:
                continue
            methods = [
                str(row.group("method") or "").upper()
                for row in _SOURCE_CODE_METHOD_LITERAL_RE.finditer(body)
            ]
            for method in methods:
                candidates.append(
                    (method, str(path_match.group("path") or ""), line_number, line)
                )

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, int]] = set()
    for method, path, line_number, line in candidates:
        method = str(method or "").upper().strip()
        path = str(path or "").strip()
        if method not in _SOURCE_CODE_HTTP_METHODS or not path.startswith("/"):
            continue
        identity = (method, path, line_number)
        if identity in seen:
            continue
        seen.add(identity)
        locator = f"{source_id}:line:{line_number}"
        parameters = _source_code_path_parameters(path)
        rows.append(
            {
                "interface_id": (
                    f"source_code:{method}:{path}:"
                    f"{_short_hash({'source_id': source_id, 'line': line_number})}"
                ),
                "source_id": source_id,
                "source_kind": "source_code",
                "method": method,
                "path": path,
                "operation_id": _safe_slug(
                    f"{method.lower()}_{path.strip('/').replace('/', '_') or 'root'}",
                    64,
                ),
                "summary": f"Source-declared {method} {path}",
                "parameters": parameters,
                "source_locator": locator,
                "source_file": str(filename or ""),
                "source_excerpt": _redact_text(str(line).strip(), 900),
                "derivation": "source_code_http_declaration",
                "tokens": sorted(_tokens(f"{method} {path} {' '.join(parameters)}")),
            }
        )
    return rows


_MARKDOWN_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Foreign-key field-name heuristic for markdown contract detection. Matches
# snake_case (*_id), camelCase (*Id), and coupon reference codes. Used only to
# *declare* x-foreign-key on request_schema.properties; an explicit `外键=是`
# table column overrides via the parsed field_dictionary entry.
_FK_NAME_RE = re.compile(r"(?i)(_id$|Id$|coupon_?code$|^code$)")

# Declared role terms used to detect contract-stated actor permission
# requirements in markdown specs (e.g. "seller/admin 可用" or
# "**所需角色**：seller, admin"). An operation declaring one of these for an
# actor is a 403 candidate when the actor's role is absent from the list.
_ROLE_TERMS = frozenset({
    "buyer", "seller", "admin", "manager", "guest", "owner",
    "operator", "user", "tenant", "member",
})
# Matches an explicit "所需角色：seller, admin" / "required_roles: admin" line.
# Allows optional Markdown emphasis markers (e.g. "**所需角色**：admin") between
# the label and the colon, which is how the benchmark_mall contract is written.
_ROLE_DECL_RE = re.compile(r"(?:所需角色|required_roles|roles?)\s*\*{0,2}\s*[:：]\s*([^\n*]+)", re.IGNORECASE)
# Matches the informal "seller/admin 可用" (dual, slash-separated) or
# "admin 可用" (single role) phrasings common in benchmark_mall.
_ROLE_PHRASE_RE = re.compile(r"([a-z]+)\s*(?:/\s*([a-z]+))?\s*可用", re.IGNORECASE)


def _markdown_required_roles(section: str) -> list[str]:
    """Extract actor roles required to invoke an operation from its markdown section.

    Returns roles declared via an explicit ``所需角色`` / ``required_roles`` line,
    or via the informal ``X/Y 可用`` phrasing. Empty when the contract states no
    role restriction (the permission guard is then a no-op and never blocks).
    """
    text = section or ""
    roles: set[str] = set()
    for m in _ROLE_DECL_RE.finditer(text):
        for tok in re.split(r"[,\uFF0C\u3001/\s]+", m.group(1)):
            t = tok.strip().lower()
            if t in _ROLE_TERMS:
                roles.add(t)
    for m in _ROLE_PHRASE_RE.finditer(text):
        for g in (m.group(1), m.group(2)):
            if not g:
                continue
            t = g.strip().lower()
            if t in _ROLE_TERMS:
                roles.add(t)
    return sorted(roles)


def _markdown_api_operations(text: str, source_id: str = "") -> list[dict[str, Any]]:
    matches = list(MARKDOWN_API_ENDPOINT_RE.finditer(text or ""))
    rows: list[dict[str, Any]] = []
    if not matches:
        return rows
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text or "")
        section = str(text or "")[start:end]
        methods = [part.strip().upper() for part in re.split(r"\s*/\s*", match.group("methods")) if part.strip()]
        json_examples = _json_blocks(section)
        example_fields = sorted({name for sample in json_examples[:2] for name in _flatten_json_field_names(sample)})
        entries = _field_dictionary_entries(section, None, source_id)
        table_fields = [str(row.get("field") or "") for row in entries]
        required_fields = [
            str(row.get("field"))
            for row in entries
            if row.get("required") is True and row.get("field")
        ]
        foreign_key_fields = [
            str(row.get("field"))
            for row in entries
            if row.get("field") and (
                row.get("foreign_key") is True
                or _FK_NAME_RE.search(str(row.get("field") or ""))
            )
        ]
        # Contract-declared actor permission requirement (e.g. "seller/admin 可用"
        # or an explicit "所需角色" line). Drives the pre-transport actor
        # permission guard (§8.5.4). Empty => guard is a no-op.
        required_roles = _markdown_required_roles(section)
        all_fields = sorted({field for field in [*example_fields, *table_fields] if field})
        summary_line = next((line.strip(" #-*") for line in section.splitlines() if line.strip() and not line.strip().startswith("|")), "")
        tag_candidates = re.findall(r"`([A-Za-z0-9_\-]{2,40})`", section[:600])
        for method in methods:
            path = str(match.group("path") or "/")
            operation: dict[str, Any] = {
                "interface_id": f"markdown_api:{method}:{path}",
                "source_id": source_id,
                "source_kind": "markdown_api",
                "method": method,
                "path": path,
                "operation_id": _safe_slug(f"{method.lower()}_{path.strip('/').replace('/', '_').replace('{', '').replace('}', '') or 'root'}", 64),
                "summary": summary_line or f"{method} {path}",
                "tags": sorted(set(tag_candidates[:8])),
                "parameters": [field.split(".", 1)[0].replace("[]", "") for field in all_fields[:24]],
                "field_dictionary": all_fields[:40],
                "source_excerpt": _redact_text((match.group(0) + "\n" + section[:900]).strip(), 900),
                "tokens": sorted(_tokens(f"{path} {summary_line} {' '.join(all_fields)} {' '.join(tag_candidates[:8])}")),
            }
            # Surface contract-declared required request fields as request_schema so the
            # required-body-field pre-transport guard (_missing_required_body_fields) can block
            # malformed payloads (e.g. benchmark_mall POST /api/products/admin missing `sku`)
            # before any HTTP call. Only for write methods: GET query params must not be
            # mistaken for required body fields.
            if method in _MARKDOWN_WRITE_METHODS:
                request_props: dict[str, Any] = {}
                for row in entries:
                    fname = row.get("field")
                    if not fname:
                        continue
                    if fname not in required_fields and fname not in foreign_key_fields:
                        continue
                    prop: dict[str, Any] = {
                        "type": str(row.get("type") or "string") or "string",
                        "description": _redact_text(str(row.get("description") or ""), 200),
                    }
                    if fname in foreign_key_fields:
                        prop["x-foreign-key"] = True
                    request_props[str(fname)] = prop
                # Attach the contract's request example (template tokens like
                # "<order_id>" / "<address_id>" kept intact) as
                # request_schema.content so the binding graph can detect body
                # placeholders and either resolve them (e.g. order_id via
                # GET /api/orders) or fail-fast with BLOCKED_MISSING_BINDING
                # instead of leaking literal tokens to the target. Without this,
                # build_binding_plan sees zero placeholders and silently lets
                # the operation proceed to an HTTP call with unresolved tokens.
                # Many write endpoints (POST /api/orders, /payments/pay,
                # /refunds, /inventory/*, /coupons/use) declare their request
                # body only via a "请求" json example and have no field table,
                # so this must run for every write method, not only those with
                # a required/foreign-key field dictionary.
                request_content: dict[str, Any] = {}
                if json_examples:
                    request_content["application/json"] = {"example": json_examples[0]}
                if request_props or request_content:
                    operation["request_schema"] = {
                        "type": "object",
                        "required": required_fields,
                        "properties": request_props,
                        **({"content": request_content} if request_content else {}),
                    }
            # Declared role restriction applies to every method (403s also hit
            # admin read/management endpoints), so attach it unconditionally.
            if required_roles:
                operation["required_roles"] = required_roles
            rows.append(operation)
    return rows


def _sql_table_body_declarations(body: str) -> list[str]:
    """Split a CREATE TABLE body into top-level column/constraint declarations.

    Newline-oriented parsing misses single-line DDL where columns are comma-
    separated inside one ``(...)`` group. Split on commas that are outside
    nested parentheses so ``CHECK (role IN (...))`` stays intact. Newlines are
    treated as whitespace so both compact and pretty-printed DDL share one path.
    """
    declarations: list[str] = []
    current: list[str] = []
    depth = 0
    for char in str(body or ""):
        if char == "(":
            depth += 1
            current.append(char)
            continue
        if char == ")":
            depth = max(0, depth - 1)
            current.append(char)
            continue
        if char == "," and depth == 0:
            piece = "".join(current).strip()
            if piece:
                declarations.append(piece)
            current = []
            continue
        current.append(char)
    piece = "".join(current).strip()
    if piece:
        declarations.append(piece)
    return declarations


def _sql_tables(text: str, source_id: str = "") -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for match in re.finditer(r"(?is)create\s+table\s+(?:if\s+not\s+exists\s+)?[`\"\[]?([a-zA-Z0-9_]+)[`\"\]]?\s*\((.*?)\)\s*;", text):
        name, body = match.group(1), match.group(2)
        columns: list[str] = []
        foreign_keys: list[str] = []
        identity_fields: list[str] = []
        unique_columns: list[str] = []
        column_types: dict[str, str] = {}
        check_constraints: list[dict[str, Any]] = []
        for declaration in _sql_table_body_declarations(body):
            clean = declaration.strip().strip(",")
            if not clean:
                continue
            ref = re.search(r"(?i)references\s+[`\"\[]?([a-zA-Z0-9_]+)", clean)
            if ref:
                foreign_keys.append(ref.group(1))
            col = re.match(r"[`\"\[]?([a-zA-Z_][a-zA-Z0-9_]*)[`\"\]]?\s+", clean)
            if col and col.group(1).lower() not in {"primary", "foreign", "constraint", "unique", "key", "index", "check"}:
                column = col.group(1)
                columns.append(column)
                column_types[column] = _sql_column_type(clean[col.end():])
                # Inline form: "sku TEXT UNIQUE" / "id SERIAL PRIMARY KEY".
                if _declares_identity(clean[col.end():]):
                    identity_fields.append(column)
                if re.search(r"(?i)\bunique\b", clean[col.end():]):
                    unique_columns.append(column)
            else:
                # Table-constraint form: "PRIMARY KEY (id)" / "UNIQUE (sku, org)".
                identity_fields.extend(_identity_columns_from_constraint_calls(clean))
                unique_columns.extend(_unique_columns_from_constraint_calls(clean))
            check_constraints.extend(_check_constraint_columns(clean))
        tables.append({
            "table_id": f"table:{name}",
            "source_id": source_id,
            "name": name,
            "columns": sorted(set(columns)),
            "identity_fields": sorted(set(identity_fields)),
            "unique_columns": sorted(set(unique_columns)),
            "column_types": {
                key: column_types[key] for key in sorted(column_types)
            },
            "check_constraints": check_constraints,
            "foreign_keys": sorted(set(foreign_keys)),
            "derivation": "sql_ddl",
            "tokens": sorted(_tokens(f"{name} {' '.join(columns)} {' '.join(foreign_keys)}")),
        })
    return tables


_SQL_TYPE_TOKENS = re.compile(
    r"(?i)(numeric|decimal|int|integer|bigint|smallint|tinyint|float|double|real|money|serial|text|varchar|char|uuid|boolean|bool|timestamptz|timestamp|date|time|json|jsonb|bytea)"
)


def _sql_column_type(rest: str) -> str:
    """Normalize the declared SQL column type (e.g. NUMERIC(12,2) -> numeric).

    Only generic SQL vocabulary; unknown declarations stay empty rather than
    being guessed. Used by the schema-constraint dimension to decide which
    industry-universal invariants a column can carry (numeric boundaries,
    uniqueness intent).
    """
    match = _SQL_TYPE_TOKENS.search(rest)
    if not match:
        return ""
    return match.group(1).lower()


def _unique_columns_from_constraint_calls(clean: str) -> list[str]:
    """Columns named by a table-level ``UNIQUE (...)`` constraint call."""
    if not re.match(r"(?i)\bunique\b", clean):
        return []
    return _columns_from_constraint_parens(clean)


def _columns_from_constraint_parens(clean: str) -> list[str]:
    out: list[str] = []
    for match in re.finditer(r"\(([^()]*)\)", clean):
        for part in re.split(r"[, ]+", match.group(1)):
            token = part.strip().strip("`\"[]")
            if re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", token):
                out.append(token)
    return out


def _check_constraint_columns(clean: str) -> list[dict[str, Any]]:
    """Per-column constraints declared by ``CHECK (...)`` clauses.

    Parses the common DDL shapes only (``col >= 0``, ``col > 0``,
    ``col IN (...)``); anything else is left out of the structured list. The
    presence of a guard is what the schema-constraint dimension reads — a
    money/quantity column without any non-negative CHECK is where the
    industry-universal boundary invariant applies.
    """
    if not re.search(r"(?i)\bcheck\b", clean):
        return []
    out: list[dict[str, Any]] = []
    # Greedy to the LAST closing paren: CHECK bodies are single-level, and a
    # left-to-right innermost scan would grab the value list parens instead of
    # the constraint expression.
    for match in re.finditer(r"(?i)\bcheck\s*\((.*)\)", clean):
        body = match.group(1).strip()
        if not body:
            continue
        column = re.match(r"[`\"\[]?([a-zA-Z_][a-zA-Z0-9_]*)[`\"\]]?\s*", body)
        if not column:
            continue
        column_name = column.group(1)
        remainder = body[column.end():].strip()
        operator = re.match(r"(>=|<=|>|<|=|!=|<>|in|IN)", remainder)
        if not operator:
            continue
        operator_text = operator.group(1).lower()
        value_text = remainder[operator.end():].strip().strip("'\"")
        if operator_text == "in":
            values = [
                item.strip().strip("'\"")
                for item in value_text.strip("()").split(",")
                if item.strip()
            ]
            out.append({
                "column": column_name,
                "operator": "in",
                "values": values,
            })
        else:
            out.append({
                "column": column_name,
                "operator": operator_text,
                "value": value_text,
            })
    return out


def _json_schema_tables(payload: Any, source_id: str = "") -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    candidates: list[tuple[str, Any]] = []
    if isinstance(payload, dict):
        schemas = ((payload.get("components") or {}).get("schemas") if isinstance(payload.get("components"), dict) else None) or payload.get("schemas") or payload.get("tables")
        if isinstance(schemas, dict):
            candidates = list(schemas.items())
        elif isinstance(schemas, list):
            candidates = [(str(item.get("name") or item.get("table") or f"table_{idx+1}"), item) for idx, item in enumerate(schemas) if isinstance(item, dict)]
    for name, body in candidates:
        props = body.get("properties") if isinstance(body, dict) else {}
        columns = list(props.keys()) if isinstance(props, dict) else list(body.get("columns") or []) if isinstance(body, dict) else []
        foreign_keys = [str(x) for x in (body.get("foreign_keys") or body.get("relations") or [])] if isinstance(body, dict) else []
        tables.append({
            "table_id": f"table:{name}", "source_id": source_id, "name": str(name),
            "columns": [str(x) for x in columns], "foreign_keys": foreign_keys,
            "tokens": sorted(_tokens(f"{name} {' '.join(str(x) for x in columns)} {' '.join(foreign_keys)}")),
        })
    return tables


def _uiux_specs_from_text(text: str, source_id: str, source_type: str, filename: str) -> list[dict[str, Any]]:
    if source_type not in {"uiux_spec", "uiux_svg"}:
        return []
    specs: list[dict[str, Any]] = []
    title = _clean_markup_text(next(iter(SVG_TITLE_RE.findall(text or "")), "")) if source_type == "uiux_svg" else ""
    description = _clean_markup_text(next(iter(SVG_DESC_RE.findall(text or "")), ""))
    text_labels = [_clean_markup_text(item, 80) for item in SVG_TEXT_RE.findall(text or "")]
    attr_labels = [_clean_markup_text(item, 80) for item in SVG_TAG_ATTR_RE.findall(text or "")]
    labels = [label for label in [*text_labels, *attr_labels] if label]
    component_keywords = re.findall(r"(?im)(?:component|组件|控件|button|input|table|modal|drawer|chart|card)\s*[:：-]?\s*([A-Za-z0-9_\-\u4e00-\u9fff ]{2,60})", text or "")
    state_keywords = re.findall(r"(?im)^\s*(?:state|states|状态)\s*[:：-]\s*([A-Za-z0-9_\-\u4e00-\u9fff、, /]{2,120})\s*$", text or "")
    components = sorted({label for label in [*component_keywords, *labels[:20]] if label})[:24]
    states: list[str] = []
    for item in state_keywords:
        states.extend([part.strip() for part in re.split(r"[,/|、]", item) if part.strip()])
    known_state_labels = ("Loading", "Error", "Empty", "Success", "加载", "错误", "空状态", "成功")
    for label in labels:
        for token in known_state_labels:
            if _norm(token) in _norm(label):
                states.append(label)
                break
    if not states:
        low = _norm(text[:8000])
        for token in ("loading", "error", "empty", "success", "加载", "错误", "空状态", "成功"):
            if _norm(token) in low:
                states.append(token)
    name = title or Path(filename).stem
    specs.append({
        "ui_spec_id": f"ui:{source_id}:{_short_hash({'name': name, 'type': source_type})}",
        "source_id": source_id,
        "source_type": source_type,
        "name": name,
        "description": _redact_text(description or " ".join(labels[:6]), 320),
        "components": components,
        "states": sorted(set(states))[:12],
        "text_labels": labels[:30],
        "tokens": sorted(_tokens(f"{name} {description} {' '.join(components)} {' '.join(labels[:20])}")),
    })
    return specs




def _uiux_requirements_from_json(
    payload: dict[str, Any],
    source_id: str,
    filename: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Parse a structured UI/UX requirements document into UI specs + rules.

    The document (Benchmark UI/UX requirements JSON) declares screens with
    URLs, a role visibility matrix, an order-action button matrix, numbered
    requirements and executable UI oracles. Each oracle becomes a grounded
    rule (rule_type=ui_state_consistency) whose precondition/action/expected
    ride as structured fields; each screen becomes a UI design spec carrying
    its URL, regions and interaction matrices. All values come from the
    document — nothing is inferred or industry-hardcoded.
    """
    specs: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    doc_name = str((payload.get("document") or {}).get("name") or Path(filename).stem)
    for screen in list(payload.get("screens")):
        if not isinstance(screen, dict):
            continue
        screen_id = _clean_markup_text(screen.get("id")) or f"screen_{len(specs) + 1}"
        spec: dict[str, Any] = {
            "ui_spec_id": f"ui:{source_id}:{screen_id}",
            "source_id": source_id,
            "source_type": "uiux_requirements",
            "name": _clean_markup_text(screen.get("name")) or screen_id,
            "url": _clean_markup_text(screen.get("url")),
            "regions": [
                _clean_markup_text(region)
                for region in list(screen.get("regions"))
                if _clean_markup_text(region)
            ],
            "viewport": dict(screen.get("viewport") or {}),
            "states": ["loading", "error", "empty", "success"],
        }
        role_visibility = payload.get("role_visibility")
        if isinstance(role_visibility, dict):
            spec["role_visibility"] = role_visibility
        action_matrix = payload.get("order_action_matrix")
        if isinstance(action_matrix, dict):
            spec["order_action_matrix"] = action_matrix
        specs.append(spec)

    for requirement in list(payload.get("requirements")):
        if not isinstance(requirement, dict):
            continue
        req_id = _clean_markup_text(requirement.get("id"))
        rule_text = _clean_markup_text(requirement.get("rule"))
        if not req_id or not rule_text:
            continue
        statement = f"{req_id}：{rule_text}"
        rule: dict[str, Any] = {
            "rule_id": f"rule:{source_id}:{req_id}",
            "source_id": source_id,
            "statement": statement,
            "rule_type": "ui_state_consistency",
            "risk_type": "ui",
            "severity": "P1",
            "tokens": sorted(_tokens(statement)),
        }
        for key in ("screen", "type", "field", "negative_examples"):
            if requirement.get(key) is not None:
                rule[key] = requirement[key]
        rules.append(rule)

    for oracle in list(payload.get("oracles")):
        if not isinstance(oracle, dict):
            continue
        oracle_id = _clean_markup_text(oracle.get("id"))
        # The document's oracles are Gherkin-shaped ({given, when, then}); a
        # flat rule/expected text is only one possible form. Without a flat
        # text the expectation sentences are the rule statement — dropping
        # them silently would hide every oracle from the obligation chain.
        then_rows = [
            _clean_markup_text(item)
            for item in list(oracle.get("then"))
            if _clean_markup_text(item)
        ]
        rule_text = (
            _clean_markup_text(oracle.get("rule"))
            or _clean_markup_text(oracle.get("expected"))
            or " ".join(then_rows)
        )
        if not oracle_id or not rule_text:
            continue
        statement = f"{oracle_id}：{rule_text}"
        ui_oracle = {
            key: oracle[key]
            for key in ("given", "when", "then", "precondition", "action", "expected")
            if oracle.get(key) is not None
        }
        rule: dict[str, Any] = {
            "rule_id": f"rule:{source_id}:{oracle_id}",
            "source_id": source_id,
            "statement": statement,
            "rule_type": "ui_state_consistency",
            "risk_type": "ui",
            "severity": "P1",
            "tokens": sorted(_tokens(statement)),
            "ui_oracle": ui_oracle,
        }
        rules.append(rule)
    return specs, rules


def _markdown_table_rows(text: str) -> list[dict[str, str]]:
    """Parse ordinary Markdown tables without assuming a document language."""
    lines = [line.strip() for line in str(text or "").splitlines()]
    rows: list[dict[str, str]] = []
    index = 0
    while index + 1 < len(lines):
        header_line = lines[index]
        separator_line = lines[index + 1]
        if "|" not in header_line or "|" not in separator_line:
            index += 1
            continue
        headers = [cell.strip() for cell in header_line.strip("|").split("|")]
        separators = [cell.strip() for cell in separator_line.strip("|").split("|")]
        if (
            not headers
            or len(headers) != len(separators)
            or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separators)
        ):
            index += 1
            continue
        index += 2
        while index < len(lines) and "|" in lines[index]:
            values = [cell.strip() for cell in lines[index].strip("|").split("|")]
            if len(values) == len(headers) and any(values):
                rows.append(dict(zip(headers, values)))
            index += 1
    return rows


def _permission_field(item: dict[str, Any], aliases: set[str]) -> Any:
    for key, value in item.items():
        if _norm(key).replace(" ", "_") in aliases:
            return value
    return ""


def _permission_decision(item: dict[str, Any], narrative: str) -> str:
    for key, value in item.items():
        normalized_key = _norm(key).replace(" ", "_")
        if normalized_key in {"allowed", "is_allowed"} and isinstance(value, bool):
            return "allow" if value else "deny"
    raw = _permission_field(
        item,
        {"decision", "effect", "outcome", "access", "policy_effect"},
    )
    normalized = _norm(raw or narrative).replace("-", " ").replace("_", " ")
    decision_markers = _lexicon_dict("permission_decision_markers")
    deny_markers = decision_markers.get("deny") or [
        "deny",
        "denied",
        "forbid",
        "forbidden",
        "not allowed",
        "cannot",
        "prohibit",
        "\u4e0d\u5f97",
        "\u7981\u6b62",
    ]
    if any(_norm(marker) in normalized for marker in deny_markers):
        return "deny"
    allow_markers = decision_markers.get("allow") or [
        "allow",
        "allowed",
        "grant",
        "permit",
        "\u5141\u8bb8",
        "\u6388\u6743",
    ]
    if raw and any(_norm(marker) in normalized for marker in allow_markers):
        return "allow"
    positive_permission_fields = {
        "action",
        "actions",
        "allowed_actions",
        "capability",
        "capabilities",
        "permission",
        "permissions",
        "\u6743\u9650",
        "\u6743\u9650\u8bf4\u660e",
        "\u64cd\u4f5c",
        "\u80fd\u529b",
    }
    if item.get("__permission_declaration_table") is True and narrative and any(
        _norm(key).replace(" ", "_") in positive_permission_fields
        for key in item
    ):
        return "allow"
    return ""


def _permission_action_values(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if not str(value or "").strip():
        return []
    return _permission_action_aliases(value)


def _permission_resource_aliases(value: Any) -> list[str]:
    text = str(value or "").strip()
    normalized = _norm(text)
    if not normalized:
        return []
    aliases: list[str] = []
    for source_token, target_tokens in _lexicon_dict("entity_token_lexicon").items():
        candidates = [source_token, *target_tokens]
        if any(_norm(token) and _norm(token) in normalized for token in candidates):
            aliases.extend(str(token).strip().lower() for token in target_tokens if str(token).strip())
    return list(dict.fromkeys(aliases))


def _permission_action_aliases(value: Any) -> list[str]:
    text = str(value or "").strip()
    normalized = _norm(text)
    if not normalized:
        return []
    actions: list[str] = []
    for source_token, target_tokens in _lexicon_dict("verb_action_lexicon").items():
        candidates = [source_token, *target_tokens]
        if any(_norm(token) and _norm(token) in normalized for token in candidates):
            actions.extend(str(token).strip().lower() for token in target_tokens if str(token).strip())
    for method in (*SAFE_METHODS, *WRITE_METHODS):
        if method.lower() in normalized.split():
            actions.append(method)
    return list(dict.fromkeys(actions))


def _permission_scope(value: Any) -> str:
    normalized = _norm(value)
    if any(token in normalized for token in ("自己的", "自己", "本人", "own", "self", "owned")):
        return "own"
    if any(token in normalized for token in (
        "other", "another", "different", "other_owner",
        "其他", "他人", "别人的", "非本人",
    )):
        return "other_owner"
    if any(token in normalized for token in ("tenant", "租户", "organization", "组织")):
        return "tenant"
    if any(token in normalized for token in ("所有", "全部", "all", "global")):
        return "all"
    return "unspecified"


def _negative_permission_clause(line: str) -> str:
    """Keep only the clause governed by a negative permission marker."""

    text = str(line or "").strip()
    if not text:
        return ""
    marker = re.compile(
        r"(?i)(?:cannot|can't|must\s+not|not\s+allowed|forbidden|"
        r"不得|不能|禁止|不允许)"
    )
    matches = list(marker.finditer(text))
    if not matches:
        return text
    clause = text[matches[-1].end():].strip(" \t:-：")
    if not clause:
        return text
    clause = re.split(r"[,;，；。！？]", clause, maxsplit=1)[0].strip()
    return clause or text


# ── Permission crosstab tables ──
# Rows are operations/resources, columns are roles, cells are decision glyphs.
# Only the glyph vocabulary is fixed; role and operation names come from the source.
_DECISION_ALLOW = {
    "✓", "✔", "√", "yes", "y", "true", "allow", "allowed", "grant", "granted",
    "rw", "r", "w", "是", "有", "可", "允许",
}
_DECISION_DENY = {
    "✗", "✘", "×", "x", "no", "n", "false", "deny", "denied", "forbidden",
    "-", "—", "–", "na", "n/a", "否", "无", "不可", "禁止",
}


def _decision_token(value: Any) -> str:
    """Normalize a decision cell without discarding symbol glyphs.

    The general `_norm` helper strips non-alphanumerics, which erases check and
    cross marks entirely — exactly the characters a permission matrix relies on.
    """
    return str(value or "").strip().lower()


def _permission_crosstab_entries(text: str, source_id: str) -> list[dict[str, Any]]:
    """Extract permissions from role-column decision matrices.

    A column qualifies as a role column when every populated cell is a decision
    glyph and at least one of them grants. A table needs two such columns before it
    is read as a matrix, which keeps ordinary data tables out.
    """
    from ._format_normalizer import extract_tables_from_markdown

    rows: list[dict[str, Any]] = []
    for table in extract_tables_from_markdown(text):
        headers = [str(h).strip() for h in table.get("headers") or []]
        table_rows = table.get("rows") or []
        if len(headers) < 3 or not table_rows:
            continue
        subject_header = headers[0]
        role_headers: list[str] = []
        for header in headers[1:]:
            if not header:
                continue
            values = [_decision_token(row.get(header, "")) for row in table_rows]
            populated = [v for v in values if v]
            if not populated:
                continue
            if not all(v in _DECISION_ALLOW or v in _DECISION_DENY for v in populated):
                continue
            if not any(v in _DECISION_ALLOW for v in populated):
                continue
            role_headers.append(header)
        if len(role_headers) < 2:
            continue
        section = str(table.get("source_locator") or "")
        for index, row in enumerate(table_rows):
            subject = str(row.get(subject_header, "")).strip()
            if not subject:
                continue
            for role in role_headers:
                decision_token = _decision_token(row.get(role, ""))
                if not decision_token:
                    continue
                allowed = decision_token in _DECISION_ALLOW
                actions = _permission_action_aliases(subject) or [subject]
                resource_aliases = _permission_resource_aliases(subject) or [subject.strip().lower()]
                rows.append({
                    "permission_id": f"perm:{source_id}:{_short_hash({'r': role, 's': subject, 'i': index})}",
                    "source_id": source_id,
                    "role": role,
                    "resource": subject,
                    "resource_aliases": resource_aliases,
                    "actions": actions if allowed else [],
                    "denied_actions": [] if allowed else actions,
                    "decision": "allow" if allowed else "deny",
                    "scope": "",
                    "derivation": "permission_crosstab",
                    "evidence": _redact_text(f"{section} | {subject} | {role} = {row.get(role, '')}", 280),
                })
    return rows


def _permission_evidence_excerpt(
    item: dict[str, Any],
    *,
    role: str,
    resource: str,
    decision: str,
    actions: list[str],
) -> str:
    """Build operator-visible evidence from exact source-declared permission facts.

    Prefer an explicit source ``evidence`` / quote string. Never dump ``str(dict)``
    of the whole permission row — that is parser noise, not source evidence.
    """
    raw = item.get("evidence")
    if isinstance(raw, str) and raw.strip():
        return _redact_text(raw.strip(), 280)
    if isinstance(raw, dict):
        quote = str(raw.get("quote") or raw.get("verbatim_quote") or "").strip()
        if quote:
            return _redact_text(quote, 280)
    for key in ("quote", "source_excerpt", "verbatim_quote"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return _redact_text(value.strip(), 280)
    bits: list[str] = []
    if role:
        bits.append(f"role={role}")
    if resource:
        bits.append(f"resource={resource}")
    if decision:
        bits.append(f"decision={decision}")
    if actions:
        bits.append(
            "actions=" + ",".join(str(action).strip() for action in actions if str(action).strip())
        )
    return _redact_text("; ".join(bits), 280)


_NON_PERMISSION_SOURCE_TYPES = frozenset({
    "test_data",
    "test_accounts",
    "credential_catalog",
    "credentials",
})
_COMPOSED_SOURCE_MARKER_RE = re.compile(
    r"<!--\s*qualibug:source\b(?P<attributes>.*?)-->",
    re.IGNORECASE | re.DOTALL,
)


def _permission_source_line_types(text: str, default_source_type: str) -> list[str]:
    """Return the declared source type for each line in a composed document."""

    current = str(default_source_type or "").strip().lower()
    types: list[str] = []
    for line in str(text or "").splitlines():
        marker = _COMPOSED_SOURCE_MARKER_RE.search(line)
        if marker:
            source_type_match = re.search(
                r"(?:^|\s)source_type=(?P<value>[^\s>]+)",
                str(marker.group("attributes") or ""),
                flags=re.IGNORECASE,
            )
            if source_type_match:
                current = str(source_type_match.group("value") or "").strip().lower()
        types.append(current)
    return types


def _permission_entries(
    text: str,
    payload: Any,
    source_id: str,
    source_type: str = "",
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    normalized_source_type = str(source_type or "").strip().lower()
    if normalized_source_type in _NON_PERMISSION_SOURCE_TYPES:
        return []
    source_line_types = _permission_source_line_types(text, normalized_source_type)
    if isinstance(payload, dict):
        for key in ("permissions", "matrix", "roles", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend([item for item in value if isinstance(item, dict)])
    elif isinstance(payload, list):
        candidates.extend([item for item in payload if isinstance(item, dict)])
    candidates.extend(_csv_rows(text))
    candidates.extend([
        {**row, "__permission_declaration_table": True}
        for row in _markdown_table_rows(text)
    ])
    for idx, item in enumerate(candidates):
        role = str(_permission_field(item, {"role", "actor", "user_role", "principal", "角色", "用户角色"}) or "").strip()
        resource = str(_permission_field(item, {"resource", "module", "object", "path", "endpoint", "资源", "模块", "对象", "接口"}) or "").strip()
        actions = _permission_field(item, {"actions", "action", "permissions", "permission", "operation", "allowed_actions", "权限", "权限说明", "操作", "能力"})
        scope_value = _permission_field(item, {"scope", "data_scope", "tenant_scope", "范围", "数据范围"})
        denied_actions = _permission_field(
            item,
            {"denied_actions", "forbidden_actions", "prohibited_actions"},
        )
        if not role or (not resource and not str(actions or denied_actions or "").strip()):
            continue
        narrative = str(actions or resource).strip()
        permission_decision = _permission_decision(item, narrative)
        denied_action_values = _permission_action_values(denied_actions)
        normalized_narrative = _norm(narrative)
        if any(marker in normalized_narrative for marker in ("所有权限", "全部权限", "all permissions", "full access")):
            rows.append({
                "permission_id": f"perm:{source_id}:{idx+1}:all",
                "source_id": source_id,
                "role": role,
                "resource": "*",
                "resource_aliases": ["*"],
                "actions": ["*"],
                **({"decision": permission_decision} if permission_decision else {}),
                **({"denied_actions": denied_action_values} if denied_action_values else {}),
                "scope": "all",
                "evidence": _permission_evidence_excerpt(
                    item,
                    role=role,
                    resource="*",
                    decision=permission_decision,
                    actions=["*"],
                ),
            })
            continue
        clauses = [part.strip() for part in re.split(r"[,;，；、。]", narrative) if part.strip()]
        if resource:
            clauses = [narrative]
        for clause_index, clause in enumerate(clauses or [narrative]):
            resource_aliases = _permission_resource_aliases(resource or clause)
            if resource and not resource_aliases:
                resource_aliases = [resource.strip().lower()]
            if not resource_aliases:
                continue
            if isinstance(actions, list):
                action_values = [str(value).strip() for value in actions if str(value).strip()]
            else:
                action_values = _permission_action_aliases(clause)
            if {str(value).lower() for value in action_values} & {"read", "view", "list", "query", "get"}:
                action_values = [
                    value for value in action_values
                    if str(value).lower() in {"read", "view", "list", "query", "get"}
                ]
            clause_norm = _norm(clause)
            if any(marker in clause_norm for marker in ("只读", "read only", "readonly")):
                action_values.extend(["GET", "HEAD", "OPTIONS", "read", "view", "list"])
            action_values = list(dict.fromkeys(action_values))
            if not action_values and not denied_action_values:
                action_values = ["read"]
            for resource_index, resource_alias in enumerate(resource_aliases):
                rows.append({
                    "permission_id": f"perm:{source_id}:{idx+1}:{clause_index+1}:{resource_index+1}",
                    "source_id": source_id,
                    "role": role,
                    "resource": resource_alias,
                    "resource_aliases": resource_aliases,
                    "actions": action_values,
                    **({"decision": permission_decision} if permission_decision else {}),
                    **({"denied_actions": denied_action_values} if denied_action_values else {}),
                    "scope": str(scope_value or "").strip() or _permission_scope(clause),
                    "evidence": _permission_evidence_excerpt(
                        item,
                        role=role,
                        resource=resource_alias,
                        decision=permission_decision,
                        actions=action_values,
                    ),
                })
    role_words = _lexicon_dict("role_words") or ROLE_WORDS
    for line_index, line in enumerate(text.splitlines()):
        if (
            line_index < len(source_line_types)
            and source_line_types[line_index] in _NON_PERMISSION_SOURCE_TYPES
        ):
            continue
        if _permission_decision({}, line) != "deny":
            continue
        line_norm = _norm(line)
        negative_clause = _negative_permission_clause(line)
        roles = [
            role
            for role, aliases in role_words.items()
            if any(
                _norm(alias) and _norm(alias) in line_norm
                for alias in [role, *aliases]
            )
        ]
        resource_aliases = _permission_resource_aliases(negative_clause)
        if not roles or not resource_aliases:
            continue
        # An unidentified action must NOT become a wildcard deny. The source
        # 「warehouse 可以调整库存，但不能改商品价格」 restricts one action on one field;
        # falling back to ["*"] turned it into "warehouse is denied every action on
        # product", and the oracle then asserted that GET /api/products must fail for
        # warehouse. It does not, so the run reported a defect the source never claimed
        # -- 9 of 18 deliverable findings on a real target came from this one fallback.
        #
        # For a DENY the fail-closed direction is to deny LESS, not more. When the
        # action cannot be determined the row is recorded with decision "unknown", which
        # behavior_ir maps to permission_unknown -- the relation type that exists for
        # exactly this case -- so the restriction stays visible without becoming an
        # assertion.
        action_values = _permission_action_aliases(negative_clause)
        negative_decision = "deny"
        if not action_values:
            action_values = ["unspecified"]
            negative_decision = "unknown"
        for role in roles:
            role_aliases = [role, *role_words.get(role, [])]
            role_resource_aliases = set(
                _permission_resource_aliases(" ".join(role_aliases))
            )
            scoped_resource_aliases = [
                resource_alias
                for resource_alias in resource_aliases
                if resource_alias not in role_resource_aliases
            ]
            for resource_index, resource_alias in enumerate(scoped_resource_aliases):
                rows.append({
                    "permission_id": (
                        f"perm:{source_id}:narrative:{line_index+1}:"
                        f"{role}:{resource_index+1}"
                    ),
                    "source_id": source_id,
                    "role": role,
                    "resource": resource_alias,
                    "resource_aliases": scoped_resource_aliases,
                    "actions": action_values,
                    "decision": negative_decision,
                    "scope": _permission_scope(negative_clause),
                    "evidence": _redact_text(line, 280),
                })
    if rows:
        return _dedupe_by_id(rows, "permission_id")
    for idx, line in enumerate(text.splitlines()):
        if (
            idx < len(source_line_types)
            and source_line_types[idx] in _NON_PERMISSION_SOURCE_TYPES
        ):
            continue
        normalized = _norm(line)
        if not normalized or not any(marker in normalized for marker in ("权限", "permission", "role", "访问", "只能", "tenant")):
            continue
        role_match = re.search(r"(?i)(?:role|角色|用户)\s*[:：=]\s*([^,;，；]+)", line)
        resource_match = re.search(r"(?i)(?:resource|资源|模块|对象|接口)\s*[:：=]\s*([^,;，；]+)", line)
        if role_match or resource_match:
            rows.append({"permission_id": f"perm:{source_id}:line:{idx+1}", "source_id": source_id, "role": (role_match.group(1).strip() if role_match else "unspecified_role"), "resource": (resource_match.group(1).strip() if resource_match else "unspecified_resource"), "actions": ["read"], "scope": "document_declared", "evidence": _redact_text(line, 280)})
    return rows


def _ticket_rows(text: str, payload: Any, source_id: str, source_type: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates: list[Any] = []
    if isinstance(payload, dict):
        for key in ("issues", "bugs", "tickets", "items", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates.extend(value)
        if not candidates and any(key in payload for key in ("title", "summary", "description")):
            candidates.append(payload)
    elif isinstance(payload, list):
        candidates.extend(payload)
    candidates.extend(_csv_rows(text))
    for idx, item in enumerate(candidates):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("summary") or item.get("name") or item.get("description") or "").strip()
        if not title:
            continue
        severity = str(item.get("severity") or item.get("priority") or "P2").upper()
        if severity not in {"P0", "P1", "P2", "P3"}:
            severity = "P2"
        rows.append({
            "risk_id": f"history:{source_id}:{idx+1}",
            "source_id": source_id,
            "source_type": source_type,
            "title": _redact_text(title, 320),
            "severity": severity,
            "status": str(item.get("status") or "historical"),
            "risk_type": _risk_type_from_text(title),
            "evidence": _redact_text(str(item.get("description") or title), 600),
        })
    if rows:
        return rows
    for idx, line in enumerate(text.splitlines()):
        if any(marker in _norm(line) for marker in ("缺陷", "bug", "故障", "incident", "越权", "重复", "金额")):
            rows.append({"risk_id": f"history:{source_id}:line:{idx+1}", "source_id": source_id, "source_type": source_type, "title": _redact_text(line, 320), "severity": "P1" if any(x in _norm(line) for x in ("p0", "p1", "严重", "资金", "越权")) else "P2", "status": "historical", "risk_type": _risk_type_from_text(line), "evidence": _redact_text(line, 600)})
    return rows


def _rule_type_from_text(text: str) -> str:
    norm = _norm(text)
    risk_terms = _lexicon_dict("risk_terms") or RISK_TERMS
    if any(_norm(term) in norm for term in risk_terms.get("permission_boundary", [])):
        return "permission"
    if any(_norm(term) in norm for term in risk_terms.get("async_event", [])):
        return "async_event"
    if any(_norm(term) in norm for term in risk_terms.get("data_conservation", [])):
        return "conservation"
    if any(_norm(term) in norm for term in risk_terms.get("data_reconciliation", [])):
        return "reconciliation"
    if any(_norm(term) in norm for term in risk_terms.get("state_machine", [])):
        return "state_transition"
    if any(_norm(term) in norm for term in risk_terms.get("idempotency", [])):
        return "idempotency"
    return "business_rule"


def _risk_type_from_text(text: str) -> str:
    norm = _norm(text)
    risk_terms = _lexicon_dict("risk_terms") or RISK_TERMS
    if any(_norm(term) in norm for term in risk_terms.get("async_event", [])):
        return "async_event"
    if any(_norm(term) in norm for term in risk_terms.get("idempotency", [])):
        return "idempotency"
    for name, terms in risk_terms.items():
        if name in {"async_event", "idempotency"}:
            continue
        if any(_norm(term) in norm for term in terms):
            return name
    return "business_rule"


def _typed_validation_constraint(text: str) -> dict[str, Any]:
    fields = [
        value.strip()
        for value in re.findall(r"`([^`]+)`", text)
        if value.strip()
    ]
    if len(fields) != 1:
        return {}
    norm = _norm(text)
    positive_integer_markers = _lexicon_list("positive_integer_markers")
    if not positive_integer_markers:
        return {}
    if not any(_norm(marker) in norm for marker in positive_integer_markers):
        return {}
    field_tokens = [
        token
        for token in re.split(r"[.\[\]]+", fields[0])
        if token
    ]
    if not field_tokens:
        return {}
    return {
        "operator": "field_constraint",
        "operands": [{
            "field_tokens": field_tokens,
            "validation_constraint": "exclusiveMinimum",
            "validation_constraint_value": 0,
        }],
    }


def _rule_markers(name: str, fallback: tuple[str, ...]) -> tuple[str, ...]:
    configured = tuple(
        marker
        for marker in (_norm(value) for value in _lexicon_list(name))
        if marker
    )
    return configured or fallback


def _rule_modality(
    statement: str,
) -> tuple[str, str, str, str]:
    """Return modality, polarity, governed behavior, and written marker."""

    normalized = _norm(statement)
    prohibited = _rule_markers(
        "rule_prohibited_markers",
        (
            "mustnot", "shallnot", "shouldnot", "doesnot", "donot",
            "cannot", "notallowed", "forbidden", "prohibited",
            "不得", "不能", "不可", "不应", "禁止",
        ),
    )
    required = _rule_markers(
        "rule_required_markers",
        (
            "must", "shall", "should", "required", "require",
            "必须", "应当", "应该", "需要", "须", "确保",
        ),
    )
    exclusive = _rule_markers(
        "rule_exclusive_markers",
        ("only", "onlyallowed", "仅限", "只能", "仅能"),
    )

    def _matched(markers: tuple[str, ...]) -> str:
        matches = [marker for marker in markers if marker and marker in normalized]
        return max(matches, key=len) if matches else ""

    marker = _matched(prohibited)
    if marker:
        modality, polarity = "PROHIBITED", "negative"
    else:
        marker = _matched(exclusive)
        if marker:
            modality, polarity = "EXCLUSIVE", "positive"
        else:
            marker = _matched(required)
            if marker:
                modality, polarity = "REQUIRED", "positive"
            else:
                return "", "", "", ""

    behavior = statement
    written = ""
    if marker:
        marker_match = re.search(re.escape(marker), _norm(statement))
        if marker_match:
            # _norm removes spaces and punctuation, so locate the written marker
            # independently before falling back to the whole exact statement.
            written_matches = [
                candidate
                for candidate in (
                    *_lexicon_list("rule_prohibited_markers"),
                    *_lexicon_list("rule_exclusive_markers"),
                    *_lexicon_list("rule_required_markers"),
                )
                if (
                    candidate
                    and _norm(candidate) == marker
                    and re.search(re.escape(candidate), statement, flags=re.I)
                )
            ]
            written = max(written_matches, key=len) if written_matches else ""
            if written:
                remainder = re.split(
                    re.escape(written),
                    statement,
                    maxsplit=1,
                    flags=re.I,
                )[-1].strip(" :：,，;；")
                negated_actions = {
                    _norm(value)
                    for value in _lexicon_list("rule_negated_action_markers")
                    if _norm(value)
                }
                behavior = (
                    written[1:] + remainder
                    if _norm(written) in negated_actions
                    else remainder
                )
    return modality, polarity, behavior, written


def _semantic_rule_frame(
    statement: str,
) -> dict[str, Any]:
    modality, polarity, behavior, written_marker = _rule_modality(statement)
    if not modality:
        return {}

    condition = ""
    subject = ""
    condition_markers = _lexicon_list("rule_condition_markers")
    for marker in condition_markers:
        match = re.match(
            rf"\s*{re.escape(marker)}\s+(?P<condition>.+?)[,，;；]\s*(?P<behavior>.+)",
            statement,
            flags=re.I,
        )
        if match:
            condition = match.group("condition").strip()
            behavior = match.group("behavior").strip()
            break
    if not condition:
        state_condition = re.match(
            r"\s*(?:(?P<subject>[^:：,，;；]+)\s*[:：]\s*)?"
            r"(?P<condition>.*?(?:status|state|状态)\s*(?:=|is|为)\s*[^,，;；]+)"
            r"[,，;；]\s*(?P<behavior>.+)",
            statement,
            flags=re.I,
        )
        if state_condition:
            subject = (state_condition.group("subject") or "").strip()
            condition = state_condition.group("condition").strip()
            remainder = state_condition.group("behavior").strip()
            _, _, parsed_behavior, _ = _rule_modality(remainder)
            behavior = parsed_behavior or remainder

    if not subject and not condition and written_marker:
        marker_match = re.search(
            re.escape(written_marker),
            statement,
            flags=re.I,
        )
        if marker_match and marker_match.start() > 0:
            marker_prefix = statement[:marker_match.start()]
            if not re.search(r"[,，;；]", marker_prefix):
                subject = marker_prefix.strip(" :：,，;；")
    if condition and subject.casefold() == condition.casefold():
        subject = ""
    if not condition:
        state_subject = re.match(
            r"(?P<condition>.+?(?:status|state|状态))的(?P<subject>.+)",
            subject,
            flags=re.I,
        )
        if state_subject:
            condition = state_subject.group("condition").strip()
            subject = state_subject.group("subject").strip()

    anchors = list(dict.fromkeys([
        *re.findall(r"`([^`]+)`", statement),
        *re.findall(r"\b[A-Z][A-Z0-9_]{2,}\b", statement),
    ]))
    return {
        "schema_version": "qualibug.business-semantic-frame.v1",
        "modality": modality,
        "polarity": polarity,
        "condition": condition,
        "subject": subject,
        "behavior": behavior,
        "source_anchors": anchors[:20],
        "source_grounded": True,
    }


def _rule_clause_candidates(line: str) -> list[str]:
    """Keep semantic cells from tabular rows instead of credential columns."""

    stripped = line.strip(" -•\t")
    if not stripped or re.fullmatch(r"\|?[\s:|-]+\|?", stripped):
        return []
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    if len(cells) <= 1:
        return [stripped]
    semantic_cells = [
        cell
        for cell in cells
        if cell and _semantic_rule_frame(cell)
    ]
    return semantic_cells


def _rules_from_text(
    text: str,
    source_id: str,
    source_type: str,
) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    allow_relaxed_async_rules = source_type == "collaboration_document"
    seen_statements: set[str] | None = set() if source_type == "collaboration_document" else None
    lines: list[tuple[int, str]] = []
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        for clause in re.split(r"[。.!?；;]", raw_line):
            lines.extend(
                (line_number, candidate)
                for candidate in _rule_clause_candidates(
                    clause,
                )
            )
    for idx, (line_number, line) in enumerate(lines):
        # All downstream rule fields must derive from the same redacted source
        # representation. Building the semantic frame from the raw line while
        # storing a redacted statement makes the frame unverifiable (for example
        # a bearer token or URL query is present in the frame but absent from the
        # source statement), which correctly blocks Behavior IR later but too late
        # to identify the parser's provenance mismatch.
        statement = _redact_text(line, 720)
        norm = _norm(statement)
        if len(norm) < 8:
            continue
        semantic_frame = _semantic_rule_frame(statement)
        rule_type = _rule_type_from_text(statement)
        if not semantic_frame and not (
            allow_relaxed_async_rules
            and rule_type in {"idempotency", "async_event"}
        ):
            continue
        if seen_statements is not None:
            key = _norm(statement)
            if key in seen_statements:
                continue
            seen_statements.add(key)
        rule = {
            "rule_id": f"rule:{source_id}:{idx+1}",
            "source_id": source_id,
            "source_type": source_type,
            "source_locator": f"line:{line_number}",
            "statement": statement,
            "rule_type": rule_type,
            "risk_type": _risk_type_from_text(statement),
            "severity": "P0" if rule_type in {"conservation", "permission"} and any(x in norm for x in ("资金", "余额", "账本", "payment", "balance", "tenant", "租户", "病历")) else "P1" if rule_type in {"conservation", "permission", "reconciliation"} else "P2",
            "tokens": sorted(_tokens(statement)),
        }
        if semantic_frame:
            rule["semantic_frame"] = semantic_frame
        rule.update(_typed_validation_constraint(statement))
        rules.append(rule)
    return rules[:180]


def _roles_from_text(text: str, source_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    lower = _norm(text)
    role_words = _lexicon_dict("role_words") or ROLE_WORDS
    for role, words in role_words.items():
        evidence = next((word for word in words if _norm(word) in lower), "")
        if evidence:
            out.append({"role_id": f"role:{source_id}:{role}", "source_id": source_id, "role": role, "evidence": evidence})
    return out


def _state_machines_from_text(text: str, source_id: str) -> list[dict[str, Any]]:
    token_pattern = r"[A-Za-z0-9_\-\u4e00-\u9fff]{2,32}"
    separator_pattern = r"(?:->|→|到|至)"
    chain_pattern = re.compile(
        rf"{token_pattern}(?:\s*{separator_pattern}\s*{token_pattern})+"
    )

    allowed_markers = _lexicon_list("allowed_transition_markers")
    forbidden_markers = _lexicon_list("forbidden_transition_markers")

    def classified_transitions_in(
        section_text: str,
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        allowed: list[tuple[str, str]] = []
        forbidden: list[tuple[str, str]] = []
        mode = "allowed"
        for line in section_text.splitlines():
            line_norm = _norm(line)
            if any(_norm(marker) in line_norm for marker in forbidden_markers):
                mode = "forbidden"
            elif any(_norm(marker) in line_norm for marker in allowed_markers):
                mode = "allowed"
            target = forbidden if mode == "forbidden" else allowed
            for chain_match in chain_pattern.finditer(line):
                raw_tokens = re.split(rf"\s*{separator_pattern}\s*", chain_match.group(0))
                normalized = [_normalize_state_token(token) for token in raw_tokens]
                for src, dst in zip(normalized, normalized[1:]):
                    if src and dst and _norm(src) != _norm(dst):
                        pair = (src, dst)
                        if pair not in target:
                            target.append(pair)
                    elif src and not dst:
                        # A wildcard target ("CLOSED -> 任意状态") means the
                        # source is a real state with NO legal outgoing
                        # transition. Record the source state alone so the
                        # terminal state stays in the state set.
                        pair = (src, "")
                        if pair not in target:
                            target.append(pair)
        return allowed, forbidden

    heading_markers = _lexicon_list("state_machine_heading_markers")
    heading_matches = list(re.finditer(r"(?m)^(#{1,6})\s+(.+?)\s*$", text))
    recognized_heading_indexes = {
        index
        for index, heading_match in enumerate(heading_matches)
        if any(
            _norm(marker) in _norm(heading_match.group(2))
            for marker in heading_markers
        )
    }
    sections: list[tuple[str, str]] = []
    for index, heading_match in enumerate(heading_matches):
        if index not in recognized_heading_indexes:
            continue
        heading = heading_match.group(2).strip().strip("`# ")
        level = len(heading_match.group(1))
        section_end = len(text)
        for next_heading in heading_matches[index + 1:]:
            if len(next_heading.group(1)) <= level:
                section_end = next_heading.start()
                break
        direct_parts: list[str] = []
        cursor = heading_match.end()
        for nested_index in sorted(recognized_heading_indexes):
            if nested_index <= index:
                continue
            nested_heading = heading_matches[nested_index]
            if nested_heading.start() >= section_end:
                break
            if len(nested_heading.group(1)) <= level:
                continue
            direct_parts.append(text[cursor:nested_heading.start()])
            nested_level = len(nested_heading.group(1))
            nested_end = section_end
            for after_nested in heading_matches[nested_index + 1:]:
                if len(after_nested.group(1)) <= nested_level:
                    nested_end = min(after_nested.start(), section_end)
                    break
            cursor = max(cursor, nested_end)
        direct_parts.append(text[cursor:section_end])
        sections.append((heading, "\n".join(direct_parts)))

    def object_from_heading(heading: str) -> str:
        candidate = heading
        for marker in sorted(heading_markers, key=len, reverse=True):
            candidate = re.sub(re.escape(marker), " ", candidate, flags=re.IGNORECASE)
        candidate = re.sub(r"[^\w.\-]+", "_", candidate, flags=re.UNICODE).strip("_.-")
        aliases = _permission_resource_aliases(candidate)
        return aliases[0] if aliases else candidate.lower() or "document_workflow"

    scoped = [
        (object_from_heading(heading), *classified_transitions_in(section_text))
        for heading, section_text in sections
    ]
    scoped = [row for row in scoped if row[1] or row[2]]
    if not scoped:
        fallback_allowed, fallback_forbidden = classified_transitions_in(text)
        if fallback_allowed or fallback_forbidden:
            scoped = [("document_workflow", fallback_allowed, fallback_forbidden)]

    out: list[dict[str, Any]] = []
    for index, (object_name, transitions, forbidden_transitions) in enumerate(scoped, start=1):
        states: list[str] = []
        for src, dst in [*transitions, *forbidden_transitions]:
            if src and src not in states:
                states.append(src)
            if dst and dst not in states:
                states.append(dst)
        out.append({
            "state_machine_id": f"state:{source_id}:{index}",
            "source_id": source_id,
            "object": object_name,
            "states": states[:24],
            "transitions": [{"from": src, "to": dst} for src, dst in transitions[:40]],
            "forbidden_transitions": [
                {"from": src, "to": dst} for src, dst in forbidden_transitions[:40]
            ],
            "evidence": _redact_text(
                "; ".join([
                    *(f"allowed:{src}->{dst}" for src, dst in transitions),
                    *(f"forbidden:{src}->{dst}" for src, dst in forbidden_transitions),
                ]),
                700,
            ),
        })
    return out


def _parse_source(blob: bytes, filename: str, source_type: str, source_id: str) -> dict[str, Any]:
    started_at_utc = _now()
    parse_errors: list[dict[str, Any]] = []
    suffix = Path(filename).suffix.lower()
    if suffix == ".docx":
        text = _decode_docx(blob)
    elif suffix == ".pdf":
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / filename
            fake.write_bytes(blob)
            text = _decode_pdf(fake, blob)
    elif suffix in {".xlsx", ".xls"}:
        # Excel: decode to text summary; structured tables extracted via format normalizer
        try:
            from ._format_normalizer import extract_tables_from_excel_bytes
            _xl_tables = extract_tables_from_excel_bytes(blob, filename)
            # Build a text representation for downstream text-based parsers
            _xl_lines: list[str] = []
            for _xl_t in _xl_tables:
                _xl_lines.append(f"## {_xl_t.get('source_locator', 'sheet')}")
                _xl_headers = _xl_t.get("headers") or []
                if _xl_headers:
                    _xl_lines.append("| " + " | ".join(_xl_headers) + " |")
                    _xl_lines.append("|" + "|".join(["---"] * len(_xl_headers)) + "|")
                for _xl_row in _xl_t.get("rows") or []:
                    _xl_lines.append("| " + " | ".join(str(_xl_row.get(h, "")) for h in _xl_headers) + " |")
                _xl_lines.append("")
            text = "\n".join(_xl_lines)
        except ImportError:
            text = ""
            parse_errors.append({
                "stage": "decode",
                "code": "FORMAT_DECODE_UNSUPPORTED",
                "identity": source_id,
                "retryability": "after_dependency_install",
                "operator_action": "install openpyxl: pip install openpyxl",
                "detail": "Excel parsing requires openpyxl which is not installed",
                "gap_type": "format_decode_unsupported",
            })
        except Exception as xl_exc:
            text = ""
            parse_errors.append({
                "stage": "decode",
                "code": "EXCEL_DECODE_FAILED",
                "identity": source_id,
                "retryability": "after_source_fix",
                "operator_action": "validate Excel file integrity",
                "detail": f"{type(xl_exc).__name__}: {xl_exc}"[:500],
            })
    else:
        text = blob.decode("utf-8", errors="replace")
    payload = None
    _structured_source = suffix in {".json", ".yaml", ".yml"} or (
        source_type in {"openapi", "postman", "historical_bug", "ticket"}
        and text.lstrip().startswith(("{", "["))
    )
    if suffix in {".yaml", ".yml"}:
        try:
            import yaml

            payload = yaml.safe_load(text)
            if payload is not None and not isinstance(payload, (dict, list)):
                raise ValueError("YAML root must be an object or array")
        except Exception as exc:
            parse_errors.append({
                "stage": "parse",
                "code": "YAML_PARSE_FAILED",
                "identity": source_id,
                "retryability": "after_source_fix",
                "operator_action": "validate YAML syntax and document root",
                "detail": f"{type(exc).__name__}: {exc}"[:500],
            })
    elif _structured_source:
        payload = _json_or_none(text)
        if text.strip() and payload is None:
            parse_errors.append({
                "stage": "parse",
                "code": "JSON_PARSE_FAILED",
                "identity": source_id,
                "retryability": "after_source_fix",
                "operator_action": "validate JSON syntax and encoding",
                "detail": "structured JSON source could not be decoded",
            })
    openapi = payload if source_type == "openapi" and isinstance(payload, dict) else {}
    postman = payload if source_type == "postman" and isinstance(payload, dict) else {}
    operations = _openapi_operations(openapi, source_id) + _postman_operations(postman, source_id)
    if source_type == "source_code":
        operations.extend(_source_code_operations(text, source_id, filename))
        if not operations and _SOURCE_CODE_ROUTE_SIGNAL_RE.search(text or ""):
            parse_errors.append({
                "stage": "extraction",
                "code": "SOURCE_CODE_HTTP_OPERATIONS_EMPTY",
                "identity": source_id,
                "retryability": "after_parser_enhancement_or_source_fix",
                "operator_action": "inspect source-code route declaration shape and parser coverage",
                "detail": "route-like source syntax was present but no literal HTTP operation was extracted",
                "gap_type": "source_code_http_operation_extraction_empty",
            })
    # HAR: parse JSON and extract operations
    har_errors: list[dict[str, Any]] = []
    if source_type == "har":
        try:
            from ai_test_asset_center.har_importer import import_har_endpoints, extract_har_error_patterns
            har_file = Path(filename)
            if suffix == ".har":
                # Write blob to temp file for HAR parser
                import tempfile as _tmp
                with _tmp.NamedTemporaryFile(suffix=".har", delete=False) as tf:
                    tf.write(blob)
                    tf.flush()
                    har_endpoints = import_har_endpoints(tf.name)
                    har_errors_raw = extract_har_error_patterns(tf.name)
                try:
                    Path(tf.name).unlink()
                except OSError:
                    pass
                operations.extend([
                    {"path": ep["path"], "method": ep["method"], "capability": ep["capability_code"],
                     "source": "har_traffic", "summary": ep["summary"]}
                    for ep in har_endpoints
                ])
                har_errors = [
                    {"endpoint": e.endpoint, "method": e.method, "status": e.status,
                     "message": e.error_message, "count": e.count}
                    for e in har_errors_raw
                ]
        except Exception as har_err:
            parse_errors.append({
                "stage": "parse",
                "code": "HAR_PARSE_FAILED",
                "identity": source_id,
                "retryability": "after_source_or_parser_fix",
                "operator_action": "validate HAR JSON and parser compatibility",
                "detail": f"{type(har_err).__name__}: {har_err}"[:500],
            })
            logger.warning("HAR parsing failed for %s: %s", filename, har_err)
    # Application logs: run log analysis
    log_errors: list[dict[str, Any]] = []
    if source_type == "application_log":
        try:
            from ai_test_asset_center.log_analyzer import analyze_logs
            import tempfile as _tmp2
            with _tmp2.NamedTemporaryFile(suffix=".log", delete=False) as tf:
                tf.write(blob)
                tf.flush()
                log_result = analyze_logs(tf.name)
            try:
                Path(tf.name).unlink()
            except OSError:
                pass
            log_errors = [
                {"error_type": c.error_type, "count": c.count,
                 "message": c.message_pattern, "severity": c.severity}
                for c in log_result.get("error_clusters", [])
            ]
            # Also add slow endpoints as operations
            for s in log_result.get("slow_endpoints", []):
                operations.append({
                    "path": s.path, "method": s.method,
                    "capability": "read", "source": "access_log",
                    "summary": f"P95={s.p95_ms:.0f}ms, err_rate={s.error_rate:.1%}",
                })
        except Exception as log_err:
            parse_errors.append({
                "stage": "parse",
                "code": "APPLICATION_LOG_PARSE_FAILED",
                "identity": source_id,
                "retryability": "after_source_or_parser_fix",
                "operator_action": "validate log encoding and parser compatibility",
                "detail": f"{type(log_err).__name__}: {log_err}"[:500],
            })
            logger.warning("Log analysis failed for %s: %s", filename, log_err)
    if source_type == "markdown_api" or (source_type == "openapi" and suffix in {".md", ".markdown", ".txt"}):
        operations.extend(_markdown_api_operations(text, source_id))
        if text.strip() and not operations:
            parse_errors.append({
                "stage": "parse",
                "code": "MARKDOWN_API_NO_OPERATIONS",
                "identity": source_id,
                "retryability": "after_source_fix",
                "operator_action": "add source-declared HTTP method and path headings",
                "detail": "no executable API operation could be parsed from Markdown",
            })
    # ── Phase 2: Remove classification gate (R2 fix) ──
    # ALL sources attempt structured extraction. source_type affects confidence, not access.
    # High-confidence: SQL DDL and JSON schema for their declared types
    tables = _sql_tables(text, source_id) if source_type == "database_schema" else []
    tables += _json_schema_tables(payload, source_id) if source_type in {"database_schema", "openapi"} else []
    # Field dictionary: always attempt (was gated on db_field_dictionary/database_schema)
    field_dictionary = _field_dictionary_entries(text, payload, source_id)
    # Generic table extraction from format normalizer (Phase 1 output)
    from ._format_normalizer import extract_document_structure as _extract_doc_struct
    _doc_struct = _extract_doc_struct(text, raw_bytes=blob, filename=filename, suffix=suffix)
    for _fn_table in _doc_struct.tables:
        _fn_headers = _fn_table.get("headers") or []
        if _is_field_definition_table(_fn_headers):
            # This table is a field definition — extract field records
            # Derive table name from the section heading in effect for this table,
            # through the same canonicalization every other declaration path uses.
            _section = str(_fn_table.get("source_locator") or "")
            _derived_table = _canonical_entity_name(_section_table_label(_section))[0] or "default"
            for _fn_row in _fn_table.get("rows") or []:
                _f_name = _pick_first(_fn_row, ("field", "field_name", "fieldname", "column", "column_name", "name", "attribute", "字段", "字段名", "列名", "属性", "名称"))
                if not _f_name:
                    continue
                _f_type = _pick_first(_fn_row, ("type", "data_type", "datatype", "field_type", "字段类型", "类型", "数据类型"))
                _f_desc = _pick_first(_fn_row, ("description", "desc", "comment", "remark", "note", "说明", "描述", "备注"))
                _f_req = _pick_first(_fn_row, ("required", "nullable", "必填", "是否必填", "constraint", "约束"))
                field_dictionary.append({
                    "field_id": f"field:{source_id}:{_short_hash({'table': _derived_table, 'field': _f_name})}",
                    "source_id": source_id,
                    "table": _derived_table,
                    "table_id": f"table:{_derived_table}",
                    "field": _f_name,
                    "field_path": _f_name,
                    "type": _f_type,
                    "required": _doc_bool(_f_req),
                    "description": _redact_text(_f_desc, 320),
                    "derivation": "generic_table_extraction",
                    "tokens": sorted(_tokens(f"{_derived_table} {_f_name} {_f_type} {_f_desc}")),
                })
    # Entities declared by an inventory table carry no fields of their own; they
    # are what inline `table.field` references resolve against.
    tables += _entity_inventory_rows(text, source_id)
    _declared_entities = {str(row.get("name") or "") for row in tables}
    _declared_entities |= {str(row.get("table") or "") for row in field_dictionary}
    field_dictionary += _inline_qualified_field_rows(text, source_id, _declared_entities)
    field_dictionary = _dedupe_by_id(field_dictionary, "field_id")
    # Build tables from field dictionary (for ALL sources, not just db_field_dictionary)
    if field_dictionary:
        tables += _field_dictionary_tables(field_dictionary, source_id)
    tables = _apply_constraint_list_identities(_merge_table_identities(tables), text)
    ui_specs = _uiux_specs_from_text(text, source_id, source_type, filename)
    _uiux_rules: list[dict[str, Any]] = []
    if source_type == "uiux_requirements" and isinstance(payload, dict):
        ui_specs, _uiux_rules = _uiux_requirements_from_json(
            payload, source_id, filename
        )
    permissions = _dedupe_by_id(
        [*_permission_entries(text, payload, source_id, source_type),
         *_permission_crosstab_entries(text, source_id)],
        "permission_id",
    )
    tickets = _ticket_rows(text, payload, source_id, source_type) if source_type in {"historical_bug", "ticket"} else []
    parser = "yaml" if suffix in {".yaml", ".yml"} else "json" if payload is not None else suffix.lstrip(".") or "text"
    parse_status = "parsed" if text.strip() else "metadata_only"
    text_hash = _hash_bytes(text.encode("utf-8"))
    outputs = {
        "operations": len(operations),
        "tables": len(tables),
        "fields": len(field_dictionary),
        "ui_specs": len(ui_specs),
        "permissions": len(permissions),
        "tickets": len(tickets),
        "rules": len(_uiux_rules)
        if source_type == "uiux_requirements"
        else len(_rules_from_text(text, source_id, source_type)),
        "roles": len(_roles_from_text(text, source_id)),
        "state_machines": len(_state_machines_from_text(text, source_id)),
    }
    # ── Phase 0: zero-output coverage gap detection ──
    # If a source SHOULD contain structured definitions but produces 0, emit gap.
    _structured_types = {"database_schema", "db_field_dictionary", "permission_matrix"}
    _has_2d_table = bool(_markdown_table_blocks(text)) if text.strip() else False
    _structured_output_count = outputs["tables"] + outputs["fields"] + outputs["permissions"]
    _extraction_outcome = ""
    if _structured_output_count > 0:
        _extraction_outcome = "EXTRACTED"
    elif source_type in _structured_types or _has_2d_table:
        # Source expected to yield structured data but produced nothing
        _extraction_outcome = "EMPTY_NO_STRUCTURE_FOUND"
        _gap_code = "structured_extraction_empty"
        if suffix not in {".md", ".txt", ".rst", ".html", ".htm", ".csv", ".sql", ".json", ".yaml", ".yml", ".docx", ".pdf", ".xml"}:
            _extraction_outcome = "EMPTY_PARSER_UNSUPPORTED_SHAPE"
            _gap_code = "unsupported_document_shape"
        parse_errors.append({
            "stage": "extraction",
            "code": "STRUCTURED_EXTRACTION_EMPTY",
            "identity": source_id,
            "retryability": "after_parser_enhancement",
            "operator_action": "source appears to contain structured definitions but parser produced zero rows",
            "detail": f"source_type={source_type} has_2d_table={_has_2d_table} tables={outputs['tables']} fields={outputs['fields']} permissions={outputs['permissions']}",
            "gap_type": _gap_code,
        })
    elif parse_status == "metadata_only":
        _extraction_outcome = "SKIPPED_NOT_APPLICABLE"
    else:
        _extraction_outcome = "EMPTY_NO_STRUCTURE_FOUND"
    receipt = _parser_receipt(
        source_id=source_id,
        filename=filename,
        source_type=source_type,
        parser=parser,
        detected_format=_detected_source_format(filename, source_type, text, payload),
        text_hash=text_hash,
        text_length=len(text),
        outputs=outputs,
        errors=parse_errors,
        parse_status=parse_status,
        started_at_utc=started_at_utc,
        extraction_outcome=_extraction_outcome,
    )
    # ── Reuse _doc_struct from Phase 2 generic extraction above ──
    # ── Phase 2: multi-label source_types (R1 fix) ──
    _source_types = _classify_source_multi(filename, text)
    if source_type and source_type not in _source_types:
        _source_types.insert(0, source_type)
    return {
        "text": text,
        "payload": payload,
        "openapi": openapi,
        "operations": operations,
        "tables": tables,
        "field_dictionary": field_dictionary,
        "ui_specs": ui_specs,
        "permissions": permissions,
        "tickets": tickets,
        "har_errors": har_errors,
        "log_errors": log_errors,
        "rules": _uiux_rules
        if source_type == "uiux_requirements"
        else _rules_from_text(text, source_id, source_type),
        "roles": _roles_from_text(text, source_id),
        "state_machines": _state_machines_from_text(text, source_id),
        "parse_status": parse_status,
        "parser": parser,
        "text_hash": text_hash,
        "text_length": len(text),
        "parser_receipt": receipt,
        "parse_errors": parse_errors,
        "document_structure": _doc_struct.to_dict(),
        "source_types": _source_types,
    }


