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
    "_classify_source", "_csv_rows", "_doc_bool", "_field_dictionary_entries",
    "_field_dictionary_tables", "_flatten_json_field_names", "_infer_field_rows_from_markdown",
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
        (suffix == ".har" or (suffix == ".json" and isinstance(data, dict) and "log" in data), "har"),
        (suffix == ".log" or (suffix == ".txt" and _has("log", "日志", "access", "error")), "application_log"),
        (suffix == ".svg" or "<svg" in str(text or "").lower(), "uiux_svg"),
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
    for condition, source_type in rules:
        if condition:
            return source_type
    if explicit in SOURCE_TYPES:
        return explicit
    return "collaboration_document"


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
            summary = str(operation.get("summary") or operation.get("description") or "")
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
        if actual:
            return str(item.get(actual) or "").strip()
    return ""


def _infer_field_rows_from_markdown(text: str, source_id: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_table = ""
    lines = str(text or "").splitlines()
    for line in lines:
        heading = re.match(r"^\s*#{1,6}\s*(.+?)\s*$", line)
        if heading:
            label = heading.group(1).strip()
            table_match = re.search(r"(?i)(?:table|表|数据表)\s*[:：]?\s*([A-Za-z0-9_\-\u4e00-\u9fff]+)", label)
            current_table = table_match.group(1) if table_match else label
        inline = re.search(r"(?i)(?:table|表|数据表)\s*[:：=]\s*([A-Za-z0-9_\-\u4e00-\u9fff]+)", line)
        if inline:
            current_table = inline.group(1)
    for block in _markdown_table_blocks(text):
        for row in block:
            table_name = _pick_first(row, ("table", "table_name", "table name", "表", "数据表")) or current_table
            field_name = _pick_first(row, ("field", "field_name", "field name", "column", "column_name", "字段", "列名", "属性"))
            if not field_name:
                continue
            field_type = _pick_first(row, ("type", "data_type", "datatype", "字段类型", "类型"))
            description = _pick_first(row, ("description", "desc", "comment", "说明", "描述", "备注"))
            required = _pick_first(row, ("required", "nullable", "必填", "是否必填"))
            rows.append({
                "field_id": f"field:{source_id}:{_short_hash({'table': table_name or 'default', 'field': field_name})}",
                "source_id": source_id,
                "table": table_name or "default",
                "table_id": f"table:{table_name or 'default'}",
                "field": field_name,
                "field_path": field_name,
                "type": field_type,
                "required": _doc_bool(required),
                "description": _redact_text(description, 320),
                "tokens": sorted(_tokens(f"{table_name} {field_name} {field_type} {description}")),
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
        rows.append({
            "field_id": f"field:{source_id}:{_short_hash({'table': table_name or 'default', 'field': field_name})}",
            "source_id": source_id,
            "table": table_name or "default",
            "table_id": f"table:{table_name or 'default'}",
            "field": field_name,
            "field_path": field_name,
            "type": field_type,
            "required": _doc_bool(required),
            "description": _redact_text(description, 320),
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
        tables.append({
            "table_id": f"table:{table_name}",
            "source_id": source_id,
            "name": table_name,
            "columns": columns,
            "foreign_keys": [],
            "field_dictionary": items,
            "tokens": sorted(_tokens(f"{table_name} {' '.join(columns)} {' '.join(str(item.get('description') or '') for item in items[:12])}")),
        })
    return tables


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
        table_fields = [str(row.get("field") or "") for row in _field_dictionary_entries(section, None, source_id)]
        all_fields = sorted({field for field in [*example_fields, *table_fields] if field})
        summary_line = next((line.strip(" #-*") for line in section.splitlines() if line.strip() and not line.strip().startswith("|")), "")
        tag_candidates = re.findall(r"`([A-Za-z0-9_\-]{2,40})`", section[:600])
        for method in methods:
            path = str(match.group("path") or "/")
            rows.append({
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
            })
    return rows


def _sql_tables(text: str, source_id: str = "") -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    for match in re.finditer(r"(?is)create\s+table\s+(?:if\s+not\s+exists\s+)?[`\"\[]?([a-zA-Z0-9_]+)[`\"\]]?\s*\((.*?)\)\s*;", text):
        name, body = match.group(1), match.group(2)
        columns: list[str] = []
        foreign_keys: list[str] = []
        for line in body.splitlines():
            clean = line.strip().strip(",")
            if not clean:
                continue
            ref = re.search(r"(?i)references\s+[`\"\[]?([a-zA-Z0-9_]+)", clean)
            if ref:
                foreign_keys.append(ref.group(1))
            col = re.match(r"[`\"\[]?([a-zA-Z_][a-zA-Z0-9_]*)[`\"\]]?\s+", clean)
            if col and col.group(1).lower() not in {"primary", "foreign", "constraint", "unique", "key", "index"}:
                columns.append(col.group(1))
        tables.append({
            "table_id": f"table:{name}",
            "source_id": source_id,
            "name": name,
            "columns": sorted(set(columns)),
            "foreign_keys": sorted(set(foreign_keys)),
            "tokens": sorted(_tokens(f"{name} {' '.join(columns)} {' '.join(foreign_keys)}")),
        })
    return tables


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


def _permission_entries(text: str, payload: Any, source_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
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
        evidence_item = {
            key: value
            for key, value in item.items()
            if not str(key).startswith("__")
        }
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
                "evidence": _redact_text(str(evidence_item), 280),
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
                    "evidence": _redact_text(str(evidence_item), 280),
                })
    role_words = _lexicon_dict("role_words") or ROLE_WORDS
    for line_index, line in enumerate(text.splitlines()):
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
        action_values = _permission_action_aliases(negative_clause) or ["*"]
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
                    "decision": "deny",
                    "scope": _permission_scope(negative_clause),
                    "evidence": _redact_text(line, 280),
                })
    if rows:
        return _dedupe_by_id(rows, "permission_id")
    for idx, line in enumerate(text.splitlines()):
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


def _rules_from_text(text: str, source_id: str, source_type: str) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    allow_relaxed_async_rules = source_type == "collaboration_document"
    seen_statements: set[str] | None = set() if source_type == "collaboration_document" else None
    lines = [line.strip(" -•\t") for line in re.split(r"[\n。.!?；;]", text) if line.strip()]
    for idx, line in enumerate(lines):
        norm = _norm(line)
        if len(norm) < 8:
            continue
        indicator = any(
            marker in norm
            for marker in (
                "必须",
                "不得",
                "不能",
                "不可",
                "不应",
                "只能",
                "禁止",
                "应当",
                "应该",
                "should",
                "must",
                "only",
                "not allowed",
                "cannot",
                "must not",
                "require",
                "一致",
                "守恒",
                "审批",
            )
        )
        rule_type = _rule_type_from_text(line)
        if not indicator and not (allow_relaxed_async_rules and rule_type in {"idempotency", "async_event"}):
            continue
        statement = _redact_text(line, 720)
        if seen_statements is not None:
            key = _norm(statement)
            if key in seen_statements:
                continue
            seen_statements.add(key)
        rule = {
            "rule_id": f"rule:{source_id}:{idx+1}",
            "source_id": source_id,
            "source_type": source_type,
            "statement": statement,
            "rule_type": rule_type,
            "risk_type": _risk_type_from_text(line),
            "severity": "P0" if rule_type in {"conservation", "permission"} and any(x in norm for x in ("资金", "余额", "账本", "payment", "balance", "tenant", "租户", "病历")) else "P1" if rule_type in {"conservation", "permission", "reconciliation"} else "P2",
            "tokens": sorted(_tokens(line)),
        }
        rule.update(_typed_validation_constraint(line))
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
            if src not in states:
                states.append(src)
            if dst not in states:
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
    # HAR: parse JSON and extract operations
    har_errors: list[dict[str, Any]] = []
    if source_type == "har":
        try:
            from .har_importer import import_har_endpoints, extract_har_error_patterns
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
            from .log_analyzer import analyze_logs
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
    tables = _sql_tables(text, source_id) if source_type == "database_schema" else []
    tables += _json_schema_tables(payload, source_id) if source_type in {"database_schema", "openapi"} else []
    field_dictionary = _field_dictionary_entries(text, payload, source_id) if source_type in {"db_field_dictionary", "database_schema"} else []
    if source_type == "db_field_dictionary":
        tables += _field_dictionary_tables(field_dictionary, source_id)
    ui_specs = _uiux_specs_from_text(text, source_id, source_type, filename)
    permissions = _permission_entries(text, payload, source_id)
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
        "rules": len(_rules_from_text(text, source_id, source_type)),
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
        "rules": _rules_from_text(text, source_id, source_type),
        "roles": _roles_from_text(text, source_id),
        "state_machines": _state_machines_from_text(text, source_id),
        "parse_status": parse_status,
        "parser": parser,
        "text_hash": text_hash,
        "text_length": len(text),
        "parser_receipt": receipt,
        "parse_errors": parse_errors,
    }


