import json
from pathlib import Path

from ai_test_asset_center.private_pilot_coverage_steering_patch import (
    _attach_coverage_steering_result,
    _steer_slices,
)


def test_steer_slices_prioritizes_gap_risk_families_without_creating_slices(tmp_path: Path) -> None:
    project = "demo_project"
    benchmark_dir = tmp_path / "platform_outputs" / project / "benchmark"
    benchmark_dir.mkdir(parents=True)
    (benchmark_dir / "benchmark_metrics.json").write_text(
        json.dumps({
            "benchmark_active": False,
            "coverage_matrix": {
                "families": [
                    {"family": "tenant_isolation", "coverage_status": "gap"},
                    {"family": "authorization_access_control", "coverage_status": "confirmed_with_evidence"},
                    {"family": "concurrency_race_condition", "coverage_status": "candidate_only"},
                ]
            },
        }),
        encoding="utf-8",
    )

    slices = [
        {"slice_id": "s_permission", "kind": "permission", "entity": "orders", "priority": 0.7},
        {"slice_id": "s_money", "kind": "money", "entity": "orders", "priority": 0.9},
        {"slice_id": "s_isolation", "kind": "isolation", "entity": "orders", "priority": 0.6},
        {"slice_id": "s_concurrency", "kind": "concurrency", "entity": "orders", "priority": 0.5},
    ]

    steered, diagnostic = _steer_slices(slices, root=tmp_path, project=project)

    assert [item["slice_id"] for item in steered] == ["s_isolation", "s_concurrency", "s_money", "s_permission"]
    assert diagnostic["status"] == "applied"
    assert diagnostic["gap_family_weights"]["tenant_isolation"] == 50
    assert diagnostic["gap_family_weights"]["concurrency_race_condition"] == 25
    assert steered[0]["_coverage_steering_family"] == "tenant_isolation"
    assert steered[0]["_coverage_steering_reason"] == "prioritize_current_coverage_matrix_gap"
    assert len(steered) == len(slices)


def test_steer_slices_noops_without_actionable_coverage_matrix(tmp_path: Path) -> None:
    steered, diagnostic = _steer_slices(
        [{"slice_id": "s1", "kind": "permission"}],
        root=tmp_path,
        project="missing_project",
    )

    assert [item["slice_id"] for item in steered] == ["s1"]
    assert diagnostic["status"] == "not_applied"


def test_attach_coverage_steering_result_surfaces_diagnostics() -> None:
    result = {
        "phases": {"incremental_discovery": {"status": "planned"}},
        "behavior_slice_ledger": {"selected_slice_ids": ["s_isolation"]},
    }
    diagnostic = {
        "status": "applied",
        "reason": "gap_family_slices_prioritized",
        "gap_family_weights": {"tenant_isolation": 50},
        "top_steered_slice_ids": ["s_isolation"],
    }

    enriched = _attach_coverage_steering_result(result, diagnostic)

    assert enriched["coverage_steering"]["status"] == "applied"
    assert enriched["coverage_steering"]["gap_family_weights"]["tenant_isolation"] == 50
    assert enriched["coverage_steering"]["honesty_rule"].startswith("Coverage and learning steering only reorder")
    assert enriched["phases"]["incremental_discovery"]["coverage_steering"]["status"] == "applied"
    assert enriched["behavior_slice_ledger"]["coverage_steering"]["top_steered_slice_ids"] == ["s_isolation"]
