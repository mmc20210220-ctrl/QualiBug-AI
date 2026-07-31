"""Run the formal event observer after the trigger and before cleanup.

Registered observers normally execute in the Finalizer, after cleanup compensation. That order
is correct for final-state observers but wrong for event delivery: cleanup may emit a second
event or remove correlation state and contaminate the observation window. This installer:

1. intercepts the mainline cleanup call;
2. performs the event observation exactly once before cleanup;
3. stores the typed receipt and its evidence on ``observations``;
4. makes the Finalizer's registered handler return that same receipt instead of polling again.

Cleanup always proceeds even when observation is indeterminate or the observer harness fails.
"""
from __future__ import annotations

import copy
from typing import Any

from . import formal_event_surface as _surface

_PRE_RECEIPT_KEY = "pre_cleanup_event_observer_receipt"
_INSTALL_MARKER = "_qualibug_event_pre_cleanup_observer_installed"
_ORIGINAL_CLEANUP_MARKER = "_qualibug_original_cleanup_before_event_patch"
_ORIGINAL_HANDLER_MARKER = "_qualibug_original_event_handler_before_pre_cleanup_patch"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _requires_event_observer(exp: dict[str, Any]) -> bool:
    return any(
        _text(_dict(row).get("observer_id")) == _surface.OBSERVER_ID
        for row in _list(_dict(exp).get("observers"))
        if isinstance(row, dict)
    )


def _event_assertion(exp: dict[str, Any]) -> dict[str, Any]:
    """Return the unique assertion owned by the formal event surface.

    Selecting the first assertion is only accidentally correct while every event
    protocol emits one assertion. A future compound protocol could place a state
    assertion first and make the event observer read the wrong property contract.
    Ambiguity is fail-closed; the observer must never guess which assertion owns
    the event contract.
    """
    matches = [
        dict(row)
        for row in _list(_dict(exp).get("assertions"))
        if isinstance(row, dict)
        and _text(row.get("kind")) == _surface.ASSERTION_KIND
    ]
    return matches[0] if len(matches) == 1 else {}


def _pre_observe_event(
    *,
    exp: dict[str, Any],
    observations: dict[str, Any],
    campaign_id: str,
    execution_id: str,
) -> dict[str, Any] | None:
    if not _requires_event_observer(exp):
        return None
    existing = _dict(observations.get(_PRE_RECEIPT_KEY))
    if existing:
        return existing
    assertion = _event_assertion(exp)
    if not assertion:
        from .observer_contracts_base import build_observer_receipt

        receipt = build_observer_receipt(
            observer_id=_surface.OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="EVENT_ASSERTION_IDENTITY_NOT_UNIQUE",
            evidence={"matching_assertion_count": 0},
        )
    else:
        try:
            from . import observer_contracts_base as _observers

            handler = _observers._REGISTERED_OBSERVER_HANDLERS.get(
                _surface.OBSERVER_ID
            )
            if not callable(handler):
                handler = _surface._event_observer_handler
            receipt = handler({
                "observer_id": _surface.OBSERVER_ID,
                "experiment": exp,
                "observations": observations,
                "assertion": assertion,
                "property": _dict(assertion.get("property")),
                "control_observation": _dict(observations.get("control_observation")),
                "treatment_observation": _dict(observations.get("treatment_observation")),
                "execution_steps": _list(observations.get("execution_steps")),
                "campaign_id": campaign_id,
                "execution_id": execution_id,
            })
            validated = _observers.validate_observer_receipt(_dict(receipt))
            evidence = copy.deepcopy(_dict(validated.get("evidence")))
            event_evidence = _dict(evidence.get(_surface.EVIDENCE_KEY))
            if event_evidence:
                event_evidence["observation_phase"] = "pre_cleanup"
                evidence[_surface.EVIDENCE_KEY] = event_evidence
                receipt = _observers.build_observer_receipt(
                    observer_id=_surface.OBSERVER_ID,
                    status=_text(validated.get("status")),
                    reason_code=_text(validated.get("reason_code")),
                    evidence=evidence,
                    campaign_id=_text(validated.get("campaign_id")),
                    execution_id=_text(validated.get("execution_id")),
                )
            else:
                receipt = validated
        except Exception as exc:  # noqa: BLE001 - cleanup must still proceed
            from .observer_contracts_base import build_observer_receipt

            receipt = build_observer_receipt(
                observer_id=_surface.OBSERVER_ID,
                status="INDETERMINATE",
                reason_code="EVENT_PRE_CLEANUP_OBSERVER_FAILED",
                evidence={"error_type": type(exc).__name__},
            )
    receipt = copy.deepcopy(_dict(receipt))
    evidence = _dict(receipt.get("evidence"))
    observations[_PRE_RECEIPT_KEY] = copy.deepcopy(receipt)
    if _text(receipt.get("status")).upper() == "OBSERVED":
        for key, value in evidence.items():
            if key not in observations:
                observations[key] = copy.deepcopy(value)
    return receipt


def install_formal_event_pre_cleanup_observer() -> None:
    """Patch the imported mainline cleanup symbol and registered event handler idempotently."""
    from . import experiment_executor as _executor
    from . import observer_contracts_base as _observers

    if getattr(_executor, _INSTALL_MARKER, False):
        return
    original_cleanup = getattr(
        _executor,
        _ORIGINAL_CLEANUP_MARKER,
        _executor.execute_experiment_cleanup_compensation,
    )
    setattr(_executor, _ORIGINAL_CLEANUP_MARKER, original_cleanup)

    original_handler = getattr(
        _surface,
        _ORIGINAL_HANDLER_MARKER,
        _observers._REGISTERED_OBSERVER_HANDLERS.get(_surface.OBSERVER_ID)
        or _surface._event_observer_handler,
    )
    setattr(_surface, _ORIGINAL_HANDLER_MARKER, original_handler)

    def event_handler_reusing_pre_cleanup(envelope: dict[str, Any]) -> dict[str, Any]:
        observations = _dict(_dict(envelope).get("observations"))
        precomputed = _dict(observations.get(_PRE_RECEIPT_KEY))
        if precomputed:
            return copy.deepcopy(precomputed)
        return original_handler(envelope)

    def cleanup_after_event_observation(*args: Any, **kwargs: Any) -> dict[str, Any]:
        exp = _dict(kwargs.get("exp"))
        observations = _dict(kwargs.get("observations"))
        _pre_observe_event(
            exp=exp,
            observations=observations,
            campaign_id=_text(
                kwargs.get("resolved_campaign_id") or kwargs.get("campaign_id")
            ),
            execution_id=_text(kwargs.get("resolved_execution_id")),
        )
        return original_cleanup(*args, **kwargs)

    _observers._REGISTERED_OBSERVER_HANDLERS[_surface.OBSERVER_ID] = (
        event_handler_reusing_pre_cleanup
    )
    _executor.execute_experiment_cleanup_compensation = cleanup_after_event_observation
    setattr(_executor, _INSTALL_MARKER, True)


__all__ = [
    "install_formal_event_pre_cleanup_observer",
]
