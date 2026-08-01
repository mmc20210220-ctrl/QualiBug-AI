"""Run the formal event observer after the trigger and before cleanup.

Registered observers normally execute in the Finalizer, after cleanup compensation. That order
is correct for final-state observers but wrong for event delivery: cleanup may emit a second
event or remove correlation state and contaminate the observation window. This installer:

1. registers a first-class pre-cleanup hook on the mainline cleanup authority;
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
_BASE_HANDLER_MARKER = "_qualibug_base_event_handler_before_pre_cleanup_registration"
_HOOK_NAME = "formal_event_pre_cleanup_observer"


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


def _event_trigger_step_id(exp: dict[str, Any]) -> str:
    """Return the unique formal Event trigger step identity, never a position guess."""
    matches = [
        _text(row.get("step_id"))
        for row in _list(_dict(exp).get("treatment_plan"))
        if isinstance(row, dict)
        and (
            _text(row.get("protocol_step")) == "event_trigger"
            or _text(row.get("intent")) == "trigger_source_declared_event"
        )
        and _text(row.get("step_id"))
    ]
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else ""


def _event_trigger_match_count(exp: dict[str, Any]) -> int:
    return len(
        [
            row
            for row in _list(_dict(exp).get("treatment_plan"))
            if isinstance(row, dict)
            and (
                _text(row.get("protocol_step")) == "event_trigger"
                or _text(row.get("intent")) == "trigger_source_declared_event"
            )
            and _text(row.get("step_id"))
        ]
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


def _registered_handler() -> Any:
    """Resolve the current composed authority while preserving the explicit test seam.

    Production wrappers are installed in the observer registry. The public surface handler
    normally remains the exact authority captured when pre-cleanup was installed. A test may
    explicitly replace that surface handler after installation; honor only that deliberate
    replacement, otherwise use the fully composed registry chain.
    """
    from . import observer_contracts_base as observers

    current_surface = _surface._event_observer_handler
    installed_base = getattr(_surface, _BASE_HANDLER_MARKER, current_surface)
    if current_surface is not installed_base:
        return current_surface
    return observers._REGISTERED_OBSERVER_HANDLERS.get(
        _surface.OBSERVER_ID
    ) or current_surface


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
    step_id = _event_trigger_step_id(exp)
    assertion = _event_assertion(exp)
    if not step_id:
        from .observer_contracts_base import build_observer_receipt

        receipt = build_observer_receipt(
            observer_id=_surface.OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="EVENT_TRIGGER_STEP_IDENTITY_NOT_UNIQUE",
            evidence={
                "matching_event_trigger_step_count": _event_trigger_match_count(exp),
            },
            campaign_id=campaign_id,
            execution_id=execution_id,
        )
    elif not assertion:
        from .observer_contracts_base import build_observer_receipt

        receipt = build_observer_receipt(
            observer_id=_surface.OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="EVENT_ASSERTION_IDENTITY_NOT_UNIQUE",
            evidence={
                "matching_assertion_count": 0,
                "step_id": step_id,
            },
            campaign_id=campaign_id,
            execution_id=execution_id,
        )
    else:
        try:
            from . import observer_contracts_base as observers

            handler = _registered_handler()
            if not callable(handler):
                raise RuntimeError("formal_event_observer_handler_not_registered")
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
            validated = observers.validate_observer_receipt(_dict(receipt))
            evidence = copy.deepcopy(_dict(validated.get("evidence")))
            event_evidence = _dict(evidence.get(_surface.EVIDENCE_KEY))
            if event_evidence:
                event_evidence["observation_phase"] = "pre_cleanup"
                evidence[_surface.EVIDENCE_KEY] = event_evidence
            evidence["step_id"] = step_id
            receipt = observers.build_observer_receipt(
                observer_id=_surface.OBSERVER_ID,
                status=_text(validated.get("status")),
                reason_code=_text(validated.get("reason_code")),
                evidence=evidence,
                campaign_id=_text(validated.get("campaign_id")) or campaign_id,
                execution_id=_text(validated.get("execution_id")) or execution_id,
            )
        except Exception as exc:  # noqa: BLE001 - cleanup must still proceed
            from .observer_contracts_base import build_observer_receipt

            receipt = build_observer_receipt(
                observer_id=_surface.OBSERVER_ID,
                status="INDETERMINATE",
                reason_code="EVENT_PRE_CLEANUP_OBSERVER_FAILED",
                evidence={
                    "error_type": type(exc).__name__,
                    "step_id": step_id,
                },
                campaign_id=campaign_id,
                execution_id=execution_id,
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
    """Register the pre-cleanup observer without replacing mainline symbols."""
    from . import experiment_cleanup_executor as cleanup_executor
    from . import observer_contracts_base as observers

    if getattr(cleanup_executor, _INSTALL_MARKER, False):
        return

    original_handler = getattr(
        _surface,
        _BASE_HANDLER_MARKER,
        observers._REGISTERED_OBSERVER_HANDLERS.get(_surface.OBSERVER_ID)
        or _surface._event_observer_handler,
    )
    setattr(_surface, _BASE_HANDLER_MARKER, original_handler)

    def event_handler_reusing_pre_cleanup(envelope: dict[str, Any]) -> dict[str, Any]:
        observations = _dict(_dict(envelope).get("observations"))
        precomputed = _dict(observations.get(_PRE_RECEIPT_KEY))
        if precomputed:
            return copy.deepcopy(precomputed)
        authority = getattr(
            _surface,
            _BASE_HANDLER_MARKER,
            _surface._event_observer_handler,
        )
        return authority(envelope)

    def pre_cleanup_hook(context: dict[str, Any]) -> None:
        exp = _dict(context.get("exp"))
        observations = _dict(context.get("observations"))
        _pre_observe_event(
            exp=exp,
            observations=observations,
            campaign_id=_text(
                context.get("resolved_campaign_id") or context.get("campaign_id")
            ),
            execution_id=_text(context.get("resolved_execution_id")),
        )

    observers._REGISTERED_OBSERVER_HANDLERS[_surface.OBSERVER_ID] = (
        event_handler_reusing_pre_cleanup
    )
    cleanup_executor.register_cleanup_pre_hook(_HOOK_NAME, pre_cleanup_hook)
    setattr(cleanup_executor, _INSTALL_MARKER, True)


__all__ = [
    "install_formal_event_pre_cleanup_observer",
]
