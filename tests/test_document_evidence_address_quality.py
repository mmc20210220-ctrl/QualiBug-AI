from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    DocumentSource,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.evidence_closure import (
    apply_document_evidence_closure,
)


def _source() -> DocumentSource:
    return DocumentSource(
        source_id="source:test",
        filename="rules.txt",
        data="客服可以查看工单".encode("utf-8"),
    )


def _ir(locator: str) -> dict:
    return {
        "schema": "qualibug.document-structure-ir.v1",
        "format": "text",
        "filename": "rules.txt",
        "plain_text": "客服可以查看工单",
        "blocks": [
            {
                "block_id": "block:1",
                "type": "PARAGRAPH",
                "region": "body",
                "order": 1,
                "text": "客服可以查看工单",
                "source_locator": locator,
            }
        ],
        "unsupported_content": [],
        "structure_receipt": {
            "status": "COMPLETE",
            "unsupported_content": [],
        },
    }


def test_weak_locator_is_traceable_but_not_exact() -> None:
    result = apply_document_evidence_closure(_ir("opaque-source-address"), _source())
    receipt = result["evidence_closure_receipt"]

    assert receipt["status"] == "PASS"
    assert receipt["source_traceability_rate"] == 1.0
    assert receipt["exact_address_rate"] == 0.0
    assert receipt["weak_address_authority_block_count"] == 1
    assert result["blocks"][0]["evidence_address"]["address_kind"] == "SOURCE_LOCATOR"


def test_docx_style_block_and_character_locator_is_exact() -> None:
    result = apply_document_evidence_closure(
        _ir("rules.docx#block=1;chars=0-8"),
        _source(),
    )
    receipt = result["evidence_closure_receipt"]

    assert receipt["exact_address_rate"] == 1.0
    assert receipt["weak_address_authority_block_count"] == 0
    assert result["blocks"][0]["evidence_address"]["address_kind"] == "EXACT_SOURCE_LOCATOR"


def test_same_locator_with_different_text_blocks_evidence_closure() -> None:
    document_ir = _ir("rules.txt#line=1")
    document_ir["blocks"].append(
        {
            "block_id": "block:2",
            "type": "PARAGRAPH",
            "region": "body",
            "order": 2,
            "text": "客服不得删除工单",
            "source_locator": "rules.txt#line=1",
        }
    )
    result = apply_document_evidence_closure(document_ir, _source())
    receipt = result["evidence_closure_receipt"]

    assert receipt["status"] == "BLOCKED"
    assert receipt["locator_conflict_count"] == 1
    assert any(
        row.get("reason_code") == "DOCUMENT_EVIDENCE_LOCATOR_CONFLICT"
        for row in result["unsupported_content"]
    )
