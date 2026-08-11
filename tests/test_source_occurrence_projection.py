from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center._utils import _save_registry
from ai_test_asset_center.enterprise_knowledge_center.source_occurrence_projection import (
    project_source_occurrence_assets,
)


def _asset() -> dict:
    return {
        "asset_id": "asset:occurrence",
        "source_inventory": [
            {
                "source_id": "src_canonical",
                "external_ref": "legacy/path.md",
                "original_name": "rules.md",
                "source_type": "business_rules",
                "content_hash": "hash-rules",
                "status": "active",
            }
        ],
        "rule_library": [
            {
                "rule_id": "rule:refund",
                "source_id": "src_canonical",
                "statement": "Only OPEN orders may be refunded",
            }
        ],
        "document_structure_assets": {
            "schema": "qualibug.enterprise-document-structure-assets.v1",
            "source_count": 1,
            "items": [
                {
                    "source_id": "src_canonical",
                    "filename": "rules.md",
                    "format": "md",
                    "blocks": [
                        {
                            "block_id": "block:refund",
                            "type": "PARAGRAPH",
                            "order": 1,
                            "text": "Only OPEN orders may be refunded",
                            "source_locator": "rules.md#line=2;chars=0-34",
                            "evidence_address": {
                                "source_id": "src_canonical",
                                "source_locator": "rules.md#line=2;chars=0-34",
                                "address_kind": "EXACT_SOURCE_LOCATOR",
                            },
                        }
                    ],
                    "structure_receipt": {
                        "status": "COMPLETE",
                        "source_id": "src_canonical",
                    },
                    "evidence_closure_receipt": {
                        "status": "PASS",
                        "source_id": "src_canonical",
                        "source_hash": "hash-rules",
                    },
                    "ingestion_pipeline_receipt": {
                        "final_status": "COMPLETE",
                        "source_id": "src_canonical",
                    },
                }
            ],
        },
        "summary": {"rule_count": 1},
        "governance": {},
    }


def _registry() -> dict:
    return {
        "phase": "enterprise_knowledge_center",
        "project_id": "projection-demo",
        "sources": [],
        "audit_events": [],
        "content_assets": [
            {
                "content_asset_id": "content:sha256:hash-rules",
                "content_hash": "hash-rules",
            }
        ],
        "interpretation_assets": [
            {
                "interpretation_asset_id": "interpretation:rules",
                "content_asset_id": "content:sha256:hash-rules",
                "canonical_source_id": "src_canonical",
            }
        ],
        "source_occurrences": [
            {
                "source_occurrence_id": "occurrence:support",
                "source_ref": "departments/support/rules.md",
                "canonical_source_id": "src_canonical",
                "content_asset_id": "content:sha256:hash-rules",
                "interpretation_asset_id": "interpretation:rules",
                "content_hash": "hash-rules",
                "source_type": "business_rules",
                "version": 1,
                "status": "active",
                "parse_reused": False,
            },
            {
                "source_occurrence_id": "occurrence:finance",
                "source_ref": "departments/finance/rules.md",
                "canonical_source_id": "src_canonical",
                "content_asset_id": "content:sha256:hash-rules",
                "interpretation_asset_id": "interpretation:rules",
                "content_hash": "hash-rules",
                "source_type": "business_rules",
                "version": 1,
                "status": "active",
                "parse_reused": True,
            },
        ],
        "governance": {},
    }


def test_projection_creates_two_evidence_views_without_duplicating_business_facts(tmp_path) -> None:
    _save_registry("projection-demo", tmp_path, _registry())
    asset = _asset()
    original_asset = deepcopy(asset)
    original_rules = deepcopy(asset["rule_library"])

    result = project_source_occurrence_assets(
        asset,
        project_id="projection-demo",
        root=tmp_path,
    )

    receipt = result["source_occurrence_projection_receipt"]
    assert receipt["status"] == "PASS"
    assert receipt["canonical_source_count"] == 1
    assert receipt["active_source_occurrence_count"] == 2
    assert receipt["occurrence_evidence_view_count"] == 2
    assert receipt["adapter_execution_repeated"] is False
    assert receipt["semantic_extraction_repeated"] is False
    assert result["rule_library"] == original_rules

    occurrence_inventory = result["source_occurrence_inventory"]
    assert {row["external_ref"] for row in occurrence_inventory} == {
        "departments/support/rules.md",
        "departments/finance/rules.md",
    }
    assert len({row["canonical_source_id"] for row in occurrence_inventory}) == 1

    structure = result["document_structure_assets"]
    assert structure["canonical_parse_count"] == 1
    assert structure["adapter_execution_count"] == 1
    assert structure["occurrence_evidence_view_count"] == 2
    views = structure["occurrence_items"]
    assert len(views) == 2
    assert {row["source_id"] for row in views} == {
        "occurrence:support",
        "occurrence:finance",
    }
    assert {row["blocks"][0]["canonical_block_id"] for row in views} == {
        "block:refund"
    }
    assert {row["blocks"][0]["evidence_address"]["source_ref"] for row in views} == {
        "departments/support/rules.md",
        "departments/finance/rules.md",
    }
    assert all(row["adapter_execution_repeated"] is False for row in views)
    assert asset == original_asset


def test_projection_fails_closed_when_occurrence_has_no_canonical_inventory(tmp_path) -> None:
    registry = _registry()
    registry["source_occurrences"][0]["canonical_source_id"] = "src_missing"
    _save_registry("projection-missing", tmp_path, registry)

    result = project_source_occurrence_assets(
        _asset(),
        project_id="projection-missing",
        root=tmp_path,
    )

    receipt = result["source_occurrence_projection_receipt"]
    assert receipt["status"] == "BLOCKED"
    assert receipt["missing_canonical_count"] == 1
    assert receipt["automatic_source_occurrence_winner_used"] is False
    assert receipt["missing_canonical"][0]["reason_code"] == (
        "SOURCE_OCCURRENCE_CANONICAL_INVENTORY_MISSING"
    )
