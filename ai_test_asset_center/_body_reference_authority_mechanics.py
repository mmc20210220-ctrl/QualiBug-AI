"""Source-declared body-reference target authority.

A request field named ``addressId`` proves neither that it is a relationship nor
that the target is the ``address`` entity. Likewise ``x-foreign-key: true``
proves a relationship exists but not what it references. Formal dependency and
runtime binding need a target identity.

This module resolves one body field only from explicit target-bearing metadata
on the request schema / field dictionary. The target may identify a Behavior IR
entity, a source operation with one entity, or an explicit collection path that
maps uniquely to one entity. Missing and ambiguous targets remain unresolved.
"""
from __future__ import annotations

import re
from typing import Any

SCHEMA_VERSION = "qualibug.body-reference-authority.v1"

_TARGET_KEYS = (
    "x-reference-target",
    "x-resource-ref",
    "x-entity-ref",
    "x-foreign-key-target",
    "reference_ref",
    "resource_ref",
    "entity_ref",
    "foreign_key_ref",
    "references",
    "reference_target",
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


def _normalize_path(value: Any) -> str:
    raw = _text(value).split("?", 1)[0]
    if not raw.startswith("/"):
        return ""
    raw = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"{\1}", raw)
    raw = re.sub(r"/+", "/", raw)
    return raw.rstrip("/") or "/"


def _request_schema_root(operation: dict[str, Any]) -> dict[str, Any]:
    schema = _dict(
        operation.get("request_schema")
        or operation.get("requestBody")
        or operation.get("request_body_schema")
    )
    content = _dict(schema.get("content"))
    if content:
        media = _dict(content.get("application/json"))
        nested = _dict(media.get("schema"))
        if nested:
            schema = nested
    return schema


def _body_path_tokens(path: str) -> list[str]:
    return [
        name or index
        for name, index in re.findall(
            r"([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+)\]",
            _text(path),
        )
        if name or index
    ]


def _schema_node(operation: dict[str, Any], body_path: str) -> dict[str, Any]:
    node = _request_schema_root(operation)
    if not node:
        return {}
    for token in _body_path_tokens(body_path):
        while _text(node.get("type")).lower() == "array" and _dict(node.get("items")):
            node = _dict(node.get("items"))
        if token.isdigit():
            if _text(node.get("type")).lower() == "array":
                node = _dict(node.get("items"))
                if not node:
                    return {}
            continue
        child = _dict(_dict(node.get("properties")).get(token))
        if not child:
            return {}
        node = child
    return node


def _target_values(row: dict[str, Any]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    for key in _TARGET_KEYS:
        value = row.get(key)
        if isinstance(value, (str, int)) and _text(value):
            values.append((key, _text(value)))
    foreign_key = row.get("x-foreign-key")
    if isinstance(foreign_key, str) and _text(foreign_key):
        values.append(("x-foreign-key", _text(foreign_key)))
    foreign_key = row.get("foreign_key")
    if isinstance(foreign_key, str) and _text(foreign_key).lower() not in {
        "true", "false", "yes", "no", "是", "否",
    }:
        values.append(("foreign_key", _text(foreign_key)))
    target = _dict(row.get("foreign_key_target") or row.get("reference_target"))
    for key in ("entity_ref", "resource_ref", "table", "path", "target"):
        value = _text(target.get(key))
        if value:
            values.append((f"target.{key}", value))
    return values


def _field_dictionary_rows(operation: dict[str, Any], body_path: str) -> list[dict[str, Any]]:
    leaf = _text(body_path).split(".")[-1].split("[")[0]
    normalized = re.sub(r"\[\d+\]", "[]", _text(body_path))
    matches: list[dict[str, Any]] = []
    for raw in _list(operation.get("field_dictionary")):
        row = _dict(raw)
        field = _text(row.get("field_path") or row.get("field") or row.get("name"))
        if not field:
            continue
        field_normalized = re.sub(r"\[\d+\]", "[]", field)
        if field_normalized in {normalized, leaf} or field == leaf:
            matches.append(row)
    return matches


def _entity_aliases(entity: dict[str, Any]) -> set[str]:
    aliases = {
        _norm(entity.get("id")),
        _norm(entity.get("name")),
        _norm(entity.get("table")),
        _norm(entity.get("table_name")),
    }
    aliases.update(_norm(value) for value in _list(entity.get("source_entity_names")))
    aliases.discard("")
    expanded = set(aliases)
    for value in list(aliases):
        if value.endswith("ies") and len(value) > 3:
            expanded.add(value[:-3] + "y")
        if value.endswith("es") and len(value) > 2:
            expanded.add(value[:-2])
        if value.endswith("s") and len(value) > 1:
            expanded.add(value[:-1])
    return expanded


def _target_name_token(value: str) -> str:
    raw = _text(value)
    if not raw:
        return ""
    path = _normalize_path(raw)
    if path:
        static = [
            part for part in path.strip("/").split("/")
            if part and not (part.startswith("{") and part.endswith("}"))
        ]
        return _norm(static[-1] if static else "")
    # SQL-ish REFERENCES addresses(id), addresses.id or entity:address.
    match = re.search(r"(?i)references\s+([A-Za-z_][A-Za-z0-9_]*)", raw)
    if match:
        return _norm(match.group(1))
    if "(" in raw:
        raw = raw.split("(", 1)[0]
    if "." in raw:
        raw = raw.split(".", 1)[0]
    if ":" in raw and not raw.startswith("http"):
        raw = raw.rsplit(":", 1)[-1]
    return _norm(raw)


def _operation_entity_refs(operation: dict[str, Any], behavior_ir: dict[str, Any]) -> set[str]:
    refs = {
        _text(value)
        for value in [
            *_list(operation.get("entity_refs")),
            operation.get("entity_ref"),
        ]
        if _text(value)
    }
    op_ref = _text(operation.get("id") or operation.get("operation_id"))
    entity_ids = {
        _text(row.get("id"))
        for row in _list(_dict(behavior_ir).get("entities"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    for raw in _list(_dict(behavior_ir).get("relations")):
        relation = _dict(raw)
        if _text(relation.get("status")) in {"conflicting", "unsupported"}:
            continue
        relation_refs = {
            _text(relation.get("operation_ref")),
            _text(relation.get("from_ref")),
            _text(relation.get("to_ref")),
            _text(relation.get("entity_ref")),
        }
        if op_ref in relation_refs:
            refs.update(ref for ref in relation_refs if ref in entity_ids)
    return refs


def _resolve_target_entity(raw_target: str, behavior_ir: dict[str, Any]) -> tuple[str, str]:
    entities = [
        _dict(row)
        for row in _list(_dict(behavior_ir).get("entities"))
        if isinstance(row, dict) and _text(_dict(row).get("id"))
    ]
    operations = [
        _dict(row)
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict) and _text(_dict(row).get("id") or _dict(row).get("operation_id"))
    ]
    target_text = _text(raw_target)
    if not target_text:
        return "", "target_empty"

    exact_entity = [entity for entity in entities if _text(entity.get("id")) == target_text]
    if len(exact_entity) == 1:
        return _text(exact_entity[0].get("id")), "entity_id"

    exact_operation = [
        operation for operation in operations
        if _text(operation.get("id") or operation.get("operation_id")) == target_text
    ]
    if len(exact_operation) == 1:
        refs = sorted(_operation_entity_refs(exact_operation[0], behavior_ir))
        if len(refs) == 1:
            return refs[0], "operation_entity_ref"
        return "", "target_operation_entity_ambiguous"

    path = _normalize_path(target_text)
    if path:
        path_ops = [
            operation for operation in operations
            if _normalize_path(operation.get("path") or operation.get("raw_path")) == path
        ]
        refs = sorted(
            {
                ref
                for operation in path_ops
                for ref in _operation_entity_refs(operation, behavior_ir)
            }
        )
        if len(refs) == 1:
            return refs[0], "operation_path_entity_ref"

    token = _target_name_token(target_text)
    matches = [entity for entity in entities if token and token in _entity_aliases(entity)]
    if len(matches) == 1:
        return _text(matches[0].get("id")), "entity_source_name"
    if len(matches) > 1:
        return "", "target_entity_ambiguous"
    return "", "target_entity_unresolved"


def resolve_body_reference(
    operation: dict[str, Any],
    body_path: str,
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Resolve one explicit body-reference target or a named fail-closed reason."""

    candidates: list[dict[str, str]] = []
    node = _schema_node(operation, body_path)
    for key, target in _target_values(node):
        candidates.append({
            "source": f"request_schema:{key}",
            "target": target,
        })
    for row in _field_dictionary_rows(operation, body_path):
        for key, target in _target_values(row):
            candidates.append({
                "source": f"field_dictionary:{key}",
                "target": target,
            })

    relation_declared_without_target = bool(
        node.get("x-foreign-key") is True
        or node.get("foreign_key") is True
        or any(
            _dict(row).get("foreign_key") is True
            for row in _field_dictionary_rows(operation, body_path)
        )
    )
    if not candidates:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "UNRESOLVED",
            "reason_code": (
                "BODY_REFERENCE_TARGET_MISSING"
                if relation_declared_without_target
                else "BODY_REFERENCE_RELATION_UNDECLARED"
            ),
            "operation_ref": _text(operation.get("id") or operation.get("operation_id")),
            "body_path": _text(body_path),
            "target_entity_ref": "",
            "authorities": [],
        }

    resolved: dict[str, list[str]] = {}
    unresolved_reasons: list[str] = []
    for candidate in candidates:
        entity_ref, basis = _resolve_target_entity(candidate["target"], behavior_ir)
        if not entity_ref:
            unresolved_reasons.append(basis)
            continue
        resolved.setdefault(entity_ref, []).append(
            f"{candidate['source']}:{basis}"
        )

    if len(resolved) != 1:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "UNRESOLVED",
            "reason_code": (
                "BODY_REFERENCE_TARGET_AMBIGUOUS"
                if len(resolved) > 1
                else "BODY_REFERENCE_TARGET_UNRESOLVED"
            ),
            "operation_ref": _text(operation.get("id") or operation.get("operation_id")),
            "body_path": _text(body_path),
            "target_entity_ref": "",
            "candidate_entity_refs": sorted(resolved),
            "unresolved_target_reasons": sorted(set(unresolved_reasons)),
            "authorities": sorted(
                {authority for values in resolved.values() for authority in values}
            ),
        }

    entity_ref = next(iter(resolved))
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "RESOLVED",
        "reason_code": "",
        "operation_ref": _text(operation.get("id") or operation.get("operation_id")),
        "body_path": _text(body_path),
        "target_entity_ref": entity_ref,
        "authorities": sorted(set(resolved[entity_ref])),
    }


__all__ = [
    "SCHEMA_VERSION",
    "resolve_body_reference",
]
