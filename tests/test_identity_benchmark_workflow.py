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
    identity_benchmark_paths,
    load_identity_benchmark_audit,
    load_identity_ground_truth,
    load_identity_quality_policy,
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


def test_successful_import_rebuilds_and_records_audit(tmp_path, monkeypatch) -> None:
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
    assert load_identity_ground_truth("project-a", tmp_path)["manifest_id"] == "manifest:current"
    events = load_identity_benchmark_audit("project-a", tmp_path)["events"]
    assert events[-1]["event"] == "identity_ground_truth_imported"
    assert events[-1]["actor"]["name"] == "qa"


def test_rebuild_failure_restores_previous_ground_truth(tmp_path, monkeypatch) -> None:
    prior = _truth()
    prior["benchmark_id"] = "prior"
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_benchmark_repository import (
        save_identity_ground_truth,
    )

    save_identity_ground_truth("project-a", prior, tmp_path)

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


def test_valid_quality_policy_rebuilds_through_same_workflow(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(workflow, "_asset", lambda project, root, rebuild=False: _asset(tmp_path))
    policy = {
        "schema": QUALITY_POLICY_SCHEMA,
        "enforce": True,
        "thresholds": {
            "minimum_pairwise_precision": 0.98,
            "minimum_pairwise_recall": 0.95,
            "maximum_overmerge_rate": 0.02,
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
