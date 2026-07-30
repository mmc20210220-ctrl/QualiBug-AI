from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    DocumentSource,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.evidence_closure import (
    apply_document_evidence_closure,
)


def test_generated_slide_label_is_not_business_text_authority() -> None:
    source = DocumentSource(
        source_id="source:pptx",
        filename="empty-slide.pptx",
        data=b"presentation",
    )
    document_ir = {
        "format": "pptx",
        "filename": source.filename,
        "plain_text": "## Slide 1\n真实图片文字",
        "blocks": [
            {
                "block_id": "slide:1",
                "type": "HEADING",
                "region": "body",
                "order": 1,
                "text": "Slide 1",
                "source_locator": "empty-slide.pptx#slide=1",
                "slide": 1,
                "structure_evidence": {
                    "method": "native_presentation_slide_identity"
                },
            },
            {
                "block_id": "ocr:1",
                "type": "PARAGRAPH",
                "region": "body",
                "order": 2,
                "text": "真实图片文字",
                "source_locator": "empty-slide.pptx#page=1;ocr_line=1",
                "page": 1,
                "bbox": [10, 20, 200, 60],
            },
        ],
        "unsupported_content": [],
        "structure_receipt": {"status": "COMPLETE", "unsupported_content": []},
    }

    result = apply_document_evidence_closure(document_ir, source)

    heading = result["blocks"][0]
    assert heading["generated_structure_label"] is True
    assert heading["excluded_from_plain_text_projection"] is True
    assert heading["business_semantics_allowed"] is False
    assert result["plain_text"] == "真实图片文字"
    receipt = result["evidence_closure_receipt"]
    assert receipt["generated_structure_label_count"] == 1
    assert receipt["formal_authority_block_count"] == 1
    assert receipt["source_traceability_rate"] == 1.0
    assert receipt["exact_address_rate"] == 1.0
