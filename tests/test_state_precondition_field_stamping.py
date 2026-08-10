# -*- coding: utf-8 -*-
"""Attack-B Fix 1: state-precondition steps carry the authoritative state field.

Regression coverage for run16's 64 runtime-materialized state obligations that
were blocked ``BLOCKED_STATE_PRECONDITION_FIELD_MISSING`` because precondition
steps carried no ``state_field`` and no readback contract. The planner must
stamp every establishment step with the state field resolved from the Behavior
IR (never a hardcoded literal), and the compile-time freezer must consume it.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center.state_precondition_compile_freezer import (
    freeze_state_precondition_fields,
)
from ai_test_asset_center.state_precondition_planner import (
    plan_state_precondition,
    STATUS_PLANNED,
)


def _entity(name: str, fields: list[dict]) -> dict:
    return {"id": f"bir_entity_{name}", "name": name, "kind": "resource", "fields": fields}


def _state(state_id: str, name: str, entity: str) -> dict:
    return {"id": state_id, "name": name, "value": name, "entity_ref": entity}


def _op(op_id: str, path: str, method: str = "POST") -> dict:
    return {"id": op_id, "operation_id": op_id, "method": method, "path": path, "read_write": "write"}


def _machine_ir(state_field: str = "status", field_name: str = "status") -> dict:
    """Minimal industry-neutral state machine: created -> active -> done."""
    s_created = _state("s_created", "CREATED", "order")
    s_active = _state("s_active", "ACTIVE", "order")
    s_done = _state("s_done", "DONE", "order")
    op_create = _op("op_create", "/api/orders")
    op_activate = _op("op_activate", "/api/orders/:id/activate")
    op_finish = _op("op_finish", "/api/orders/:id/finish")
    ir = {
        "entities": [_entity("order", [{"name": field_name, "semantic_type": "STATE"}])],
        "states": [s_created, s_active, s_done],
        "operations": [op_create, op_activate, op_finish],
        "relations": [
            {"id": "rel_1", "relation_type": "transitions", "from_ref": "s_created", "to_ref": "s_active", "operation_ref": "op_create"},
            {"id": "rel_2", "relation_type": "transitions", "from_ref": "s_active", "to_ref": "s_done", "operation_ref": "op_activate"},
            {"id": "rel_3", "relation_type": "transitions", "from_ref": "s_done", "to_ref": "s_active", "operation_ref": "op_finish"},
        ],
    }
    # keep the state_field name referenced so the fixture is honest
    assert state_field
    return ir


class TestPlannerStampsStateField:
    def test_steps_carry_state_field_and_readback_contract(self):
        ir = _machine_ir()
        plan = plan_state_precondition(
            behavior_ir=ir,
            from_state="DONE",
            actors=["actor_op"],
        )
        assert plan["status"] == STATUS_PLANNED
        steps = plan["steps"]
        assert len(steps) == 2
        for step in steps:
            assert step["state_field"] == "status"
            assert step["readback_contract"]["state_field"] == "status"
            assert step["readback_contract"]["required_fields"] == [{"field": "status"}]
            assert step["runtime_body_plan"]["readback_contract"]["state_field"] == "status"

    def test_state_field_resolved_from_ir_not_hardcoded(self):
        # A machine whose state field is NOT "status" must still resolve.
        ir = _machine_ir(field_name="lifecycle_state")
        plan = plan_state_precondition(
            behavior_ir=ir,
            from_state="DONE",
            actors=["actor_op"],
        )
        assert plan["status"] == STATUS_PLANNED
        detail = plan["detail"]
        assert detail["state_field"] == "lifecycle_state"
        for step in plan["steps"]:
            assert step["state_field"] == "lifecycle_state"

    def test_explicit_state_field_wins(self):
        ir = _machine_ir()
        plan = plan_state_precondition(
            behavior_ir=ir,
            from_state="DONE",
            actors=["actor_op"],
            state_field="stage",
        )
        assert plan["status"] == STATUS_PLANNED
        assert plan["detail"]["state_field"] == "stage"
        assert plan["steps"][0]["state_field"] == "stage"

    def test_entry_state_plan_is_empty_but_planned(self):
        ir = _machine_ir()
        plan = plan_state_precondition(
            behavior_ir=ir,
            from_state="CREATED",
            actors=["actor_op"],
        )
        assert plan["status"] == STATUS_PLANNED
        assert plan["steps"] == []


class TestFreezerConsumesStampedField:
    def test_freeze_passes_with_stamped_steps(self):
        ir = _machine_ir()
        plan = plan_state_precondition(
            behavior_ir=ir,
            from_state="DONE",
            actors=["actor_op"],
        )
        experiment = {
            "experiment_id": "exp_stamped",
            "compile_receipt": {"status": "COMPILED"},
            "precondition_plan": plan["steps"],
            # the runtime-materialized obligation carries NO assertion state_field
            "assertions": [
                {
                    "assertion_id": "assert_state",
                    "kind": "forbidden_state_transition",
                    "property": {"expression": {"kind": "forbidden_state_transition"}},
                }
            ],
        }
        frozen = freeze_state_precondition_fields(experiment)
        receipt = frozen["state_precondition_freeze_receipt"]
        assert receipt["status"] == "FROZEN"
        assert frozen["compile_receipt"]["state_precondition_freeze_status"] == "FROZEN"
        assert all(
            step["state_field"] == "status" for step in frozen["precondition_plan"]
        )

    def test_fail_closed_when_field_unresolvable(self):
        # The freezer must still block experiments whose plan has no field
        # anywhere (genuinely unresolved) instead of guessing a field name.
        experiment = {
            "experiment_id": "exp_unresolved",
            "compile_receipt": {"status": "COMPILED"},
            "precondition_plan": [
                {
                    "step_id": "precondition_1",
                    "phase": "fixture",
                    "operation_ref": "op_create",
                    "intent": "state_precondition_establishment",
                }
            ],
            "assertions": [
                {
                    "assertion_id": "assert_state",
                    "kind": "state_transition",
                    "property": {"expression": {"kind": "state_transition"}},
                }
            ],
        }
        frozen = freeze_state_precondition_fields(experiment)
        receipt = frozen["state_precondition_freeze_receipt"]
        assert receipt["status"] == "BLOCKED"
        assert receipt["reason_code"] == "BLOCKED_STATE_PRECONDITION_FIELD_MISSING"


class TestReachabilityAcceptsAnyStateField:
    def test_target_state_not_gated_on_literal_status(self):
        from ai_test_asset_center.precondition_reachability import (
            PreconditionGoal,
            ReachabilityAnalyzer,
        )

        analyzer = ReachabilityAnalyzer([], [])
        goal = PreconditionGoal(
            goal_id="g",
            internal_rule_id="r",
            required_conditions=[
                {
                    "condition_id": "state_done",
                    "field_id": "lifecycle_state",
                    "expected_expression": "DONE",
                }
            ],
        )
        assert analyzer._get_target_state(goal) == "DONE"

    def test_target_state_empty_when_no_condition(self):
        from ai_test_asset_center.precondition_reachability import (
            PreconditionGoal,
            ReachabilityAnalyzer,
        )

        analyzer = ReachabilityAnalyzer([], [])
        goal = PreconditionGoal(goal_id="g", internal_rule_id="r")
        assert analyzer._get_target_state(goal) == ""
