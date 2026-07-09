import json
from pathlib import Path

from ai_test_asset_center.private_pilot_coverage_matrix_patch import inject_coverage_matrix


def test_inject_coverage_matrix_lifts_benchmark_matrix_to_command_center(tmp_path: Path) -> None:
    project = "demo_project"
    benchmark_dir = tmp_path / "platform_outputs" / project / "benchmark"
    benchmark_dir.mkdir(parents=True)
    matrix = {
        "schema_version": "risk_invariant_coverage_v1",
        "ontology_family_count": 16,
        "ontology_invariant_count": 55,
        "covered_family_count": 3,
        "confirmed_family_count": 1,
        "family_coverage_rate": 0.1875,
        "confirmed_family_rate": 0.0625,
        "families": [
            {"family": "authorization_access_control", "display_name": "权限与访问控制", "coverage_status": "confirmed_with_evidence", "confirmed_count": 1, "candidate_count": 0, "target_invariant_count": 5, "touched_invariant_count": 2},
            {"family": "concurrency_race_condition", "display_name": "并发竞态", "coverage_status": "candidate_only", "confirmed_count": 0, "candidate_count": 1, "target_invariant_count": 3, "touched_invariant_count": 1},
            {"family": "tenant_isolation", "display_name": "租户隔离", "coverage_status": "gap", "target_invariant_count": 4},
        ],
        "invariants": [{"invariant": "actor_must_have_required_role", "family": "authorization_access_control", "coverage_status": "confirmed_with_evidence", "confirmed_count": 1}],
        "honesty_note": "not recall",
    }
    (benchmark_dir / "benchmark_metrics.json").write_text(
        json.dumps({"benchmark_active": False, "ground_truth_available": False, "coverage_matrix": matrix}),
        encoding="utf-8",
    )
    (tmp_path / "platform_outputs" / project / "scan_result.json").write_text(
        json.dumps({
            "coverage_steering": {
                "status": "applied",
                "reason": "gap_family_slices_prioritized",
                "steered_slice_count": 2,
                "gap_family_weights": {"tenant_isolation": 50},
                "top_steered_slice_ids": ["s_isolation"],
            },
            "regression_suite_refresh": {
                "status": "refreshed",
                "suite_ref": "platform_outputs/demo_project/regression_suite/regression_suite.json",
                "summary": {
                    "total_probe_count": 4,
                    "smoke_count": 2,
                    "release_count": 4,
                    "full_count": 4,
                    "confirmed_ledger_probe_count": 1,
                    "ci_gate_recommendation": "block_on_p0_p1_regression_failure",
                },
            },
        }),
        encoding="utf-8",
    )

    payload = {
        "data": {
            "project_id": project,
            "defects": [{"id": "d1"}],
            "clues": [{"id": "c1"}],
            "risks": [{"id": "r1"}],
            "scan_meta": {},
            "value_metrics": {},
            "executive_summary": {},
            "data_contract": {},
        }
    }

    injected = inject_coverage_matrix(payload, root=tmp_path)
    data = injected["data"]

    assert data["coverage_matrix"]["schema_version"] == "risk_invariant_coverage_v1"
    assert data["coverage_matrix_summary"]["covered_family_count"] == 3
    assert data["coverage_matrix_summary"]["gap_family_count"] == 1
    assert data["coverage_matrix"]["summary"]["risk_family_count"] == 16
    assert data["coverage_matrix"]["risk_family_coverage"]["authorization_access_control"]["coverage_status"] == "confirmed_with_evidence"
    assert data["coverage_matrix"]["risk_family_coverage"]["tenant_isolation"]["coverage_rate"] == 0.0
    assert data["coverage_matrix"]["invariant_coverage"]["actor_must_have_required_role"]["coverage_rate"] == 1.0
    assert data["coverage_matrix"]["behavior_slice_seed_contract"]["status"] == "ready"
    assert data["coverage_matrix"]["behavior_slice_seed_contract"]["can_seed_behavior_slices"] is True
    assert data["coverage_matrix"]["behavior_slice_seed_contract"]["seed_family_count"] == 3
    assert data["coverage_matrix"]["behavior_slice_seed_contract"]["seed_invariant_count"] == 1
    assert data["coverage_matrix"]["behavior_slice_seed_contract"]["priority_families"] == ["concurrency_race_condition", "tenant_isolation"]
    assert data["behavior_slice_seed_contract"]["source"] == "coverage_matrix"
    assert data["coverage_gaps"][0]["kind"] == "RISK_FAMILY_COVERAGE_GAP"
    assert data["coverage_gaps"][0]["family"] == "tenant_isolation"
    assert data["coverage_steering"]["status"] == "applied"
    assert data["scan_meta"]["coverage_steering"]["top_steered_slice_ids"] == ["s_isolation"]
    assert data["regression_suite_refresh"]["status"] == "refreshed"
    assert data["regression_suite"]["release_count"] == 4
    assert data["value_metrics"]["risk_invariant_coverage_rate"] == 0.1875
    assert data["value_metrics"]["risk_family_gap_count"] == 1
    assert data["value_metrics"]["coverage_steered_slice_count"] == 2
    assert data["value_metrics"]["regression_suite_probe_count"] == 4
    assert data["value_metrics"]["confirmed_ledger_regression_probe_count"] == 1
    assert data["value_metrics"]["behavior_slice_seed_status"] == "ready"
    assert data["value_metrics"]["behavior_slice_seed_family_count"] == 3
    assert data["value_metrics"]["behavior_slice_seed_invariant_count"] == 1
    assert data["executive_summary"]["risk_invariant_coverage_label"] == "风险家族覆盖 19%，确认覆盖 6%"
    assert data["executive_summary"]["coverage_steering_label"] == "已按覆盖缺口优先调度 2 个行为 slice"
    assert data["executive_summary"]["regression_suite_refresh_label"] == "已自动刷新 4 个回归探针"
    assert data["executive_summary"]["behavior_slice_seed_label"] == "可用覆盖矩阵种子：3 个风险家族 / 1 个业务不变量"
    assert data["evidence_classification"] == {"confirmed": 1, "candidate": 2, "clue": 1}
    assert "not recall" in data["data_contract"]["coverage_matrix"]["honesty_rule"].lower()
    assert "behavior_slice_seed_contract" in data["data_contract"]["coverage_matrix"]["frontend_compatibility_keys"]
    assert data["data_contract"]["behavior_slice_seed_contract"]["display_key"] == "behavior_slice_seed_contract"
    assert data["data_contract"]["coverage_steering"]["display_key"] == "coverage_steering"
    assert data["data_contract"]["regression_suite_refresh"]["display_key"] == "regression_suite_refresh"


def test_inject_coverage_steering_without_matrix(tmp_path: Path) -> None:
    project = "steering_only"
    output_dir = tmp_path / "platform_outputs" / project
    output_dir.mkdir(parents=True)
    (output_dir / "scan_result.json").write_text(
        json.dumps({
            "behavior_slice_ledger": {
                "coverage_steering": {
                    "status": "not_applied",
                    "reason": "coverage_matrix_without_actionable_gaps",
                    "steered_slice_count": 0,
                }
            },
            "regression_suite_refresh": {
                "status": "skipped",
                "reason": "no_confirmed_findings_or_confirmed_ledger",
                "summary": {"total_probe_count": 0, "confirmed_ledger_probe_count": 0},
            },
        }),
        encoding="utf-8",
    )

    payload = {"data": {"project_id": project, "scan_meta": {}, "value_metrics": {}, "executive_summary": {}, "data_contract": {}}}

    injected = inject_coverage_matrix(payload, root=tmp_path)
    data = injected["data"]

    assert data["coverage_steering"]["status"] == "not_applied"
    assert data["value_metrics"]["coverage_steering_status"] == "not_applied"
    assert data["regression_suite_refresh"]["status"] == "skipped"
    assert data["value_metrics"]["regression_suite_refresh_status"] == "skipped"
    assert "coverage_matrix" not in data
    assert data["data_contract"]["coverage_steering"]["display_key"] == "coverage_steering"
    assert data["data_contract"]["regression_suite_refresh"]["display_key"] == "regression_suite_refresh"
