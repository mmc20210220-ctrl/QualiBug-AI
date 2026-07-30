from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    DocumentSource,
    SupplementalContext,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.page_rendering import (
    PageRenderBatch,
    RenderedPage,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.rendered_ocr_adapter import (
    OcrSupplementalAdapter,
)


class FakeOcrProvider:
    name = "fake-ocr"
    version = "1"

    def __init__(self, confidence: float = 0.99) -> None:
        self.confidence = confidence

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
    ) -> list[dict]:
        del image_bytes, source_id, filename, image_index
        return [
            {
                "text": f"第{page}页业务规则",
                "bbox": [10, 20, 200, 60],
                "confidence": self.confidence,
                "image_width_px": 1280,
                "image_height_px": 720,
            }
        ]


class FakeRendererRegistry:
    def __init__(self, default_pages: tuple[int, ...] = (1, 2)) -> None:
        self.default_pages = default_pages

    def can_render(self, source: DocumentSource) -> bool:
        del source
        return True

    def render(
        self,
        source: DocumentSource,
        *,
        pages=None,
    ) -> PageRenderBatch:
        selected = tuple(int(page) for page in (pages or self.default_pages))
        rendered = tuple(
            RenderedPage(
                page=page,
                image_index=0,
                image_bytes=b"fake-image",
                width_px=1280,
                height_px=720,
                source_locator=f"{source.filename}#rendered-page={page}",
                renderer_name="fake-renderer",
                renderer_version="1",
                render_method="test",
                dpi=144,
            )
            for page in selected
        )
        return PageRenderBatch(
            pages=rendered,
            receipt={
                "renderer_name": "fake-renderer",
                "renderer_version": "1",
                "missing_pages": [],
                "rendered_pages": list(selected),
            },
        )


def _source(filename: str) -> DocumentSource:
    return DocumentSource(
        source_id=f"source:{filename}",
        filename=filename,
        data=b"office-container",
    )


def test_presentation_image_gap_runs_rendered_ocr_for_target_slide() -> None:
    source = _source("rules.pptx")
    context = SupplementalContext(
        primary_document_ir={},
        trigger_gaps=(
            {
                "reason_code": "PRESENTATION_IMAGE_CONTENT_UNPARSED",
                "source_locator": "rules.pptx#slide=2",
                "count": 1,
            },
        ),
        requested_capabilities=("PAGE_RENDERING", "OCR", "TEXT_EXTRACTION"),
    )
    adapter = OcrSupplementalAdapter(
        provider=FakeOcrProvider(),
        renderer_registry=FakeRendererRegistry(),
    )

    match = adapter.probe_supplemental(source, context)
    assert match is not None
    assert match.mode == "SUPPLEMENTAL"

    result = adapter.extract_supplemental(source, context)
    assert result["structure_receipt"]["status"] == "COMPLETE"
    assert result["structure_receipt"]["formal_ocr_minimum_confidence"] == 0.85
    assert result["structure_receipt"]["formal_ocr_projection_count"] == 1
    assert result["resolves_gaps"] == [
        {
            "reason_code": "PRESENTATION_IMAGE_CONTENT_UNPARSED",
            "pages": [],
            "resolution": "OFFICE_VISUAL_TEXT_RECOVERED_ON_ALL_TARGETS",
            "resolved_rendered_pages": [2],
        }
    ]
    assert result["blocks"][0]["page"] == 2
    assert result["blocks"][0]["structure_evidence"]["page_rendering"][
        "renderer_name"
    ] == "fake-renderer"
    assert not any(
        row.get("reason_code") == "OCR_PAGE_LAYOUT_PROJECTED"
        for row in result["unsupported_content"]
    )


def test_spreadsheet_image_gap_clears_only_after_all_rendered_pages_recover() -> None:
    source = _source("rules.xlsx")
    context = SupplementalContext(
        primary_document_ir={},
        trigger_gaps=(
            {
                "reason_code": "SPREADSHEET_EMBEDDED_IMAGE_NOT_SEMANTICALLY_PARSED",
                "source_locator": "rules.xlsx#sheet=规则",
                "count": 1,
            },
        ),
        requested_capabilities=("PAGE_RENDERING", "OCR", "TEXT_EXTRACTION"),
    )
    adapter = OcrSupplementalAdapter(
        provider=FakeOcrProvider(),
        renderer_registry=FakeRendererRegistry(default_pages=(1, 2)),
    )

    result = adapter.extract_supplemental(source, context)
    assert result["structure_receipt"]["status"] == "COMPLETE"
    assert result["resolves_gaps"][0]["reason_code"] == (
        "SPREADSHEET_EMBEDDED_IMAGE_NOT_SEMANTICALLY_PARSED"
    )
    assert result["resolves_gaps"][0]["resolved_rendered_pages"] == [1, 2]


def test_low_confidence_office_ocr_remains_blocked_and_does_not_clear_gap() -> None:
    source = _source("rules.pptx")
    context = SupplementalContext(
        primary_document_ir={},
        trigger_gaps=(
            {
                "reason_code": "PRESENTATION_IMAGE_CONTENT_UNPARSED",
                "source_locator": "rules.pptx#slide=1",
                "count": 1,
            },
        ),
        requested_capabilities=("PAGE_RENDERING", "OCR", "TEXT_EXTRACTION"),
    )
    adapter = OcrSupplementalAdapter(
        provider=FakeOcrProvider(confidence=0.60),
        renderer_registry=FakeRendererRegistry(default_pages=(1,)),
    )

    result = adapter.extract_supplemental(source, context)
    assert result["structure_receipt"]["status"] == "BLOCKED"
    assert result["resolves_gaps"] == []
    assert any(
        row.get("reason_code") == "OCR_TEXT_LOW_CONFIDENCE"
        for row in result["unsupported_content"]
    )
