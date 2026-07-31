from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_annotation_manifest import (
    MANIFEST_SCHEMA,
    build_identity_annotation_manifest,
    project_identity_annotation_manifest,
)


def test_manifest_is_closed_world_but_blind_to_predictions() -> None:
    result = {
        "mentions": [
            {
                "mention_id": "m:order:prd",
                "mention_type": "BUSINESS_OBJECT",
                "raw_label": "订单",
                "source_id": "prd",
                "source_locator": "section:2",
                "role": "object",
                "scope": {"system": "erp"},
            },
            {
                "mention_id": "m:orders-table",
                "mention_type": "TECHNICAL_ARTIFACT",
                "raw_label": "orders",
                "source_id": "db",
                "source_locator": "table:orders",
                "entity_id": "entity:should-not-leak",
            },
        ],
        "clusters": [
            {
                "entity_id": "entity:order",
                "canonical_label": "订单",
                "member_mention_ids": ["m:order:prd"],
            }
        ],
    }

    manifest = build_identity_annotation_manifest(result)

    assert manifest["schema"] == MANIFEST_SCHEMA
    assert manifest["mention_count"] == 1
    assert manifest["mentions"][0]["mention_ref"] == "m:order:prd"
    assert manifest["mentions"][0]["annotation_status"] == "UNLABELED"
    assert manifest["contains_product_cluster_suggestions"] is False
    assert manifest["contains_predicted_entity_ids"] is False
    assert "entity_id" not in manifest["mentions"][0]
    assert "canonical_label" not in manifest["mentions"][0]
    assert "clusters" not in manifest


def test_projection_publishes_manifest_without_calling_it_ground_truth() -> None:
    asset: dict = {}
    result = {
        "mentions": [
            {
                "mention_id": "m:order:api",
                "mention_type": "BUSINESS_OBJECT",
                "raw_label": "销售订单",
                "source_id": "api",
                "source_locator": "POST /orders",
                "role": "object",
                "scope": {"system": "erp"},
            }
        ]
    }

    projected = project_identity_annotation_manifest(asset, result)

    assert projected["annotation_manifest"]["is_ground_truth"] is False
    assert asset["enterprise_identity_annotation_manifest"]["mention_count"] == 1
    assert asset["summary"]["enterprise_identity_annotation_manifest_count"] == 1
