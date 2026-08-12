"""Source-backed database body-reference projection helpers."""
from __future__ import annotations
from typing import Any
import re

from .database_body_reference_projection_common import _dict, _list, _text, _operation_ref

def _identifier_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", _text(value).lower())


def _database_model_tables(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in [*_list(asset.get("data_tables")), *_list(asset.get("tables"))]:
        table = _dict(raw)
        table_id = _text(table.get("table_id"))
        evidence = _dict(table.get("evidence_address"))
        source_declared = (
            _text(table.get("derivation")) == "database_model_document_ir"
            or bool(_list(table.get("database_model_declarations")))
        )
        if (
            not table_id
            or not source_declared
            or not _text(table.get("source_id"))
            or not _text(table.get("source_locator"))
            or evidence.get("exact") is not True
        ):
            continue
        result[table_id] = table
    return result


def _entity_table_id(entity: dict[str, Any]) -> str:
    table = _text(entity.get("table") or entity.get("table_id"))
    if not table:
        return ""
    return table if table.startswith("table:") else f"table:{table}"


def _entities_by_table(model: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for raw in _list(model.get("entities")):
        entity = _dict(raw)
        table_id = _entity_table_id(entity)
        if table_id and _text(entity.get("id")):
            result.setdefault(table_id, []).append(entity)
    return result


def _operation_output_entities(operation: dict[str, Any], model: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    op_ref = _operation_ref(operation)
    entities = {
        _text(row.get("id")): _dict(row)
        for row in _list(model.get("entities"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    seen: set[str] = set()
    for raw in _list(model.get("relations")):
        relation = _dict(raw)
        if (
            _text(relation.get("operation_ref")) != op_ref
            or _text(relation.get("relation_type")) not in {"produces", "writes"}
            or _text(relation.get("status")) != "accepted"
            or not _list(relation.get("source_refs"))
        ):
            continue
        entity_ref = _text(relation.get("to_ref") or relation.get("entity_ref"))
        entity = entities.get(entity_ref)
        if not entity or entity_ref in seen or not _entity_table_id(entity):
            continue
        seen.add(entity_ref)
        rows.append((entity, relation))
    return rows


def _body_property_paths(operation: dict[str, Any]) -> list[str]:
    schema = _dict(operation.get("request_schema") or operation.get("requestBody"))
    content = _dict(schema.get("content"))
    if content:
        media = next((
            _dict(value)
            for key, value in content.items()
            if isinstance(value, dict) and ("json" in _text(key).lower() or not key)
        ), {})
        schema = _dict(media.get("schema")) or schema
    result: list[str] = []

    def visit(node: dict[str, Any], prefix: str = "") -> None:
        for name, raw in _dict(node.get("properties")).items():
            if not isinstance(raw, dict):
                continue
            path = f"{prefix}.{name}" if prefix else _text(name)
            if not path:
                continue
            result.append(path)
            child = _dict(raw)
            if _text(child.get("type")).lower() == "object" or child.get("properties"):
                visit(child, path)

    visit(schema)
    return result
