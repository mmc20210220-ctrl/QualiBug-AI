"""Source-backed operation-to-service ownership without benchmark naming rules.

Service ownership is an execution identity: using the wrong owner either sends a
request to the wrong target or makes a valid operation look service-agnostic.
Prefer explicit metadata carried by the exact operation/source/interface.  The
historical ``*_service.json`` filename convention remains compatibility-only.
"""
from __future__ import annotations

from pathlib import PurePath
from typing import Any


_GENERIC_SOURCE_IDS = frozenset({"api_spec", "submitted_api_spec"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _explicit_service_ref(row: dict[str, Any]) -> str:
    return _text(
        row.get("service")
        or row.get("service_name")
        or row.get("system_ref")
        or row.get("application_ref")
        or row.get("application_id")
        or row.get("component_ref")
    )


def _operation_source_ids(operation: dict[str, Any]) -> set[str]:
    ids = {
        _text(ref.get("source_id"))
        for ref in _list(_dict(operation).get("source_refs"))
        if isinstance(ref, dict) and _text(ref.get("source_id"))
    }
    ids.update(
        _text(value)
        for value in _list(_dict(operation).get("source_ids"))
        if _text(value)
    )
    canonical = _text(_dict(operation).get("canonical_contract_source_id"))
    if canonical:
        ids.add(canonical)
    return {value for value in ids if value not in _GENERIC_SOURCE_IDS}


def _legacy_filename_service(row: dict[str, Any]) -> str:
    """Compatibility for historical assets; never the primary authority."""

    raw = _text(
        row.get("filename")
        or row.get("original_name")
        or row.get("name")
        or row.get("logical_key")
    )
    if not raw:
        return ""
    name = PurePath(raw.split("#", 1)[0]).name
    suffix = "_service.json"
    return name[: -len(suffix)] if name.endswith(suffix) else ""


def source_backed_operation_service_name(
    operation: dict[str, Any],
    data: dict[str, Any],
) -> str:
    """Resolve one exact source-backed service identity, or return empty."""

    direct = _explicit_service_ref(_dict(operation))
    if direct:
        return direct
    source_ids = _operation_source_ids(operation)
    if not source_ids:
        return ""

    source_rows = [
        row
        for row in _list(_dict(data).get("sources") or _dict(data).get("source_inventory"))
        if isinstance(row, dict)
        and _text(row.get("source_id") or row.get("id")) in source_ids
    ]
    explicit = {
        _explicit_service_ref(row)
        for row in source_rows
        if _explicit_service_ref(row)
    }
    if len(explicit) == 1:
        return next(iter(explicit))
    if len(explicit) > 1:
        return ""

    interface_rows: list[dict[str, Any]] = []
    for raw in _list(_dict(data).get("interfaces")):
        if not isinstance(raw, dict):
            continue
        own_ids = {
            _text(value) for value in _list(raw.get("source_ids")) if _text(value)
        }
        canonical = _text(raw.get("canonical_contract_source_id"))
        if canonical:
            own_ids.add(canonical)
        if own_ids & source_ids:
            interface_rows.append(raw)
    explicit = {
        _explicit_service_ref(row)
        for row in interface_rows
        if _explicit_service_ref(row)
    }
    if len(explicit) == 1:
        return next(iter(explicit))
    if len(explicit) > 1:
        return ""

    # Compatibility only: accept the old source filename convention when the
    # exact source id identifies one unambiguous legacy service name.
    legacy = {
        value
        for value in (_legacy_filename_service(row) for row in source_rows)
        if value
    }
    if len(legacy) == 1:
        return next(iter(legacy))
    return ""


def install_operation_service_ownership_authority(core: Any) -> None:
    core._service_name_from_source_refs = source_backed_operation_service_name
