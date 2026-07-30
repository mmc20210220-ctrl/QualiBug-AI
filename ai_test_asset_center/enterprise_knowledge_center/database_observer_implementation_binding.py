"""Inject only approved operation-scoped database observers into implementation slots.

The base implementation binder intentionally preserves broad source-backed candidates. Once an
API operation has entered the storage-mapping authority domain, raw same-name database fields must
not bypass that decision layer. This module replaces raw DATABASE_FIELD candidates for the scoped
interface with exact approved observer field bindings while preserving non-database observers.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Iterable


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _text(value).lower())


def _field_labels(row: dict[str, Any]) -> set[str]:
    path = [
        _text(value)
        for value in _list(row.get("api_property_path"))
        if _text(value) and _text(value) not in {"[]", "*"}
    ]
    return {
        value
        for value in {
            _norm(row.get("api_field_name")),
            _norm(row.get("database_field_name")),
            _norm(".".join(path)),
            _norm(path[-1] if path else ""),
        }
        if value
    }


def _slot_label(slot: dict[str, Any]) -> str:
    value = _text(slot.get("source_field_candidate"))
    return _norm(value.split(".")[-1])


def _scope_interface_ids(api_rows: Iterable[Any]) -> set[str]:
    return {
        _text(row.get("interface_id"))
        for row in api_rows
        if isinstance(row, dict)
        and bool(row.get("authoritative"))
        and _text(row.get("interface_id"))
    }


def _mapping_scope_exists(asset: dict[str, Any], interface_ids: set[str]) -> bool:
    if not interface_ids:
        return False
    return any(
        isinstance(row, dict)
        and _text(row.get("interface_id")) in interface_ids
        for row in [
            *_list(asset.get("api_operation_database_table_candidates")),
            *_list(asset.get("api_operation_database_field_candidates")),
        ]
    )


def apply_approved_database_observers_to_slot(
    slot: dict[str, Any],
    *,
    asset: dict[str, Any],
    api_rows: Iterable[Any],
) -> dict[str, Any]:
    """Replace scoped raw DB candidates with approved Observer bindings."""
    result = deepcopy(slot)
    interface_ids = _scope_interface_ids(api_rows)
    if not _mapping_scope_exists(asset, interface_ids):
        result["database_mapping_authority_scope_applied"] = False
        return result

    target = _slot_label(result)
    approved_rows = [
        dict(row)
        for row in _list(asset.get("approved_database_observer_field_bindings"))
        if isinstance(row, dict)
        and _text(row.get("interface_id")) in interface_ids
        and bool(row.get("runtime_observer_authoritative"))
        and target
        and target in _field_labels(row)
    ]
    non_database = [
        deepcopy(row)
        for row in _list(result.get("bindings"))
        if isinstance(row, dict)
        and _text(row.get("binding_kind")) != "DATABASE_FIELD"
    ]
    approved_bindings = [
        {
            "binding_kind": "DATABASE_FIELD",
            "observer_id": _text(row.get("observer_id")),
            "field_binding_id": _text(row.get("field_binding_id")),
            "interface_id": _text(row.get("interface_id")),
            "field_id": _text(row.get("database_field_id")),
            "table_id": _text(row.get("database_table_id")),
            "table": _text(row.get("database_table_id")),
            "field": _text(row.get("database_field_name")),
            "api_field_id": _text(row.get("api_field_id")),
            "api_field_name": _text(row.get("api_field_name")),
            "value_source": _text(row.get("value_source")),
            "status": "BOUND_APPROVED_READ_ONLY_OBSERVER",
            "authoritative": True,
            "read_only": True,
            "write_target_allowed": False,
            "oracle_authority_allowed": False,
            "derivation": "operator_approved_database_observer_contract",
            "mapping_decision_id": _text(row.get("mapping_decision_id")),
            "evidence": deepcopy(_list(row.get("evidence"))),
        }
        for row in approved_rows
    ]
    result["bindings"] = [*non_database, *approved_bindings]
    result["database_mapping_authority_scope_applied"] = True
    result["raw_database_field_candidates_removed"] = True
    result["approved_database_observer_binding_count"] = len(approved_bindings)
    if not approved_bindings and not non_database:
        result["status"] = "UNBOUND"
        result["reason_code"] = "APPROVED_DATABASE_OBSERVER_FIELD_REQUIRED"
    return result


__all__ = ["apply_approved_database_observers_to_slot"]
