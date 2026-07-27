"""V1.5.0 Integration tests: compiler fixture binding, executor ledger wiring,
finalizer TRUE_COMPLETED, and protocol lazy registration.

Industry-neutral: uses synthetic Behavior IR with generic operations.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center.experiment_protocols import (
    compile_family_protocol,
    _ensure_v150_protocols,
)
from ai_test_asset_center.multi_step_protocol import TEMPLATE_MULTI_STEP_PROCESS
from ai_test_asset_center.experiment_protocol_registry import resolve_family_protocol
from ai_test_asset_center.process_step_execution import (
    ProcessStepLedger,
    evaluate_true_completed,
    evaluate_per_step_evidence_completeness,
    evaluate_process_completion,
)
from ai_test_asset_center.state_precondition_planner import (
    plan_state_precondition,
    build_transition_graph,
)


# ─── Protocol Lazy Registration Integration ───────────────────────────────────


class TestProtocolLazyRegistration:
    def test_ensure_registers_protocols(self):
        _ensure_v150_protocols()
        reg = resolve_family_protocol("process", TEMPLATE_MULTI_STEP_PROCESS)
        assert reg is not None

    def test_compile_family_protocol_triggers_registration(self):
        """compile_family_protocol calls _ensure_v150_protocols internally."""
        result = compile_family_protocol(
            risk_family="process",
            operation={"id": "op_x", "method": "POST", "path": "/api/x"},
            operation_ref="op_x",
            control_actor_ref="",
            treatment_actor_ref="actor_1",
            property_spec={"template": TEMPLATE_MULTI_STEP_PROCESS, "process_steps": [
                {"step_id": "s1", "operation_ref": "op_x"},
                {"step_id": "s2", "operation_ref": "op_y"},
            ]},
            behavior_ir={"operations": [], "relations": [], "entities": []},
        )
        # Should resolve via registered protocol (not fall through to built-in)
        assert result.get("_registry_protocol_id") == f"process:{TEMPLATE_MULTI_STEP_PROCESS}"


# ─── State Precondition Planner Integration ───────────────────────────────────


class TestStatePreconditionPlanner:
    def _ir_with_transitions(self):
        return {
            "operations": [
                {"id": "op_create", "method": "POST", "path": "/api/records"},
                {"id": "op_submit", "method": "POST", "path": "/api/records/{id}/submit"},
                {"id": "op_approve", "method": "POST", "path": "/api/records/{id}/approve"},
            ],
            "states": [
                {"id": "st_draft", "value": "draft"},
                {"id": "st_created", "value": "created"},
                {"id": "st_submitted", "value": "submitted"},
                {"id": "st_approved", "value": "approved"},
            ],
            "relations": [
                {"relation_type": "transitions", "operation_ref": "op_create", "from_ref": "st_draft", "to_ref": "st_created"},
                {"relation_type": "transitions", "operation_ref": "op_submit", "from_ref": "st_created", "to_ref": "st_submitted"},
                {"relation_type": "transitions", "operation_ref": "op_approve", "from_ref": "st_submitted", "to_ref": "st_approved"},
            ],
            "entities": [],
        }

    def test_build_transition_graph(self):
        ir = self._ir_with_transitions()
        graph = build_transition_graph(ir)
        assert "draft" in graph
        assert graph["draft"][0]["to"] == "created"

    def test_plan_precondition_path_exists(self):
        ir = self._ir_with_transitions()
        plan = plan_state_precondition(
            behavior_ir=ir,
            from_state="submitted",
            actors=["actor_admin"],
            start_state="draft",
        )
        assert plan["status"] == "PLANNED"
        assert len(plan["steps"]) >= 1

    def test_plan_precondition_unreachable(self):
        ir = self._ir_with_transitions()
        plan = plan_state_precondition(
            behavior_ir=ir,
            from_state="nonexistent_state",
            actors=["actor_admin"],
        )
        assert plan["status"] == "BLOCKED"

    def test_plan_precondition_already_at_target(self):
        ir = self._ir_with_transitions()
        plan = plan_state_precondition(
            behavior_ir=ir,
            from_state="draft",
            actors=["actor_admin"],
        )
        # draft is an entry state (nothing transitions into it)
        assert plan["status"] == "PLANNED"
        assert len(plan["steps"]) == 0

    def test_plan_blocked_without_actor(self):
        ir = self._ir_with_transitions()
        plan = plan_state_precondition(
            behavior_ir=ir,
            from_state="submitted",
            actors=[],
            start_state="draft",
        )
        assert plan["status"] == "BLOCKED"
        assert "actor" in plan["reason_code"]


# ─── Executor → Finalizer TRUE_COMPLETED Integration ──────────────────────────


class TestExecutorFinalizerIntegration:
    def test_ledger_to_true_completed_flow(self):
        """Simulate executor producing ledger, finalizer evaluating TRUE_COMPLETED."""
        # Executor phase: record steps
        ledger = ProcessStepLedger(experiment_id="exp_int", fixture_id="fix_int")
        ledger.record_step_execution(
            step_id="treatment_1", phase="treatment",
            operation_ref="op_create", actor_ref="actor_1", status_code=201,
        )
        ledger.record_step_execution(
            step_id="treatment_2", phase="treatment",
            operation_ref="op_submit", actor_ref="actor_1", status_code=200,
        )

        # Evidence completeness gate
        evidence = evaluate_per_step_evidence_completeness(
            planned_step_ids=["treatment_1", "treatment_2"],
            ledger=ledger,
        )
        assert evidence["complete"] is True

        # Process completion
        completion = evaluate_process_completion(
            expected_step_ids=["treatment_1", "treatment_2"],
            ledger=ledger,
            evidence_complete=evidence["complete"],
        )
        assert completion["result"] == "PROCESS_COMPLETED"

        # Finalizer: TRUE_COMPLETED
        tc = evaluate_true_completed(
            fixture_materialized=True,
            state_precondition_established=True,
            all_required_steps_executed=True,
            per_step_evidence_complete=evidence["complete"],
            minimal_oracle_evaluated=True,
            cleanup_executed=True,
            cleanup_verified=True,
            environment_restored=True,
        )
        assert tc["true_completed"] is True
        assert tc["terminal_state"] == "TRUE_COMPLETED"

    def test_partial_execution_not_true_completed(self):
        """Partial execution must NOT produce TRUE_COMPLETED."""
        ledger = ProcessStepLedger(experiment_id="exp_int2", fixture_id="fix_int2")
        ledger.record_step_execution(
            step_id="treatment_1", phase="treatment",
            operation_ref="op_create", actor_ref="actor_1", status_code=201,
        )
        # treatment_2 planned but never executed

        evidence = evaluate_per_step_evidence_completeness(
            planned_step_ids=["treatment_1", "treatment_2"],
            ledger=ledger,
        )
        assert evidence["complete"] is False

        tc = evaluate_true_completed(
            fixture_materialized=True,
            state_precondition_established=True,
            all_required_steps_executed=False,
            per_step_evidence_complete=False,
            minimal_oracle_evaluated=True,
            cleanup_executed=True,
            cleanup_verified=True,
            environment_restored=True,
        )
        assert tc["true_completed"] is False
        assert tc["terminal_state"] == "PROCESS_PARTIAL"
