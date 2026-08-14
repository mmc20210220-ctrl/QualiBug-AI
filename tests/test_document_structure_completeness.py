from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.document_structure_gate import (
    apply_document_structure_completeness,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.schema import (
    empty_model,
)


def _asset() -> dict:
    return {
        "enterprise_comprehension_gate": {
            "status": "PASS",
            "entry_allowed": True,
        }
    }


def test_unparsed_document_content_prevents_complete_understanding() -> None:
    asset = _asset()
    asset["document_structure_assets"] = {
        "source_count": 1,
        "block_count": 3,
        "unsupported_content_count": 1,
        "errors": [],
        "items": [
            {
                "source_id": "src-docx",
                "filename": "流程制度.docx",
                "format": "docx",
                "blocks": [{"block_id": "b1"}],
                "unsupported_content": [
                    {
                        "kind": "TEXTBOX",
                        "count": 1,
                        "status": "PRESENT_REQUIRES_SECONDARY_PARSER",
                    }
                ],
                "structure_receipt": {"status": "PARTIAL"},
                # The unparsed-content gate is tested in isolation: the source
                # already passed the ingestion pipeline and evidence-closure
                # gates, so the only remaining gap is the partial content.
                "ingestion_pipeline_receipt": {"final_status": "COMPLETE"},
                "evidence_closure_receipt": {
                    "status": "PASS",
                    "untraceable_authority_block_count": 0,
                    "weak_address_authority_block_count": 0,
                    "locator_conflict_count": 0,
                },
            }
        ],
    }

    model = apply_document_structure_completeness(empty_model(), asset)
    unknown = next(
        row
        for row in model["unknowns"]
        if row["kind"] == "DOCUMENT_STRUCTURE_CONTENT_UNPARSED"
    )
    assert unknown["blocks_formal_understanding"] is False
    assert model["gate"]["status"] == "PARTIAL_ENTERPRISE_UNDERSTANDING"
    assert model["gate"]["entry_allowed"] is False


def test_document_structure_parse_failure_blocks_understanding() -> None:
    asset = _asset()
    asset["document_structure_assets"] = {
        "source_count": 0,
        "block_count": 0,
        "unsupported_content_count": 0,
        "items": [],
        "errors": [
            {
                "source_id": "src-broken",
                "filename": "损坏制度.docx",
                "code": "DOCX_DOCUMENT_STRUCTURE_IR_FAILED",
            }
        ],
    }

    model = apply_document_structure_completeness(empty_model(), asset)
    unknown = next(
        row
        for row in model["unknowns"]
        if row["kind"] == "DOCUMENT_STRUCTURE_PARSE_FAILED"
    )
    assert unknown["blocks_formal_understanding"] is True
    assert model["gate"]["status"] == "BLOCKED_ENTERPRISE_UNDERSTANDING_CRITICAL_UNKNOWN"
    assert model["gate"]["entry_allowed"] is False
