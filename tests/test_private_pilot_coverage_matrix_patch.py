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
            {"family": "authorization_access_control", "coverage_status": "confirmed_with_evidence"},
            {"family": "concurrency_race_condition", "coverage_status": "candidate_only"},
            {"family": "tenant_isolation", "coverage_status": "gap"},
        ],
        "invariants": [{"invariant": "actor_must_have_required_role", "coverage_status": "confirmed_with_evidence"}],
        "honesty_note": "not recall",
    }
    (benchmark_dir / "benchmark_metrics.json").write_text(
        json.dumps({"benchmark_active": False, "ground_truth_available": False, "coverage_matrix": matrix}),
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
    assert data["value_metrics"]["risk_invariant_coverage_rate"] == 0.1875
    assert data["executive_summary"]["risk_invariant_coverage_label"] == "风险家族覆盖 19%，确认覆盖 6%"
    assert data["evidence_classification"] == {"confirmed": 1, "candidate": 2, "clue": 1}
    assert "not recall" in data["data_contract"]["coverage_matrix"]["honesty_rule"].lower()
