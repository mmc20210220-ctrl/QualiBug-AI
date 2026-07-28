"""Add strict performance-contract transport to the existing scan context authority.

The customer scan body already reaches ``build_campaign_context_from_scan_body``.
This bridge wraps that single pure builder so both the initial scan and approval
retry preserve explicit ``performance_formal_contracts``. It validates only the
JSON transport shape; source identity, operation/actor identity and numeric SLO
semantics remain owned by the performance overlay and IR binder.
"""
from __future__ import annotations

from typing import Any

from . import private_pilot_scan_context_contract as _context

_INSTALL_MARKER = "_qualibug_performance_scan_context_bridge_installed"
_ORIGINAL_MARKER = "_qualibug_original_build_campaign_context_before_performance"


def _contracts(body: dict[str, Any]) -> list[dict[str, Any]] | None:
    if "performance_formal_contracts" not in body:
        return None
    raw = body.get("performance_formal_contracts")
    if not isinstance(raw, list):
        raise ValueError("performance_formal_contracts_not_list")
    output: list[dict[str, Any]] = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise ValueError(f"performance_formal_contract_not_object:{index}")
        output.append(dict(value))
    return output


def install_performance_scan_context_bridge() -> None:
    if getattr(_context, _INSTALL_MARKER, False):
        return
    original = getattr(
        _context,
        _ORIGINAL_MARKER,
        _context.build_campaign_context_from_scan_body,
    )
    setattr(_context, _ORIGINAL_MARKER, original)

    def build_with_performance_contracts(
        body: dict[str, Any],
    ) -> dict[str, Any]:
        context = dict(original(body))
        contracts = _contracts(body)
        if contracts is not None:
            context["performance_formal_contracts"] = contracts
        return context

    _context.build_campaign_context_from_scan_body = build_with_performance_contracts
    setattr(_context, _INSTALL_MARKER, True)


def restore_performance_scan_context_bridge() -> None:
    original = getattr(_context, _ORIGINAL_MARKER, None)
    if original is not None:
        _context.build_campaign_context_from_scan_body = original
    setattr(_context, _INSTALL_MARKER, False)


__all__ = [
    "install_performance_scan_context_bridge",
    "restore_performance_scan_context_bridge",
]
