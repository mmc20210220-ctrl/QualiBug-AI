from __future__ import annotations

from ai_test_asset_center.discovery_loss_funnel import (
    build_discovery_loss_funnel,
)
from ai_test_asset_center.discovery_runtime_quality_projection import (
    project_discovery_quality,
)


def _attempt(
    obligation_id: str,
    *,
    terminal_stage: str,
    terminal_status: str,
    reason_code: str = "",
    risk_family: str = "state",
    compiled: bool = False,
    executed: bool = False,
) -> dict:
    stages = []
    if compiled:
        stages.append({"stage": "compile", "status": "COMPILED"})
    elif terminal_stage == "compile":
        stages.append({
            "stage": "compile",
            "status": terminal_status,
            "reason_code": reason_code,
        })
    if executed:
        stages.append({"stage": "execution", "status": "EXECUTED"})
    if terminal_stage == "gate":
        stages.append({
            "stage": "gate",
            "status": terminal_status,
            "reason_code": reason_code,
        })
    return {
        "obligation_id": obligation_id,
        "risk_family": risk_family,
        "terminal_stage": terminal_stage,
        "terminal_status": terminal_status,
        "reason_code": reason_code,
        "stages": stages,
    }


def _result() -> dict:
    return {
        "agent_semantic_link_receipt": {
            "status": "COMPLETED",
            "proposal_count": 8,
            "accepted_relationship_count": 3,
        },
        "behavior_ir": {
            "semantic_operation_binding_receipt": {
                "status": "BOUND",
                "accepted_binding_count": 3,
                "bound_invariant_count": 2,
            },
            "effect_observer_binding_receipt": {
                "status": "BOUND_WITH_GAPS",
                "added_relation_count": 1,
            },
        },
        "test_obligations": {
            "obligations": [
                {"obligation_id": f"obl_{index}"}
                for index in range(1, 6)
            ],
        },
        "obligation_attempt_ledger": {
            "run_id": "run_1",
            "campaign_id": "campaign_1",
            "selected_count": 3,
            "terminal_count": 3,
            "complete": True,
            "attempts": [
                _attempt(
                    "obl_1",
                    terminal_stage="gate",
                    terminal_status="DELIVERABLE",
                    compiled=True,
                    executed=True,
                ),
                _attempt(
                    "obl_2",
                    terminal_stage="compile",
                    terminal_status="BLOCKED",
                    reason_code="BLOCKED_MISSING_BINDING",
                    risk_family="conservation",
                ),
                _attempt(
                    "obl_3",
                    terminal_stage="gate",
                    terminal_status="REJECTED",
                    reason_code="ORACLE_NO_VIOLATION",
                    risk_family="temporal",
                    compiled=True,
                    executed=True,
                ),
            ],
        },
        "experiment_execution": {
            "selected_count": 3,
            "executed_count": 2,
            "results": {
                "obl_1": {
                    "obligation_id": "obl_1",
                    "observer_receipts": [{
                        "observer_id": "entity_state",
                        "status": "OBSERVED",
                        "reason_code": "",
                    }],
                    "oracle_verdict": {
                        "status": "VIOLATION",
                        "verdict": "confirmed_violation",
                    },
                },
                "obl_3": {
                    "obligation_id": "obl_3",
                    "observer_receipts": [{
                        "observer_id": "process_timeline",
                        "status": "INDETERMINATE",
                        "reason_code": "PROCESS_TIMELINE_REQUIRED_STEPS_INCOMPLETE",
                    }],
                    "oracle_verdict": {},
                },
            },
        },
        "formal_count_projection": {
            "canonical_defect_ids": ["defect_1"],
        },
        "evidence_graphs": [{"graph_id": "graph_1"}],
        "execution_trace_summaries": [{"experiment_id": "exp_1"}],
        "phases": {"execution": {"status": "partial"}},
        "findings": [{
            "finding_id": "finding_1",
            "canonical_defect_id": "defect_1",
        }],
    }


def test_loss_funnel_uses_only_formal_receipt_transitions() -> None:
    funnel = build_discovery_loss_funnel(_result())

    assert funnel["measurement_status"] == "MEASURED"
    stage_counts = {
        row["stage"]: row["count"] for row in funnel["stages"]
    }
    assert stage_counts == {
        "generated": 5,
        "selected": 3,
        "compiled": 2,
        "executed": 2,
        "observed": 1,
        "oracle_evaluated": 1,
        "deliverable": 1,
        "canonical_defect": 1,
    }
    assert funnel["losses"]["terminal_reason_counts"] == {
        "BLOCKED_MISSING_BINDING": 1,
        "ORACLE_NO_VIOLATION": 1,
    }
    assert funnel["losses"]["observer_reason_counts"] == {
        "PROCESS_TIMELINE_REQUIRED_STEPS_INCOMPLETE": 1,
    }
    assert funnel["upstream_readiness"]["bound_invariant_count"] == 2
    assert funnel["upstream_readiness"][
        "effect_observer_relation_count"
    ] == 1


def test_ground_truth_metrics_stay_explicitly_unmeasured() -> None:
    funnel = build_discovery_loss_funnel(_result())

    quality = funnel["external_quality_metrics"]
    assert quality == {
        "status": "NOT_MEASURED",
        "recall": None,
        "precision": None,
        "f1": None,
        "true_positive": None,
        "false_positive": None,
        "false_negative": None,
        "required_evidence": "external_hidden_ground_truth_evaluator",
    }
    assert funnel["interpretation_contract"][
        "no_ground_truth_quality_claims"
    ] is True


def test_quality_projection_attaches_funnel_without_changing_findings() -> None:
    source = _result()
    projected = project_discovery_quality(source)

    assert projected["findings"] == source["findings"]
    assert projected["discovery_loss_funnel"]["measurement_status"] == (
        "MEASURED"
    )


def test_public_runtime_exports_quality_projecting_execution_entry() -> None:
    from ai_test_asset_center import discovery_runtime
    from ai_test_asset_center.discovery_runtime_quality_projection import (
        run_experiment_candidate,
    )

    # discovery_runtime adds scan-stage progress marking around the quality
    # projection entry, so it is a distinct wrapper, not a raw re-export.
    assert discovery_runtime.run_experiment_candidate is not run_experiment_candidate
