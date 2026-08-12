"""Propagate body-reference identity through exact source descriptions.

A description becomes an authority only after another body field from the same source
has already been resolved to exactly one entity by a stronger authority (for example
an exact database FK). No translation, field-name-to-entity mapping, or fuzzy matching
is permitted.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _operation_ref(operation: dict[str, Any]) -> str:
    return _text(operation.get("id") or operation.get("operation_id"))


def _source_ids(operation: dict[str, Any]) -> set[str]:
    return {
        _text(row.get("source_id"))
        for row in _list(operation.get("source_refs"))
        if isinstance(row, dict) and _text(row.get("source_id"))
    }


def _schema(operation: dict[str, Any]) -> dict[str, Any]:
    schema = _dict(operation.get("request_schema") or operation.get("requestBody"))
    content = _dict(schema.get("content"))
    if content:
        media = next((
            _dict(value)
            for key, value in content.items()
            if isinstance(value, dict) and ("json" in _text(key).lower() or not key)
        ), {})
        schema = _dict(media.get("schema")) or schema
    return schema


def _body_metadata(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    def visit(node: dict[str, Any], prefix: str = "") -> None:
        for name, raw in _dict(node.get("properties")).items():
            if not isinstance(raw, dict):
                continue
            path = f"{prefix}.{name}" if prefix else _text(name)
            if not path:
                continue
            result[path] = {
                "description": _text(raw.get("description")),
                "format": _text(raw.get("format")).lower(),
            }
            if _text(raw.get("type")).lower() == "object" or raw.get("properties"):
                visit(raw, path)

    visit(_schema(operation))
    return result


def _label(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value)).casefold()


def _stable(*parts: Any) -> str:
    raw = "\x1f".join(_text(part) for part in parts)
    return "body_reference:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def project_exact_description_anchor_relations(
    model: dict[str, Any],
    resolved_relations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    operations = {
        _operation_ref(row): _dict(row)
        for row in _list(model.get("operations"))
        if isinstance(row, dict) and _operation_ref(row)
    }
    metadata = {ref: _body_metadata(op) for ref, op in operations.items()}

    anchors: dict[tuple[str, str], set[str]] = {}
    anchor_rows: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for raw in resolved_relations:
        relation = _dict(raw)
        if _text(relation.get("status")) != "RESOLVED" or not _text(relation.get("target_entity_ref")):
            continue
        op_ref = _text(relation.get("operation_ref"))
        body_path = _text(relation.get("body_path"))
        meta = _dict(metadata.get(op_ref, {}).get(body_path))
        label = _label(meta.get("description"))
        if not label or _text(meta.get("format")) != "uuid":
            continue
        for source_id in _source_ids(operations.get(op_ref, {})):
            key = (source_id, label)
            target = _text(relation.get("target_entity_ref"))
            anchors.setdefault(key, set()).add(target)
            anchor_rows.setdefault((source_id, label, target), []).append(relation)

    existing = {
        (_text(row.get("operation_ref")), _text(row.get("body_path")))
        for row in resolved_relations
        if isinstance(row, dict) and _text(row.get("status")) == "RESOLVED"
    }
    projected: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for op_ref, operation in operations.items():
        op_sources = _source_ids(operation)
        if not op_sources:
            continue
        for body_path, meta in metadata.get(op_ref, {}).items():
            if (op_ref, body_path) in existing or _text(meta.get("format")) != "uuid":
                continue
            label = _label(meta.get("description"))
            if not label:
                continue
            candidates: set[str] = set()
            anchors_used: list[dict[str, Any]] = []
            for source_id in op_sources:
                targets = anchors.get((source_id, label), set())
                if len(targets) != 1:
                    continue
                target = next(iter(targets))
                candidates.add(target)
                anchors_used.extend(anchor_rows.get((source_id, label, target), []))
            if len(candidates) != 1:
                continue
            target = next(iter(candidates))
            identity = (op_ref, body_path, target)
            if identity in seen:
                continue
            seen.add(identity)
            projected.append({
                "schema": "qualibug.body-reference-relation.v1",
                "id": _stable(op_ref, body_path, target, label),
                "status": "RESOLVED",
                "reason_code": "",
                "operation_ref": op_ref,
                "body_path": body_path,
                "target_entity_ref": target,
                "authority": "source_exact_body_description+resolved_relation_anchor",
                "source_description": _text(meta.get("description")),
                "anchor_relation_ids": sorted({_text(row.get("id")) for row in anchors_used if _text(row.get("id"))}),
                "source_refs": [dict(row) for row in _list(operation.get("source_refs")) if isinstance(row, dict)],
                "field_name_entity_inference_allowed": False,
                "description_translation_allowed": False,
                "fuzzy_description_matching_allowed": False,
            })
    return projected


__all__ = ["project_exact_description_anchor_relations"]
