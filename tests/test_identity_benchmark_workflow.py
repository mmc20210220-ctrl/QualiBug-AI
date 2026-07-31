from __future__ import annotations

from copy import deepcopy

import pytest

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    identity_benchmark_workflow as workflow,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_benchmark import (
    ANNOTATION_SCOPE,
    GROUND_TRUTH_SCHEMA,
    QUALITY_POLICY_SCHEMA,
    evaluate_identity_resolution,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_benchmark_repository import (
    append_identity_benchmark_snapshot,
    identity_benchmark_paths,
    load_identity_benchmark_audit,
    load_identity_benchmark_history,
    load_identity_ground_truth,
    load_identity_quality_policy,
    payload_fingerprint,
    save_identity_ground_truth,
)


def _resolution() -> dict:
    return {
        "mentions": [
            {
                "mention_id": "m1",
                "mention_type": "BUSINESS_OBJECT",
                "raw_label": "销售订单",
                "source_id": "prd",
                "source_locator": "section:1",
                "role": "subject",
            },
            {
                "mention_id": "m2",
                "mention_type": "BUSINESS_OBJECT",
                "raw_label": "SO",
                "source_id": "api",
                "source_locator": "operation:create",
                "role": "object",
            },
        ],
        "clusters": [
            {
                "entity_id": "entity:order",
                "member_mention_ids": ["m1", "m2"],
            }
        ],
        "edges": [],
        "conflicts": [],
        "gate": {"status": "PASS", "entry_allowed": True, "metrics": {}},
    }


def _asset(tmp_path) -> dict:
    resolution = _resolution()
    ground_truth = load_identity_ground_truth("project-a", tmp_path)
    policy = load_identity_quality_policy("project-a", tmp_path)
    history = load_identity_benchmark_history("project-a", tmp_path)
    benchmark = (
        evaluate_identity_resolution(
            resolution, ground_truth, quality_policy=policy
        )
        if ground_truth
        else {"status": "NOT_MEASURED", "quality_gate": {"status": "NOT_CONFIGURED"}}
    )
    return {
        "enterprise_identity_annotation_manifest": {
            "schema": "qualibug.enterprise-identity-annotation-manifest.v1",
            "manifest_id": "manifest:current",
            "mention_count": 2,
            "mentions": [
                {"mention_ref": "m1", "raw_label": "销售订单"},
                {"mention_ref": "m2", "raw_label": "SO"},
            ],
        },
        "enterprise_identity_benchmark_repository_receipt": {
            "ground_truth_fingerprint": (
                payload_fingerprint(ground_truth) if ground_truth else ""
            ),
            "quality_policy_fingerprint": (
                payload_fingerprint(policy) if policy else ""
            ),
        },
        "enterprise_identity_benchmark_history": history,
        "enterprise_identity_resolution": resolution,
        "enterprise_identity_benchmark": benchmark,
        "enterprise_identity_gate": resolution["gate"],
        "enterprise_understanding_model": {},
    }


def _truth(manifest_id: str = "manifest:current") -> dict:
    return {
        "schema": GROUND_TRUTH_SCHEMA,
        "benchmark_id": "truth:test",
        "manifest_id": manifest_id,
        "annotation_scope": ANNOTATION_SCOPE,
        "ground_truth_generated_from_product_output": False,
        "clusters": [
            {
                "cluster_ref": "truth:order",
                "mention_refs": ["m1", "m2"],
                "annotation_status": "CONFIRMED",
            }
        ],
    }


def _actor() -> dict:
    return {"name": "qa", "role": "qa_lead", "tenant_id": "tenant-a"}


def test_stale_manifest_is_rejected_before_persistence(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(workflow, "_asset", lambda project, root, rebuild=False: _asset(tmp_path))

    with pytest.raises(ValueError, match="identity_ground_truth_manifest_stale"):
        workflow.import_identity_ground_truth(
            "project-a",
            _truth("manifest:old"),
            manifest_id="manifest:old",
            actor=_actor(),
            root=tmp_path,
        )

    assert not identity_benchmark_paths("project-a", tmp_path)["ground_truth"].exists()


def test_incomplete_closed_world_truth_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(workflow, "_asset", lambda project, root, rebuild=False: _asset(tmp_path))
    incomplete = _truth()
    incomplete["clusters"] = [
        {"cluster_ref": "truth:one", "mention_refs": ["m1"]}
    ]

    with pytest.raises(
        ValueError, match="IDENTITY_GROUND_TRUTH_MENTION_UNIVERSE_INCOMPLETE"
    ):
        workflow.import_identity_ground_truth(
            "project-a",
            incomplete,
            manifest_id="manifest:current",
            actor=_actor(),
            root=tmp_path,
        )

    assert not identity_benchmark_paths("project-a", tmp_path)["ground_truth"].exists()


def test_successful_import_rebuilds_and_records_snapshot(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(workflow, "_asset", lambda project, root, rebuild=False: _asset(tmp_path))

    result = workflow.import_identity_ground_truth(
        "project-a",
        _truth(),
        manifest_id="manifest:current",
        actor=_actor(),
        root=tmp_path,
    )

    assert result["benchmark"]["status"] == "MEASURED"
    assert result["benchmark"]["metrics"]["pairwise_precision"] == 1.0
    assert result["history"]["snapshot_count"] == 1
    assert result["history"]["latest_snapshot"]["trigger"] == "GROUND_TRUTH_IMPORT"
    assert load_identity_ground_truth("project-a", tmp_path)["manifest_id"] == "manifest:current"
    events = load_identity_benchmark_audit("project-a", tmp_path)["events"]
    assert events[-1]["event"] == "identity_ground_truth_imported"
    assert events[-1]["actor"]["name"] == "qa"
    assert events[-1]["snapshot_id"]


def test_rebuild_failure_restores_previous_ground_truth_and_history(tmp_path, monkeypatch) -> None:
    prior = _truth()
    prior["benchmark_id"] = "prior"
    save_identity_ground_truth("project-a", prior, tmp_path)
    append_identity_benchmark_snapshot(
        "project-a",
        {
            "schema": "qualibug.enterprise-identity-benchmark-snapshot.v1",
            "snapshot_id": "snapshot:prior",
            "recorded_at_utc": "2026-07-31T12:00:00Z",
            "measurement_status": "MEASURED",
            "manifest_id": "manifest:current",
            "ground_truth_fingerprint": payload_fingerprint(prior),
            "metrics": {},
            "errors": [],
        },
        tmp_path,
    )

    def failing_asset(project, root, rebuild=False):
        if rebuild:
            raise RuntimeError("rebuild failed")
        return _asset(tmp_path)

    monkeypatch.setattr(workflow, "_asset", failing_asset)
    changed = deepcopy(_truth())
    changed["benchmark_id"] = "changed"

    with pytest.raises(RuntimeError, match="rebuild failed"):
        workflow.import_identity_ground_truth(
            "project-a",
            changed,
            manifest_id="manifest:current",
            actor=_actor(),
            root=tmp_path,
        )

    assert load_identity_ground_truth("project-a", tmp_path)["benchmark_id"] == "prior"
    assert [
        row["snapshot_id"]
        for row in load_identity_benchmark_history("project-a", tmp_path)["snapshots"]
    ] == ["snapshot:prior"]
    events = load_identity_benchmark_audit("project-a", tmp_path)["events"]
    assert events[-1]["event"] == "identity_ground_truth_import_rolled_back"


def test_invalid_quality_policy_is_not_persisted(tmp_path) -> None:
    invalid = {
        "schema": QUALITY_POLICY_SCHEMA,
        "enforce": True,
        "thresholds": {"minimum_pairwise_precision": 1.5},
    }

    with pytest.raises(ValueError, match="identity_quality_policy_invalid"):
        workflow.update_identity_quality_policy(
            "project-a", invalid, actor=_actor(), root=tmp_path, rebuild=False
        )

    assert not identity_benchmark_paths("project-a", tmp_path)["quality_policy"].exists()


def test_enforced_regression_requires_thresholds(tmp_path) -> None:
    invalid = {
        "schema": QUALITY_POLICY_SCHEMA,
        "enforce": True,
        "enforce_regression": True,
        "thresholds": {"minimum_pairwise_precision": 0.9},
    }

    with pytest.raises(
        ValueError, match="identity_regression_thresholds_required_when_enforced"
    ):
        workflow.update_identity_quality_policy(
            "project-a", invalid, actor=_actor(), root=tmp_path, rebuild=False
        )


def test_valid_quality_and_regression_policy_rebuilds(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(workflow, "_asset", lambda project, root, rebuild=False: _asset(tmp_path))
    policy = {
        "schema": QUALITY_POLICY_SCHEMA,
        "enforce": True,
        "enforce_regression": True,
        "thresholds": {
            "minimum_pairwise_precision": 0.98,
            "minimum_pairwise_recall": 0.95,
            "maximum_overmerge_rate": 0.02,
        },
        "regression_thresholds": {
            "maximum_pairwise_precision_drop": 0.01,
            "maximum_pairwise_recall_drop": 0.01,
        },
    }

    result = workflow.update_identity_quality_policy(
        "project-a", policy, actor=_actor(), root=tmp_path
    )

    assert result["quality_policy"] == policy
    assert load_identity_quality_policy("project-a", tmp_path) == policy
    assert load_identity_benchmark_audit("project-a", tmp_path)["events"][-1][
        "event"
    ] == "identity_quality_policy_updated"


def test_manual_remeasurement_appends_versioned_snapshot(tmp_path, monkeypatch) -> None:
    save_identity_ground_truth("project-a", _truth(), tmp_path)
    monkeypatch.setattr(workflow, "_asset", lambda project, root, rebuild=False: _asset(tmp_path))

    first = workflow.run_identity_benchmark(
        "project-a", actor=_actor(), root=tmp_path
    )
    second = workflow.run_identity_benchmark(
        "project-a", actor=_actor(), root=tmp_path
    )

    assert first["history"]["snapshot_count"] == 1
    assert second["history"]["snapshot_count"] == 2
    assert second["history"]["latest_snapshot"]["trigger"] == "MANUAL_REMEASURE"
    assert load_identity_benchmark_audit("project-a", tmp_path)["events"][-1][
        "event"
    ] == "identity_benchmark_remeasured"


def test_manual_remeasurement_restores_history_when_snapshot_fails(tmp_path, monkeypatch) -> None:
    save_identity_ground_truth("project-a", _truth(), tmp_path)
    append_identity_benchmark_snapshot(
        "project-a",
        {
            "schema": "qualibug.enterprise-identity-benchmark-snapshot.v1",
            "snapshot_id": "snapshot:prior",
            "recorded_at_utc": "2026-07-31T12:00:00Z",
            "measurement_status": "MEASURED",
            "manifest_id": "manifest:current",
            "ground_truth_fingerprint": payload_fingerprint(_truth()),
            "metrics": {},
            "errors": [],
        },
        tmp_path,
    )
    monkeypatch.setattr(workflow, "_asset", lambda project, root, rebuild=False: _asset(tmp_path))
    monkeypatch.setattr(
        workflow,
        "_record_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("snapshot failed")),
    )

    with pytest.raises(RuntimeError, match="snapshot failed"):
        workflow.run_identity_benchmark(
            "project-a", actor=_actor(), root=tmp_path
        )

    assert [
        row["snapshot_id"]
        for row in load_identity_benchmark_history("project-a", tmp_path)["snapshots"]
    ] == ["snapshot:prior"]
