"""Canonical per-step process observation and sequence assertion.

The executor publishes one ``qualibug.process-timeline.v1`` receipt. This
observer consumes its ``events`` list and joins status facts from the live
ProcessStepLedger. The former append-only list shape remains readable only for
migration; no new second timeline is authored.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

OBSERVER_ID = "process_step_timeline"
SURFACE = "process_timeline"
ADAPTER = "http_api"
EVIDENCE_KEY = "process_step_timeline"
KIND_SEQUENCE_ORDER = "step_sequence_order"
KIND_PROCESS_COMPLETION = "process_completion"

_BUSINESS_PHASES = frozenset({"control", "treatment"})
_EXECUTION_EVENTS = frozenset(
    {
        "TRANSPORT_STARTED",
        "TRANSPORT_COMPLETED",
        "AFTER_STATE_OBSERVED",
        "STEP_COMPLETED",
        "STEP_FAILED",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _declared(spec: dict[str, Any], key: str) -> Any:
    source = _dict(spec)
    if key in source:
        return source[key]
    return _dict(source.get("property")).get(key)


def _timeline_events(observations: dict[str, Any]) -> list[dict[str, Any]]:
    raw = observations.get("process_timeline")
    if isinstance(raw, dict):
        rows = [
            dict(row)
            for row in _list(raw.get("events"))
            if isinstance(row, dict)
        ]
    else:
        rows = [dict(row) for row in _list(raw) if isinstance(row, dict)]

    events: list[dict[str, Any]] = []
    for row in rows:
        if _text(row.get("event_type")):
            events.append(row)
            continue
        if "status_code" not in row:
            events.append(row)
            continue
        try:
            status_code = int(row.get("status_code") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "legacy process timeline status_code must be an integer"
            ) from exc
        migrated = {
            **row,
            "event_type": "STEP_COMPLETED" if status_code > 0 else "STEP_FAILED",
            "event_source": "legacy_flat_process_timeline",
        }
        events.append(migrated)
    return events


def _ledger_rows(observations: dict[str, Any]) -> dict[str, dict[str, Any]]:
    ledger = observations.get("process_step_ledger")
    if ledger is None or not hasattr(ledger, "all_rows"):
        return {}
    return {
        _text(row.get("step_id")): dict(row)
        for row in ledger.all_rows()
        if isinstance(row, dict) and _text(row.get("step_id"))
    }


def observe_process_steps(envelope: dict[str, Any]) -> dict[str, Any]:
    """Turn the canonical timeline into a typed observer receipt."""
    from .observer_contracts_base import _receipt

    observations = _dict(envelope.get("observations"))
    events = _timeline_events(observations)
    business_events = [
        row
        for row in events
        if _text(row.get("phase")) in _BUSINESS_PHASES
        and _text(row.get("step_id"))
        and _text(row.get("event_type")) in _EXECUTION_EVENTS
    ]
    if not business_events:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="PROCESS_TIMELINE_ABSENT",
            evidence={
                "timeline_schema": _text(_dict(observations.get("process_timeline")).get("schema_version")),
                "event_count": len(events),
                "reason": "no business-step execution events were recorded",
            },
        )

    rows = _ledger_rows(observations)
    step_order: list[str] = []
    terminal_event_by_step: dict[str, dict[str, Any]] = {}
    for event in business_events:
        step_id = _text(event.get("step_id"))
        if step_id not in step_order:
            step_order.append(step_id)
        terminal_event_by_step[step_id] = event

    steps: list[dict[str, Any]] = []
    for ordinal, step_id in enumerate(step_order, 1):
        event = terminal_event_by_step[step_id]
        row = _dict(rows.get(step_id))
        status_code = int(row.get("status_code") or 0)
        final_status = _text(
            row.get("final_step_status") or row.get("final_status")
        )
        reached_transport = status_code > 0 or _text(event.get("event_type")) in {
            "TRANSPORT_STARTED",
            "TRANSPORT_COMPLETED",
            "AFTER_STATE_OBSERVED",
            "STEP_COMPLETED",
        }
        steps.append(
            {
                "step_id": step_id,
                "phase": _text(event.get("phase")),
                "ordinal": int(event.get("step_ordinal") or ordinal),
                "operation_ref": _text(
                    event.get("operation_ref") or row.get("operation_ref")
                ),
                "actor_ref": _text(event.get("actor_ref") or row.get("actor_ref")),
                "status_code": status_code,
                "final_status": final_status,
                "terminal_event_type": _text(event.get("event_type")),
                "reached_transport": reached_transport,
            }
        )

    unreached = [row["step_id"] for row in steps if not row["reached_transport"]]
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        reason_code="",
        evidence={
            EVIDENCE_KEY: {
                "surface": SURFACE,
                "timeline_schema": _text(
                    _dict(observations.get("process_timeline")).get("schema_version")
                ),
                "steps": steps,
                "observed_order": step_order,
                "step_count": len(steps),
                "steps_not_reaching_transport": unreached,
                "coverage_complete": not unreached,
            }
        },
    )


def evaluate_step_sequence_order(envelope: dict[str, Any]) -> dict[str, Any]:
    """Compare observed order with a source-declared expected order."""
    spec = _dict(envelope.get("spec"))
    expected_order = [
        _text(item)
        for item in _list(_declared(spec, "expected_step_order"))
        if _text(item)
    ]
    if len(expected_order) < 2:
        return {
            "passed": None,
            "reason_code": "STEP_ORDER_NOT_DECLARED",
            "expected": None,
            "actual": {"declared_steps": expected_order},
        }

    payload = _dict(_dict(envelope.get("observations")).get(EVIDENCE_KEY))
    observed_order = [
        _text(item) for item in _list(payload.get("observed_order")) if _text(item)
    ]
    if not observed_order:
        return {
            "passed": None,
            "reason_code": "PROCESS_TIMELINE_ABSENT",
            "expected": {"expected_step_order": expected_order},
            "actual": None,
        }

    missing = [step_id for step_id in expected_order if step_id not in observed_order]
    if missing:
        return {
            "passed": None,
            "reason_code": "DECLARED_STEP_NOT_OBSERVED",
            "expected": {"expected_step_order": expected_order},
            "actual": {"observed_order": observed_order, "missing": missing},
        }
    if _list(payload.get("steps_not_reaching_transport")):
        return {
            "passed": None,
            "reason_code": "PROCESS_COVERAGE_INCOMPLETE",
            "expected": {"expected_step_order": expected_order},
            "actual": {
                "observed_order": observed_order,
                "steps_not_reaching_transport": list(
                    payload["steps_not_reaching_transport"]
                ),
            },
        }

    expected_set = set(expected_order)
    observed_declared = [
        step_id for step_id in observed_order if step_id in expected_set
    ]
    return {
        "passed": observed_declared == expected_order,
        "reason_code": "",
        "expected": {"expected_step_order": expected_order},
        "actual": {
            "observed_order": observed_declared,
            "full_observed_order": observed_order,
        },
    }


def evaluate_process_completion(envelope: dict[str, Any]) -> dict[str, Any]:
    """Verdict for the multi-step process protocol's completion assertion.

    Every declared expected step must have been observed reaching transport;
    when an expected order is declared, the observed order of the declared
    steps must match it exactly. Missing steps, steps that never reached
    transport, or an order break are explicit verdicts — never a silent pass.
    """
    spec = _dict(envelope.get("spec"))
    expected_steps = [
        _text(item)
        for item in _list(_declared(spec, "expected_steps"))
        if _text(item)
    ]
    expected_order = [
        _text(item)
        for item in _list(_declared(spec, "expected_order"))
        if _text(item)
    ]
    payload = _dict(_dict(envelope.get("observations")).get(EVIDENCE_KEY))
    observed_order = [
        _text(item) for item in _list(payload.get("observed_order")) if _text(item)
    ]
    if not expected_steps:
        return {
            "passed": None,
            "reason_code": "PROCESS_EXPECTED_STEPS_NOT_DECLARED",
            "expected": None,
            "actual": None,
        }
    if not observed_order:
        return {
            "passed": None,
            "reason_code": "PROCESS_TIMELINE_ABSENT",
            "expected": {"expected_steps": expected_steps},
            "actual": None,
        }
    missing = [
        step_id for step_id in expected_steps if step_id not in observed_order
    ]
    if missing:
        return {
            "passed": None,
            "reason_code": "DECLARED_STEP_NOT_OBSERVED",
            "expected": {"expected_steps": expected_steps},
            "actual": {"observed_order": observed_order, "missing": missing},
        }
    if _list(payload.get("steps_not_reaching_transport")):
        return {
            "passed": None,
            "reason_code": "PROCESS_COVERAGE_INCOMPLETE",
            "expected": {"expected_steps": expected_steps},
            "actual": {
                "observed_order": observed_order,
                "steps_not_reaching_transport": list(
                    payload["steps_not_reaching_transport"]
                ),
            },
        }
    if expected_order:
        expected_set = set(expected_order)
        observed_declared = [
            step_id for step_id in observed_order if step_id in expected_set
        ]
        if observed_declared != expected_order:
            return {
                "passed": False,
                "reason_code": "PROCESS_STEP_ORDER_VIOLATION",
                "expected": {
                    "expected_steps": expected_steps,
                    "expected_order": expected_order,
                },
                "actual": {"observed_order": observed_declared},
            }
    return {
        "passed": True,
        "reason_code": "",
        "expected": {
            "expected_steps": expected_steps,
            "expected_order": expected_order,
        },
        "actual": {"observed_order": observed_order},
    }


def install_process_step_surface() -> dict[str, str]:
    """Install the observer and ordering assertion through existing registries."""
    from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
    from .observer_contracts_base import OBSERVER_REGISTRY, register_observer

    installed: dict[str, str] = {}
    if OBSERVER_ID not in OBSERVER_REGISTRY:
        installed["observer"] = register_observer(
            OBSERVER_ID,
            surface=SURFACE,
            adapter=ADAPTER,
            handler=observe_process_steps,
            evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["observer"] = OBSERVER_ID

    if KIND_SEQUENCE_ORDER in set(registered_assertion_kinds()):
        installed[KIND_SEQUENCE_ORDER] = KIND_SEQUENCE_ORDER
    else:
        installed[KIND_SEQUENCE_ORDER] = register_assertion_kind(
            KIND_SEQUENCE_ORDER,
            evaluator=evaluate_step_sequence_order,
            required_evidence_keys=(EVIDENCE_KEY,),
        )
    if KIND_PROCESS_COMPLETION in set(registered_assertion_kinds()):
        installed[KIND_PROCESS_COMPLETION] = KIND_PROCESS_COMPLETION
    else:
        installed[KIND_PROCESS_COMPLETION] = register_assertion_kind(
            KIND_PROCESS_COMPLETION,
            evaluator=evaluate_process_completion,
            required_evidence_keys=(EVIDENCE_KEY,),
        )
    logger.info("process step surface installed: %s", installed)
    return installed
