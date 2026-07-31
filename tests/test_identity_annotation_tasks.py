from __future__ import annotations

import json

import pytest

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_annotation_tasks import (
    COMPILATION_SCHEMA,
    SUBMISSION_SCHEMA,
    TASK_PACKAGE_SCHEMA,
    build_identity_annotation_task_package,
    compile_identity_annotation_submissions,
)


def _asset() -> dict:
    mentions = [
        {
            "mention_id": "mention:a",
            "mention_type": "BUSINESS_OBJECT",
            "raw_label": "销售订单",
            "source_id": "prd",
            "source_locator": "section:1",
            "source_kind": "BUSINESS_FACT",
            "scope": {"system": "sales"},
            "role": "subject",
            "evidence": [{"source_id": "prd", "source_locator": "section:1", "quote": "销售订单提交后进入待审核状态。"}],
        },
        {
            "mention_id": "mention:b",
            "mention_type": "BUSINESS_OBJECT",
            "raw_label": "订单",
            "source_id": "openapi",
            "source_locator": "POST /orders",
            "source_kind": "BUSINESS_FACT",
            "scope": {"system": "sales"},
            "role": "object",
            "evidence": [{"source_id": "openapi", "source_locator": "POST /orders", "quote": "创建销售订单。"}],
        },
        {
            "mention_id": "mention:c",
            "mention_type": "BUSINESS_OBJECT",
            "raw_label": "采购订单",
            "source_id": "procurement",
            "source_locator": "section:8",
            "source_kind": "BUSINESS_OBJECT_ASSET",
            "scope": {"system": "procurement"},
            "role": "",
            "evidence": [{"source_id": "procurement", "source_locator": "section:8", "quote": "采购订单由采购部门创建。"}],
        },
    ]
    return {
        "enterprise_identity_annotation_manifest": {
            "schema": "qualibug.enterprise-identity-annotation-manifest.v1",
            "manifest_id": "manifest:1",
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


def _submission(package: dict, *, name: str, groups: dict[str, str], role: str = "ANNOTATOR") -> dict:
    return {
        "schema": SUBMISSION_SCHEMA,
        "task_package_id": package["task_package_id"],
        "manifest_id": package["manifest_id"],
        "annotation_scope": "CLOSED_WORLD_IDENTITY_MENTIONS",
        "generated_from_product_output": False,
        "annotator": {"name": name, "role": role},
        "annotations": [
            {"mention_ref": mention_ref, "annotation_status": "CONFIRMED", "annotation_cluster_ref": cluster_ref}
            for mention_ref, cluster_ref in groups.items()
        ],
    }


def _merged_groups() -> dict[str, str]:
    return {"mention:a": "A1", "mention:b": "A1", "mention:c": "A2"}


def _split_groups() -> dict[str, str]:
    return {"mention:a": "B1", "mention:b": "B2", "mention:c": "B3"}


def test_task_package_is_blind_and_contains_bounded_source_context() -> None:
    package = build_identity_annotation_task_package(_asset(), batch_size=2)
    assert package["schema"] == TASK_PACKAGE_SCHEMA
    assert package["task_count"] == 3
    assert package["batch_count"] == 2
    assert package["progress"] == {
        "total_task_count": 3,
        "completed_task_count": 0,
        "remaining_task_count": 3,
        "completion_rate": 0.0,
        "status": "NOT_STARTED",
    }
    assert package["contains_product_cluster_suggestions"] is False
    assert package["contains_predicted_entity_ids"] is False
    encoded = json.dumps(package, ensure_ascii=False)
    for forbidden in ('"entity_id"', '"cluster_id"', '"predicted_entity_id"', '"canonical_label"', '"comparison_keys"'):
        assert forbidden not in encoded
    assert package["tasks"][0]["context"][0]["quote"]


def test_single_submission_compiles_closed_world_ground_truth() -> None:
    package = build_identity_annotation_task_package(_asset())
    result = compile_identity_annotation_submissions(
        package,
        _submission(package, name="annotator-a", groups=_merged_groups()),
    )
    assert result["schema"] == COMPILATION_SCHEMA
    assert result["status"] == "READY"
    assert result["review_status"] == "SINGLE_ANNOTATOR"
    assert result["progress"]["status"] == "COMPLETE"
    assert result["annotated_mention_count"] == 3
    assert result["cluster_count"] == 2
    assert result["ground_truth"]["ground_truth_generated_from_product_output"] is False
    assert sorted(len(row["member_refs"]) for row in result["ground_truth"]["clusters"]) == [1, 2]


def test_double_blind_agreement_ignores_local_cluster_names() -> None:
    package = build_identity_annotation_task_package(_asset())
    result = compile_identity_annotation_submissions(
        package,
        _submission(package, name="annotator-a", groups=_merged_groups()),
        secondary_submission=_submission(
            package,
            name="annotator-b",
            groups={"mention:a": "B9", "mention:b": "B9", "mention:c": "B3"},
        ),
    )
    assert result["status"] == "READY"
    assert result["review_status"] == "DOUBLE_BLIND_AGREED"
    assert result["disagreement_count"] == 0


def test_double_blind_disagreement_requires_review_and_cannot_import() -> None:
    package = build_identity_annotation_task_package(_asset())
    result = compile_identity_annotation_submissions(
        package,
        _submission(package, name="annotator-a", groups=_merged_groups()),
        secondary_submission=_submission(package, name="annotator-b", groups=_split_groups()),
    )
    assert result["status"] == "REVIEW_REQUIRED"
    assert result["progress"]["status"] == "AWAITING_ADJUDICATION"
    assert result["ground_truth_import_allowed"] is False
    assert result["ground_truth"] == {}
    assert result["disagreement_count"] > 0


def test_adjudication_resolves_double_blind_disagreement() -> None:
    package = build_identity_annotation_task_package(_asset())
    result = compile_identity_annotation_submissions(
        package,
        _submission(package, name="annotator-a", groups=_merged_groups()),
        secondary_submission=_submission(package, name="annotator-b", groups=_split_groups()),
        adjudication_submission=_submission(package, name="reviewer", role="ADJUDICATOR", groups=_merged_groups()),
    )
    assert result["status"] == "READY"
    assert result["review_status"] == "ADJUDICATED"
    assert result["ground_truth_import_allowed"] is True
    assert result["disagreement_count"] > 0


def test_nested_product_prediction_fields_are_rejected() -> None:
    package = build_identity_annotation_task_package(_asset())
    primary = _submission(package, name="annotator-a", groups=_merged_groups())
    primary["annotations"][0]["review_context"] = {"predicted_entity_id": "entity:copied-from-product"}
    with pytest.raises(ValueError, match="product_prediction_fields_cannot_be_identity_annotation"):
        compile_identity_annotation_submissions(package, primary)


def test_adjudicator_must_be_independent_and_explicit() -> None:
    package = build_identity_annotation_task_package(_asset())
    primary = _submission(package, name="annotator-a", groups=_merged_groups())
    secondary = _submission(package, name="annotator-b", groups=_split_groups())
    wrong_role = _submission(package, name="reviewer", groups=_merged_groups())
    with pytest.raises(ValueError, match="identity_adjudicator_role_required"):
        compile_identity_annotation_submissions(package, primary, secondary_submission=secondary, adjudication_submission=wrong_role)
    same_person = _submission(package, name="ANNOTATOR-A", role="ADJUDICATOR", groups=_merged_groups())
    with pytest.raises(ValueError, match="identity_adjudicator_must_be_independent"):
        compile_identity_annotation_submissions(package, primary, secondary_submission=secondary, adjudication_submission=same_person)


def test_adjudicator_role_cannot_occupy_primary_or_secondary_position() -> None:
    package = build_identity_annotation_task_package(_asset())
    primary_adjudicator = _submission(package, name="reviewer", role="ADJUDICATOR", groups=_merged_groups())
    with pytest.raises(ValueError, match="identity_primary_annotator_role_invalid"):
        compile_identity_annotation_submissions(package, primary_adjudicator)
    primary = _submission(package, name="annotator-a", groups=_merged_groups())
    secondary_adjudicator = _submission(package, name="reviewer", role="ADJUDICATOR", groups=_split_groups())
    with pytest.raises(ValueError, match="identity_secondary_annotator_role_invalid"):
        compile_identity_annotation_submissions(package, primary, secondary_submission=secondary_adjudicator)


def test_unnecessary_or_orphan_adjudication_is_rejected() -> None:
    package = build_identity_annotation_task_package(_asset())
    primary = _submission(package, name="annotator-a", groups=_merged_groups())
    adjudication = _submission(package, name="reviewer", role="ADJUDICATOR", groups=_merged_groups())
    with pytest.raises(ValueError, match="identity_adjudication_requires_secondary_submission"):
        compile_identity_annotation_submissions(package, primary, adjudication_submission=adjudication)
    agreeing_secondary = _submission(
        package,
        name="annotator-b",
        groups={"mention:a": "B9", "mention:b": "B9", "mention:c": "B3"},
    )
    with pytest.raises(ValueError, match="identity_adjudication_not_required"):
        compile_identity_annotation_submissions(
            package,
            primary,
            secondary_submission=agreeing_secondary,
            adjudication_submission=adjudication,
        )


def test_incomplete_submission_is_rejected_before_ground_truth_compilation() -> None:
    package = build_identity_annotation_task_package(_asset())
    incomplete = _submission(package, name="annotator-a", groups={"mention:a": "A1", "mention:b": "A1"})
    with pytest.raises(ValueError, match="identity_annotation_submission_incomplete"):
        compile_identity_annotation_submissions(package, incomplete)
