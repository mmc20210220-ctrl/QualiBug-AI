from __future__ import annotations

from typing import Any, Iterable

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    DocumentAdapterRegistry,
    DocumentSource,
    OcrSupplementalAdapter,
    PageRendererRegistry,
    RenderedPage,
    build_default_page_renderer_registry,
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.builtin_adapters import (
    UnknownBinaryDocumentAdapter,
)


class _AvailableOcrProvider:
    name = "available-test-ocr"
    version = "test"

    def available(self) -> bool:
        return True

    def recognize(self, image_bytes: bytes, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "text": "审批矩阵",
                "bbox": [0, 0, 100, 30],
                "confidence": 0.99,
                "image_width_px": 1000,
                "image_height_px": 1400,
            }
        ]


class _AlwaysRenderer:
    name = "always-renderer"
    version = "test"
    priority = 999

    def available(self) -> bool:
        return True

    def supports(self, source: DocumentSource) -> bool:
        return True

    def render(
        self,
        source: DocumentSource,
        *,
        pages: Iterable[int] | None = None,
    ) -> list[RenderedPage]:
        return [
            RenderedPage(
                page=1,
                image_index=0,
                image_bytes=b"rendered",
                width_px=1000,
                height_px=1400,
                source_locator=f"{source.filename}#rendered_page=1",
                renderer_name=self.name,
                renderer_version=self.version,
                render_method="test",
            )
        ]


def test_default_renderer_registry_uses_pdfium_not_pymupdf() -> None:
    names = [renderer.name for renderer in build_default_page_renderer_registry().all()]
    assert "pdfium-pdf-page-renderer" in names
    assert not any("pymupdf" in name.lower() for name in names)


def test_spreadsheet_is_not_silently_downgraded_to_visual_ocr() -> None:
    ocr = OcrSupplementalAdapter(
        provider=_AvailableOcrProvider(),
        renderer_registry=PageRendererRegistry([_AlwaysRenderer()]),
    )
    ir = build_document_structure_ir(
        b"not-a-real-workbook",
        filename="审批矩阵.xlsx",
        source_id="sheet-1",
        registry=DocumentAdapterRegistry([ocr, UnknownBinaryDocumentAdapter()]),
    )
    assert ir["parsing_plan"]["detected_family"] == "xlsx"
    assert ir["parsing_plan"]["selected_adapters"][0]["adapter_name"] == "unknown-binary-fallback"
    assert ir["structure_receipt"]["status"] == "BLOCKED"
    assert set(ir["parsing_plan"]["required_capabilities"]) == {
        "FORMULA_EXTRACTION",
        "STYLE_SEMANTICS",
        "TABLE_STRUCTURE",
    }
