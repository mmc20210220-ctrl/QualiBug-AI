"""Strict cleanup identity extraction shared by cleanup execution paths.

A cleanup target may resolve from the cleanup contract's declared identity
column.  When an API exposes a generic primary key instead, only the response
root or one standard response envelope (data/result/item/resource) is eligible.
Unrelated nested objects are never identity authority for a destructive cleanup.
"""
from __future__ import annotations

from typing import Any

_GENERIC_PRIMARY_KEYS = ("id", "uuid", "guid", "key")
_RESPONSE_ENTITY_ENVELOPES = ("data", "result", "item", "resource")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _field_key(value: Any) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _scalar_for_keys(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    allowed = {_field_key(key) for key in keys}
    matches: list[str] = []
    for key, value in row.items():
        if _field_key(key) not in allowed:
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            continue
        token = _text(value)
        if token:
            matches.append(token)
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else ""


def strict_observed_resource_identity(
    body: Any,
    *,
    identity_column: str = "",
) -> str:
    """Return one cleanup-safe resource identity or empty when unproven."""

    row = _dict(body)
    if not row:
        return ""
    column = _text(identity_column)
    declared_keys = (column,) if column else ()

    # Exact declared column always wins and is checked at the resource root or
    # one standard response envelope only.
    if declared_keys:
        direct = _scalar_for_keys(row, declared_keys)
        if direct:
            return direct
        nested_declared: list[str] = []
        for envelope in _RESPONSE_ENTITY_ENVELOPES:
            nested = _dict(row.get(envelope))
            value = _scalar_for_keys(nested, declared_keys)
            if value:
                nested_declared.append(value)
        unique_declared = list(dict.fromkeys(nested_declared))
        if len(unique_declared) == 1:
            return unique_declared[0]
        if len(unique_declared) > 1:
            return ""

    # A generic API primary-key spelling may bridge a differently named DB key,
    # but it must identify the response resource itself, never a nested relation.
    direct_generic = _scalar_for_keys(row, _GENERIC_PRIMARY_KEYS)
    if direct_generic:
        return direct_generic
    nested_generic: list[str] = []
    for envelope in _RESPONSE_ENTITY_ENVELOPES:
        nested = _dict(row.get(envelope))
        value = _scalar_for_keys(nested, _GENERIC_PRIMARY_KEYS)
        if value:
            nested_generic.append(value)
    unique_generic = list(dict.fromkeys(nested_generic))
    return unique_generic[0] if len(unique_generic) == 1 else ""


__all__ = ["strict_observed_resource_identity"]
