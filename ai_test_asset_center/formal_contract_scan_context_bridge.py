"""One additive bridge for formal contracts absent from the legacy scan builder.

Multiple independent wrappers around the same function are order-sensitive: restoring
one wrapper can silently remove another. This module owns one composition point for
performance and stability contract transport. UI and event contracts remain first-class
in the underlying builder.
"""
from __future__ import annotations

from typing import Any

from . import private_pilot_scan_context_contract as _context

_INSTALL_MARKER = "_qualibug_formal_contract_scan_context_bridge_installed"
_ORIGINAL_MARKER = "_qualibug_original_build_campaign_context_before_formal_contracts"


def _object_list(
    body: dict[str, Any],
    key: str,
    item_prefix: str,
) -> list[dict[str, Any]] | None:
    if key not in body:
        return None
    raw = body.get(key)
    if not isinstance(raw, list):
        raise ValueError(f"{key}_not_list")
    rows: list[dict[str, Any]] = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise ValueError(f"{item_prefix}_not_object:{index}")
        rows.append(dict(value))
    return rows


def install_formal_contract_scan_context_bridge() -> None:
    if getattr(_context, _INSTALL_MARKER, False):
        return
    original = getattr(
        _context,
        _ORIGINAL_MARKER,
        _context.build_campaign_context_from_scan_body,
    )
    setattr(_context, _ORIGINAL_MARKER, original)

    def build_with_formal_contracts(body: dict[str, Any]) -> dict[str, Any]:
        context = dict(original(body))
        performance = _object_list(
            body,
            "performance_formal_contracts",
            "performance_formal_contract",
        )
        stability = _object_list(
            body,
            "stability_formal_contracts",
            "stability_formal_contract",
        )
        if performance is not None:
            context["performance_formal_contracts"] = performance
        if stability is not None:
            context["stability_formal_contracts"] = stability
        return context

    _context.build_campaign_context_from_scan_body = build_with_formal_contracts
    setattr(_context, _INSTALL_MARKER, True)


def restore_formal_contract_scan_context_bridge() -> None:
    original = getattr(_context, _ORIGINAL_MARKER, None)
    if original is not None:
        _context.build_campaign_context_from_scan_body = original
    setattr(_context, _INSTALL_MARKER, False)


__all__ = [
    "install_formal_contract_scan_context_bridge",
    "restore_formal_contract_scan_context_bridge",
]
