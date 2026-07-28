from __future__ import annotations

from ai_test_asset_center.discovery_runtime_quality_projection import (
    project_discovery_quality,
)


def test_formal_event_surface_funnel_joins_upstream_and_runtime_receipts() -> None:
    result = {
        "behavior_ir": {
            "scan_event_contract_overlay_receipt": {
                "status": "OVERLAID",
                "scan_contract_count": 2,
                "contract_added_count": 1,
                "coverage_gap_count": 1,
            },
            "source_event_contract_binding_receipt": {
                "status": "BOUND",
                "contract_count": 1,
                "bound_invariant_count": 1,
                "coverage_gap_count": 0,
                "reason_counts": {},
            },
        },
        "test_obligations": {
            "source_event_obligation_receipt": {
                "status": "COMPILED",
                "obligation_count": 1,
                "misclassified_obligation_count_removed": 1,
                "complete_family_vector": True,
                "skipped_reason_counts": {},
            },
            "obligations": [{
                "obligation_id": "event-obl-1",
                "risk_family": "event_delivery_consistency",
            }],
        },
        "experiment_execution": {
            "selected_count": 1,
            "results": {
                "event-obl-1": {
                    "obligation_id": "event-obl-1",
                    "observer_receipts": [{
                        "observer_id": "source_event_delivery_reader",
                        "status": "OBSERVED",
                        "reason_code": "",
                        "evidence": {
                            "source_event_delivery_observation": {
                                "coverage_complete": True,
                            },
                        },
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
                "obligation_id": "event-obl-1",
                "risk_family": "event_delivery_consistency",
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
        "formal_count_projection": {"canonical_defect_ids": ["event-defect-1"]},
        "findings": [{
            "finding_id": "event-finding-1",
            "canonical_defect_id": "event-defect-1",
        }],
        "evidence_graphs": [],
        "execution_trace_summaries": [],
        "phases": {"execution": {"status": "completed"}},
    }

    projected = project_discovery_quality(result)
    event = projected["discovery_loss_funnel"]["surface_funnels"]["formal_event"]

    assert event["stages"] == [
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
    assert event["upstream_receipts"]["complete_family_vector"] is True
    assert event["external_quality_metrics"]["status"] == "NOT_MEASURED"
    assert event["capability_boundary"] == {
        "direct_broker_protocol_supported": False,
        "observation_adapter": "approved_target_relative_http_get",
        "count_semantics": "unique_stable_event_ids_within_full_window",
        "duplicate_physical_delivery_of_same_event_id_provable": False,
        "ordering_contract_supported": False,
        "raw_event_payloads_included": False,
    }


def test_formal_event_funnel_keeps_zero_counts_without_contracts() -> None:
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

    event = projected["discovery_loss_funnel"]["surface_funnels"]["formal_event"]
    assert all(stage["count"] == 0 for stage in event["stages"])
    assert event["external_quality_metrics"]["recall"] is None
