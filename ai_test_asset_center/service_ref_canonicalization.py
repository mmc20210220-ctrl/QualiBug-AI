"""Routing-only canonicalization of Behavior IR service references.

Some OpenAPI parsers carry an operation ``server`` URL where another source
carries a logical service name.  Behavior IR preserves that source value, which
is correct, but the execution topology is keyed by service identity.  This
module maps a URL-shaped service ref to a topology key only when the normalized
URL has exactly one declared owner.

The projection is routing-only: it deep-copies the IR, never mutates semantic
source evidence, and never guesses by host, port, path prefix or filename.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .target_policy import normalize_base_url


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_url(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return ""
    try:
        return normalize_base_url(raw)
    except ValueError:
        return ""


def unique_service_by_url(
    topology: dict[str, dict[str, Any]],
) -> dict[str, str]:
    """Return only exact topology URLs with one unambiguous service owner."""

    names_by_url: dict[str, list[str]] = {}
    for raw_name, raw_row in _dict(topology).items():
        name = _text(raw_name)
        row = _dict(raw_row)
        url = _normalized_url(
            row.get("approved_base_url")
            or row.get("base_url")
            or row.get("url")
        )
        if name and url:
            names_by_url.setdefault(url, []).append(name)
    return {
        url: names[0]
        for url, names in names_by_url.items()
        if len(set(names)) == 1
    }


def canonical_service_ref(
    raw_ref: Any,
    topology: dict[str, dict[str, Any]],
) -> str:
    """Canonicalize an exact service key or uniquely-owned exact URL."""

    raw = _text(raw_ref)
    if not raw:
        return ""
    if raw in topology:
        return raw
    normalized = _normalized_url(raw)
    if not normalized:
        return raw
    return unique_service_by_url(topology).get(normalized, raw)


def canonicalize_behavior_ir_service_refs(
    behavior_ir: dict[str, Any],
    topology: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Return a routing projection with exact URL service refs canonicalized."""

    if not topology:
        return behavior_ir
    projected = deepcopy(_dict(behavior_ir))
    operations: list[Any] = []
    for raw in _list(projected.get("operations")):
        if not isinstance(raw, dict):
            operations.append(raw)
            continue
        operation = dict(raw)
        original = _text(
            operation.get("_service_name")
            or operation.get("service")
            or operation.get("service_name")
        )
        canonical = canonical_service_ref(original, topology)
        if canonical and canonical != original:
            operation["_service_name"] = canonical
            operation["service"] = canonical
            operation["routing_original_service_ref"] = original
            operation["routing_service_ref_authority"] = "exact_topology_url_match"
        operations.append(operation)
    projected["operations"] = operations
    return projected
