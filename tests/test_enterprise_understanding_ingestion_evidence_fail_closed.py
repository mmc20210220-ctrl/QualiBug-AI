from __future__ import annotations

from benchmark_evaluator.enterprise_understanding import measure_ingestion_evidence


def _asset(item: dict) -> dict:
    return {
        "source_inventory": [{"source_id": "source:rules", "status": "active"}],
        "document_structure_assets": {
            "schema": "qualibug.enterprise-document-structure-assets.v1",
            "source_count": 1,
            "items": [item],
        },
    }


def _base_item() -> dict:
    return {
        "source_id": "source:rules",
        "filename": "rules.docx",
        "format": "docx",
        "blocks": [
            {
                "block_id": "block:1",
                "type": "PARAGRAPH",
                "region": "body",
                "text": "客服可以查看工单",
                "source_hash": "sha256:source",
                "evidence_address": {
                    "source_locator": "rules.docx#block=1;chars=0-8",
                    "address_kind": "EXACT_SOURCE_LOCATOR",
                },
            }
        ],
        "unsupported_content": [],
    }


def test_missing_structure_receipt_is_not_accepted() -> None:
    item = _base_item()
    item["evidence_closure_receipt"] = {
        "status": "PASS",
        "formal_authority_block_count": 1,
        "source_hash_bound_block_count": 1,
        "traceable_authority_block_count": 1,
        "exact_address_authority_block_count": 1,
    }

    result = measure_ingestion_evidence(_asset(item))

    assert result["summary"]["ingestion_acceptance_rate"] == 0.0
    assert result["summary"]["receipt_integrity_gate_pass"] is False
    assert result["summary"]["highest_impact_gap"] == (
        "DOCUMENT_STRUCTURE_RECEIPT_MISSING"
    )


def test_missing_evidence_closure_receipt_blocks_exact_address_claim() -> None:
    item = _base_item()
    item["structure_receipt"] = {"status": "COMPLETE"}

    result = measure_ingestion_evidence(_asset(item))

    assert result["summary"]["receipt_integrity_gate_pass"] is False
    assert result["summary"]["highest_impact_gap"] == (
        "DOCUMENT_EVIDENCE_CLOSURE_RECEIPT_MISSING"
    )
    source = result["evidence_address_analysis"]["sources"][0]
    assert source["silent_loss_risk"] is True
    assert "FORMAL_BLOCKS_WITHOUT_EVIDENCE_CLOSURE_RECEIPT" in source[
        "silent_loss_reasons"
    ]


def test_zero_source_hash_binding_cannot_pass_even_with_exact_addresses() -> None:
    item = _base_item()
    item["structure_receipt"] = {"status": "COMPLETE"}
    item["evidence_closure_receipt"] = {
        "status": "PASS",
        "formal_authority_block_count": 1,
        "source_hash_bound_block_count": 0,
        "traceable_authority_block_count": 1,
        "exact_address_authority_block_count": 1,
        "untraceable_authority_block_count": 0,
        "weak_address_authority_block_count": 0,
        "locator_conflict_count": 0,
    }

    result = measure_ingestion_evidence(_asset(item))

    assert result["summary"]["source_hash_binding_rate"] == 0.0
    assert result["summary"]["exact_address_rate"] == 1.0
    assert result["summary"]["receipt_integrity_gate_pass"] is False
    assert result["summary"]["highest_impact_gap"] == (
        "DOCUMENT_EVIDENCE_SOURCE_HASH_BINDING_INCOMPLETE"
    )
