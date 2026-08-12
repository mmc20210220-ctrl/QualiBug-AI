"""Project operator-approved database FK lineage into Behavior IR body references.

This bridge intentionally requires a complete authority chain:
request field -> approved API/DB field mapping -> exact database FK -> approved
parent-table API mapping -> source-bound Behavior IR entity.  Field spelling,
route shape, and schema-name similarity are never sufficient.
"""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any

SCHEMA_VERSION = "qualibug.database-body-reference-projection.v1"
RELATION_SCHEMA = "qualibug.body-reference-relation.v1"
_READY = "READY_FOR_RUNTIME_CONNECTION_BINDING"
_DB_AUTHORITY = "DATABASE_MODEL_SOURCE_DECLARATION"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_path(value: Any) -> str:
    raw = _text(value).split("?", 1)[0]
    if not raw.startswith("/"):
        return ""
    raw = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"{\1}", raw)
    raw = re.sub(r"/+", "/", raw)
    return raw.rstrip("/") or "/"


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(value) for value in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _operation_ref(operation: dict[str, Any]) -> str:
    return _text(operation.get("id") or operation.get("operation_id"))


def _operation_index(model: dict[str, Any]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    result: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for raw in _list(model.get("operations")):
        operation = _dict(raw)
        method = _text(operation.get("method")).upper()
        path = _normalize_path(operation.get("path") or operation.get("raw_path"))
        if method and path:
            result.setdefault((method, path), []).append(operation)
    return result


def _operation_entity_refs(operation: dict[str, Any], model: dict[str, Any]) -> set[str]:
    entity_ids = {
        _text(row.get("id"))
        for row in _list(model.get("entities"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    refs = {
        _text(value)
        for value in [*_list(operation.get("entity_refs")), operation.get("entity_ref")]
        if _text(value) in entity_ids
    }
    op_ref = _operation_ref(operation)
    for raw in _list(model.get("relations")):
        relation = _dict(raw)
        if not _list(relation.get("source_refs")):
            continue
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


def _approved_contract(raw: Any) -> dict[str, Any]:
    contract = _dict(raw)
    if (
        _text(contract.get("status")) != _READY
        or contract.get("mapping_authoritative") is not True
        or not _text(contract.get("table_mapping_decision_id"))
        or not _text(contract.get("database_table_id"))
    ):
        return {}
    return contract


def _contract_operation(
    contract: dict[str, Any],
    operation_index: dict[tuple[str, str], list[dict[str, Any]]],
) -> dict[str, Any]:
    key = (
        _text(contract.get("method")).upper(),
        _normalize_path(contract.get("path")),
    )
    matches = operation_index.get(key, [])
    return matches[0] if len(matches) == 1 else {}


def _normalize_body_path(value: Any) -> str:
    raw = _text(value)
    if raw.startswith("$."):
        raw = raw[2:]
    return raw


def _body_path(binding: dict[str, Any]) -> str:
    value_source = _text(binding.get("value_source"))
    if value_source.startswith("request.body."):
        suffix = value_source[len("request.body."):]
        return _normalize_body_path(suffix)
    path = [_text(value) for value in _list(binding.get("api_property_path")) if _text(value)]
    if path:
        return _normalize_body_path(".".join(path))
    field = _text(binding.get("api_field_name"))
    return _normalize_body_path(field)


def _approved_request_field_bindings(contract: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in _list(contract.get("field_bindings")):
        binding = _dict(raw)
        if (
            binding.get("authoritative") is not True
            or not _text(binding.get("mapping_decision_id"))
            or not _text(binding.get("database_field_name"))
        ):
            continue
        body_path = _body_path(binding)
        if not body_path:
            continue
        result.append({**binding, "body_path": body_path})
    return result


def _authoritative_fk(raw: Any) -> dict[str, Any]:
    relationship = _dict(raw)
    evidence = _dict(relationship.get("evidence_address"))
    child_columns = [_text(value) for value in _list(relationship.get("child_columns")) if _text(value)]
    parent_columns = [_text(value) for value in _list(relationship.get("parent_columns")) if _text(value)]
    if (
        _text(relationship.get("contract_authority")) != _DB_AUTHORITY
        or not _text(relationship.get("source_id"))
        or not _text(relationship.get("source_locator"))
        or evidence.get("exact") is not True
        or not _text(relationship.get("child_table_id"))
        or not _text(relationship.get("parent_table_id"))
        or not child_columns
        or len(child_columns) != len(parent_columns)
    ):
        return {}
    return relationship


def _parent_table_entity(
    parent_table_id: str,
    parent_columns: list[str],
    contracts: list[dict[str, Any]],
    operation_index: dict[tuple[str, str], list[dict[str, Any]]],
    model: dict[str, Any],
) -> tuple[str, list[str]]:
    entity_refs: set[str] = set()
    authority_refs: list[str] = []
    for contract in contracts:
        if _text(contract.get("database_table_id")) != parent_table_id:
            continue
        selected = [_text(value) for value in _list(contract.get("selected_identity_key")) if _text(value)]
        if selected != parent_columns:
            continue
        operation = _contract_operation(contract, operation_index)
        if not operation:
            continue
        refs = _operation_entity_refs(operation, model)
        if len(refs) != 1:
            continue
        entity_refs.update(refs)
        authority_refs.append(_text(contract.get("table_mapping_decision_id")))
    if len(entity_refs) != 1:
        return "", []
    return next(iter(entity_refs)), sorted(set(authority_refs))
