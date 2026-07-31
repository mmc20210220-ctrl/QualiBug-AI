from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_benchmark import (
    ANNOTATION_SCOPE,
    GROUND_TRUTH_SCHEMA,
    QUALITY_POLICY_SCHEMA,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_benchmark_repository import (
    apply_identity_benchmark_repository,
    identity_benchmark_paths,
    load_identity_benchmark_audit,
    load_identity_ground_truth,
    load_identity_quality_policy,
    restore_identity_benchmark_file,
    save_identity_ground_truth,
    save_identity_quality_policy,
    snapshot_identity_benchmark_file,
)


def _truth() -> dict:
    return {
        "schema": GROUND_TRUTH_SCHEMA,
        "annotation_scope": ANNOTATION_SCOPE,
        "ground_truth_generated_from_product_output": False,
        "manifest_id": "manifest:test",
        "clusters": [{"cluster_ref": "truth:1", "mention_refs": ["m1"]}],
    }


def _policy() -> dict:
    return {
        "schema": QUALITY_POLICY_SCHEMA,
        "enforce": True,
        "thresholds": {"minimum_pairwise_precision": 0.98},
    }


def test_repository_round_trip_and_composition_injection(tmp_path) -> None:
    save_identity_ground_truth("project-a", _truth(), tmp_path)
    save_identity_quality_policy("project-a", _policy(), tmp_path)

    assert load_identity_ground_truth("project-a", tmp_path) == _truth()
    assert load_identity_quality_policy("project-a", tmp_path) == _policy()

    asset = apply_identity_benchmark_repository(
        {"project_id": "project-a"}, project_id="project-a", root=tmp_path
    )
    assert asset["enterprise_identity_ground_truth"] == _truth()
    assert asset["enterprise_identity_quality_policy"] == _policy()
    receipt = asset["enterprise_identity_benchmark_repository_receipt"]
    assert receipt["ground_truth_loaded"] is True
    assert receipt["quality_policy_loaded"] is True
    assert receipt["ground_truth_fingerprint"]


def test_repository_snapshot_restore_is_transactional(tmp_path) -> None:
    path = identity_benchmark_paths("project-a", tmp_path)["ground_truth"]
    assert snapshot_identity_benchmark_file(path) is None

    save_identity_ground_truth("project-a", _truth(), tmp_path)
    snapshot = snapshot_identity_benchmark_file(path)
    assert snapshot

    changed = {**_truth(), "manifest_id": "manifest:changed"}
    save_identity_ground_truth("project-a", changed, tmp_path)
    restore_identity_benchmark_file(path, snapshot)
    assert load_identity_ground_truth("project-a", tmp_path) == _truth()

    restore_identity_benchmark_file(path, None)
    assert not path.exists()


def test_empty_repository_removes_stale_asset_inputs(tmp_path) -> None:
    asset = {
        "enterprise_identity_ground_truth": {"stale": True},
        "enterprise_identity_quality_policy": {"stale": True},
    }
    projected = apply_identity_benchmark_repository(
        asset, project_id="project-a", root=tmp_path
    )
    assert "enterprise_identity_ground_truth" not in projected
    assert "enterprise_identity_quality_policy" not in projected
    assert load_identity_benchmark_audit("project-a", tmp_path)["events"] == []
