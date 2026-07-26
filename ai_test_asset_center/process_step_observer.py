"""Per-step process observation and the ordering assertion it enables.

``experiment_plan_executor`` now appends a ``process_timeline`` row per step when a phase has
more than one, but a channel nobody reads is inert — the exact shape of defect this codebase
had in several places (a registry entry with no dispatch branch, an assertion kind whose
evidence no observer produces, an entire lexicon file that did not exist). This module closes
the loop: an observer that turns the timeline into a receipt, and an assertion kind that can
state something no existing kind could.

WHAT ORDERING ADDS
==================
Every built-in assertion kind is positive-polarity and single-window: it states that a value
became something. None can state "step A must precede step B", so a process whose steps are
individually correct but executed in the wrong order was unfalsifiable — the failure mode of
approval-before-validation, ship-before-payment, and every other sequencing defect.

SOURCE-DECLARED, LIKE EVERYTHING ELSE
=====================================
The expected order arrives on the assertion property. It is never derived from the plan
itself, which would make the assertion tautological: a plan compared against its own step
order can only ever pass.

FAIL-CLOSED
===========
A timeline missing a declared step, or a step that never reached transport, yields
INDETERMINATE with a named reason rather than a violation. A step that did not run is not
evidence that ordering was broken.
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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _declared(spec: dict[str, Any], key: str) -> Any:
    """Read a declaration from either shape the compiler produces.

    evaluate_assertion passes the WHOLE assertion dict as spec; the compiler spreads
    protocol assertion keys at that top level while nesting the source-derived property
    spec under "property". Reading one shape turns every declaration into "not declared".
    """
    source = _dict(spec)
    if key in source:
        return source[key]
    return _dict(source.get("property")).get(key)


def observe_process_steps(envelope: dict[str, Any]) -> dict[str, Any]:
    """Turn the per-step timeline into a receipt, or refuse with a named reason."""
    from .observer_contracts_base import _receipt

    observations = _dict(envelope.get("observations"))
    timeline = [row for row in _list(observations.get("process_timeline")) if isinstance(row, dict)]
    if not timeline:
        # The executor only writes the timeline when a phase has more than one step, so an
        # empty one means this experiment is single-step -- not that observation failed.
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="PROCESS_TIMELINE_ABSENT",
            evidence={"reason": "no phase in this experiment executed more than one step"},
        )

    steps = [
        {
            "step_id": _text(row.get("step_id")),
            "phase": _text(row.get("phase")),
            "ordinal": row.get("step_ordinal"),
            "operation_ref": _text(row.get("operation_ref")),
            "actor_ref": _text(row.get("actor_ref")),
            "status_code": row.get("status_code"),
            "reached_transport": bool(row.get("status_code")),
        }
        for row in timeline
    ]
    observed_order = [row["step_id"] for row in steps]
    unreached = [row["step_id"] for row in steps if not row["reached_transport"]]

    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        reason_code="",
        evidence={
            EVIDENCE_KEY: {
                "surface": SURFACE,
                "steps": steps,
                "observed_order": observed_order,
                "step_count": len(steps),
                "steps_not_reaching_transport": unreached,
                # A partially executed process is reported as such rather than presented as
                # a complete ordering reading.
                "coverage_complete": not unreached,
            }
        },
    )


def evaluate_step_sequence_order(envelope: dict[str, Any]) -> dict[str, Any]:
    """Observed step order must match a source-declared expected order."""
    spec = _dict(envelope.get("spec"))
    expected_order = [_text(item) for item in _list(_declared(spec, "expected_step_order")) if _text(item)]
    if len(expected_order) < 2:
        # One step has no order. Refusing keeps a vacuous "ordering verified" out of the
        # record.
        return {
            "passed": None,
            "reason_code": "STEP_ORDER_NOT_DECLARED",
            "expected": None,
            "actual": {"declared_steps": expected_order},
        }

    payload = _dict(_dict(envelope.get("observations")).get(EVIDENCE_KEY))
    observed_order = [_text(item) for item in _list(payload.get("observed_order")) if _text(item)]
    if not observed_order:
        return {
            "passed": None,
            "reason_code": "PROCESS_TIMELINE_ABSENT",
            "expected": {"expected_step_order": expected_order},
            "actual": None,
        }

    missing = [step_id for step_id in expected_order if step_id not in observed_order]
    if missing:
        # A declared step that never ran is not evidence that ordering was broken.
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
                "steps_not_reaching_transport": list(payload["steps_not_reaching_transport"]),
            },
        }

    # Compare only the declared steps, in the order they were observed. A plan may contain
    # steps the declaration says nothing about; those must not affect the verdict.
    observed_declared = [step_id for step_id in observed_order if step_id in set(expected_order)]
    return {
        "passed": observed_declared == expected_order,
        "reason_code": "",
        "expected": {"expected_step_order": expected_order},
        "actual": {"observed_order": observed_declared, "full_observed_order": observed_order},
    }


def install_process_step_surface() -> dict[str, str]:
    """Install the per-step observer and the ordering assertion kind, in that order.

    register_assertion_kind refuses a kind whose evidence key no registered observer
    declares it produces, so the observer must exist first. Idempotent.
    """
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
    logger.info("process step surface installed: %s", installed)
    return installed
