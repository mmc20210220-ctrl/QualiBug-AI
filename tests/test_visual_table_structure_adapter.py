from __future__ import annotations

import io
from typing import Any, Iterable

from PIL import Image, ImageDraw

from ai_test_asset_center.enterprise_knowledge_center._document_ir_fact_evidence import (
    align_business_facts_to_document_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    AdapterMatch,
    DocumentAdapter,
    DocumentAdapterRegistry,
    DocumentSource,
    OcrSupplementalAdapter,
    PageRendererRegistry,
    RenderedPage,
    RuledGridVisualTableProvider,
    SupplementalContext,
    VisualTableSupplementalAdapter,
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.builtin_adapters import (
    UnknownBinaryDocumentAdapter,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.contract import (
    CAP_FONT_EVIDENCE,
    CAP_HEADER_FOOTER,
    CAP_HEADING_HIERARCHY,
    CAP_IMAGE_PRESENCE,
    CAP_LIST_HIERARCHY,
    CAP_PAGE_LAYOUT,
    CAP_TABLE_REGION_DETECTION,
    CAP_TEXT_COORDINATES,
    CAP_TEXT_EXTRACTION,
    MODE_PRIMARY,
)
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion.visual_table_projection import (
    apply_visual_table_projection_authority,
)


def _png_bytes(width: int = 300, height: int = 220, *, grid: bool = False) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    if grid:
        draw = ImageDraw.Draw(image)
        for y in (20, 90, 160):
            draw.line((30, y, 270, y), fill="black", width=2)
        for x in (30, 150, 270):
            draw.line((x, 20, x, 160), fill="black", width=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _StaticPageRenderer:
    name = "static-page-renderer"
    version = "test"
    priority = 999

    def __init__(self, image_bytes: bytes, pages: Iterable[int] = (1,)) -> None:
        self.image_bytes = image_bytes
        self.pages = list(pages)
        self.requests: list[list[int]] = []

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
        requested = sorted(int(value) for value in (pages or []))
        self.requests.append(requested)
        selected = requested or self.pages
        return [
            RenderedPage(
                page=page,
                image_index=0,
                image_bytes=self.image_bytes,
                width_px=300,
                height_px=220,
                source_locator=f"{source.filename}#rendered_page={page}",
                renderer_name=self.name,
                renderer_version=self.version,
                render_method="test_full_page_render",
                dpi=200,
            )
            for page in selected
            if page in self.pages
        ]


class _CellOcrProvider:
    name = "cell-test-ocr"
    version = "test"

    def __init__(self, confidence: float = 0.99) -> None:
        self.confidence = confidence
        self.calls: list[int] = []

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
        self.calls.append(image_index)
        return [
            {
                "text": f"单元格{image_index}",
                "bbox": [1, 1, 30, 16],
                "confidence": self.confidence,
                "image_width_px": 60,
                "image_height_px": 40,
            }
        ]


class _RegionTableProvider:
    name = "region-table-test-provider"
    version = "test"

    def __init__(self, unresolved_region_ids: Iterable[str] = ()) -> None:
        self.unresolved = set(unresolved_region_ids)
        self.calls: list[str] = []

    def available(self) -> bool:
        return True

    def detect(
        self,
        rendered_page: RenderedPage,
        *,
        region_bbox: list[int] | None = None,
        target_region_id: str = "",
    ) -> list[dict[str, Any]]:
        self.calls.append(target_region_id)
        if target_region_id in self.unresolved:
            return []
        left, top, right, bottom = region_bbox or [20, 20, 280, 180]
        middle_x = int((left + right) / 2)
        middle_y = int((top + bottom) / 2)
        cells = []
        for row_index, (y0, y1) in enumerate(((top, middle_y), (middle_y, bottom))):
            for column_index, (x0, x1) in enumerate(((left, middle_x), (middle_x, right))):
                cells.append(
                    {
                        "row_index": row_index,
                        "column_index": column_index,
                        "row_span": 1,
                        "column_span": 1,
                        "bbox": [x0, y0, x1, y1],
                        "border_complete": True,
                        "border_support": {
                            "top": 1.0,
                            "bottom": 1.0,
                            "left": 1.0,
                            "right": 1.0,
                        },
                    }
                )
        return [
            {
                "bbox": [left, top, right, bottom],
                "row_count": 2,
                "column_count": 2,
                "cells": cells,
                "confidence": 0.99,
                "complete_cell_border_ratio": 1.0,
                "target_region_id": target_region_id,
                "detection_method": "test-provider",
            }
        ]


class _PdfTablePrimary(DocumentAdapter):
    name = "pdf-table-primary-test"
    parser_version = "test"
    priority = 999
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

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        return AdapterMatch(
            self.name,
            200,
            "test-pdf-table-regions",
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        tables = [
            {
                "block_id": "region-1",
                "type": "TABLE_REGION",
                "parent_id": "",
                "page": 1,
                "order": 1,
                "region": "body",
                "bbox": [20.0, 110.0, 140.0, 200.0],
                "text": "",
                "excluded_from_main_flow": True,
                "source_locator": f"{source.filename}#page=1;table_region=1",
            },
            {
                "block_id": "region-2",
                "type": "TABLE_REGION",
                "parent_id": "",
                "page": 1,
                "order": 2,
                "region": "body",
                "bbox": [160.0, 110.0, 280.0, 200.0],
                "text": "",
                "excluded_from_main_flow": True,
                "source_locator": f"{source.filename}#page=1;table_region=2",
            },
        ]
        unsupported = [
            {
                "kind": "PDF_TABLE_REGION_NOT_CELL_PARSED",
                "reason_code": "PDF_TABLE_REGION_NOT_CELL_PARSED",
                "count": 2,
                "pages": [1],
                "region_ids": ["region-1", "region-2"],
                "severity": "P1",
                "blocks_formal_understanding": False,
                "included_in_plain_text_authority": True,
            }
        ]
        return {
            "schema": "qualibug.document-structure-ir.v1",
            "format": "pdf",
            "filename": source.filename,
            "plain_text": "",
            "blocks": tables,
            "sections": [],
            "tables": tables,
            "pages": [{"page": 1, "width_pt": 300.0, "height_pt": 220.0}],
            "unsupported_content": unsupported,
            "structure_receipt": {
                "schema": "qualibug.document-structure-receipt.v1",
                "status": "PARTIAL",
                "format": "pdf",
                "page_count": 1,
                "block_count": 2,
                "unsupported_content_count": 2,
                "unsupported_content": unsupported,
            },
        }


def test_builtin_ruled_grid_provider_recovers_two_by_two_geometry() -> None:
    rendered = RenderedPage(
        page=1,
        image_index=0,
        image_bytes=_png_bytes(grid=True),
        width_px=300,
        height_px=220,
        source_locator="grid.png#rendered_page=1",
        renderer_name="test",
        renderer_version="test",
        render_method="test",
    )
    tables = RuledGridVisualTableProvider().detect(rendered)
    assert len(tables) == 1
    assert tables[0]["row_count"] == 2
    assert tables[0]["column_count"] == 2
    assert len(tables[0]["cells"]) == 4
    assert tables[0]["confidence"] >= 0.72
    assert all(row["border_complete"] for row in tables[0]["cells"])


def test_visual_table_adapter_emits_table_row_cell_hierarchy() -> None:
    renderer = _StaticPageRenderer(_png_bytes())
    adapter = VisualTableSupplementalAdapter(
        provider=_RegionTableProvider(),
        renderer_registry=PageRendererRegistry([renderer]),
        ocr_provider=_CellOcrProvider(),
    )
    source = DocumentSource("image-table", "审批矩阵.png", _png_bytes())
    ir = adapter.extract(source)
    tables = [row for row in ir["blocks"] if row["type"] == "TABLE"]
    rows = [row for row in ir["blocks"] if row["type"] == "TABLE_ROW"]
    cells = [row for row in ir["blocks"] if row["type"] == "TABLE_CELL"]
    assert len(tables) == 1
    assert len(rows) == 2
    assert len(cells) == 4
    assert tables[0]["formal_table_structure"] is True
    assert all(row["parent_id"] in {value["block_id"] for value in rows} for row in cells)
    assert "单元格1" in ir["plain_text"]
    assert ir["structure_receipt"]["formal_table_count"] == 1


def test_image_pipeline_selects_ocr_and_table_adapters() -> None:
    image_bytes = _png_bytes()
    renderer = _StaticPageRenderer(image_bytes)
    ocr_provider = _CellOcrProvider()
    registry = DocumentAdapterRegistry(
        [
            OcrSupplementalAdapter(
                provider=ocr_provider,
                renderer_registry=PageRendererRegistry([renderer]),
            ),
            VisualTableSupplementalAdapter(
                provider=_RegionTableProvider(),
                renderer_registry=PageRendererRegistry([renderer]),
                ocr_provider=ocr_provider,
            ),
            UnknownBinaryDocumentAdapter(),
        ]
    )
    ir = build_document_structure_ir(
        image_bytes,
        filename="审批矩阵.png",
        source_id="image-pipeline",
        registry=registry,
    )
    selected = [row["adapter_name"] for row in ir["parsing_plan"]["selected_adapters"]]
    assert selected == ["ocr-visual-text", "visual-table-structure"]
    assert {row["type"] for row in ir["blocks"]}.issuperset({"PARAGRAPH", "TABLE", "TABLE_CELL"})
    assert ir["parsing_plan"]["missing_capabilities"] == []


def test_pdf_table_gap_is_not_cleared_when_one_region_on_page_fails() -> None:
    image_bytes = _png_bytes()
    renderer = _StaticPageRenderer(image_bytes)
    table_adapter = VisualTableSupplementalAdapter(
        provider=_RegionTableProvider(unresolved_region_ids={"region-2"}),
        renderer_registry=PageRendererRegistry([renderer]),
        ocr_provider=_CellOcrProvider(),
    )
    ir = build_document_structure_ir(
        b"%PDF-fake",
        filename="制度.pdf",
        source_id="pdf-partial-table",
        registry=DocumentAdapterRegistry([_PdfTablePrimary(), table_adapter]),
    )
    assert any(
        row.get("reason_code") == "PDF_TABLE_REGION_NOT_CELL_PARSED"
        for row in ir["unsupported_content"]
    )
    unresolved = next(
        row
        for row in ir["unsupported_content"]
        if row.get("reason_code") == "VISUAL_TABLE_STRUCTURE_NOT_RECOVERED"
    )
    assert unresolved["region_ids"] == ["region-2"]
    assert ir["adapter_merge_receipt"]["applied_gap_resolution_count"] == 0


def test_pdf_table_gap_is_cleared_only_after_every_region_on_page_is_formal() -> None:
    image_bytes = _png_bytes()
    renderer = _StaticPageRenderer(image_bytes)
    table_adapter = VisualTableSupplementalAdapter(
        provider=_RegionTableProvider(),
        renderer_registry=PageRendererRegistry([renderer]),
        ocr_provider=_CellOcrProvider(),
    )
    ir = build_document_structure_ir(
        b"%PDF-fake",
        filename="制度.pdf",
        source_id="pdf-complete-table",
        registry=DocumentAdapterRegistry([_PdfTablePrimary(), table_adapter]),
    )
    assert not any(
        row.get("reason_code") == "PDF_TABLE_REGION_NOT_CELL_PARSED"
        for row in ir["unsupported_content"]
    )
    assert ir["adapter_merge_receipt"]["applied_gap_resolution_count"] == 1
    assert len([row for row in ir["blocks"] if row.get("type") == "TABLE"]) == 2


def test_formal_table_cells_supersede_overlapping_page_ocr_for_fact_authority() -> None:
    structure = {
        "plain_text": "整行错误投影\n单元格规则",
        "blocks": [
            {
                "block_id": "table-1",
                "type": "TABLE",
                "page": 1,
                "order": 1,
                "region": "body",
                "bbox": [0, 0, 200, 100],
                "formal_table_structure": True,
                "excluded_from_main_flow": True,
                "source_locator": "table.png#table=1",
            },
            {
                "block_id": "ocr-row",
                "type": "PARAGRAPH",
                "page": 1,
                "order": 2,
                "region": "body",
                "bbox": [10, 10, 190, 40],
                "text": "单元格规则",
                "source_locator": "table.png#ocr_line=1",
                "structure_evidence": {
                    "coordinate_system": "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE"
                },
            },
            {
                "block_id": "cell-1",
                "type": "TABLE_CELL",
                "parent_id": "row-1",
                "table_block_id": "table-1",
                "page": 1,
                "order": 3,
                "region": "body",
                "bbox": [10, 10, 100, 40],
                "text": "单元格规则",
                "source_locator": "table.png#table=1;row=0;column=0",
            },
        ],
        "structure_receipt": {},
    }
    projected = apply_visual_table_projection_authority(structure)
    ocr_block = next(row for row in projected["blocks"] if row["block_id"] == "ocr-row")
    assert ocr_block["excluded_from_plain_text_projection"] is True
    assert projected["plain_text"] == "单元格规则"

    asset = {
        "business_fact_ledger": {
            "items": [
                {
                    "fact_id": "fact-table",
                    "raw_statement": "单元格规则",
                    "source_spans": [{"source_id": "table-source"}],
                }
            ]
        }
    }
    aligned = align_business_facts_to_document_ir(
        asset,
        [
            {
                "source_id": "table-source",
                "document_structure": projected,
            }
        ],
    )
    fact = aligned["business_fact_ledger"]["items"][0]
    assert fact["document_structure_alignment"]["block_type"] == "TABLE_CELL"
    assert fact["document_structure_alignment"]["block_id"] == "cell-1"


def test_visual_table_adapter_refuses_spreadsheet_visual_downgrade() -> None:
    adapter = VisualTableSupplementalAdapter(
        provider=_RegionTableProvider(),
        renderer_registry=PageRendererRegistry([_StaticPageRenderer(_png_bytes())]),
        ocr_provider=_CellOcrProvider(),
    )
    source = DocumentSource("sheet", "审批矩阵.xlsx", b"not-a-real-workbook")
    assert adapter.probe(source) is None
    assert adapter.probe_supplemental(
        source,
        SupplementalContext(
            primary_document_ir={},
            trigger_gaps=(
                {
                    "reason_code": "PDF_TABLE_REGION_NOT_CELL_PARSED",
                    "count": 1,
                    "pages": [1],
                },
            ),
        ),
    ) is None


def test_blank_image_does_not_create_false_ruled_table() -> None:
    rendered = RenderedPage(
        page=1,
        image_index=0,
        image_bytes=_png_bytes(),
        width_px=300,
        height_px=220,
        source_locator="blank.png#rendered_page=1",
        renderer_name="test",
        renderer_version="test",
        render_method="test",
    )
    assert RuledGridVisualTableProvider().detect(rendered) == []
