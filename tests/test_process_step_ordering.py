"""Per-step evidence, and the ordering assertion it makes possible.

Every built-in assertion kind is positive-polarity and single-window: it states that a value
became something. None can state "step A must precede step B". So a process whose steps are
each individually correct but executed in the wrong order was unfalsifiable — the failure
mode behind approve-before-validate, ship-before-payment, and every other sequencing defect.

The evidence for it did not exist either. ``control_observation`` / ``treatment_observation``
are single slots the executor OVERWRITES per step, so in a multi-step phase steps 1..N-1 left
no trace and the experiment still reported a verdict from the last one.

Two properties are pinned here:

* the ``process_timeline`` channel is additive and guarded on len(plan) > 1, so every
  existing one-step-per-phase experiment sees a byte-identical observations dict
* every refusal is INDETERMINATE with a named reason, never a violation. A step that did not
  run is not evidence that ordering was broken.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import pytest

from ai_test_asset_center import assertion_dsl_base as adb
from ai_test_asset_center import observer_contracts_base as ocb
from ai_test_asset_center.assertion_dsl_base import SUPPORTED_KINDS, evaluate_assertion
from ai_test_asset_center.observer_contracts_base import OBSERVER_REGISTRY
from ai_test_asset_center.process_step_observer import (
    EVIDENCE_KEY,
    KIND_SEQUENCE_ORDER,
    OBSERVER_ID,
    install_process_step_surface,
    observe_process_steps,
)


@pytest.fixture()
def process_surface() -> Iterator[dict[str, str]]:
    installed = install_process_step_surface()
    yield installed
    adb._REGISTERED_ASSERTION_EVALUATORS.pop(KIND_SEQUENCE_ORDER, None)
    adb._REGISTERED_KIND_EVIDENCE_KEYS.pop(KIND_SEQUENCE_ORDER, None)
    SUPPORTED_KINDS.discard(KIND_SEQUENCE_ORDER)
    OBSERVER_REGISTRY.pop(OBSERVER_ID, None)
    ocb._REGISTERED_OBSERVER_HANDLERS.pop(OBSERVER_ID, None)


def _timeline(*rows: tuple[str, int]) -> dict[str, Any]:
    return {
        "process_timeline": [
            {
                "step_id": step_id, "phase": "treatment", "step_ordinal": index + 1,
                "operation_ref": "op-x", "actor_ref": "actor-1", "status_code": status,
            }
            for index, (step_id, status) in enumerate(rows)
        ]
    }


def _payload(order: list[str], *, unreached: list[str] | None = None) -> dict[str, Any]:
    return {
        EVIDENCE_KEY: {
            "observed_order": list(order),
            "steps": [],
            "steps_not_reaching_transport": list(unreached or []),
        }
    }


def _assertion(order: list[str]) -> dict[str, Any]:
    return {
        "assertion_id": "a1", "kind": KIND_SEQUENCE_ORDER,
        "property": {"expected_step_order": order},
    }


# ── the additive channel ────────────────────────────────────────────────────

def test_timeline_is_only_written_for_multi_step_phases() -> None:
    """A one-step phase must leave the observations dict byte-identical.

    Asserted at the source, because the guard is what makes every existing experiment
    unaffected; if it were dropped, ~3100 stored experiments would gain a key.
    """
    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center" / "experiment_plan_step_executor_core.py"
    ).read_text(encoding="utf-8")
    assert "if len(plan) > 1:" in source
    assert 'observations.setdefault("process_timeline", []).append(' in source


def test_timeline_is_not_written_into_barrier_timeline() -> None:
    """barrier_timeline's observer returns INDETERMINATE without a release event and two
    distinct participants, so feeding ordinary sequential steps into it would manufacture a
    degraded concurrency reading from a process that has no concurrency in it."""
    source = (
        Path(__file__).resolve().parents[1]
        / "ai_test_asset_center" / "experiment_plan_step_executor_core.py"
    ).read_text(encoding="utf-8")
    # The per-step block writes process_timeline, never barrier_timeline.
    block = source[source.index("Per-step evidence channel"):]
    block = block[:block.index("return results")]
    assert "process_timeline" in block
    assert "barrier_timeline" not in block.replace(
        "# Deliberately NOT written into barrier_timeline", ""
    )


# ── observer ────────────────────────────────────────────────────────────────

def test_observer_turns_the_timeline_into_a_receipt(process_surface) -> None:
    receipt = observe_process_steps(
        {"observations": _timeline(("submit", 200), ("approve", 200), ("ship", 201))}
    )
    assert receipt["status"] == "OBSERVED"
    payload = receipt["evidence"][EVIDENCE_KEY]
    assert payload["observed_order"] == ["submit", "approve", "ship"]
    assert payload["step_count"] == 3
    assert payload["coverage_complete"] is True


def test_observer_reports_a_partially_executed_process_as_incomplete(process_surface) -> None:
    receipt = observe_process_steps(
        {"observations": _timeline(("submit", 200), ("approve", 0))}
    )
    payload = receipt["evidence"][EVIDENCE_KEY]
    assert payload["steps_not_reaching_transport"] == ["approve"]
    assert payload["coverage_complete"] is False


def test_absent_timeline_is_reported_as_single_step_not_as_failure(process_surface) -> None:
    receipt = observe_process_steps({"observations": {}})
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PROCESS_TIMELINE_ABSENT"


# ── the ordering verdict ────────────────────────────────────────────────────

def test_correct_order_passes(process_surface) -> None:
    receipt = evaluate_assertion(
        _assertion(["submit", "approve", "ship"]),
        observations=_payload(["submit", "approve", "ship"]),
    )
    assert receipt["status"] == "PASS"


def test_wrong_order_is_a_violation(process_surface) -> None:
    """The capability that did not exist: approve before submit is a defect."""
    receipt = evaluate_assertion(
        _assertion(["submit", "approve", "ship"]),
        observations=_payload(["approve", "submit", "ship"]),
    )
    assert receipt["status"] == "VIOLATION"
    assert receipt["actual"]["observed_order"] == ["approve", "submit", "ship"]


def test_undeclared_steps_do_not_affect_the_verdict(process_surface) -> None:
    """A plan may contain steps the declaration says nothing about."""
    receipt = evaluate_assertion(
        _assertion(["submit", "approve"]),
        observations=_payload(["setup", "submit", "teardown_probe", "approve"]),
    )
    assert receipt["status"] == "PASS"
    assert receipt["actual"]["observed_order"] == ["submit", "approve"]


def test_declared_step_that_never_ran_is_indeterminate(process_surface) -> None:
    """A step that did not run is not evidence that ordering was broken."""
    receipt = evaluate_assertion(
        _assertion(["submit", "approve", "ship"]),
        observations=_payload(["submit", "ship"]),
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "DECLARED_STEP_NOT_OBSERVED"
    assert receipt["actual"]["missing"] == ["approve"]


def test_step_that_never_reached_transport_is_indeterminate(process_surface) -> None:
    receipt = evaluate_assertion(
        _assertion(["submit", "approve"]),
        observations=_payload(["submit", "approve"], unreached=["approve"]),
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PROCESS_COVERAGE_INCOMPLETE"


def test_single_declared_step_declares_no_order(process_surface) -> None:
    """One step has no order; a vacuous "ordering verified" must stay out of the record."""
    receipt = evaluate_assertion(
        _assertion(["only_step"]), observations=_payload(["only_step"])
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "STEP_ORDER_NOT_DECLARED"


def test_order_is_never_derived_from_the_plan_itself(process_surface) -> None:
    """Comparing a plan against its own order can only ever pass — a tautology.

    With no declaration the kind must refuse, not infer the expectation from what happened.
    """
    receipt = evaluate_assertion(
        {"assertion_id": "a1", "kind": KIND_SEQUENCE_ORDER, "property": {}},
        observations=_payload(["b", "a"]),
    )
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "STEP_ORDER_NOT_DECLARED"


# ── installation ordering ───────────────────────────────────────────────────

def test_install_registers_observer_before_kind(process_surface) -> None:
    assert process_surface["observer"] == OBSERVER_ID
    assert OBSERVER_ID in OBSERVER_REGISTRY
    assert KIND_SEQUENCE_ORDER in SUPPORTED_KINDS


def test_install_is_idempotent(process_surface) -> None:
    again = install_process_step_surface()
    assert again["observer"] == OBSERVER_ID


def test_nothing_leaks_after_teardown() -> None:
    assert OBSERVER_ID not in OBSERVER_REGISTRY
    assert KIND_SEQUENCE_ORDER not in SUPPORTED_KINDS
