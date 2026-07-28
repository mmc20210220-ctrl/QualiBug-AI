from __future__ import annotations

from ai_test_asset_center.discovery_runtime_quality_projection import (
    project_discovery_quality,
)


def test_formal_ui_surface_funnel_joins_upstream_and_runtime_receipts() -> None:
    result = {
        "behavior_ir": {
            "scan_ui_contract_overlay_receipt": {
                "status": "OVERLAID",
                "formal_candidate_count": 2,
                "contract_added_count": 1,
                "coverage_gap_count": 1,
            },
            "source_ui_contract_binding_receipt": {
                "status": "BOUND",
                "contract_count": 1,
                "bound_invariant_count": 1,
                "coverage_gap_count": 0,
                "reason_counts": {},
            },
        },
        "test_obligations": {
            "source_ui_obligation_receipt": {
                "status": "COMPILED",
                "obligation_count": 1,
                "misclassified_obligation_count_removed": 1,
                "complete_family_vector": True,
                "skipped_reason_counts": {},
            },
            "obligations": [{
                "obligation_id": "ui-obl-1",
                "risk_family": "ui_state_consistency",
            }],
        },
        "experiment_execution": {
            "selected_count": 1,
            "results": {
                "ui-obl-1": {
                    "obligation_id": "ui-obl-1",
                    "observer_receipts": [{
                        "observer_id": "ui_source_expectation_reader",
                        "status": "OBSERVED",
                        "reason_code": "",
                    }],
                    "oracle_verdict": {
                        "status": "VIOLATION",
                        "verdict": "confirmed_violation",
                    },
                },
            },
        },
        "obligation_attempt_ledger": {
            "selected_count": 1,
            "complete": True,
            "attempts": [{
                "obligation_id": "ui-obl-1",
                "risk_family": "ui_state_consistency",
                "terminal_stage": "gate",
                "terminal_status": "DELIVERABLE",
                "reason_code": "",
                "stages": [
                    {"stage": "compile", "status": "COMPILED"},
                    {"stage": "execution", "status": "EXECUTED"},
                    {"stage": "gate", "status": "DELIVERABLE"},
                ],
            }],
        },
        "formal_count_projection": {
            "canonical_defect_ids": ["defect-ui-1"],
        },
        "findings": [{
            "finding_id": "finding-ui-1",
            "canonical_defect_id": "defect-ui-1",
        }],
        "evidence_graphs": [],
        "execution_trace_summaries": [],
        "phases": {"execution": {"status": "completed"}},
    }

    projected = project_discovery_quality(result)
    ui = projected["discovery_loss_funnel"]["surface_funnels"]["formal_ui"]

    assert ui["stages"] == [
        {"stage": "source_contract", "count": 2},
        {"stage": "ir_bound", "count": 1},
        {"stage": "obligation_generated", "count": 1},
        {"stage": "selected", "count": 1},
        {"stage": "compiled", "count": 1},
        {"stage": "executed", "count": 1},
        {"stage": "observed", "count": 1},
        {"stage": "oracle_evaluated", "count": 1},
        {"stage": "oracle_violation", "count": 1},
        {"stage": "deliverable", "count": 1},
    ]
    assert ui["upstream_receipts"]["complete_family_vector"] is True
    assert ui["outcomes"] == {
        "property_held_count": 0,
        "violation_count": 1,
        "deliverable_count": 1,
    }
    assert ui["external_quality_metrics"]["status"] == "NOT_MEASURED"
    assert ui["provider_findings_consumed"] is False


def test_formal_ui_funnel_keeps_zero_counts_when_no_ui_contract_exists() -> None:
    projected = project_discovery_quality({
        "behavior_ir": {},
        "test_obligations": {"obligations": []},
        "experiment_execution": {"selected_count": 0, "results": {}},
        "obligation_attempt_ledger": {
            "selected_count": 0,
            "complete": True,
            "attempts": [],
        },
        "formal_count_projection": {"canonical_defect_ids": []},
        "findings": [],
        "phases": {"execution": {"status": "plan_only"}},
    })

    ui = projected["discovery_loss_funnel"]["surface_funnels"]["formal_ui"]
    assert all(stage["count"] == 0 for stage in ui["stages"])
    assert ui["external_quality_metrics"]["recall"] is None
