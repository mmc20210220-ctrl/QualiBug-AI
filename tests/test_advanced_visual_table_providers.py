from __future__ import annotations

import io
from typing import Any, Iterable

from PIL import Image, ImageDraw

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    CompositeVisualTableProvider,
    DocumentSource,
    MergedCellRuledGridVisualTableProvider,
    PageRendererRegistry,
    RenderedPage,
    TextAlignedVisualTableProvider,
    VisualTableSupplementalAdapter,
    build_default_registry,
)


def _rendered(image_bytes: bytes, *, width: int = 300, height: int = 240) -> RenderedPage:
    return RenderedPage(
        page=1,
        image_index=0,
        image_bytes=image_bytes,
        width_px=width,
        height_px=height,
        source_locator="visual-table.png#rendered_page=1",
        renderer_name="advanced-table-test-renderer",
        renderer_version="test",
        render_method="test",
    )


def _merged_header_grid() -> bytes:
    image = Image.new("RGB", (300, 240), "white")
    draw = ImageDraw.Draw(image)
    for y in (20, 70, 120, 170, 220):
        draw.line((20, y, 280, y), fill="black", width=3)
    draw.line((20, 20, 20, 220), fill="black", width=3)
    draw.line((280, 20, 280, 220), fill="black", width=3)
    # The first row spans both columns; the internal vertical boundary starts below it.
    draw.line((150, 70, 150, 220), fill="black", width=3)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _blank_png() -> bytes:
    image = Image.new("RGB", (320, 220), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _StaticRenderer:
    name = "advanced-table-static-renderer"
    version = "test"
    priority = 999

    def __init__(self, image_bytes: bytes, width: int = 320, height: int = 220) -> None:
        self.image_bytes = image_bytes
        self.width = width
        self.height = height

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
                image_bytes=self.image_bytes,
                width_px=self.width,
                height_px=self.height,
                source_locator=f"{source.filename}#rendered_page=1",
                renderer_name=self.name,
                renderer_version=self.version,
                render_method="test",
            )
        ]


class _BorderlessWords:
    name = "borderless-word-layout-test-provider"
    version = "test"

    def available(self) -> bool:
        return True

    def words(
        self,
        rendered_page: RenderedPage,
        *,
        region_bbox: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        rows = [
            # A centered title/header spans all inferred columns.
            ("审批矩阵", [20, 10, 290, 30], (1, 1, 1)),
            ("状态", [20, 55, 70, 75], (1, 1, 2)),
            ("动作", [130, 55, 180, 75], (1, 1, 2)),
            ("角色", [240, 55, 290, 75], (1, 1, 2)),
            ("待审核", [20, 95, 80, 115], (1, 1, 3)),
            ("审核", [130, 95, 180, 115], (1, 1, 3)),
            ("主管", [240, 95, 290, 115], (1, 1, 3)),
            ("已审核", [20, 135, 80, 155], (1, 1, 4)),
            ("发货", [130, 135, 180, 155], (1, 1, 4)),
            ("仓管", [240, 135, 290, 155], (1, 1, 4)),
        ]
        return [
            {
                "text": value,
                "bbox": bbox,
                "confidence": 0.99,
                "line_key": line_key,
            }
            for value, bbox, line_key in rows
        ]


class _ParagraphWords:
    name = "paragraph-word-layout-test-provider"
    version = "test"

    def available(self) -> bool:
        return True

    def words(
        self,
        rendered_page: RenderedPage,
        *,
        region_bbox: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        return [
            {
                "text": f"这是普通段落第{index}行",
                "bbox": [20, 20 + index * 35, 280, 40 + index * 35],
                "confidence": 0.99,
                "line_key": (1, 1, index),
            }
            for index in range(1, 6)
        ]


class _CellOcr:
    name = "advanced-table-cell-ocr"
    version = "test"

    def available(self) -> bool:
        return True

    def recognize(self, image_bytes: bytes, **kwargs: Any) -> list[dict[str, Any]]:
        index = int(kwargs.get("image_index") or 0)
        return [
            {
                "text": f"单元格{index}",
                "bbox": [1, 1, 30, 15],
                "confidence": 0.99,
                "image_width_px": 60,
                "image_height_px": 30,
            }
        ]


def test_missing_internal_border_becomes_column_span() -> None:
    provider = MergedCellRuledGridVisualTableProvider()
    tables = provider.detect(_rendered(_merged_header_grid()))
    assert len(tables) == 1
    table = tables[0]
    assert table["merged_cell_resolution"] == "RESOLVED"
    assert table["merged_cell_count"] == 1
    merged = next(
        cell
        for cell in table["cells"]
        if int(cell.get("column_span") or 1) == 2
    )
    assert merged["row_index"] == 0
    assert merged["column_index"] == 0
    assert merged["row_span"] == 1
    assert merged["border_complete"] is True
    assert len(table["cells"]) == 7


def test_borderless_alignment_recovers_columns_and_spanning_header() -> None:
    provider = TextAlignedVisualTableProvider(word_provider=_BorderlessWords())
    tables = provider.detect(_rendered(_blank_png(), width=320, height=220))
    assert len(tables) == 1
    table = tables[0]
    assert table["row_count"] == 4
    assert table["column_count"] == 3
    assert table["geometry_formal"] is True
    assert table["boundary_evidence_mode"] == "TEXT_ALIGNMENT_NOT_VISIBLE_BORDERS"
    header = next(cell for cell in table["cells"] if cell["row_index"] == 0)
    assert header["column_index"] == 0
    assert header["column_span"] == 3
    assert header["text"] == "审批矩阵"


def test_plain_paragraph_lines_do_not_become_borderless_table() -> None:
    provider = TextAlignedVisualTableProvider(word_provider=_ParagraphWords())
    assert provider.detect(_rendered(_blank_png(), width=320, height=220)) == []


def test_existing_visual_table_adapter_accepts_borderless_provider_contract() -> None:
    source = DocumentSource("borderless", "审批矩阵.png", _blank_png())
    adapter = VisualTableSupplementalAdapter(
        provider=TextAlignedVisualTableProvider(word_provider=_BorderlessWords()),
        renderer_registry=PageRendererRegistry([_StaticRenderer(source.data)]),
        ocr_provider=_CellOcr(),
    )
    ir = adapter.extract(source)
    table = next(row for row in ir["blocks"] if row.get("type") == "TABLE")
    cells = [row for row in ir["blocks"] if row.get("type") == "TABLE_CELL"]
    assert table["formal_table_structure"] is True
    assert len(cells) == 10
    assert any(int(row.get("column_span") or 1) == 3 for row in cells)
    assert ir["structure_receipt"]["formal_table_count"] == 1


def test_composite_provider_prefers_ruled_observation_for_same_region() -> None:
    class _FixedProvider:
        def __init__(self, name: str, confidence: float, method: str) -> None:
            self.name = name
            self.version = "test"
            self.confidence = confidence
            self.method = method

        def available(self) -> bool:
            return True

        def detect(self, rendered_page: RenderedPage, **kwargs: Any) -> list[dict[str, Any]]:
            return [
                {
                    "bbox": [20, 20, 280, 180],
                    "cells": [],
                    "row_count": 3,
                    "column_count": 3,
                    "confidence": self.confidence,
                    "geometry_formal": True,
                    "target_region_id": "region-1",
                    "detection_method": self.method,
                }
            ]

    provider = CompositeVisualTableProvider(
        [
            _FixedProvider("borderless", 0.92, "borderless_repeated_word_box_column_alignment"),
            _FixedProvider("ruled", 0.92, "ruled_grid_pixel_line_intersections"),
        ]
    )
    tables = provider.detect(_rendered(_blank_png()))
    assert len(tables) == 1
    assert tables[0]["contributing_provider"] == "ruled"
    assert len(tables[0]["alternative_provider_observations"]) == 1


def test_default_registry_uses_composite_visual_table_provider() -> None:
    adapter = build_default_registry().get("visual-table-structure")
    assert adapter.provider.name == "composite-visual-table-provider"
    provider_names = [provider.name for provider in adapter.provider.providers]
    assert provider_names == [
        "merged-cell-ruled-grid-visual-table-provider",
        "text-aligned-borderless-visual-table-provider",
    ]
