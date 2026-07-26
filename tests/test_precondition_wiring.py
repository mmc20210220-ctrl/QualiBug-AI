"""Wiring the precondition planner into the compiler, behind the registry marker.

This is the highest-risk edit in the protocol-registry work, because it changes the condition
on the state-family rewrite from ``if family == "state"`` to
``if family == "state" and not _registry_protocol_id``. If that marker ever leaked onto a
built-in protocol result, every state experiment would lose its assertion rewrite and compile
as a bare probe.

The marker is set in exactly one place — experiment_protocols, after a registry hit — and
three properties are pinned here:

* a built-in state compile still carries no marker, no precondition_plan key, and its
  existing behaviour
* the ``precondition_plan`` key is emitted CONDITIONALLY, so ~3100 stored experiment dicts
  keep their exact shape
* an unreachable or unresolved source state BLOCKS rather than compiling an experiment whose
  precondition was never established
"""

from __future__ import annotations

from typing import Any, Iterator

import pytest

from ai_test_asset_center import experiment_protocol_registry as reg
from ai_test_asset_center.experiment_compiler_obligation import (
    BLOCK_REASONS,
    compile_experiment_for_obligation,
    make_experiment,
)


FAMILY = "test_only_precondition_family"
TEMPLATE = "test_only_precondition_template"


def _behavior_ir() -> dict[str, Any]:
    return {
        "schema_version": "qualibug.behavior-ir.v2",
        "operations": [
            {"id": "op-pay", "service": "s", "method": "POST", "path": "/api/orders/pay",
             "path_template": "/api/orders/pay", "read_write": "write"},
            {"id": "op-ship", "service": "s", "method": "POST", "path": "/api/orders/ship",
             "path_template": "/api/orders/ship", "read_write": "write"},
            {"id": "op-read", "service": "s", "method": "GET", "path": "/api/orders",
             "path_template": "/api/orders", "read_write": "read"},
        ],
        "actors": [{"id": "a1", "role": "admin", "credential_secret_ref": "r1",
                    "status": "active"}],
        "entities": [{"id": "e1", "name": "order"}],
        "states": [
            {"id": "st_C", "value": "CREATED", "entity_ref": "e1"},
            {"id": "st_P", "value": "PAID", "entity_ref": "e1"},
            {"id": "st_S", "value": "SHIPPED", "entity_ref": "e1"},
        ],
        "relations": [
            {"relation_type": "transitions", "from_ref": "st_C", "to_ref": "st_P",
             "operation_ref": "op-pay"},
            {"relation_type": "transitions", "from_ref": "st_P", "to_ref": "st_S",
             "operation_ref": "op-ship"},
        ],
        "invariants": [],
    }


def _obligation(family: str, template: str, from_state: str = "SHIPPED") -> dict[str, Any]:
    return {
        "schema_version": "qualibug.test-obligation.v1",
        "obligation_id": "obl-precondition",
        "risk_family": family,
        "subject_refs": ["op-read"],
        "property": {
            "operation_ref": "op-read", "actor_ref": "a1",
            "template": template, "from_state": from_state,
        },
        "required_actors": ["a1"],
        "required_operations": ["op-read"],
        "required_fixtures": [],
        "required_observers": ["http_response"],
        "cleanup_requirement": {"required": False},
    }


@pytest.fixture()
def registered() -> Iterator[Any]:
    def register(*, requires_precondition: bool = True, **flags: Any) -> None:
        def compiler(_envelope: dict[str, Any]) -> dict[str, Any]:
            return {
                "status": "COMPILED",
                "control_plan": [],
                "treatment_plan": [{
                    "step_id": "treatment_1", "actor_ref": "a1", "operation_ref": "op-read",
                }],
                "assertion": {"kind": "http_status"},
                "requires_state_precondition": requires_precondition,
            }
        reg._REGISTERED_FAMILY_PROTOCOLS[(FAMILY, TEMPLATE)] = {
            "protocol_id": f"{FAMILY}:{TEMPLATE}", "compiler": compiler,
            "observers": [], "assertion_kind": "",
            "emits_control": flags.get("emits_control", False),
            "per_step_evidence": flags.get("per_step_evidence", False),
        }
    yield register
    reg._REGISTERED_FAMILY_PROTOCOLS.pop((FAMILY, TEMPLATE), None)


def _compile(from_state: str = "SHIPPED") -> dict[str, Any]:
    return compile_experiment_for_obligation(
        _obligation(FAMILY, TEMPLATE, from_state),
        behavior_ir=_behavior_ir(), environment_type="test",
    )


# ── the guard ───────────────────────────────────────────────────────────────

def test_builtin_state_compile_is_untouched() -> None:
    """The highest-risk property. A leaked marker would strip every state assertion."""
    experiment = compile_experiment_for_obligation(
        _obligation("state", "state_transition"),
        behavior_ir=_behavior_ir(), environment_type="test",
    )
    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert "_registry_protocol_id" not in experiment
    assert "precondition_plan" not in experiment


def test_precondition_plan_key_is_emitted_conditionally() -> None:
    """An unconditional empty list would change the shape of every stored experiment."""
    assert "precondition_plan" not in make_experiment(obligation_id="o1")
    assert "precondition_plan" not in make_experiment(obligation_id="o1", precondition_plan=[])
    with_plan = make_experiment(
        obligation_id="o1", precondition_plan=[{"step_id": "precondition_1"}]
    )
    assert with_plan["precondition_plan"] == [{"step_id": "precondition_1"}]


# ── establishment ───────────────────────────────────────────────────────────

def test_reachable_source_state_is_established(registered) -> None:
    registered(requires_precondition=True)
    experiment = _compile("SHIPPED")

    assert experiment["compile_receipt"]["status"] == "COMPILED"
    steps = experiment["precondition_plan"]
    assert [step["operation_ref"] for step in steps] == ["op-pay", "op-ship"]
    assert {step["phase"] for step in steps} == {"fixture"}


def test_establishment_steps_stay_out_of_the_measured_window(registered) -> None:
    """_main_governed_write_steps keeps only control/treatment.

    If establishment ran as a control step, before_state would snapshot the state BEFORE the
    precondition existed and a correctly-run experiment would report
    STATE_PRECONDITION_NOT_MET.
    """
    registered(requires_precondition=True)
    experiment = _compile("SHIPPED")

    assert all(step["phase"] == "fixture" for step in experiment["precondition_plan"])
    control_ids = {step.get("step_id") for step in experiment.get("control_plan") or []}
    treatment_ids = {step.get("step_id") for step in experiment.get("treatment_plan") or []}
    precondition_ids = {step["step_id"] for step in experiment["precondition_plan"]}
    assert not (precondition_ids & (control_ids | treatment_ids))


@pytest.mark.parametrize("from_state", ["REFUNDED", "unknown_state", ""])
def test_unestablishable_source_state_blocks(registered, from_state: str) -> None:
    """Compiling an experiment whose precondition was never established would report a
    verdict about a state the target was never in."""
    registered(requires_precondition=True)
    receipt = _compile(from_state)["compile_receipt"]

    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == "BLOCKED_PRECONDITION_UNREACHABLE"
    assert f"{FAMILY}:{TEMPLATE}" in receipt["detail"]


def test_protocol_not_requesting_a_precondition_gets_none(registered) -> None:
    registered(requires_precondition=False)
    experiment = _compile("SHIPPED")

    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert "precondition_plan" not in experiment


def test_block_reasons_are_registered() -> None:
    for code in ("BLOCKED_PRECONDITION_UNREACHABLE", "BLOCKED_STEP_CLEANUP_UNCOVERED"):
        assert code in BLOCK_REASONS
