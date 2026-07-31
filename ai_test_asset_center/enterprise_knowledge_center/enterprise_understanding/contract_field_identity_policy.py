"""Pure authority policy for source-to-contract request-field identity."""
from __future__ import annotations

import re
from typing import Any

from .schema import text

FLAT_REQUEST_LOCATIONS = frozenset({"PATH", "QUERY", "HEADER", "COOKIE"})
NESTED_REQUEST_LOCATIONS = frozenset({"BODY", "FORM"})


def normalize_field_identity(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text(value).lower())


def contract_schema_identity(field_binding: dict[str, Any]) -> str:
    return text(field_binding.get("schema_path") or field_binding.get("field"))


def exact_contract_field_identity(
    field_binding: dict[str, Any],
    *,
    interface_id: str,
    location: str,
    source_field: str,
) -> bool:
    """Accept only source interface + location + complete schema identity.

    Nested request payloads require the complete declared path. A source candidate
    named ``status`` cannot bind ``order.status``. Flat request locations may use the
    declared parameter name because their schema identity is not nested.
    """
    if not field_binding or not text(source_field):
        return False
    requested_location = text(location).upper()
    if text(field_binding.get("interface_id")) != text(interface_id):
        return False
    if text(field_binding.get("location")).upper() != requested_location:
        return False

    source_identity = normalize_field_identity(source_field)
    schema_identity = normalize_field_identity(
        contract_schema_identity(field_binding)
    )
    if not source_identity or not schema_identity:
        return False
    if requested_location in NESTED_REQUEST_LOCATIONS:
        return source_identity == schema_identity
    if requested_location in FLAT_REQUEST_LOCATIONS:
        field_identity = normalize_field_identity(field_binding.get("field"))
        return source_identity in {schema_identity, field_identity}
    return False


__all__ = [
    "FLAT_REQUEST_LOCATIONS",
    "NESTED_REQUEST_LOCATIONS",
    "normalize_field_identity",
    "contract_schema_identity",
    "exact_contract_field_identity",
]
