from __future__ import annotations

from ai_test_asset_center.discovery_runtime_quality_projection import project_discovery_quality


def test_stability_funnel_joins_source_and_runtime_receipts() -> None:
    result = {
        "behavior_ir": {
            "scan_stability_contract_overlay_receipt": {
                "status": "OVERLAID",
                "scan_contract_count": 1,
                "contract_added_count": 1,
                "coverage_gap_count": 0,
            },
            "source_stability_contract_binding_receipt": {
                "status": "BOUND",
                "contract_count": 1,
                "bound_invariant_count": 1,
                "coverage_gap_count": 0,
            },
        },
        "test_obligations": {
            "source_stability_obligation_receipt": {
                "status": "COMPILED",
                "complete_family_vector": True,
            },
            "obligations": [{
                "obligation_id": "stability-obl-1",
                "risk_family": "stability_reliability",
            }],
        },
        "experiment_execution": {
            "results": {
                "stability-obl-1": {
                    "obligation_id": "stability-obl-1",
                    "observer_receipts": [{
                        "observer_id": "source_http_read_stability_reader",
                        "status": "OBSERVED",
                        "reason_code": "",
                        "evidence": {
                            "source_http_read_stability": {
                                "failed_sample_count": 1,
                                "retried_sample_count": 1,
                            },
                        },
                    }],
                    "oracle_verdict": {"status": "VIOLATION"},
                },
            },
        },
        "obligation_attempt_ledger": {
            "selected_count": 1,
            "complete": True,
            "attempts": [{
                "obligation_id": "stability-obl-1",
                "risk_family": "stability_reliability",
                "terminal_status": "DELIVERABLE",
                "reason_code": "",
                "stages": [
                    {"stage": "compile", "status": "COMPILED"},
                    {"stage": "execution", "status": "EXECUTED"},
                    {"stage": "gate", "status": "DELIVERABLE"},
                ],
            }],
        },
        "formal_count_projection": {"canonical_defect_ids": ["stable-defect"]},
        "findings": [{"finding_id": "stable-finding"}],
        "phases": {"execution": {"status": "completed"}},
    }

    projected = project_discovery_quality(result)
    stability = projected["discovery_loss_funnel"]["surface_funnels"][
        "formal_stability"
    ]

    assert [row["count"] for row in stability["stages"]] == [
        1, 1, 1, 1, 1, 1, 1, 1, 1, 1
    ]
    assert stability["losses"]["failed_sample_count"] == 1
    assert stability["losses"]["retried_sample_count"] == 1
    assert stability["capability_boundary"]["long_duration_soak_supported"] is False
    assert stability["external_quality_metrics"]["status"] == "NOT_MEASURED"


def test_stability_funnel_is_zero_without_contracts() -> None:
    projected = project_discovery_quality({
        "behavior_ir": {},
        "test_obligations": {"obligations": []},
        "experiment_execution": {"results": {}},
        "obligation_attempt_ledger": {
            "selected_count": 0,
            "complete": True,
            "attempts": [],
        },
        "formal_count_projection": {"canonical_defect_ids": []},
        "findings": [],
        "phases": {"execution": {"status": "plan_only"}},
    })
    stability = projected["discovery_loss_funnel"]["surface_funnels"][
        "formal_stability"
    ]
    assert all(row["count"] == 0 for row in stability["stages"])
