"""V1.5.0 Process Step Execution and Observer unit tests.

Covers: ProcessStepLedger, evidence completeness gate, process completion oracle,
reverse cleanup ledger, TRUE_COMPLETED formula, and the existing
process_step_observer (sequence order assertion kind).
Industry-neutral: no hardcoded business domain.
"""
from __future__ import annotations

import pytest

from ai_test_asset_center.process_step_execution import (
    ProcessStepLedger,
    evaluate_per_step_evidence_completeness,
    evaluate_process_completion,
    build_reverse_cleanup_ledger,
    evaluate_true_completed,
    PROCESS_COMPLETED,
    PROCESS_PARTIALLY_EXECUTED,
    PROCESS_FAILED,
    PROCESS_EVIDENCE_INCOMPLETE,
    TRUE_COMPLETED,
    FIXTURE_BLOCKED,
    PRECONDITION_BLOCKED,
    PROCESS_PARTIAL,
    EVIDENCE_INCOMPLETE,
    CLEANUP_FAILED_STATE,
    ENVIRONMENT_DIRTY,
)
from ai_test_asset_center.process_step_observer import (
    observe_process_steps,
    evaluate_step_sequence_order,
    install_process_step_surface,
    OBSERVER_ID,
    KIND_SEQUENCE_ORDER,
)


# ─── §21: ProcessStepLedger ───────────────────────────────────────────────────


class TestProcessStepLedger:
    def test_record_and_retrieve(self):
        ledger = ProcessStepLedger(experiment_id="exp_1", fixture_id="fix_A")
        row = ledger.record_step_execution(
            step_id="treatment_1",
            phase="treatment",
            operation_ref="op_create",
            actor_ref="actor_admin",
            status_code=201,
        )
        assert row["step_id"] == "treatment_1"
        assert row["step_ordinal"] == 1
        assert ledger.get_step_row("treatment_1") is row

    def test_ordinal_increments(self):
        ledger = ProcessStepLedger(experiment_id="exp_1")
        ledger.record_step_execution(step_id="s1", phase="treatment", operation_ref="op1", actor_ref="a1")
        ledger.record_step_execution(step_id="s2", phase="treatment", operation_ref="op2", actor_ref="a1")
        assert ledger.get_step_row("s1")["step_ordinal"] == 1
        assert ledger.get_step_row("s2")["step_ordinal"] == 2

    def test_executed_step_ids(self):
        ledger = ProcessStepLedger(experiment_id="exp_1")
        ledger.record_step_execution(step_id="s1", phase="treatment", operation_ref="op1", actor_ref="a1", status_code=200)
        ledger.record_step_execution(step_id="s2", phase="treatment", operation_ref="op2", actor_ref="a1", status_code=201)
        assert ledger.executed_step_ids() == ["s1", "s2"]

    def test_successful_write_step_ids(self):
        ledger = ProcessStepLedger(experiment_id="exp_1")
        ledger.record_step_execution(step_id="s1", phase="treatment", operation_ref="op1", actor_ref="a1", status_code=201)
        ledger.record_step_execution(step_id="s2", phase="treatment", operation_ref="op2", actor_ref="a1", status_code=500, final_status="FAILED")
        assert ledger.successful_write_step_ids() == ["s1"]

    def test_failed_step_ids(self):
        ledger = ProcessStepLedger(experiment_id="exp_1")
        ledger.record_step_execution(step_id="s1", phase="treatment", operation_ref="op1", actor_ref="a1", status_code=200)
        ledger.record_step_execution(step_id="s2", phase="treatment", operation_ref="op2", actor_ref="a1", status_code=422, final_status="FAILED")
        assert ledger.failed_step_ids() == ["s2"]

    def test_timeline_events(self):
        ledger = ProcessStepLedger(experiment_id="exp_1")
        ledger.record_step_execution(step_id="s1", phase="treatment", operation_ref="op1", actor_ref="a1")
        ledger.record_timeline_event(step_id="s1", phase="treatment", event_type="TRANSPORT_COMPLETED")
        timeline = ledger.timeline()
        assert len(timeline) == 1
        assert timeline[0]["event_type"] == "TRANSPORT_COMPLETED"

    def test_build_timeline_receipt(self):
        ledger = ProcessStepLedger(experiment_id="exp_1")
        ledger.record_timeline_event(step_id="s1", phase="treatment", event_type="STEP_READY")
        receipt = ledger.build_timeline_receipt()
        assert receipt["experiment_id"] == "exp_1"
        assert receipt["event_count"] == 1


# ─── §22: Per-Step Evidence Completeness ──────────────────────────────────────


class TestEvidenceCompleteness:
    def test_complete(self):
        ledger = ProcessStepLedger(experiment_id="exp_1")
        ledger.record_step_execution(step_id="s1", phase="treatment", operation_ref="op1", actor_ref="a1", status_code=200, after_state_receipt_id="obs_1")
        ledger.record_step_execution(step_id="s2", phase="treatment", operation_ref="op2", actor_ref="a1", status_code=200, after_state_receipt_id="obs_2")
        result = evaluate_per_step_evidence_completeness(
            planned_step_ids=["s1", "s2"],
            ledger=ledger,
        )
        assert result["complete"] is True
        assert result["reason_code"] == ""

    def test_missing_execution(self):
        ledger = ProcessStepLedger(experiment_id="exp_1")
        ledger.record_step_execution(step_id="s1", phase="treatment", operation_ref="op1", actor_ref="a1")
        result = evaluate_per_step_evidence_completeness(
            planned_step_ids=["s1", "s2", "s3"],
            ledger=ledger,
        )
        assert result["complete"] is False
        assert "s2" in result["missing_execution"]
        assert "s3" in result["missing_execution"]

    def test_missing_observation(self):
        ledger = ProcessStepLedger(experiment_id="exp_1")
        ledger.record_step_execution(step_id="s1", phase="treatment", operation_ref="op1", actor_ref="a1", status_code=200, after_state_receipt_id="obs_1")
        ledger.record_step_execution(step_id="s2", phase="treatment", operation_ref="op2", actor_ref="a1", status_code=200)
        result = evaluate_per_step_evidence_completeness(
            planned_step_ids=["s1", "s2"],
            ledger=ledger,
            observed_step_ids=["s1"],  # s2 not observed
        )
        assert result["complete"] is False
        assert "s2" in result["missing_observation"]


# ─── §26: Process Completion Oracle ───────────────────────────────────────────


class TestProcessCompletion:
    def test_all_completed(self):
        ledger = ProcessStepLedger(experiment_id="exp_1")
        ledger.record_step_execution(step_id="s1", phase="treatment", operation_ref="op1", actor_ref="a1", status_code=200, target_reached=True, after_state_receipt_id="obs_1")
        ledger.record_step_execution(step_id="s2", phase="treatment", operation_ref="op2", actor_ref="a1", status_code=201, target_reached=True, after_state_receipt_id="obs_2")
        result = evaluate_process_completion(
            expected_step_ids=["s1", "s2"],
            ledger=ledger,
            evidence_complete=True,
        )
        assert result["result"] == PROCESS_COMPLETED

    def test_partially_executed(self):
        ledger = ProcessStepLedger(experiment_id="exp_1")
        ledger.record_step_execution(step_id="s1", phase="treatment", operation_ref="op1", actor_ref="a1", status_code=200)
        result = evaluate_process_completion(
            expected_step_ids=["s1", "s2", "s3"],
            ledger=ledger,
            evidence_complete=True,
        )
        assert result["result"] == PROCESS_PARTIALLY_EXECUTED
        assert "s2" in result["skipped_step_ids"]

    def test_failed_step(self):
        ledger = ProcessStepLedger(experiment_id="exp_1")
        ledger.record_step_execution(step_id="s1", phase="treatment", operation_ref="op1", actor_ref="a1", status_code=500, final_status="FAILED")
        result = evaluate_process_completion(
            expected_step_ids=["s1"],
            ledger=ledger,
            evidence_complete=True,
        )
        assert result["result"] == PROCESS_FAILED

    def test_evidence_incomplete(self):
        ledger = ProcessStepLedger(experiment_id="exp_1")
        ledger.record_step_execution(step_id="s1", phase="treatment", operation_ref="op1", actor_ref="a1", status_code=200)
        result = evaluate_process_completion(
            expected_step_ids=["s1"],
            ledger=ledger,
            evidence_complete=False,
        )
        assert result["result"] == PROCESS_EVIDENCE_INCOMPLETE


# ─── §29: Reverse Cleanup Ledger ──────────────────────────────────────────────


class TestReverseCleanupLedger:
    def test_all_covered(self):
        result = build_reverse_cleanup_ledger(
            experiment_id="exp_1",
            successful_write_step_ids=["s1", "s2"],
            cleanup_results=[
                {"source_step_id": "s1", "cleanup_contract_id": "c1", "verified": True},
                {"source_step_id": "s2", "cleanup_contract_id": "c2", "verified": True},
            ],
        )
        assert result["all_writes_covered"] is True
        assert result["final_status"] == "CLEANED"

    def test_uncovered_step(self):
        result = build_reverse_cleanup_ledger(
            experiment_id="exp_1",
            successful_write_step_ids=["s1", "s2"],
            cleanup_results=[
                {"source_step_id": "s1", "cleanup_contract_id": "c1", "verified": True},
            ],
        )
        assert result["all_writes_covered"] is False
        assert "s2" in result["uncovered_steps"]
        assert result["final_status"] == "CLEANUP_INCOMPLETE"


# ─── §31: TRUE_COMPLETED Formula ──────────────────────────────────────────────


class TestTrueCompleted:
    def test_all_true(self):
        result = evaluate_true_completed(
            fixture_materialized=True,
            state_precondition_established=True,
            all_required_steps_executed=True,
            per_step_evidence_complete=True,
            minimal_oracle_evaluated=True,
            cleanup_executed=True,
            cleanup_verified=True,
            environment_restored=True,
        )
        assert result["true_completed"] is True
        assert result["terminal_state"] == TRUE_COMPLETED

    def test_fixture_blocked(self):
        result = evaluate_true_completed(
            fixture_materialized=False,
            state_precondition_established=True,
            all_required_steps_executed=True,
            per_step_evidence_complete=True,
            minimal_oracle_evaluated=True,
            cleanup_executed=True,
            cleanup_verified=True,
            environment_restored=True,
        )
        assert result["true_completed"] is False
        assert result["terminal_state"] == FIXTURE_BLOCKED

    def test_precondition_blocked(self):
        result = evaluate_true_completed(
            fixture_materialized=True,
            state_precondition_established=False,
            all_required_steps_executed=True,
            per_step_evidence_complete=True,
            minimal_oracle_evaluated=True,
            cleanup_executed=True,
            cleanup_verified=True,
            environment_restored=True,
        )
        assert result["terminal_state"] == PRECONDITION_BLOCKED

    def test_process_partial(self):
        result = evaluate_true_completed(
            fixture_materialized=True,
            state_precondition_established=True,
            all_required_steps_executed=False,
            per_step_evidence_complete=True,
            minimal_oracle_evaluated=True,
            cleanup_executed=True,
            cleanup_verified=True,
            environment_restored=True,
        )
        assert result["terminal_state"] == PROCESS_PARTIAL

    def test_evidence_incomplete(self):
        result = evaluate_true_completed(
            fixture_materialized=True,
            state_precondition_established=True,
            all_required_steps_executed=True,
            per_step_evidence_complete=False,
            minimal_oracle_evaluated=True,
            cleanup_executed=True,
            cleanup_verified=True,
            environment_restored=True,
        )
        assert result["terminal_state"] == EVIDENCE_INCOMPLETE

    def test_cleanup_failed(self):
        result = evaluate_true_completed(
            fixture_materialized=True,
            state_precondition_established=True,
            all_required_steps_executed=True,
            per_step_evidence_complete=True,
            minimal_oracle_evaluated=True,
            cleanup_executed=False,
            cleanup_verified=False,
            environment_restored=True,
        )
        assert result["terminal_state"] == CLEANUP_FAILED_STATE

    def test_environment_dirty(self):
        result = evaluate_true_completed(
            fixture_materialized=True,
            state_precondition_established=True,
            all_required_steps_executed=True,
            per_step_evidence_complete=True,
            minimal_oracle_evaluated=True,
            cleanup_executed=True,
            cleanup_verified=True,
            environment_restored=False,
        )
        assert result["terminal_state"] == ENVIRONMENT_DIRTY


# ─── Existing process_step_observer: Sequence Order ───────────────────────────


class TestProcessStepObserver:
    def test_observe_empty_timeline(self):
        envelope = {"observations": {"process_timeline": []}}
        receipt = observe_process_steps(envelope)
        assert receipt["status"] == "INDETERMINATE"
        assert receipt["reason_code"] == "PROCESS_TIMELINE_ABSENT"

    def test_observe_with_steps(self):
        envelope = {"observations": {"process_timeline": [
            {"step_id": "s1", "phase": "treatment", "step_ordinal": 1, "status_code": 200, "operation_ref": "op1", "actor_ref": "a1", "event_type": "TRANSPORT_COMPLETED"},
            {"step_id": "s2", "phase": "treatment", "step_ordinal": 2, "status_code": 201, "operation_ref": "op2", "actor_ref": "a1", "event_type": "TRANSPORT_COMPLETED"},
        ]}}
        receipt = observe_process_steps(envelope)
        assert receipt["status"] == "OBSERVED"
        evidence = receipt["evidence"]["process_step_timeline"]
        assert evidence["step_count"] == 2
        assert evidence["observed_order"] == ["s1", "s2"]
        assert evidence["coverage_complete"] is True

    def test_sequence_order_pass(self):
        envelope = {
            "spec": {"expected_step_order": ["s1", "s2", "s3"]},
            "observations": {"process_step_timeline": {
                "observed_order": ["s1", "s2", "s3"],
                "steps_not_reaching_transport": [],
            }},
        }
        result = evaluate_step_sequence_order(envelope)
        assert result["passed"] is True

    def test_sequence_order_violation(self):
        envelope = {
            "spec": {"expected_step_order": ["s1", "s2", "s3"]},
            "observations": {"process_step_timeline": {
                "observed_order": ["s2", "s1", "s3"],
                "steps_not_reaching_transport": [],
            }},
        }
        result = evaluate_step_sequence_order(envelope)
        assert result["passed"] is False

    def test_sequence_order_not_declared(self):
        envelope = {
            "spec": {"expected_step_order": []},
            "observations": {"process_step_timeline": {
                "observed_order": ["s1", "s2"],
                "steps_not_reaching_transport": [],
            }},
        }
        result = evaluate_step_sequence_order(envelope)
        assert result["passed"] is None
        assert result["reason_code"] == "STEP_ORDER_NOT_DECLARED"

    def test_sequence_order_missing_step(self):
        envelope = {
            "spec": {"expected_step_order": ["s1", "s2", "s3"]},
            "observations": {"process_step_timeline": {
                "observed_order": ["s1", "s3"],
                "steps_not_reaching_transport": [],
            }},
        }
        result = evaluate_step_sequence_order(envelope)
        assert result["passed"] is None
        assert result["reason_code"] == "DECLARED_STEP_NOT_OBSERVED"

    def test_install_surface_idempotent(self):
        result1 = install_process_step_surface()
        result2 = install_process_step_surface()
        assert result1["observer"] == OBSERVER_ID
        assert result2["observer"] == OBSERVER_ID
        assert result1[KIND_SEQUENCE_ORDER] == KIND_SEQUENCE_ORDER
