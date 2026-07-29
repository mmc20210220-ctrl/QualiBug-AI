from __future__ import annotations

import io
from typing import Any, Iterable

from PIL import Image

from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    DocumentSource,
    GeometryFormalEnforcingVisualTableProvider,
    PageRendererRegistry,
    RenderedPage,
    VisualTableSupplementalAdapter,
)


def _png() -> bytes:
    image = Image.new("RGB", (200, 120), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class _RejectedGeometryProvider:
    name = "rejected-geometry-provider"
    version = "test"

    def available(self) -> bool:
        return True

    def detect(self, rendered_page: RenderedPage, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "bbox": [10, 10, 190, 110],
                "row_count": 2,
                "column_count": 2,
                "confidence": 0.95,
                "geometry_formal": False,
                "detection_method": "alignment_below_provider_gate",
                "cells": [
                    {
                        "row_index": row,
                        "column_index": column,
                        "row_span": 1,
                        "column_span": 1,
                        "bbox": [10 + column * 90, 10 + row * 50, 100 + column * 90, 60 + row * 50],
                        "border_complete": True,
                        "border_support": {"alignment": 0.9},
                    }
                    for row in range(2)
                    for column in range(2)
                ],
            }
        ]


class _Renderer:
    name = "geometry-gate-renderer"
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
                image_bytes=source.data,
                width_px=200,
                height_px=120,
                source_locator=f"{source.filename}#page=1",
                renderer_name=self.name,
                renderer_version=self.version,
                render_method="test",
            )
        ]


class _Ocr:
    name = "geometry-gate-ocr"
    version = "test"

    def available(self) -> bool:
        return True

    def recognize(self, image_bytes: bytes, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "text": "规则",
                "bbox": [1, 1, 20, 10],
                "confidence": 0.99,
                "image_width_px": 90,
                "image_height_px": 50,
            }
        ]


def test_provider_geometry_rejection_cannot_be_overridden_by_adapter_threshold() -> None:
    provider = GeometryFormalEnforcingVisualTableProvider(_RejectedGeometryProvider())
    raw = provider.detect(
        RenderedPage(
            page=1,
            image_index=0,
            image_bytes=_png(),
            width_px=200,
            height_px=120,
            source_locator="gate.png#page=1",
            renderer_name="test",
            renderer_version="test",
            render_method="test",
        )
    )
    assert all(cell["border_complete"] is False for cell in raw[0]["cells"])
    assert raw[0]["provider_geometry_gate_enforced"] is True

    source = DocumentSource("gate", "gate.png", _png())
    adapter = VisualTableSupplementalAdapter(
        provider=provider,
        renderer_registry=PageRendererRegistry([_Renderer()]),
        ocr_provider=_Ocr(),
        minimum_table_confidence=0.72,
    )
    ir = adapter.extract(source)
    table = next(row for row in ir["blocks"] if row.get("type") == "TABLE")
    assert table["formal_table_structure"] is False
    assert any(
        row.get("reason_code") == "VISUAL_TABLE_MERGED_CELL_OR_BORDER_UNRESOLVED"
        for row in ir["unsupported_content"]
    )
