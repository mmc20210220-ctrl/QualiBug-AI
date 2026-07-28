"""Fail closed when a formal UI contract has no real source identity.

``source_ui_contract_binding`` historically filled a missing source with the
literal ``ui_design_specs``. That label describes a collection, not an immutable
source asset, and allowed a hand-constructed runtime dictionary to look
source-backed. This guard keeps the existing binding authority but replaces that
fallback with one of two accepted forms:

* explicit ``source_refs`` containing a source_id;
* explicit ``source_id`` converted to one source ref.

Anything else becomes ``FORMAL_UI_SOURCE_REF_MISSING`` before Behavior IR facts
are created.
"""
from __future__ import annotations

from typing import Any

from . import source_ui_contract_binding as _binding

_INSTALL_MARKER = "_qualibug_source_ui_contract_source_guard_installed"
_ORIGINAL_VALIDATE_MARKER = "_qualibug_original_validate_ui_request_source_guard"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _strict_source_refs(contract: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [
        dict(row)
        for row in _list(_dict(contract).get("source_refs"))
        if isinstance(row, dict) and _text(row.get("source_id"))
    ]
    if refs:
        return refs
    source_id = _text(_dict(contract).get("source_id"))
    if not source_id:
        return []
    return [{
        "source_id": source_id,
        "version": _text(_dict(contract).get("source_version")),
        "locator": _text(_dict(contract).get("source_locator")),
        "kind": "formal_ui_contract",
        "quote_hash": _text(_dict(contract).get("quote_hash")),
    }]


def install_source_ui_contract_source_guard() -> None:
    if getattr(_binding, _INSTALL_MARKER, False):
        return
    original_validate = getattr(
        _binding,
        _ORIGINAL_VALIDATE_MARKER,
        _binding._validated_request,
    )
    setattr(_binding, _ORIGINAL_VALIDATE_MARKER, original_validate)

    def validate_with_source_identity(
        contract: dict[str, Any],
    ) -> tuple[dict[str, Any] | None, str]:
        if not _strict_source_refs(contract):
            return None, "FORMAL_UI_SOURCE_REF_MISSING"
        return original_validate(contract)

    # bind_source_ui_contracts looks these symbols up from its module globals on
    # every call, so the guard changes admission without replacing the authority.
    _binding._source_refs = _strict_source_refs
    _binding._validated_request = validate_with_source_identity
    setattr(_binding, _INSTALL_MARKER, True)


__all__ = ["install_source_ui_contract_source_guard"]
