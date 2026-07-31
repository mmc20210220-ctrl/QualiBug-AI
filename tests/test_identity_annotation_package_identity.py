from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_annotation_tasks import (
    SUBMISSION_SCHEMA,
    build_identity_annotation_task_package,
    compile_identity_annotation_submissions,
)


def _asset() -> dict:
    mentions = [
        {
            "mention_id": f"mention:{index}",
            "mention_type": "BUSINESS_OBJECT",
            "raw_label": f"对象{index}",
            "source_id": "prd",
            "source_locator": f"section:{index}",
            "source_kind": "BUSINESS_FACT",
            "scope": {"system": "sales"},
            "role": "subject",
            "evidence": [],
        }
        for index in range(1, 7)
    ]
    return {
        "enterprise_identity_annotation_manifest": {
            "schema": "qualibug.enterprise-identity-annotation-manifest.v1",
            "manifest_id": "manifest:stable",
            "annotation_scope": "CLOSED_WORLD_IDENTITY_MENTIONS",
            "mentions": [
                {
                    "mention_ref": row["mention_id"],
                    "raw_label": row["raw_label"],
                    "source_id": row["source_id"],
                    "source_locator": row["source_locator"],
                    "source_kind": row["source_kind"],
                    "scope": row["scope"],
                    "role": row["role"],
                    "annotation_status": "UNLABELED",
                    "annotation_cluster_ref": "",
                }
                for row in mentions
            ],
        },
        "enterprise_identity_resolution": {"mentions": mentions},
    }


def test_batch_layout_does_not_change_task_package_identity() -> None:
    small_batches = build_identity_annotation_task_package(_asset(), batch_size=2)
    large_batches = build_identity_annotation_task_package(_asset(), batch_size=5)

    assert small_batches["task_package_id"] == large_batches["task_package_id"]
    assert small_batches["batch_layout_id"] != large_batches["batch_layout_id"]
    assert small_batches["batch_count"] == 3
    assert large_batches["batch_count"] == 2


def test_submission_exported_from_one_layout_validates_against_another() -> None:
    exported = build_identity_annotation_task_package(_asset(), batch_size=2)
    current = build_identity_annotation_task_package(_asset(), batch_size=5)
    submission = {
        "schema": SUBMISSION_SCHEMA,
        "task_package_id": exported["task_package_id"],
        "manifest_id": exported["manifest_id"],
        "annotation_scope": "CLOSED_WORLD_IDENTITY_MENTIONS",
        "generated_from_product_output": False,
        "annotator": {"name": "annotator-a", "role": "ANNOTATOR"},
        "annotations": [
            {
                "mention_ref": row["mention_ref"],
                "annotation_status": "CONFIRMED",
                "annotation_cluster_ref": f"singleton:{row['mention_ref']}",
            }
            for row in exported["tasks"]
        ],
    }

    result = compile_identity_annotation_submissions(current, submission)

    assert result["status"] == "READY"
    assert result["annotated_mention_count"] == 6
