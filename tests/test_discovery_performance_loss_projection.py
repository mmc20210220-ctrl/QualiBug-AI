from __future__ import annotations

from ai_test_asset_center.discovery_runtime_quality_projection import (
    project_discovery_quality,
)


def test_performance_funnel_joins_source_and_runtime_receipts() -> None:
    result = {
        "behavior_ir": {
            "scan_performance_contract_overlay_receipt": {
                "status": "OVERLAID",
                "scan_contract_count": 2,
                "contract_added_count": 1,
                "coverage_gap_count": 1,
            },
            "source_performance_contract_binding_receipt": {
                "status": "BOUND",
                "contract_count": 1,
                "bound_invariant_count": 1,
                "coverage_gap_count": 0,
                "reason_counts": {},
            },
        },
        "test_obligations": {
            "source_performance_obligation_receipt": {
                "status": "COMPILED",
                "obligation_count": 1,
                "complete_family_vector": True,
                "skipped_reason_counts": {},
            },
            "obligations": [{
                "obligation_id": "perf-obl-1",
                "risk_family": "performance_latency",
            }],
        },
        "experiment_execution": {
            "selected_count": 1,
            "results": {
                "perf-obl-1": {
                    "obligation_id": "perf-obl-1",
                    "observer_receipts": [{
                        "observer_id": "source_http_latency_series_reader",
                        "status": "OBSERVED",
                        "reason_code": "",
                        "evidence": {
                            "source_http_latency_series": {
                                "coverage_complete": True,
                                "retried_sample_count": 0,
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
                "obligation_id": "perf-obl-1",
                "risk_family": "performance_latency",
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
        "formal_count_projection": {"canonical_defect_ids": ["perf-defect-1"]},
        "findings": [{
            "finding_id": "perf-finding-1",
            "canonical_defect_id": "perf-defect-1",
        }],
        "evidence_graphs": [],
        "execution_trace_summaries": [],
        "phases": {"execution": {"status": "completed"}},
    }

    projected = project_discovery_quality(result)
    perf = projected["discovery_loss_funnel"]["surface_funnels"][
        "formal_performance"
    ]

    assert perf["stages"] == [
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
    assert perf["upstream_receipts"]["complete_family_vector"] is True
    assert perf["external_quality_metrics"]["status"] == "NOT_MEASURED"
    assert perf["capability_boundary"]["transport_retries_accepted"] is False
    assert perf["capability_boundary"][
        "functional_non_2xx_judged_as_performance"
    ] is False


def test_performance_funnel_keeps_zero_counts_without_contracts() -> None:
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

    perf = projected["discovery_loss_funnel"]["surface_funnels"][
        "formal_performance"
    ]
    assert all(stage["count"] == 0 for stage in perf["stages"])
    assert perf["external_quality_metrics"]["recall"] is None
