from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.document_structure_gate import (
    apply_document_structure_completeness,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.schema import (
    empty_model,
)


def _reason_codes(model: dict) -> set[str]:
    return {
        str(row.get("reason_code") or row.get("kind") or "")
        for row in model.get("unknowns") or []
        if isinstance(row, dict)
    }


def _base_asset() -> dict:
    structure = build_document_structure_ir(
        "客服可以查看工单。".encode("utf-8"),
        filename="rules.txt",
        source_id="source:rules",
    )
    return {
        "source_inventory": [{"source_id": "source:rules", "status": "active"}],
        "document_structure_assets": {
            "source_count": 1,
            "items": [{"source_id": "source:rules", **structure}],
            "errors": [],
        },
        "business_fact_ledger": {
            "items": [
                {
                    "fact_id": "fact:accepted",
                    "status": "ACCEPTED",
                    "kind": "RULE",
                }
            ]
        },
        "enterprise_comprehension_gate": {"entry_allowed": True},
    }


def test_accepted_fact_requires_fact_evidence_receipt() -> None:
    result = apply_document_structure_completeness(empty_model(), _base_asset())

    assert "DOCUMENT_IR_FACT_EVIDENCE_RECEIPT_MISSING" in _reason_codes(result)
    assert result["document_structure_summary"]["accepted_fact_exact_evidence_rate"] == 0.0
    assert result["gate"]["entry_allowed"] is False


def test_receipt_cannot_silently_omit_an_accepted_fact() -> None:
    asset = _base_asset()
    asset["document_ir_fact_evidence_receipt"] = {
        "aligned_fact_count": 0,
        "unresolved_fact_count": 0,
        "aligned": [],
        "unresolved": [],
    }

    result = apply_document_structure_completeness(empty_model(), asset)

    assert "FORMAL_FACT_WITHOUT_EXACT_DOCUMENT_EVIDENCE" in _reason_codes(result)
    summary = result["document_structure_summary"]
    assert summary["omitted_accepted_fact_count"] == 1
    assert summary["accepted_fact_exact_evidence_rate"] == 0.0
    assert result["gate"]["entry_allowed"] is False


def test_only_aligned_accepted_fact_counts_as_exact_evidence() -> None:
    asset = _base_asset()
    asset["document_ir_fact_evidence_receipt"] = {
        "aligned_fact_count": 1,
        "unresolved_fact_count": 0,
        "aligned": [
            {
                "fact_id": "fact:accepted",
                "source_id": "source:rules",
                "block_id": "block:1",
                "block_ids": ["block:1"],
                "source_locator": "rules.txt#line=1;chars=0-9",
                "match_kind": "SINGLE_BLOCK",
            }
        ],
        "unresolved": [],
    }

    result = apply_document_structure_completeness(empty_model(), asset)

    summary = result["document_structure_summary"]
    assert summary["aligned_accepted_fact_count"] == 1
    assert summary["unresolved_accepted_fact_count"] == 0
    assert summary["omitted_accepted_fact_count"] == 0
    assert summary["accepted_fact_exact_evidence_rate"] == 1.0
