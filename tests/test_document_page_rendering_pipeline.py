from __future__ import annotations

from typing import Any, Iterable

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    AdapterMatch,
    DocumentAdapter,
    DocumentAdapterRegistry,
    DocumentSource,
    OcrSupplementalAdapter,
    PageRendererRegistry,
    RenderedPage,
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    CAP_PAGE_LAYOUT,
    CAP_TEXT_EXTRACTION,
    MODE_PRIMARY,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.page_rendering import (
    PageRenderBatch,
)


class _FakeOcrProvider:
    name = "fake-rendered-ocr"
    version = "test"

    def __init__(self) -> None:
        self.received: list[bytes] = []

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
        self.received.append(image_bytes)
        return [
            {
                "text": "订单不得删除。",
                "bbox": [10, 20, 180, 48],
                "confidence": 0.96,
                "image_width_px": 1000,
                "image_height_px": 1400,
            }
        ]


class _FakePageRenderer:
    name = "fake-page-renderer"
    version = "test"
    priority = 200

    def __init__(self, supported: bool = True) -> None:
        self.supported = supported
        self.requests: list[list[int]] = []

    def available(self) -> bool:
        return self.supported

    def supports(self, source: DocumentSource) -> bool:
        return self.supported

    def render(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[RenderedPage]:
        requested = sorted(int(value) for value in (pages or [1]))
        self.requests.append(requested)
        return [
            RenderedPage(
                page=page,
                image_index=0,
                image_bytes=f"rendered-page-{page}".encode("utf-8"),
                width_px=1000,
                height_px=1400,
                source_locator=f"{source.filename}#rendered_page={page}",
                renderer_name=self.name,
                renderer_version=self.version,
                render_method="fake-render",
                dpi=200,
            )
            for page in requested
        ]


class _PrimaryWithoutGaps(DocumentAdapter):
    name = "primary-without-gaps"
    mode = MODE_PRIMARY
    priority = 200
    capabilities = frozenset({CAP_TEXT_EXTRACTION, CAP_PAGE_LAYOUT})

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        return AdapterMatch(
            self.name,
            120,
            "test-primary",
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        return {
            "schema": "qualibug.document-structure-ir.v1",
            "format": "pdf",
            "filename": source.filename,
            "plain_text": "订单不得删除。",
            "blocks": [
                {
                    "block_id": "primary-block",
                    "type": "PARAGRAPH",
                    "parent_id": "",
                    "page": 1,
                    "order": 1,
                    "region": "body",
                    "text": "订单不得删除。",
                    "source_locator": f"{source.filename}#page=1;block=1",
                }
            ],
            "sections": [],
            "tables": [],
            "pages": [{"page": 1}],
            "unsupported_content": [],
            "structure_receipt": {
                "schema": "qualibug.document-structure-receipt.v1",
                "status": "COMPLETE",
                "format": "pdf",
                "block_count": 1,
                "unsupported_content_count": 0,
            },
        }


class _ScannedPrimary(_PrimaryWithoutGaps):
    name = "scanned-primary"

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        result = super().extract(source)
        result["plain_text"] = ""
        result["blocks"] = [
            {
                "block_id": "scan-2",
                "type": "SCANNED_PAGE",
                "parent_id": "",
                "page": 2,
                "order": 2,
                "region": "body",
                "text": "",
                "excluded_from_main_flow": True,
                "source_locator": f"{source.filename}#page=2",
            }
        ]
        result["pages"] = [{"page": 2, "scanned_page": True}]
        result["unsupported_content"] = [
            {
                "kind": "SCANNED_PAGE_REQUIRES_OCR",
                "reason_code": "SCANNED_PAGE_REQUIRES_OCR",
                "count": 1,
                "pages": [2],
                "severity": "P0",
                "blocks_formal_understanding": True,
                "included_in_plain_text_authority": False,
            }
        ]
        result["structure_receipt"].update(
            {
                "status": "BLOCKED",
                "block_count": 1,
                "scanned_page_count": 1,
                "unsupported_content_count": 1,
                "unsupported_content": result["unsupported_content"],
            }
        )
        return result


def test_rendered_ocr_consumes_renderer_output_not_source_bytes() -> None:
    provider = _FakeOcrProvider()
    renderer = _FakePageRenderer()
    adapter = OcrSupplementalAdapter(
        provider=provider,
        renderer_registry=PageRendererRegistry([renderer]),
    )
    ir = build_document_structure_ir(
        b"original-office-bytes",
        filename="流程说明.pptx",
        source_id="ppt-1",
        registry=DocumentAdapterRegistry([adapter]),
    )
    assert provider.received == [b"rendered-page-1"]
    assert ir["plain_text"] == "订单不得删除。"
    assert ir["structure_receipt"]["page_renderer_name"] == "fake-page-renderer"
    assert ir["blocks"][0]["structure_evidence"]["page_rendering"]["render_method"] == "fake-render"


def test_scanned_pdf_deferred_ocr_renders_only_requested_page() -> None:
    provider = _FakeOcrProvider()
    renderer = _FakePageRenderer()
    ocr = OcrSupplementalAdapter(
        provider=provider,
        renderer_registry=PageRendererRegistry([renderer]),
    )
    ir = build_document_structure_ir(
        b"%PDF-fake",
        filename="扫描制度.pdf",
        source_id="pdf-scan",
        registry=DocumentAdapterRegistry([_ScannedPrimary(), ocr]),
    )
    assert renderer.requests == [[2]]
    assert provider.received == [b"rendered-page-2"]
    assert ir["parsing_plan"]["deferred_plan"]["requested_capabilities"] == [
        "OCR",
        "PAGE_RENDERING",
        "TABLE_REGION_DETECTION",
        "TABLE_STRUCTURE",
    ]
    assert not any(
        row.get("reason_code") == "SCANNED_PAGE_REQUIRES_OCR"
        for row in ir["unsupported_content"]
    )


def test_ocr_is_not_run_eagerly_beside_native_primary_without_gap() -> None:
    provider = _FakeOcrProvider()
    renderer = _FakePageRenderer()
    ocr = OcrSupplementalAdapter(
        provider=provider,
        renderer_registry=PageRendererRegistry([renderer]),
    )
    ir = build_document_structure_ir(
        b"%PDF-fake",
        filename="普通制度.pdf",
        source_id="pdf-text",
        registry=DocumentAdapterRegistry([_PrimaryWithoutGaps(), ocr]),
    )
    assert ir["parsing_plan"]["selected_adapters"][0]["adapter_name"] == "primary-without-gaps"
    assert ir["ingestion_pipeline_receipt"]["deferred_selected_adapter_count"] == 0
    assert renderer.requests == []
    assert provider.received == []


def test_page_renderer_batch_is_fail_visible_when_no_provider_matches() -> None:
    registry = PageRendererRegistry([_FakePageRenderer(supported=False)])
    source = DocumentSource(
        source_id="unsupported-render",
        filename="unknown.abc",
        data=b"binary",
    )
    batch: PageRenderBatch = registry.render(source, pages=[1])
    assert batch.pages == ()
    assert batch.receipt["status"] == "BLOCKED"
    assert batch.receipt["reason_code"] == "PAGE_RENDERER_UNAVAILABLE_OR_FAILED"
