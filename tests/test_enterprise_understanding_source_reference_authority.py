from __future__ import annotations

import pytest

from benchmark_evaluator.enterprise_understanding.document_ground_truth import (
    DOCUMENT_GROUND_TRUTH_SCHEMA,
    DocumentGroundTruthValidationError,
    evaluate_document_ground_truth,
    validate_document_ground_truth,
)


SOURCE_REF = "projects/ticketsla_d/input/BUSINESS_RULES.md"
LOCATOR = "BUSINESS_RULES.md#line=1;chars=0-12"
TEXT_HASH = "db9d8eb4b9f5819a1e4907d106952d5a87c8d633f7148de1d67c990a1512ed0c"


def _asset(source_id: str, *, duplicate_ref: bool = False) -> dict:
    inventory = [
        {
            "source_id": source_id,
            "external_ref": SOURCE_REF,
            "original_name": "BUSINESS_RULES.md",
            "content_hash": "source-hash",
            "status": "active",
        }
    ]
    items = [
        {
            "source_id": source_id,
            "filename": "BUSINESS_RULES.md",
            "format": "md",
            "blocks": [
                {
                    "block_id": f"{source_id}:block:1",
                    "type": "HEADING",
                    "order": 1,
                    "text": "业务规则",
                    "text_hash": TEXT_HASH,
                    "source_locator": LOCATOR,
                    "evidence_address": {
                        "address_kind": "EXACT_SOURCE_LOCATOR",
                        "source_locator": LOCATOR,
                    },
                }
            ],
            "evidence_closure_receipt": {
                "source_hash": "source-hash",
                "filename": "BUSINESS_RULES.md",
            },
        }
    ]
    if duplicate_ref:
        inventory.append(
            {
                "source_id": "src_other_runtime_version",
                "external_ref": SOURCE_REF,
                "original_name": "BUSINESS_RULES.md",
                "content_hash": "other-hash",
                "status": "active",
            }
        )
        items.append(
            {
                "source_id": "src_other_runtime_version",
                "filename": "BUSINESS_RULES.md",
                "format": "md",
                "blocks": [],
                "evidence_closure_receipt": {
                    "source_hash": "other-hash",
                    "filename": "BUSINESS_RULES.md",
                },
            }
        )
    return {
        "source_inventory": inventory,
        "document_structure_assets": {"items": items},
    }


def _profile() -> dict:
    return {
        "schema": DOCUMENT_GROUND_TRUTH_SCHEMA,
        "scope_complete": True,
        "minimum_profile": {"sources": 1, "elements": 1},
        "sources": [
            {
                "ground_truth_id": "gt:source:rules",
                "source_ref": SOURCE_REF,
                "filename": "BUSINESS_RULES.md",
                "expected_format": "md",
                "criticality": "P0",
                "annotation_status": "CONFIRMED",
            }
        ],
        "elements": [
            {
                "ground_truth_id": "gt:block:heading",
                "source_ref": SOURCE_REF,
                "block_type": "HEADING",
                "source_locator": LOCATOR,
                "text_hash": TEXT_HASH,
                "address_kind": "EXACT_SOURCE_LOCATOR",
                "order": 1,
                "criticality": "P0",
                "annotation_status": "CONFIRMED",
            }
        ],
        "reading_order_pairs": [],
    }


def test_source_ref_resolves_exactly_without_runtime_source_id_in_ground_truth() -> None:
    profile = validate_document_ground_truth(_profile())

    result = evaluate_document_ground_truth(profile, _asset("src_runtime_a"))

    assert result["status"] == "PASS"
    assert result["metrics"]["source_recall"] == 1.0
    assert result["metrics"]["strict_structure_element_recall"] == 1.0
    assert result["source_alignments"][0]["candidate"]["source_id"] == "src_runtime_a"
    assert result["source_alignments"][0]["candidate"]["source_ref"] == SOURCE_REF
    assert result["runtime_source_id_required_in_human_ground_truth"] is False
    assert result["source_reference_resolution_authority"] == "SOURCE_INVENTORY_EXTERNAL_REF"


def test_same_source_ref_survives_runtime_source_id_change() -> None:
    profile = validate_document_ground_truth(_profile())

    first = evaluate_document_ground_truth(profile, _asset("src_runtime_a"))
    second = evaluate_document_ground_truth(profile, _asset("src_runtime_b"))

    assert first["profile_five_of_five_pass"] is True
    assert second["profile_five_of_five_pass"] is True
    assert first["source_alignments"][0]["candidate"]["source_id"] != second[
        "source_alignments"
    ][0]["candidate"]["source_id"]


def test_duplicate_product_source_ref_is_ambiguous_and_no_winner_is_selected() -> None:
    profile = validate_document_ground_truth(_profile())

    result = evaluate_document_ground_truth(
        profile,
        _asset("src_runtime_a", duplicate_ref=True),
    )

    source = result["source_alignments"][0]
    assert source["alignment_status"] == "AMBIGUOUS"
    assert source["candidate"] == {}
    assert result["highest_impact_gap"] == "DOCUMENT_GROUND_TRUTH_SOURCE_AMBIGUOUS"
    assert result["automatic_winner_used"] is False
    assert result["profile_five_of_five_pass"] is False


def test_ground_truth_must_declare_exactly_one_source_identity_kind() -> None:
    profile = _profile()
    profile["sources"][0]["source_id"] = "src_runtime_a"

    with pytest.raises(DocumentGroundTruthValidationError, match="exactly one"):
        validate_document_ground_truth(profile)


def test_element_must_reference_a_declared_source_identity() -> None:
    profile = _profile()
    profile["elements"][0]["source_ref"] = "projects/other/input/rules.md"

    with pytest.raises(DocumentGroundTruthValidationError, match="undeclared source"):
        validate_document_ground_truth(profile)
