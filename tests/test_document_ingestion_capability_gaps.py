from __future__ import annotations

from typing import Any

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    AdapterMatch,
    DocumentAdapter,
    DocumentAdapterRegistry,
    DocumentSource,
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    CAP_TEXT_EXTRACTION,
    MODE_PRIMARY,
)


class _TextOnlyAdapter(DocumentAdapter):
    name = "text-only-primary"
    parser_version = "1"
    priority = 100
    mode = MODE_PRIMARY
    capabilities = frozenset({CAP_TEXT_EXTRACTION})

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        return AdapterMatch(self.name, 100, "test", tuple(self.capabilities), self.mode)

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        return {
            "schema": "qualibug.document-structure-ir.v1",
            "format": "txt",
            "filename": source.filename,
            "plain_text": "订单不得删除",
            "blocks": [
                {
                    "block_id": "text-only:block:1",
                    "type": "PARAGRAPH",
                    "parent_id": "",
                    "order": 1,
                    "region": "body",
                    "text": "订单不得删除",
                    "source_locator": f"{source.filename}#line=1",
                }
            ],
            "sections": [],
            "tables": [],
            "unsupported_content": [],
            "structure_receipt": {
                "schema": "qualibug.document-structure-receipt.v1",
                "status": "COMPLETE",
                "format": "txt",
                "block_count": 1,
                "source_traceability_rate": 1.0,
                "block_type_distribution": {"PARAGRAPH": 1},
                "section_count": 0,
                "unsupported_content_count": 0,
                "unsupported_content": [],
                "document_order_is_business_flow": False,
                "filename_is_business_context": False,
            },
        }


def test_missing_required_structure_capabilities_cannot_report_complete() -> None:
    registry = DocumentAdapterRegistry([_TextOnlyAdapter()])
    ir = build_document_structure_ir(
        b"order rule",
        filename="rules.txt",
        source_id="capability-gap",
        registry=registry,
    )
    assert ir["parsing_plan"]["status"] == "PARTIAL_CAPABILITY_COVERAGE"
    assert ir["structure_receipt"]["status"] == "PARTIAL"
    missing = {
        row.get("missing_capability")
        for row in ir["unsupported_content"]
        if row.get("kind") == "DOCUMENT_ADAPTER_CAPABILITY_GAP"
    }
    assert missing == {"HEADING_HIERARCHY", "LIST_HIERARCHY"}
    assert ir["ingestion_pipeline_receipt"]["missing_capability_count"] == 2
