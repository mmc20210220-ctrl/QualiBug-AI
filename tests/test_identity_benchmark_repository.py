from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_benchmark import (
    ANNOTATION_SCOPE,
    GROUND_TRUTH_SCHEMA,
    QUALITY_POLICY_SCHEMA,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_benchmark_regression import (
    SNAPSHOT_SCHEMA,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_benchmark_repository import (
    append_identity_benchmark_snapshot,
    apply_identity_benchmark_repository,
    identity_benchmark_paths,
    load_identity_benchmark_audit,
    load_identity_benchmark_history,
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


def _snapshot(snapshot_id: str, recorded_at: str) -> dict:
    return {
        "schema": SNAPSHOT_SCHEMA,
        "snapshot_id": snapshot_id,
        "recorded_at_utc": recorded_at,
        "measurement_status": "MEASURED",
        "manifest_id": "manifest:test",
        "ground_truth_fingerprint": "truth:fingerprint",
        "metrics": {"pairwise_precision": 1.0},
        "errors": [],
    }


def test_repository_round_trip_and_composition_injection(tmp_path) -> None:
    save_identity_ground_truth("project-a", _truth(), tmp_path)
    save_identity_quality_policy("project-a", _policy(), tmp_path)
    append_identity_benchmark_snapshot(
        "project-a", _snapshot("snapshot:1", "2026-07-31T12:00:00Z"), tmp_path
    )

    assert load_identity_ground_truth("project-a", tmp_path) == _truth()
    assert load_identity_quality_policy("project-a", tmp_path) == _policy()

    asset = apply_identity_benchmark_repository(
        {"project_id": "project-a"}, project_id="project-a", root=tmp_path
    )
    assert asset["enterprise_identity_ground_truth"] == _truth()
    assert asset["enterprise_identity_quality_policy"] == _policy()
    assert asset["enterprise_identity_benchmark_history"]["snapshots"][0][
        "snapshot_id"
    ] == "snapshot:1"
    receipt = asset["enterprise_identity_benchmark_repository_receipt"]
    assert receipt["ground_truth_loaded"] is True
    assert receipt["quality_policy_loaded"] is True
    assert receipt["history_snapshot_count"] == 1
    assert receipt["ground_truth_fingerprint"]


def test_every_explicit_measurement_event_is_retained(tmp_path) -> None:
    first = _snapshot("snapshot:1", "2026-07-31T12:00:00Z")
    second = _snapshot("snapshot:2", "2026-07-31T12:05:00Z")

    first_event = append_identity_benchmark_snapshot("project-a", first, tmp_path)
    repeated_event = append_identity_benchmark_snapshot("project-a", first, tmp_path)
    second_event = append_identity_benchmark_snapshot("project-a", second, tmp_path)

    history = load_identity_benchmark_history("project-a", tmp_path)
    assert len(history["snapshots"]) == 3
    assert len({row["snapshot_id"] for row in history["snapshots"]}) == 3
    assert first_event["requested_snapshot_id"] == "snapshot:1"
    assert repeated_event["requested_snapshot_id"] == "snapshot:1"
    assert repeated_event["same_result_event_ordinal"] == 2
    assert second_event["measurement_event_sequence"] == 3


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
    assert projected["enterprise_identity_benchmark_history"]["snapshots"] == []
    assert load_identity_benchmark_audit("project-a", tmp_path)["events"] == []
