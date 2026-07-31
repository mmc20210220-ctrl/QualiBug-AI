"""Stamp honest capability boundaries on every formal event observation receipt.

The HTTP event observer deduplicates repeated polling snapshots by stable event
id. This proves the number of unique event identities seen in the complete
window. It does not prove whether a broker physically delivered the same stable
id twice, and it does not currently judge ordering. These facts belong on the
receipt itself so a downstream report cannot overstate the evidence.
"""
from __future__ import annotations

import copy
from typing import Any

from . import formal_event_surface as _surface

_INSTALL_MARKER = "_qualibug_formal_event_capability_guard_installed"
_ORIGINAL_HANDLER_MARKER = "_qualibug_original_event_handler_before_capability_guard"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stamp(receipt: dict[str, Any]) -> dict[str, Any]:
    from .observer_contracts_base import (
        build_observer_receipt,
        validate_observer_receipt,
    )

    validated = validate_observer_receipt(_dict(receipt))
    evidence = copy.deepcopy(_dict(validated.get("evidence")))
    observation = copy.deepcopy(_dict(evidence.get(_surface.EVIDENCE_KEY)))
    if not observation:
        return validated
    observation.update({
        "count_semantics": "unique_stable_event_ids_within_full_window",
        "duplicate_physical_delivery_of_same_event_id_provable": False,
        "ordering_contract_supported": False,
        "direct_broker_protocol_supported": False,
        "observation_adapter": "approved_target_relative_http_get",
    })
    evidence[_surface.EVIDENCE_KEY] = observation
    return build_observer_receipt(
        observer_id=_text(validated.get("observer_id")),
        status=_text(validated.get("status")),
        reason_code=_text(validated.get("reason_code")),
        evidence=evidence,
        campaign_id=_text(validated.get("campaign_id")),
        execution_id=_text(validated.get("execution_id")),
    )


def install_formal_event_capability_guard() -> None:
    from . import observer_contracts_base as _observers

    if getattr(_surface, _INSTALL_MARKER, False):
        return
    original = getattr(
        _surface,
        _ORIGINAL_HANDLER_MARKER,
        _surface._event_observer_handler,
    )
    setattr(_surface, _ORIGINAL_HANDLER_MARKER, original)

    def handler_with_capability_boundary(envelope: dict[str, Any]) -> dict[str, Any]:
        return _stamp(original(envelope))

    _surface._event_observer_handler = handler_with_capability_boundary
    if _surface.OBSERVER_ID in _observers._REGISTERED_OBSERVER_HANDLERS:
        _observers._REGISTERED_OBSERVER_HANDLERS[_surface.OBSERVER_ID] = (
            handler_with_capability_boundary
        )
    setattr(_surface, _INSTALL_MARKER, True)


__all__ = ["install_formal_event_capability_guard"]
