from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    build_enterprise_understanding_model,
)
from tests.test_business_object_recognition_types import _asset


def _narrative_prd_tree(text_value: str, *, heading: str = "crm") -> dict:
    return {
        "items": [{
            "source_id": "prd-source",
            "nodes": [
                {
                    "node_id": "narrative:heading",
                    "semantic_heading": True,
                    "raw_heading": heading,
                    "title": heading,
                    "path_titles": [heading],
                    "span_kind": "HEADING",
                    "evidence": {
                        "source_id": "prd-source",
                        "source_locator": "PRD.md#line=1",
                        "quote": heading,
                    },
                },
                {
                    "node_id": "narrative:paragraph",
                    "semantic_heading": False,
                    "title": text_value,
                    "path_titles": [heading],
                    "span_kind": "PARAGRAPH",
                    "evidence": {
                        "source_id": "prd-source",
                        "source_locator": "PRD.md#line=2",
                        "quote": text_value,
                    },
                },
            ],
        }]
    }


def _permission_rule(subject: str, statement: str) -> dict:
    return {
        "rule_id": "rule:narrative-permission",
        "source_id": "prd-source",
        "source_type": "prd",
        "source_locator": "PRD.md#line=2",
        "statement": statement,
        "rule_type": "permission",
        "semantic_frame": {
            "source_grounded": True,
            "subject": subject,
            "behavior": statement.removeprefix(subject),
        },
    }


def test_prd_only_relation_chain_and_permission_subject_declare_objects_without_identity_union() -> None:
    statement = "客户线索只能由归属销售访问；线索 -> 商机 -> 合同；折扣需审批。"
    model = build_enterprise_understanding_model(
        _asset(
            [],
            source_inventory=[{"source_id": "prd-source", "source_type": "prd"}],
            document_semantic_trees=_narrative_prd_tree(statement),
            rule_library=[
                _permission_rule("客户线索", "客户线索只能由归属销售访问")
            ],
            roles=[{
                "role": "sales",
                "source_id": "prd-source",
                "evidence": [{"matched_term": "销售"}],
            }],
        )
    )

    recognition = model["business_object_recognition"]
    assert set(recognition["accepted_labels"]) == {"客户线索", "线索", "商机", "合同"}
    lead_surface = next(
        row for row in recognition["candidates"] if row["labels"] == ["线索"]
    )
    assert lead_surface["status"] == "ACCEPTED_SURFACE_FORM_IDENTITY_PENDING"
    assert lead_surface["surface_parent_labels"] == ["客户线索"]
    assert lead_surface["identity_resolution_eligible"] is False
    assert {row["name"] for row in model["business_objects"]} == {
        "客户线索", "商机", "合同"
    }
    candidate_labels = {
        label for row in recognition["candidates"] for label in row["labels"]
    }
    assert not {"客户", "销售", "归属销售", "折扣", "审批", "访问"} & candidate_labels


def test_prd_only_permission_actor_subject_is_not_promoted() -> None:
    statement = "学生只能访问本人记录。"
    model = build_enterprise_understanding_model(
        _asset(
            [],
            source_inventory=[{"source_id": "prd-source", "source_type": "prd"}],
            document_semantic_trees=_narrative_prd_tree(statement, heading="education"),
            rule_library=[_permission_rule("学生", statement)],
            roles=[{
                "role": "student",
                "source_id": "prd-source",
                "evidence": "学生",
            }],
        )
    )
    assert model["business_objects"] == []
    assert model["business_object_recognition"]["candidates"] == []


def test_prd_only_state_chain_is_not_object_relation_authority() -> None:
    statement = "DRAFT -> APPROVED -> ACTIVE"
    model = build_enterprise_understanding_model(
        _asset(
            [],
            source_inventory=[{"source_id": "prd-source", "source_type": "prd"}],
            document_semantic_trees=_narrative_prd_tree(
                statement, heading="合同状态流转"
            ),
        )
    )
    assert model["business_objects"] == []
    assert model["business_object_recognition"]["candidates"] == []
