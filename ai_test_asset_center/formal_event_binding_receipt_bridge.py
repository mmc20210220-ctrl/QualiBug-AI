"""Attach durable event Binding Identity to typed observer receipts.

The observer receipt schema is content-addressed and closed. Identity is therefore added inside
its evidence payload and the receipt is rebuilt through the existing receipt authority. No raw
event id, correlation value, payload, request, response, credential or secret is copied.
"""
from __future__ import annotations

import copy
import functools
from typing import Any

from .formal_event_surface import OBSERVER_ID

EVIDENCE_IDENTITY_KEY = "formal_event_binding_identity"
_INSTALL_MARKER = "_qualibug_formal_event_binding_receipt_bridge_installed"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _identity(envelope: dict[str, Any]) -> dict[str, Any]:
    assertion = _dict(envelope.get("assertion"))
    property_spec = _dict(assertion.get("property")) or _dict(envelope.get("property"))
    source = _dict(property_spec.get("formal_event_binding_identity"))
    if _text(source.get("status")) != "BOUND":
        return {}
    allowed = (
        "schema_version",
        "status",
        "event_contract_ref",
        "implementation_binding_ref",
        "action_surface_binding_ref",
        "observer_binding_ref",
        "interface_id",
        "actor_ref",
        "scenario_ref",
        "runtime_plan_ref",
        "runtime_materialization_ref",
        "contract_field_binding_refs",
        "runtime_value_binding_refs",
        "binding_authority",
        "identity_reselection_allowed",
        "token_overlap_is_authoritative",
    )
    return {
        key: copy.deepcopy(source.get(key))
        for key in allowed
        if source.get(key) not in (None, "", [], {})
    }


def attach_event_binding_identity_to_receipt(
    receipt: dict[str, Any],
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Rebuild one typed event receipt with privacy-safe durable identity evidence."""
    from .observer_contracts_base import (
        build_observer_receipt,
        validate_observer_receipt,
    )

    validated = validate_observer_receipt(receipt)
    if _text(validated.get("observer_id")) != OBSERVER_ID:
        return validated
    identity = _identity(envelope)
    if not identity:
        return validated
    evidence = copy.deepcopy(_dict(validated.get("evidence")))
    evidence[EVIDENCE_IDENTITY_KEY] = identity
    return build_observer_receipt(
        observer_id=OBSERVER_ID,
        status=_text(validated.get("status")),
        reason_code=_text(validated.get("reason_code")),
        evidence=evidence,
        campaign_id=_text(validated.get("campaign_id")),
        execution_id=_text(validated.get("execution_id")),
    )


def install_formal_event_binding_receipt_bridge() -> None:
    """Wrap the registered event observer once without changing its observation logic."""
    from . import observer_contracts_base as observers

    if getattr(observers, _INSTALL_MARKER, False):
        return
    original = observers._REGISTERED_OBSERVER_HANDLERS.get(OBSERVER_ID)
    if not callable(original):
        raise RuntimeError("formal_event_observer_handler_not_registered")

    @functools.wraps(original)
    def observe_with_binding_identity(envelope: dict[str, Any]) -> dict[str, Any]:
        receipt = original(envelope)
        return attach_event_binding_identity_to_receipt(receipt, envelope)

    observers._REGISTERED_OBSERVER_HANDLERS[OBSERVER_ID] = (
        observe_with_binding_identity
    )
    setattr(observers, _INSTALL_MARKER, True)


__all__ = [
    "EVIDENCE_IDENTITY_KEY",
    "attach_event_binding_identity_to_receipt",
    "install_formal_event_binding_receipt_bridge",
]
