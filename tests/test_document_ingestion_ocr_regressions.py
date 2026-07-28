from __future__ import annotations

from typing import Any

from ai_test_asset_center.enterprise_knowledge_center._document_ir_fact_evidence import (
    align_business_facts_to_document_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    AdapterMatch,
    DocumentAdapter,
    DocumentAdapterRegistry,
    DocumentSource,
    OcrSupplementalAdapter,
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.builtin_adapters import (
    UnknownBinaryDocumentAdapter,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    CAP_TABLE_STRUCTURE,
    CAP_TEXT_EXTRACTION,
    MODE_PRIMARY,
)


class _TableTextAdapter(DocumentAdapter):
    name = "table-text-primary"
    mode = MODE_PRIMARY
    priority = 100
    capabilities = frozenset({CAP_TEXT_EXTRACTION, CAP_TABLE_STRUCTURE})

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        return AdapterMatch(
            self.name,
            100,
            "test-table-source",
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        return {
            "schema": "qualibug.document-structure-ir.v1",
            "format": "test",
            "filename": source.filename,
            "plain_text": "订单规则\n订单不得删除。",
            "blocks": [
                {
                    "block_id": "heading-1",
                    "type": "HEADING",
                    "parent_id": "",
                    "order": 1,
                    "region": "body",
                    "text": "订单规则",
                    "source_locator": f"{source.filename}#heading=1",
                },
                {
                    "block_id": "cell-1",
                    "type": "TABLE_CELL",
                    "parent_id": "table-1",
                    "order": 2,
                    "region": "body",
                    "text": "订单不得删除。",
                    "source_locator": f"{source.filename}#table=1;row=1;cell=1",
                },
            ],
            "sections": [],
            "tables": [],
            "unsupported_content": [],
            "structure_receipt": {
                "schema": "qualibug.document-structure-receipt.v1",
                "status": "COMPLETE",
                "format": "test",
                "block_count": 2,
                "unsupported_content_count": 0,
            },
        }


class _UnavailableOcrProvider:
    name = "unavailable"
    version = "test"

    def available(self) -> bool:
        return False

    def recognize(self, image_bytes: bytes, **kwargs: Any) -> list[dict[str, Any]]:
        raise AssertionError("unavailable provider must never execute")


def test_merged_text_projection_preserves_table_cell_business_text() -> None:
    ir = build_document_structure_ir(
        b"table-test",
        filename="规则.custom",
        source_id="table-source",
        registry=DocumentAdapterRegistry([_TableTextAdapter()]),
    )
    assert "订单规则" in ir["plain_text"]
    assert "订单不得删除。" in ir["plain_text"]


def test_fact_alignment_adds_exact_ocr_block_locator() -> None:
    asset = {
        "business_fact_ledger": {
            "items": [
                {
                    "fact_id": "fact-1",
                    "status": "ACCEPTED",
                    "raw_statement": "订单不得删除",
                    "source_spans": [
                        {
                            "source_id": "ocr-source",
                            "locator": "规则扫描.png#section=document;chars=0-6",
                            "quote": "订单不得删除",
                        }
                    ],
                }
            ]
        }
    }
    structured_sources = [
        {
            "source_id": "ocr-source",
            "filename": "规则扫描.png",
            "document_structure": {
                "blocks": [
                    {
                        "block_id": "ocr-block-1",
                        "type": "PARAGRAPH",
                        "region": "body",
                        "page": 1,
                        "bbox": [10, 20, 180, 48],
                        "text": "订单不得删除。",
                        "source_locator": "规则扫描.png#page=1;embedded_image=0;ocr_line=1",
                        "observed_by_adapters": ["ocr-visual-text"],
                    }
                ]
            },
        }
    ]
    enriched = align_business_facts_to_document_ir(asset, structured_sources)
    fact = enriched["business_fact_ledger"]["items"][0]
    assert any(
        row.get("locator") == "规则扫描.png#page=1;embedded_image=0;ocr_line=1"
        for row in fact["source_spans"]
    )
    assert fact["document_structure_alignment"]["page"] == 1
    assert fact["document_structure_alignment"]["observed_by_adapters"] == ["ocr-visual-text"]


def test_unavailable_ocr_provider_keeps_image_source_blocked() -> None:
    registry = DocumentAdapterRegistry(
        [
            OcrSupplementalAdapter(provider=_UnavailableOcrProvider()),
            UnknownBinaryDocumentAdapter(),
        ]
    )
    ir = build_document_structure_ir(
        b"\x89PNG\r\n\x1a\nno-provider",
        filename="扫描规则.png",
        source_id="image-no-ocr",
        registry=registry,
    )
    assert ir["structure_receipt"]["status"] == "BLOCKED"
    assert ir["parsing_plan"]["selected_adapters"][0]["adapter_name"] == "unknown-binary-fallback"
    assert any(
        row.get("reason_code") == "UNSUPPORTED_SOURCE_FORMAT"
        for row in ir["unsupported_content"]
    )
