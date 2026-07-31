from __future__ import annotations

import pytest

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import identity_annotation_operator as operator
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_annotation_tasks import (
    SUBMISSION_SCHEMA,
    build_identity_annotation_task_package,
)


def _package() -> dict:
    mentions = [
        {
            "mention_id": "mention:a",
            "mention_type": "BUSINESS_OBJECT",
            "raw_label": "订单",
            "source_id": "prd",
            "source_locator": "section:1",
            "source_kind": "BUSINESS_FACT",
            "scope": {},
            "role": "subject",
            "evidence": [],
        },
        {
            "mention_id": "mention:b",
            "mention_type": "BUSINESS_OBJECT",
            "raw_label": "销售订单",
            "source_id": "api",
            "source_locator": "POST /orders",
            "source_kind": "BUSINESS_FACT",
            "scope": {},
            "role": "object",
            "evidence": [],
        },
    ]
    asset = {
        "enterprise_identity_annotation_manifest": {
            "schema": "qualibug.enterprise-identity-annotation-manifest.v1",
            "manifest_id": "manifest:operator",
            "mentions": [
                {
                    "mention_ref": row["mention_id"],
                    "raw_label": row["raw_label"],
                    "source_id": row["source_id"],
                    "source_locator": row["source_locator"],
                    "source_kind": row["source_kind"],
                    "scope": {},
                    "role": row["role"],
                }
                for row in mentions
            ],
        },
        "enterprise_identity_resolution": {"mentions": mentions},
    }
    return build_identity_annotation_task_package(asset)


def _submission(package: dict, name: str, first: str, second: str) -> dict:
    return {
        "schema": SUBMISSION_SCHEMA,
        "task_package_id": package["task_package_id"],
        "manifest_id": package["manifest_id"],
        "annotation_scope": "CLOSED_WORLD_IDENTITY_MENTIONS",
        "generated_from_product_output": False,
        "annotator": {"name": name, "role": "ANNOTATOR"},
        "annotations": [
            {
                "mention_ref": "mention:a",
                "annotation_status": "CONFIRMED",
                "annotation_cluster_ref": first,
            },
            {
                "mention_ref": "mention:b",
                "annotation_status": "CONFIRMED",
                "annotation_cluster_ref": second,
            },
        ],
    }


def test_review_required_never_calls_ground_truth_import(monkeypatch: pytest.MonkeyPatch) -> None:
    package = _package()
    monkeypatch.setattr(operator, "get_identity_annotation_task_package", lambda *_args, **_kwargs: package)
    imported: list[dict] = []
    monkeypatch.setattr(operator, "import_identity_ground_truth", lambda *_args, **_kwargs: imported.append({}) or {})

    result = operator.compile_and_import_identity_annotations(
        "demo",
        {
            "primary_submission": _submission(package, "a", "A1", "A1"),
            "secondary_submission": _submission(package, "b", "B1", "B2"),
        },
        actor={"name": "owner"},
    )

    assert result["status"] == "REVIEW_REQUIRED"
    assert result["ground_truth_imported"] is False
    assert imported == []


def test_ready_compilation_delegates_persistence_to_existing_workflow(monkeypatch: pytest.MonkeyPatch) -> None:
    package = _package()
    monkeypatch.setattr(operator, "get_identity_annotation_task_package", lambda *_args, **_kwargs: package)
    captured: dict = {}

    def fake_import(project: str, ground_truth: dict, **kwargs: object) -> dict:
        captured.update(
            {
                "project": project,
                "ground_truth": ground_truth,
                "manifest_id": kwargs.get("manifest_id"),
            }
        )
        return {"benchmark": {"status": "MEASURED"}}

    monkeypatch.setattr(operator, "import_identity_ground_truth", fake_import)
    result = operator.compile_and_import_identity_annotations(
        "demo",
        {"primary_submission": _submission(package, "a", "A1", "A1")},
        actor={"name": "owner"},
    )

    assert result["status"] == "IMPORTED"
    assert result["ground_truth_imported"] is True
    assert captured["project"] == "demo"
    assert captured["manifest_id"] == package["manifest_id"]
    assert captured["ground_truth"]["schema"] == "qualibug.enterprise-identity-ground-truth.v1"


def test_adjudication_without_secondary_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    package = _package()
    monkeypatch.setattr(operator, "get_identity_annotation_task_package", lambda *_args, **_kwargs: package)

    with pytest.raises(ValueError, match="identity_adjudication_requires_secondary_submission"):
        operator.compile_and_import_identity_annotations(
            "demo",
            {
                "primary_submission": _submission(package, "a", "A1", "A1"),
                "adjudication_submission": _submission(package, "reviewer", "R1", "R1"),
            },
            actor={"name": "owner"},
        )
