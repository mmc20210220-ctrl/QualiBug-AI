"""Keep accessibility completeness authoritative after matrix aggregation."""
from __future__ import annotations

import copy
from typing import Any

from . import formal_ui_surface as _formal
from . import observer_contracts_base as _observers
from . import professional_ui_accessibility_engine as _engine

_INSTALL_MARKER = "_qualibug_accessibility_matrix_guard_installed"
_ORIGINAL_OBSERVER = "_qualibug_observer_before_accessibility_matrix_guard"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def apply_final_accessibility_gate(receipt: dict[str, Any]) -> dict[str, Any]:
    evidence = copy.deepcopy(_dict(receipt.get("evidence")))
    ui_evidence = copy.deepcopy(_dict(evidence.get(_formal.EVIDENCE_KEY)))
    observations = [
        _dict(row)
        for row in _list(ui_evidence.get("accessibility_rule_observations"))
        if isinstance(row, dict)
    ]
    if not observations:
        return receipt
    incomplete = any(
        _text(row.get("status"), limit=80) == "INDETERMINATE"
        or row.get("complete_observation") is not True
        for row in observations
    )
    typed_violation = ui_evidence.get("violation_observed") is True
    if not incomplete or typed_violation:
        return receipt
    ui_evidence["expectation_satisfied"] = None
    ui_evidence["violation_observed"] = False
    ui_evidence["accessibility_complete_observation_required_for_property_held"] = True
    evidence[_formal.EVIDENCE_KEY] = ui_evidence
    return _observers._receipt(
        observer_id=_text(receipt.get("observer_id")) or _formal.OBSERVER_ID,
        status="INDETERMINATE",
        reason_code="UI_ACCESSIBILITY_OBSERVATION_INCOMPLETE",
        evidence=evidence,
        campaign_id=_text(receipt.get("campaign_id")),
        execution_id=_text(receipt.get("execution_id")),
    )


def install_professional_ui_accessibility_matrix_guard() -> None:
    if getattr(_engine, _INSTALL_MARKER, False):
        return
    original = _observers._REGISTERED_OBSERVER_HANDLERS.get(_formal.OBSERVER_ID)
    if not callable(original):
        raise RuntimeError("formal_ui_observer_handler_missing")
    setattr(_engine, _ORIGINAL_OBSERVER, original)

    def observer_with_final_accessibility_gate(envelope: dict[str, Any]) -> dict[str, Any]:
        return apply_final_accessibility_gate(original(envelope))

    _formal._ui_observer_handler = observer_with_final_accessibility_gate
    _observers._REGISTERED_OBSERVER_HANDLERS[
        _formal.OBSERVER_ID
    ] = observer_with_final_accessibility_gate
    setattr(_engine, _INSTALL_MARKER, True)


__all__ = [
    "apply_final_accessibility_gate",
    "install_professional_ui_accessibility_matrix_guard",
]
