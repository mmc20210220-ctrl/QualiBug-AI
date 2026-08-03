from __future__ import annotations

import io
from typing import Any

import pytest

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    AdapterMatch,
    DocumentAdapter,
    DocumentAdapterRegistry,
    DocumentSource,
    OcrSupplementalAdapter,
    SupplementalContext,
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    CAP_FONT_EVIDENCE,
    CAP_HEADER_FOOTER,
    CAP_HEADING_HIERARCHY,
    CAP_IMAGE_PRESENCE,
    CAP_LIST_HIERARCHY,
    CAP_OCR,
    CAP_PAGE_LAYOUT,
    CAP_TABLE_REGION_DETECTION,
    CAP_TEXT_COORDINATES,
    CAP_TEXT_EXTRACTION,
    MODE_PRIMARY,
    MODE_SUPPLEMENTAL,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.integration import (
    enrich_asset_with_enterprise_understanding,
)


def _renderable_png() -> bytes:
    """A real decodable raster image the shared renderer registry can render."""
    pytest.importorskip("PIL")
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (320, 200), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeOcrProvider:
    name = "fake-ocr"
    version = "test"

    def __init__(self, confidence: float = 0.94, text: str = "订单不得删除。") -> None:
        self.confidence = confidence
        self.text = text

    def available(self) -> bool:
        return True

    def recognize(
        self,
        image_bytes: bytes,
        *,
        source_id: str,
        filename: str,
        page: int,
        image_index: int,
    ) -> list[dict[str, Any]]:
        return [
            {
                "text": self.text,
                "bbox": [10, 20, 180, 48],
                "confidence": self.confidence,
                "image_width_px": 800,
                "image_height_px": 1200,
            }
        ]


class _ScannedPdfPrimaryAdapter(DocumentAdapter):
    name = "fake-scanned-pdf-primary"
    parser_version = "test"
    priority = 100
    mode = MODE_PRIMARY
    capabilities = frozenset(
        {
            CAP_TEXT_EXTRACTION,
            CAP_PAGE_LAYOUT,
            CAP_TEXT_COORDINATES,
            CAP_FONT_EVIDENCE,
            CAP_HEADING_HIERARCHY,
            CAP_LIST_HIERARCHY,
            CAP_TABLE_REGION_DETECTION,
            CAP_IMAGE_PRESENCE,
            CAP_HEADER_FOOTER,
        }
    )

    def __init__(self, pages: list[int]) -> None:
        self.pages = pages

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        return AdapterMatch(
            self.name,
            120,
            "fake-scanned-pdf",
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        unsupported = [
            {
                "kind": "SCANNED_PAGE_REQUIRES_OCR",
                "reason_code": "SCANNED_PAGE_REQUIRES_OCR",
                "count": len(self.pages),
                "pages": self.pages,
                "severity": "P0",
                "blocks_formal_understanding": True,
                "included_in_plain_text_authority": False,
            }
        ]
        return {
            "schema": "qualibug.document-structure-ir.v1",
            "format": "pdf",
            "filename": source.filename,
            "plain_text": "",
            "blocks": [
                {
                    "block_id": f"scan-{page}",
                    "type": "SCANNED_PAGE",
                    "parent_id": "",
                    "page": page,
                    "order": page,
                    "region": "body",
                    "text": "",
                    "excluded_from_main_flow": True,
                    "source_locator": f"{source.filename}#page={page}",
                }
                for page in self.pages
            ],
            "sections": [],
            "tables": [],
            "pages": [{"page": page, "scanned_page": True} for page in self.pages],
            "unsupported_content": unsupported,
            "structure_receipt": {
                "schema": "qualibug.document-structure-receipt.v1",
                "status": "BLOCKED",
                "format": "pdf",
                "page_count": len(self.pages),
                "scanned_page_count": len(self.pages),
                "block_count": len(self.pages),
                "unsupported_content_count": len(self.pages),
                "unsupported_content": unsupported,
            },
        }


class _DeferredOcrAdapter(DocumentAdapter):
    name = "fake-deferred-ocr"
    parser_version = "test"
    priority = 90
    mode = MODE_SUPPLEMENTAL
    capabilities = frozenset({CAP_OCR, CAP_TEXT_EXTRACTION, CAP_TEXT_COORDINATES, CAP_PAGE_LAYOUT})

    def __init__(self, resolved_pages: list[int], failed_pages: list[int] | None = None) -> None:
        self.resolved_pages = resolved_pages
        self.failed_pages = failed_pages or []

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        return None

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        raise AssertionError("deferred adapter must not run as primary")

    def probe_supplemental(
        self,
        source: DocumentSource,
        context: SupplementalContext,
    ) -> AdapterMatch | None:
        if any(
            row.get("reason_code") == "SCANNED_PAGE_REQUIRES_OCR"
            for row in context.trigger_gaps
        ):
            return AdapterMatch(
                self.name,
                118,
                "fake-ocr-trigger",
                tuple(sorted(self.capabilities)),
                self.mode,
            )
        return None

    def extract_supplemental(
        self,
        source: DocumentSource,
        context: SupplementalContext,
    ) -> dict[str, Any]:
        blocks = [
            {
                "block_id": f"ocr-{page}",
                "type": "PARAGRAPH",
                "parent_id": "",
                "page": page,
                "order": page,
                "region": "body",
                "text": "订单不得删除。",
                "bbox": [10, 20, 180, 48],
                "source_locator": f"{source.filename}#page={page};ocr_line=1",
            }
            for page in self.resolved_pages
        ]
        unsupported: list[dict[str, Any]] = [
            {
                "kind": "OCR_PAGE_LAYOUT_PROJECTED",
                "reason_code": "OCR_PAGE_LAYOUT_PROJECTED",
                "count": 1,
                "pages": [page],
                "severity": "P1",
                "blocks_formal_understanding": False,
                "included_in_plain_text_authority": True,
            }
            for page in self.resolved_pages
        ]
        unsupported.extend(
            {
                "kind": "OCR_TEXT_NOT_RECOVERED",
                "reason_code": "OCR_TEXT_NOT_RECOVERED",
                "count": 1,
                "pages": [page],
                "severity": "P0",
                "blocks_formal_understanding": True,
                "included_in_plain_text_authority": False,
            }
            for page in self.failed_pages
        )
        critical = any(row["blocks_formal_understanding"] for row in unsupported)
        return {
            "schema": "qualibug.document-structure-ir.v1",
            "format": "pdf-ocr-supplement",
            "filename": source.filename,
            "plain_text": "\n".join(row["text"] for row in blocks),
            "blocks": blocks,
            "sections": [],
            "tables": [],
            "pages": [{"page": page, "ocr_successful": True} for page in self.resolved_pages],
            "unsupported_content": unsupported,
            "resolves_gaps": [
                {
                    "reason_code": "SCANNED_PAGE_REQUIRES_OCR",
                    "pages": self.resolved_pages,
                    "resolution": "OCR_TEXT_RECOVERED",
                }
            ],
            "structure_receipt": {
                "schema": "qualibug.document-structure-receipt.v1",
                "status": "BLOCKED" if critical else "PARTIAL",
                "format": "pdf-ocr-supplement",
                "block_count": len(blocks),
                "unsupported_content_count": len(unsupported),
                "unsupported_content": unsupported,
            },
        }


def test_deferred_ocr_resolves_scanned_page_and_recovered_text_enters_ir() -> None:
    registry = DocumentAdapterRegistry(
        [_ScannedPdfPrimaryAdapter([1]), _DeferredOcrAdapter([1])]
    )
    ir = build_document_structure_ir(
        b"%PDF-fake",
        filename="扫描制度.pdf",
        source_id="scan-1",
        registry=registry,
    )
    assert ir["parsing_plan"]["deferred_plan"]["status"] == "READY"
    assert ir["ingestion_pipeline_receipt"]["deferred_selected_adapter_count"] == 1
    assert "订单不得删除。" in ir["plain_text"]
    assert not any(
        row.get("reason_code") == "SCANNED_PAGE_REQUIRES_OCR"
        for row in ir["unsupported_content"]
    )
    assert ir["adapter_merge_receipt"]["applied_gap_resolution_count"] == 1
    assert ir["structure_receipt"]["status"] == "PARTIAL"


def test_deferred_ocr_only_resolves_successful_pages() -> None:
    registry = DocumentAdapterRegistry(
        [_ScannedPdfPrimaryAdapter([1, 2]), _DeferredOcrAdapter([1], [2])]
    )
    ir = build_document_structure_ir(
        b"%PDF-fake",
        filename="部分扫描制度.pdf",
        source_id="scan-2",
        registry=registry,
    )
    remaining_scan_gap = next(
        row
        for row in ir["unsupported_content"]
        if row.get("reason_code") == "SCANNED_PAGE_REQUIRES_OCR"
    )
    assert remaining_scan_gap["pages"] == [2]
    assert ir["structure_receipt"]["status"] == "BLOCKED"


def test_ocr_adapter_runs_standalone_for_raster_image() -> None:
    adapter = OcrSupplementalAdapter(provider=_FakeOcrProvider())
    registry = DocumentAdapterRegistry([adapter])
    ir = build_document_structure_ir(
        _renderable_png(),
        filename="审批规则.png",
        source_id="image-1",
        registry=registry,
    )
    assert ir["parsing_plan"]["detected_family"] == "image"
    assert ir["parsing_plan"]["selected_adapters"][0]["adapter_name"] == "ocr-visual-text"
    assert ir["plain_text"] == "订单不得删除。"
    assert ir["blocks"][0]["structure_evidence"]["provider"] == "fake-ocr"
    assert ir["structure_receipt"]["status"] == "PARTIAL"


def test_low_confidence_ocr_remains_formally_blocked() -> None:
    adapter = OcrSupplementalAdapter(
        provider=_FakeOcrProvider(confidence=0.2),
        minimum_confidence=0.55,
    )
    ir = build_document_structure_ir(
        _renderable_png(),
        filename="模糊扫描.png",
        source_id="image-low",
        registry=DocumentAdapterRegistry([adapter]),
    )
    assert ir["structure_receipt"]["status"] == "BLOCKED"
    assert any(
        row.get("reason_code") == "OCR_TEXT_LOW_CONFIDENCE"
        for row in ir["unsupported_content"]
    )


def test_recovered_ir_text_rebuilds_chinese_business_fact_ledger() -> None:
    structure = {
        "schema": "qualibug.document-structure-ir.v1",
        "format": "image",
        "filename": "规则扫描.png",
        "plain_text": "订单不得删除。",
        "blocks": [
            {
                "block_id": "ocr-rule-1",
                "type": "PARAGRAPH",
                "parent_id": "",
                "page": 1,
                "order": 1,
                "region": "body",
                "text": "订单不得删除。",
                "source_locator": "规则扫描.png#page=1;ocr_line=1",
            }
        ],
        "sections": [],
        "tables": [],
        "pages": [{"page": 1, "ocr_successful": True}],
        "unsupported_content": [],
        "structure_receipt": {
            "schema": "qualibug.document-structure-receipt.v1",
            "status": "COMPLETE",
            "format": "image",
            "page_count": 1,
            "block_count": 1,
            "unsupported_content_count": 0,
        },
        "adapter_receipts": [{"adapter_name": "ocr-visual-text"}],
        "parsing_plan": {"status": "READY"},
    }
    asset = {
        "source_inventory": [
            {"source_id": "ocr-source", "status": "active", "original_name": "规则扫描.png"}
        ],
        "business_objects": [{"object": "订单"}],
        "roles": [],
        "rule_library": [],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }
    enriched = enrich_asset_with_enterprise_understanding(
        asset,
        parsed_sources=[
            {
                "source_id": "ocr-source",
                "filename": "规则扫描.png",
                "text": structure["plain_text"],
                "text_authority": "merged_document_structure_ir",
                "document_structure": structure,
                "document_structure_error": {},
            }
        ],
    )
    facts = enriched["business_fact_ledger"]["items"]
    assert any(
        row.get("raw_statement") == "订单不得删除"
        and row.get("status") == "ACCEPTED"
        for row in facts
    )
    assert enriched["governance"]["ocr_recovered_text_can_create_source_backed_facts"] is True
