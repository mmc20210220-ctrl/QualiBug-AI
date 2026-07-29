"""Rendered-page visual table recovery for enterprise document ingestion.

The adapter consumes the shared ``RenderedPage`` contract and emits source-preserving
``TABLE -> TABLE_ROW -> TABLE_CELL`` Document IR.  It never infers business meaning and
never clears a native table-region gap unless every target region on a page is recovered
with sufficiently reliable geometry and cell text.
"""
from __future__ import annotations

import hashlib
import io
import math
import zipfile
from collections import Counter, defaultdict, deque
from typing import Any, Iterable, Protocol

from .._document_structure_ir import DOCUMENT_IR_SCHEMA, STRUCTURE_RECEIPT_SCHEMA
from .contract import (
    AdapterMatch,
    CAP_PAGE_RENDERING,
    CAP_TABLE_REGION_DETECTION,
    CAP_TABLE_STRUCTURE,
    CAP_TEXT_COORDINATES,
    DocumentAdapter,
    DocumentSource,
    MODE_SUPPLEMENTAL,
    SupplementalContext,
    text,
)
from .ocr_adapter import OcrProvider, TesseractOcrProvider
from .page_render_registry import PageRendererRegistry, build_default_page_renderer_registry
from .page_rendering import PageRenderBatch, RenderedPage


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _clamp_bbox(bbox: Iterable[Any], width: int, height: int) -> list[int]:
    values = list(bbox or [])
    if len(values) != 4:
        return [0, 0, max(0, width), max(0, height)]
    try:
        x0, y0, x1, y1 = (int(round(float(value))) for value in values)
    except (TypeError, ValueError):
        return [0, 0, max(0, width), max(0, height)]
    left = max(0, min(width, min(x0, x1)))
    right = max(0, min(width, max(x0, x1)))
    top = max(0, min(height, min(y0, y1)))
    bottom = max(0, min(height, max(y0, y1)))
    return [left, top, right, bottom]


def _is_spreadsheet_container(source: DocumentSource) -> bool:
    if source.suffix in {".xls", ".xlsx", ".xlsm", ".xlsb", ".ods"}:
        return True
    if not source.data.startswith(b"PK"):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(source.data)) as archive:
            return "xl/workbook.xml" in set(archive.namelist())
    except Exception:
        return False


def _page_dimensions(primary_ir: dict[str, Any]) -> dict[int, tuple[float, float]]:
    dimensions: dict[int, tuple[float, float]] = {}
    for row in _list(primary_ir.get("pages")):
        if not isinstance(row, dict):
            continue
        try:
            page = int(row.get("page") or 0)
            width = float(row.get("width_pt") or 0.0)
            height = float(row.get("height_pt") or 0.0)
        except (TypeError, ValueError):
            continue
        if page > 0 and width > 0 and height > 0:
            dimensions[page] = (width, height)
    return dimensions


def _pdf_bbox_to_pixels(
    bbox: Iterable[Any],
    *,
    page_width_pt: float,
    page_height_pt: float,
    image_width_px: int,
    image_height_px: int,
) -> list[int]:
    values = list(bbox or [])
    if len(values) != 4 or page_width_pt <= 0 or page_height_pt <= 0:
        return [0, 0, image_width_px, image_height_px]
    try:
        x0, y0, x1, y1 = (float(value) for value in values)
    except (TypeError, ValueError):
        return [0, 0, image_width_px, image_height_px]
    scale_x = image_width_px / page_width_pt
    scale_y = image_height_px / page_height_pt
    # PDF coordinates use a bottom-left origin; rendered images use a top-left origin.
    return _clamp_bbox(
        [
            x0 * scale_x,
            (page_height_pt - y1) * scale_y,
            x1 * scale_x,
            (page_height_pt - y0) * scale_y,
        ],
        image_width_px,
        image_height_px,
    )


class VisualTableProvider(Protocol):
    name: str
    version: str

    def available(self) -> bool:
        ...

    def detect(
        self,
        rendered_page: RenderedPage,
        *,
        region_bbox: list[int] | None = None,
        target_region_id: str = "",
    ) -> list[dict[str, Any]]:
        """Return table geometries in rendered-page pixel coordinates."""
        ...


class RuledGridVisualTableProvider:
    """Detect bordered table grids using content pixels only.

    This built-in provider deliberately covers ruled tables and decision matrices.  It does
    not claim borderless-table, merged-cell or semantic-header understanding.  More capable
    ML providers can implement the same protocol and register without changing the adapter.
    """

    name = "ruled-grid-visual-table-provider"
    version = "1"

    def __init__(
        self,
        *,
        darkness_threshold: int = 130,
        minimum_line_ratio: float = 0.28,
        minimum_border_support: float = 0.58,
        maximum_detection_dimension: int = 2400,
        minimum_cell_size_px: int = 8,
    ) -> None:
        self.darkness_threshold = max(0, min(255, int(darkness_threshold)))
        self.minimum_line_ratio = max(0.08, min(0.95, float(minimum_line_ratio)))
        self.minimum_border_support = max(0.2, min(0.98, float(minimum_border_support)))
        self.maximum_detection_dimension = max(480, min(6000, int(maximum_detection_dimension)))
        self.minimum_cell_size_px = max(3, int(minimum_cell_size_px))

    def available(self) -> bool:
        try:
            from PIL import Image  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def _longest_run(values: list[bool]) -> tuple[int, int, int]:
        best_length = 0
        best_start = 0
        best_end = 0
        current_start = 0
        current_length = 0
        for index, active in enumerate(values):
            if active:
                if current_length == 0:
                    current_start = index
                current_length += 1
                if current_length > best_length:
                    best_length = current_length
                    best_start = current_start
                    best_end = index + 1
            else:
                current_length = 0
        return best_length, best_start, best_end

    @staticmethod
    def _cluster_lines(rows: list[dict[str, Any]], tolerance: int = 2) -> list[dict[str, Any]]:
        if not rows:
            return []
        ordered = sorted(rows, key=lambda row: int(row["position"]))
        groups: list[list[dict[str, Any]]] = [[ordered[0]]]
        for row in ordered[1:]:
            if int(row["position"]) - int(groups[-1][-1]["position"]) <= tolerance:
                groups[-1].append(row)
            else:
                groups.append([row])
        result: list[dict[str, Any]] = []
        for group in groups:
            result.append(
                {
                    "position": int(round(sum(int(row["position"]) for row in group) / len(group))),
                    "start": min(int(row["start"]) for row in group),
                    "end": max(int(row["end"]) for row in group),
                    "support": round(max(float(row["support"]) for row in group), 4),
                }
            )
        return result

    @staticmethod
    def _line_support(
        dark: list[bool],
        width: int,
        height: int,
        *,
        orientation: str,
        position: int,
        start: int,
        end: int,
        tolerance: int = 1,
    ) -> float:
        start = max(0, start)
        if orientation == "horizontal":
            end = min(width, end)
            if end <= start:
                return 0.0
            supported = 0
            for x in range(start, end):
                if any(
                    0 <= y < height and dark[y * width + x]
                    for y in range(position - tolerance, position + tolerance + 1)
                ):
                    supported += 1
            return supported / max(1, end - start)
        end = min(height, end)
        if end <= start:
            return 0.0
        supported = 0
        for y in range(start, end):
            if any(
                0 <= x < width and dark[y * width + x]
                for x in range(position - tolerance, position + tolerance + 1)
            ):
                supported += 1
        return supported / max(1, end - start)

    def _line_candidates(
        self, dark: list[bool], width: int, height: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        horizontal: list[dict[str, Any]] = []
        vertical: list[dict[str, Any]] = []
        minimum_horizontal = max(24, int(width * self.minimum_line_ratio))
        minimum_vertical = max(24, int(height * self.minimum_line_ratio))
        for y in range(height):
            values = dark[y * width : (y + 1) * width]
            length, start, end = self._longest_run(values)
            if length >= minimum_horizontal:
                horizontal.append(
                    {
                        "position": y,
                        "start": start,
                        "end": end,
                        "support": length / max(1, width),
                    }
                )
        for x in range(width):
            values = [dark[y * width + x] for y in range(height)]
            length, start, end = self._longest_run(values)
            if length >= minimum_vertical:
                vertical.append(
                    {
                        "position": x,
                        "start": start,
                        "end": end,
                        "support": length / max(1, height),
                    }
                )
        return self._cluster_lines(horizontal), self._cluster_lines(vertical)

    @staticmethod
    def _components(
        horizontal: list[dict[str, Any]], vertical: list[dict[str, Any]]
    ) -> list[tuple[list[dict[str, Any]], list[dict[str, Any]]]]:
        adjacency: dict[tuple[str, int], set[tuple[str, int]]] = defaultdict(set)
        for h_index, h_line in enumerate(horizontal):
            y = int(h_line["position"])
            for v_index, v_line in enumerate(vertical):
                x = int(v_line["position"])
                if (
                    int(h_line["start"]) - 3 <= x <= int(h_line["end"]) + 3
                    and int(v_line["start"]) - 3 <= y <= int(v_line["end"]) + 3
                ):
                    h_node = ("h", h_index)
                    v_node = ("v", v_index)
                    adjacency[h_node].add(v_node)
                    adjacency[v_node].add(h_node)
        components: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]] = []
        seen: set[tuple[str, int]] = set()
        for node in adjacency:
            if node in seen:
                continue
            queue: deque[tuple[str, int]] = deque([node])
            seen.add(node)
            h_rows: list[dict[str, Any]] = []
            v_rows: list[dict[str, Any]] = []
            while queue:
                current = queue.popleft()
                kind, index = current
                (h_rows if kind == "h" else v_rows).append(
                    horizontal[index] if kind == "h" else vertical[index]
                )
                for neighbor in adjacency.get(current, set()):
                    if neighbor not in seen:
                        seen.add(neighbor)
                        queue.append(neighbor)
            if len(h_rows) >= 2 and len(v_rows) >= 2:
                components.append((h_rows, v_rows))
        return components

    def detect(
        self,
        rendered_page: RenderedPage,
        *,
        region_bbox: list[int] | None = None,
        target_region_id: str = "",
    ) -> list[dict[str, Any]]:
        if not self.available():
            raise RuntimeError("Pillow is required for ruled visual table detection")
        from PIL import Image, ImageOps

        image = Image.open(io.BytesIO(rendered_page.image_bytes)).convert("RGB")
        full_width, full_height = int(image.width), int(image.height)
        crop_box = _clamp_bbox(region_bbox or [0, 0, full_width, full_height], full_width, full_height)
        if crop_box[2] - crop_box[0] < 20 or crop_box[3] - crop_box[1] < 20:
            return []
        crop = ImageOps.grayscale(image.crop(tuple(crop_box)))
        original_crop_width, original_crop_height = int(crop.width), int(crop.height)
        scale = min(
            1.0,
            self.maximum_detection_dimension / max(original_crop_width, original_crop_height),
        )
        if scale < 1.0:
            crop = crop.resize(
                (
                    max(1, int(round(original_crop_width * scale))),
                    max(1, int(round(original_crop_height * scale))),
                )
            )
        width, height = int(crop.width), int(crop.height)
        pixels = list(crop.getdata())
        dark = [int(value) <= self.darkness_threshold for value in pixels]
        horizontal, vertical = self._line_candidates(dark, width, height)
        components = self._components(horizontal, vertical)
        inverse_scale = 1.0 / scale
        tables: list[dict[str, Any]] = []
        for component_index, (h_rows, v_rows) in enumerate(components, start=1):
            ys = sorted({int(row["position"]) for row in h_rows})
            xs = sorted({int(row["position"]) for row in v_rows})
            if len(ys) < 2 or len(xs) < 2:
                continue
            cells: list[dict[str, Any]] = []
            border_support_values: list[float] = []
            complete_cells = 0
            for row_index, (top, bottom) in enumerate(zip(ys, ys[1:])):
                if bottom - top < self.minimum_cell_size_px:
                    continue
                for column_index, (left, right) in enumerate(zip(xs, xs[1:])):
                    if right - left < self.minimum_cell_size_px:
                        continue
                    supports = {
                        "top": self._line_support(
                            dark,
                            width,
                            height,
                            orientation="horizontal",
                            position=top,
                            start=left,
                            end=right,
                        ),
                        "bottom": self._line_support(
                            dark,
                            width,
                            height,
                            orientation="horizontal",
                            position=bottom,
                            start=left,
                            end=right,
                        ),
                        "left": self._line_support(
                            dark,
                            width,
                            height,
                            orientation="vertical",
                            position=left,
                            start=top,
                            end=bottom,
                        ),
                        "right": self._line_support(
                            dark,
                            width,
                            height,
                            orientation="vertical",
                            position=right,
                            start=top,
                            end=bottom,
                        ),
                    }
                    minimum_support = min(supports.values())
                    border_complete = minimum_support >= self.minimum_border_support
                    complete_cells += int(border_complete)
                    border_support_values.extend(supports.values())
                    full_bbox = [
                        crop_box[0] + int(round(left * inverse_scale)),
                        crop_box[1] + int(round(top * inverse_scale)),
                        crop_box[0] + int(round(right * inverse_scale)),
                        crop_box[1] + int(round(bottom * inverse_scale)),
                    ]
                    cells.append(
                        {
                            "row_index": row_index,
                            "column_index": column_index,
                            "row_span": 1,
                            "column_span": 1,
                            "bbox": _clamp_bbox(full_bbox, full_width, full_height),
                            "border_complete": border_complete,
                            "border_support": {key: round(value, 4) for key, value in supports.items()},
                        }
                    )
            if not cells:
                continue
            table_bbox = _clamp_bbox(
                [
                    crop_box[0] + int(round(min(xs) * inverse_scale)),
                    crop_box[1] + int(round(min(ys) * inverse_scale)),
                    crop_box[0] + int(round(max(xs) * inverse_scale)),
                    crop_box[1] + int(round(max(ys) * inverse_scale)),
                ],
                full_width,
                full_height,
            )
            complete_ratio = complete_cells / max(1, len(cells))
            mean_support = sum(border_support_values) / max(1, len(border_support_values))
            confidence = max(0.0, min(1.0, mean_support * 0.65 + complete_ratio * 0.35))
            tables.append(
                {
                    "provider_table_index": component_index,
                    "bbox": table_bbox,
                    "row_count": max((int(cell["row_index"]) for cell in cells), default=-1) + 1,
                    "column_count": max((int(cell["column_index"]) for cell in cells), default=-1) + 1,
                    "cells": cells,
                    "confidence": round(confidence, 4),
                    "complete_cell_border_ratio": round(complete_ratio, 4),
                    "target_region_id": target_region_id,
                    "detection_method": "ruled_grid_pixel_line_intersections",
                }
            )
        return tables


class VisualTableSupplementalAdapter(DocumentAdapter):
    """Recover visual table geometry and cell text from shared rendered pages."""

    name = "visual-table-structure"
    parser_version = "1"
    priority = 80
    mode = MODE_SUPPLEMENTAL
    standalone = True
    capabilities = frozenset(
        {
            CAP_PAGE_RENDERING,
            CAP_TABLE_REGION_DETECTION,
            CAP_TABLE_STRUCTURE,
            CAP_TEXT_COORDINATES,
        }
    )

    def __init__(
        self,
        provider: VisualTableProvider | None = None,
        *,
        renderer_registry: PageRendererRegistry | None = None,
        ocr_provider: OcrProvider | None = None,
        minimum_table_confidence: float = 0.72,
        minimum_cell_text_confidence: float = 0.55,
        maximum_ocr_cells: int = 240,
    ) -> None:
        self.provider = provider or RuledGridVisualTableProvider()
        self.renderer_registry = renderer_registry or build_default_page_renderer_registry()
        self.ocr_provider = ocr_provider or TesseractOcrProvider()
        self.minimum_table_confidence = max(0.0, min(1.0, float(minimum_table_confidence)))
        self.minimum_cell_text_confidence = max(
            0.0, min(1.0, float(minimum_cell_text_confidence))
        )
        self.maximum_ocr_cells = max(1, int(maximum_ocr_cells))

    def _can_process(self, source: DocumentSource) -> bool:
        return (
            not _is_spreadsheet_container(source)
            and self.provider.available()
            and self.renderer_registry.can_render(source)
        )

    def probe(self, source: DocumentSource) -> AdapterMatch | None:
        if not self._can_process(source):
            return None
        return AdapterMatch(
            self.name,
            104,
            "source_can_be_rendered_for_visual_table_detection",
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    def probe_supplemental(
        self,
        source: DocumentSource,
        context: SupplementalContext,
    ) -> AdapterMatch | None:
        if not self._can_process(source):
            return None
        reasons = {
            text(row.get("reason_code") or row.get("kind"))
            for row in context.trigger_gaps
            if isinstance(row, dict)
        }
        supported_reasons = {
            "PDF_TABLE_REGION_NOT_CELL_PARSED",
            "SCANNED_PAGE_REQUIRES_OCR",
        }
        selected = sorted(reasons & supported_reasons)
        if not selected:
            return None
        return AdapterMatch(
            self.name,
            116,
            "primary_adapter_reported_visual_table_targets:" + ",".join(selected),
            tuple(sorted(self.capabilities)),
            self.mode,
        )

    @staticmethod
    def _target_pages(context: SupplementalContext) -> list[int]:
        pages: set[int] = set()
        for gap in context.trigger_gaps:
            if not isinstance(gap, dict):
                continue
            reason = text(gap.get("reason_code") or gap.get("kind"))
            if reason not in {"PDF_TABLE_REGION_NOT_CELL_PARSED", "SCANNED_PAGE_REQUIRES_OCR"}:
                continue
            for value in _list(gap.get("pages")):
                try:
                    page = int(value)
                except (TypeError, ValueError):
                    continue
                if page > 0:
                    pages.add(page)
        if pages:
            return sorted(pages)
        for table in _list(context.primary_document_ir.get("tables")):
            if not isinstance(table, dict):
                continue
            try:
                page = int(table.get("page") or 0)
            except (TypeError, ValueError):
                continue
            if page > 0:
                pages.add(page)
        return sorted(pages)

    @staticmethod
    def _target_regions(primary_ir: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
        regions: dict[int, list[dict[str, Any]]] = defaultdict(list)
        candidates = [
            row
            for row in [*_list(primary_ir.get("tables")), *_list(primary_ir.get("blocks"))]
            if isinstance(row, dict) and text(row.get("type")) == "TABLE_REGION"
        ]
        seen: set[str] = set()
        for row in candidates:
            region_id = text(row.get("block_id") or row.get("source_locator"))
            if not region_id or region_id in seen:
                continue
            seen.add(region_id)
            try:
                page = int(row.get("page") or 0)
            except (TypeError, ValueError):
                continue
            if page <= 0:
                continue
            regions[page].append(
                {
                    "region_id": region_id,
                    "bbox": list(row.get("bbox") or []),
                    "source_locator": row.get("source_locator"),
                }
            )
        return regions

    def _cell_text(
        self,
        source: DocumentSource,
        rendered: RenderedPage,
        bbox: list[int],
        *,
        cell_index: int,
    ) -> tuple[str, float, list[dict[str, Any]], str]:
        if not self.ocr_provider.available():
            return "", 0.0, [], "OCR_PROVIDER_UNAVAILABLE"
        from PIL import Image

        image = Image.open(io.BytesIO(rendered.image_bytes)).convert("RGB")
        left, top, right, bottom = _clamp_bbox(bbox, image.width, image.height)
        inset = 2
        left = min(right, left + inset)
        top = min(bottom, top + inset)
        right = max(left, right - inset)
        bottom = max(top, bottom - inset)
        if right - left < 2 or bottom - top < 2:
            return "", 0.0, [], "CELL_CROP_EMPTY"
        crop = image.crop((left, top, right, bottom))
        buffer = io.BytesIO()
        crop.save(buffer, format="PNG")
        try:
            regions = self.ocr_provider.recognize(
                buffer.getvalue(),
                source_id=source.source_id,
                filename=source.filename,
                page=rendered.page,
                image_index=cell_index,
            )
        except Exception as exc:
            return "", 0.0, [], f"{type(exc).__name__}: {exc}"[:300]
        values = [text(row.get("text")) for row in regions if isinstance(row, dict) and text(row.get("text"))]
        confidences = [
            float(row.get("confidence") or 0.0)
            for row in regions
            if isinstance(row, dict) and text(row.get("text"))
        ]
        value = "\n".join(values)
        confidence = sum(confidences) / len(confidences) if confidences else 0.0
        return value, round(confidence, 4), [dict(row) for row in regions if isinstance(row, dict)], ""

    def _build_ir(
        self,
        source: DocumentSource,
        batch: PageRenderBatch,
        *,
        primary_ir: dict[str, Any] | None = None,
        target_pages: list[int] | None = None,
        format_name: str,
    ) -> dict[str, Any]:
        primary = dict(primary_ir or {})
        requested_pages = sorted({int(page) for page in (target_pages or []) if int(page) > 0})
        page_dimensions = _page_dimensions(primary)
        target_regions = self._target_regions(primary)
        blocks: list[dict[str, Any]] = []
        tables: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []
        provider_errors: list[dict[str, Any]] = []
        region_results: dict[str, list[dict[str, Any]]] = defaultdict(list)
        page_table_counts: Counter[int] = Counter()
        table_confidences: list[float] = []
        cell_text_confidences: list[float] = []
        cell_ocr_attempted = 0
        cell_ocr_error_count = 0
        cell_index = 0

        if not batch.pages:
            unsupported.append(
                {
                    "kind": "PAGE_RENDERER_UNAVAILABLE_OR_FAILED",
                    "reason_code": "PAGE_RENDERER_UNAVAILABLE_OR_FAILED",
                    "count": max(1, len(requested_pages)),
                    "pages": requested_pages,
                    "status": "VISUAL_TABLE_SOURCE_COULD_NOT_BE_RENDERED",
                    "severity": "P0",
                    "blocks_formal_understanding": True,
                    "included_in_plain_text_authority": False,
                    "render_receipt": batch.receipt,
                }
            )

        for rendered in batch.pages:
            page_regions = target_regions.get(rendered.page) or []
            full_page_required = bool(page_regions)
            if full_page_required and "not_full_page" in text(rendered.render_method):
                unsupported.append(
                    {
                        "kind": "VISUAL_TABLE_FULL_PAGE_RENDER_REQUIRED",
                        "reason_code": "VISUAL_TABLE_FULL_PAGE_RENDER_REQUIRED",
                        "count": len(page_regions),
                        "pages": [rendered.page],
                        "status": "EMBEDDED_IMAGE_FALLBACK_CANNOT_MAP_PDF_TABLE_REGION",
                        "severity": "P1",
                        "blocks_formal_understanding": False,
                        "included_in_plain_text_authority": False,
                    }
                )
                continue
            jobs = page_regions or [{"region_id": "", "bbox": [], "source_locator": ""}]
            for job in jobs:
                region_id = text(job.get("region_id"))
                region_bbox: list[int] | None = None
                if region_id:
                    page_size = page_dimensions.get(rendered.page)
                    if page_size:
                        region_bbox = _pdf_bbox_to_pixels(
                            job.get("bbox") or [],
                            page_width_pt=page_size[0],
                            page_height_pt=page_size[1],
                            image_width_px=rendered.width_px,
                            image_height_px=rendered.height_px,
                        )
                try:
                    detected = self.provider.detect(
                        rendered,
                        region_bbox=region_bbox,
                        target_region_id=region_id,
                    )
                except Exception as exc:
                    provider_errors.append(
                        {
                            "page": rendered.page,
                            "target_region_id": region_id,
                            "detail": f"{type(exc).__name__}: {exc}"[:500],
                        }
                    )
                    continue
                if region_id:
                    region_results[region_id].extend(detected)
                for detected_table in detected:
                    table_number = len(tables) + 1
                    table_bbox = _clamp_bbox(
                        detected_table.get("bbox") or [],
                        rendered.width_px,
                        rendered.height_px,
                    )
                    table_block_id = _stable_id(
                        "visual_table",
                        source.source_id,
                        rendered.page,
                        rendered.image_index,
                        table_number,
                        table_bbox,
                        region_id,
                    )
                    table_confidence = float(detected_table.get("confidence") or 0.0)
                    table_confidences.append(table_confidence)
                    raw_cells = [
                        dict(row)
                        for row in _list(detected_table.get("cells"))
                        if isinstance(row, dict)
                    ]
                    table_block = {
                        "block_id": table_block_id,
                        "type": "TABLE",
                        "parent_id": "",
                        "page": rendered.page,
                        "order": 0,
                        "region": "body",
                        "bbox": table_bbox,
                        "text": "",
                        "excluded_from_main_flow": True,
                        "source_locator": (
                            f"{source.filename or 'visual-source'}#page={rendered.page};"
                            f"visual_table={table_number};bbox={','.join(str(value) for value in table_bbox)}"
                        ),
                        "formal_table_structure": False,
                        "structure_evidence": {
                            "provider": self.provider.name,
                            "provider_version": self.provider.version,
                            "confidence": round(table_confidence, 4),
                            "detection_method": detected_table.get("detection_method"),
                            "target_region_id": region_id,
                            "page_rendering": rendered.evidence(),
                            "coordinate_system": "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE",
                            "business_semantics_added": False,
                        },
                    }
                    blocks.append(table_block)
                    row_blocks: dict[int, dict[str, Any]] = {}
                    cell_blocks: list[dict[str, Any]] = []
                    table_text_confidences: list[float] = []
                    table_text_values: list[str] = []
                    border_complete = all(bool(row.get("border_complete")) for row in raw_cells)
                    ocr_limit_exceeded = len(raw_cells) > self.maximum_ocr_cells
                    for raw_cell in sorted(
                        raw_cells,
                        key=lambda row: (
                            int(row.get("row_index") or 0),
                            int(row.get("column_index") or 0),
                        ),
                    ):
                        row_index = int(raw_cell.get("row_index") or 0)
                        column_index = int(raw_cell.get("column_index") or 0)
                        if row_index not in row_blocks:
                            row_block_id = _stable_id("visual_table_row", table_block_id, row_index)
                            row_block = {
                                "block_id": row_block_id,
                                "type": "TABLE_ROW",
                                "parent_id": table_block_id,
                                "page": rendered.page,
                                "order": 0,
                                "region": "body",
                                "row_index": row_index,
                                "text": "",
                                "excluded_from_main_flow": True,
                                "source_locator": f"{table_block['source_locator']};row={row_index}",
                            }
                            row_blocks[row_index] = row_block
                            blocks.append(row_block)
                        cell_index += 1
                        cell_bbox = _clamp_bbox(
                            raw_cell.get("bbox") or [],
                            rendered.width_px,
                            rendered.height_px,
                        )
                        value = ""
                        confidence = 0.0
                        ocr_regions: list[dict[str, Any]] = []
                        ocr_error = ""
                        if not ocr_limit_exceeded:
                            cell_ocr_attempted += 1
                            value, confidence, ocr_regions, ocr_error = self._cell_text(
                                source,
                                rendered,
                                cell_bbox,
                                cell_index=cell_index,
                            )
                        if ocr_error:
                            cell_ocr_error_count += 1
                        if value:
                            table_text_values.append(value)
                            table_text_confidences.append(confidence)
                            cell_text_confidences.append(confidence)
                        cell_block_id = _stable_id(
                            "visual_table_cell",
                            table_block_id,
                            row_index,
                            column_index,
                            cell_bbox,
                        )
                        cell_block = {
                            "block_id": cell_block_id,
                            "type": "TABLE_CELL",
                            "parent_id": row_blocks[row_index]["block_id"],
                            "table_block_id": table_block_id,
                            "page": rendered.page,
                            "order": 0,
                            "region": "body",
                            "row_index": row_index,
                            "column_index": column_index,
                            "row_span": int(raw_cell.get("row_span") or 1),
                            "column_span": int(raw_cell.get("column_span") or 1),
                            "bbox": cell_bbox,
                            "text": value,
                            "source_locator": (
                                f"{table_block['source_locator']};row={row_index};column={column_index};"
                                f"bbox={','.join(str(item) for item in cell_bbox)}"
                            ),
                            "structure_evidence": {
                                "provider": self.provider.name,
                                "provider_version": self.provider.version,
                                "border_complete": bool(raw_cell.get("border_complete")),
                                "border_support": dict(raw_cell.get("border_support") or {}),
                                "ocr_provider": self.ocr_provider.name,
                                "ocr_provider_version": self.ocr_provider.version,
                                "ocr_confidence": round(confidence, 4),
                                "ocr_regions": ocr_regions,
                                "ocr_error": ocr_error,
                                "page_rendering": rendered.evidence(),
                                "coordinate_system": "IMAGE_PIXELS_LOCAL_TO_RENDERED_PAGE",
                                "business_semantics_added": False,
                            },
                        }
                        cell_blocks.append(cell_block)
                        blocks.append(cell_block)
                    for row_index, row_block in row_blocks.items():
                        row_cells = sorted(
                            [row for row in cell_blocks if int(row.get("row_index") or 0) == row_index],
                            key=lambda row: int(row.get("column_index") or 0),
                        )
                        row_block["text"] = "\t".join(text(row.get("text")) for row in row_cells)
                    mean_cell_text_confidence = (
                        sum(table_text_confidences) / len(table_text_confidences)
                        if table_text_confidences
                        else 0.0
                    )
                    text_formal = bool(table_text_values) and (
                        mean_cell_text_confidence >= self.minimum_cell_text_confidence
                    )
                    geometry_formal = (
                        table_confidence >= self.minimum_table_confidence and border_complete
                    )
                    formal = geometry_formal and text_formal and not ocr_limit_exceeded
                    table_block["formal_table_structure"] = formal
                    table_block["structure_evidence"]["geometry_formal"] = geometry_formal
                    table_block["structure_evidence"]["cell_text_formal"] = text_formal
                    table_block["structure_evidence"]["mean_cell_text_confidence"] = round(
                        mean_cell_text_confidence, 4
                    )
                    table_block["structure_evidence"]["cell_count"] = len(cell_blocks)
                    table_block["structure_evidence"]["cell_text_count"] = len(table_text_values)
                    page_table_counts[rendered.page] += 1
                    tables.append(
                        {
                            "block_id": table_block_id,
                            "type": "TABLE",
                            "page": rendered.page,
                            "bbox": table_bbox,
                            "row_count": int(detected_table.get("row_count") or len(row_blocks)),
                            "column_count": int(detected_table.get("column_count") or 0),
                            "cell_count": len(cell_blocks),
                            "cell_block_ids": [row["block_id"] for row in cell_blocks],
                            "confidence": round(table_confidence, 4),
                            "mean_cell_text_confidence": round(mean_cell_text_confidence, 4),
                            "formal_table_structure": formal,
                            "target_region_id": region_id,
                            "source_locator": table_block["source_locator"],
                        }
                    )
                    if ocr_limit_exceeded:
                        unsupported.append(
                            {
                                "kind": "VISUAL_TABLE_CELL_LIMIT_EXCEEDED",
                                "reason_code": "VISUAL_TABLE_CELL_LIMIT_EXCEEDED",
                                "count": len(raw_cells),
                                "pages": [rendered.page],
                                "status": "TABLE_TOO_LARGE_FOR_CELL_OCR_LIMIT",
                                "severity": "P0",
                                "blocks_formal_understanding": True,
                                "included_in_plain_text_authority": False,
                                "maximum_ocr_cells": self.maximum_ocr_cells,
                                "table_block_id": table_block_id,
                            }
                        )
                    elif not border_complete:
                        unsupported.append(
                            {
                                "kind": "VISUAL_TABLE_MERGED_CELL_OR_BORDER_UNRESOLVED",
                                "reason_code": "VISUAL_TABLE_MERGED_CELL_OR_BORDER_UNRESOLVED",
                                "count": 1,
                                "pages": [rendered.page],
                                "status": "GRID_CONTAINS_INCOMPLETE_CELL_BORDERS",
                                "severity": "P1",
                                "blocks_formal_understanding": False,
                                "included_in_plain_text_authority": bool(table_text_values),
                                "table_block_id": table_block_id,
                            }
                        )
                    if table_confidence < self.minimum_table_confidence:
                        unsupported.append(
                            {
                                "kind": "VISUAL_TABLE_GRID_LOW_CONFIDENCE",
                                "reason_code": "VISUAL_TABLE_GRID_LOW_CONFIDENCE",
                                "count": 1,
                                "pages": [rendered.page],
                                "status": "TABLE_GEOMETRY_BELOW_FORMAL_THRESHOLD",
                                "severity": "P1",
                                "blocks_formal_understanding": False,
                                "included_in_plain_text_authority": bool(table_text_values),
                                "confidence": round(table_confidence, 4),
                                "minimum_confidence": self.minimum_table_confidence,
                                "table_block_id": table_block_id,
                            }
                        )
                    if not self.ocr_provider.available():
                        unsupported.append(
                            {
                                "kind": "VISUAL_TABLE_CELL_TEXT_UNAVAILABLE",
                                "reason_code": "VISUAL_TABLE_CELL_TEXT_UNAVAILABLE",
                                "count": len(cell_blocks),
                                "pages": [rendered.page],
                                "status": "TABLE_GRID_RECOVERED_BUT_CELL_OCR_PROVIDER_UNAVAILABLE",
                                "severity": "P0",
                                "blocks_formal_understanding": True,
                                "included_in_plain_text_authority": False,
                                "table_block_id": table_block_id,
                            }
                        )
                    elif not table_text_values:
                        unsupported.append(
                            {
                                "kind": "VISUAL_TABLE_CELL_TEXT_NOT_RECOVERED",
                                "reason_code": "VISUAL_TABLE_CELL_TEXT_NOT_RECOVERED",
                                "count": len(cell_blocks),
                                "pages": [rendered.page],
                                "status": "NO_CELL_TEXT_RECOVERED",
                                "severity": "P0",
                                "blocks_formal_understanding": True,
                                "included_in_plain_text_authority": False,
                                "table_block_id": table_block_id,
                            }
                        )
                    elif mean_cell_text_confidence < self.minimum_cell_text_confidence:
                        unsupported.append(
                            {
                                "kind": "VISUAL_TABLE_CELL_TEXT_LOW_CONFIDENCE",
                                "reason_code": "VISUAL_TABLE_CELL_TEXT_LOW_CONFIDENCE",
                                "count": len(table_text_values),
                                "pages": [rendered.page],
                                "status": "CELL_TEXT_BELOW_FORMAL_THRESHOLD",
                                "severity": "P0",
                                "blocks_formal_understanding": True,
                                "included_in_plain_text_authority": True,
                                "confidence": round(mean_cell_text_confidence, 4),
                                "minimum_confidence": self.minimum_cell_text_confidence,
                                "table_block_id": table_block_id,
                            }
                        )

        if batch.receipt.get("missing_pages"):
            unsupported.append(
                {
                    "kind": "PAGE_RENDERING_TARGET_PAGES_INCOMPLETE",
                    "reason_code": "PAGE_RENDERING_TARGET_PAGES_INCOMPLETE",
                    "count": len(batch.receipt.get("missing_pages") or []),
                    "pages": list(batch.receipt.get("missing_pages") or []),
                    "status": "SOME_VISUAL_TABLE_TARGET_PAGES_COULD_NOT_BE_RENDERED",
                    "severity": "P0",
                    "blocks_formal_understanding": True,
                    "included_in_plain_text_authority": False,
                    "render_receipt": batch.receipt,
                }
            )
        if provider_errors:
            unsupported.append(
                {
                    "kind": "VISUAL_TABLE_PROVIDER_EXECUTION_FAILED",
                    "reason_code": "VISUAL_TABLE_PROVIDER_EXECUTION_FAILED",
                    "count": len(provider_errors),
                    "pages": sorted({int(row.get("page") or 0) for row in provider_errors if int(row.get("page") or 0) > 0}),
                    "status": "VISUAL_TABLE_PROVIDER_FAILED_FOR_ONE_OR_MORE_TARGETS",
                    "severity": "P0" if target_regions else "P1",
                    "blocks_formal_understanding": bool(target_regions),
                    "included_in_plain_text_authority": False,
                    "details": provider_errors,
                }
            )

        unresolved_region_ids: list[str] = []
        resolved_pages: list[int] = []
        if target_regions:
            for page, regions in target_regions.items():
                page_resolved = True
                for region in regions:
                    region_id = text(region.get("region_id"))
                    candidates = region_results.get(region_id) or []
                    formal_candidates = [
                        row
                        for row in tables
                        if text(row.get("target_region_id")) == region_id
                        and bool(row.get("formal_table_structure"))
                    ]
                    if not candidates or not formal_candidates:
                        page_resolved = False
                        unresolved_region_ids.append(region_id)
                if page_resolved and regions:
                    resolved_pages.append(page)
            if unresolved_region_ids:
                unsupported.append(
                    {
                        "kind": "VISUAL_TABLE_STRUCTURE_NOT_RECOVERED",
                        "reason_code": "VISUAL_TABLE_STRUCTURE_NOT_RECOVERED",
                        "count": len(unresolved_region_ids),
                        "pages": sorted(
                            {
                                page
                                for page, regions in target_regions.items()
                                if any(text(row.get("region_id")) in unresolved_region_ids for row in regions)
                            }
                        ),
                        "region_ids": sorted(set(unresolved_region_ids)),
                        "status": "NATIVE_TABLE_REGIONS_REMAIN_WITHOUT_FORMAL_CELL_STRUCTURE",
                        "severity": "P1",
                        "blocks_formal_understanding": False,
                        "included_in_plain_text_authority": False,
                    }
                )

        resolves_gaps: list[dict[str, Any]] = []
        if resolved_pages:
            resolves_gaps.append(
                {
                    "reason_code": "PDF_TABLE_REGION_NOT_CELL_PARSED",
                    "pages": sorted(resolved_pages),
                    "resolution": "VISUAL_TABLE_CELL_STRUCTURE_RECOVERED",
                    "provider": self.provider.name,
                }
            )

        order = 0
        for block in blocks:
            order += 1
            block["order"] = order
        plain_text_rows: list[str] = []
        for table in tables:
            cell_blocks = [
                row
                for row in blocks
                if text(row.get("type")) == "TABLE_CELL"
                and text(row.get("table_block_id")) == text(table.get("block_id"))
            ]
            by_row: dict[int, list[dict[str, Any]]] = defaultdict(list)
            for cell in cell_blocks:
                by_row[int(cell.get("row_index") or 0)].append(cell)
            for row_index in sorted(by_row):
                values = [
                    text(cell.get("text"))
                    for cell in sorted(by_row[row_index], key=lambda cell: int(cell.get("column_index") or 0))
                ]
                if any(values):
                    plain_text_rows.append("\t".join(values))

        critical = [row for row in unsupported if bool(row.get("blocks_formal_understanding"))]
        status = "BLOCKED" if critical else "PARTIAL" if unsupported else "COMPLETE"
        block_counts = Counter(text(row.get("type")) for row in blocks)
        page_rows = []
        rendered_by_page: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for rendered in batch.pages:
            rendered_by_page[rendered.page].append(rendered.evidence())
        for page in sorted(set(rendered_by_page) | set(page_table_counts)):
            page_rows.append(
                {
                    "page": page,
                    "visual_table_count": int(page_table_counts.get(page) or 0),
                    "page_rendering": rendered_by_page.get(page) or [],
                    "visual_table_provider": self.provider.name,
                }
            )
        return {
            "schema": DOCUMENT_IR_SCHEMA,
            "format": format_name,
            "filename": source.filename,
            "plain_text": "\n".join(plain_text_rows),
            "blocks": blocks,
            "sections": [],
            "tables": tables,
            "pages": page_rows,
            "unsupported_content": unsupported,
            "resolves_gaps": resolves_gaps,
            "structure_receipt": {
                "schema": STRUCTURE_RECEIPT_SCHEMA,
                "status": status,
                "format": format_name,
                "page_count": len(page_rows),
                "block_count": len(blocks),
                "source_traceability_rate": 1.0 if blocks else 0.0,
                "block_type_distribution": dict(block_counts),
                "table_count": len(tables),
                "formal_table_count": sum(1 for row in tables if bool(row.get("formal_table_structure"))),
                "table_region_target_count": sum(len(rows) for rows in target_regions.values()),
                "resolved_table_region_page_count": len(resolved_pages),
                "unresolved_table_region_count": len(set(unresolved_region_ids)),
                "mean_table_confidence": round(sum(table_confidences) / len(table_confidences), 4) if table_confidences else 0.0,
                "mean_cell_text_confidence": round(sum(cell_text_confidences) / len(cell_text_confidences), 4) if cell_text_confidences else 0.0,
                "cell_ocr_attempted_count": cell_ocr_attempted,
                "cell_ocr_error_count": cell_ocr_error_count,
                "visual_table_provider": self.provider.name,
                "visual_table_provider_version": self.provider.version,
                "page_rendering_receipt": batch.receipt,
                "unsupported_content_count": sum(int(row.get("count") or 0) for row in unsupported),
                "critical_unsupported_content_count": sum(int(row.get("count") or 0) for row in critical),
                "unsupported_content": unsupported,
                "document_order_is_business_flow": False,
                "filename_is_business_context": False,
                "business_semantics_added": False,
            },
        }

    def extract(self, source: DocumentSource) -> dict[str, Any]:
        if not self._can_process(source):
            raise RuntimeError("visual table provider or page renderer is unavailable")
        batch = self.renderer_registry.render(source)
        return self._build_ir(
            source,
            batch,
            primary_ir={},
            target_pages=sorted({row.page for row in batch.pages}),
            format_name="visual-table-standalone",
        )

    def extract_supplemental(
        self,
        source: DocumentSource,
        context: SupplementalContext,
    ) -> dict[str, Any]:
        if not self._can_process(source):
            raise RuntimeError("visual table provider or page renderer is unavailable")
        target_pages = self._target_pages(context)
        if not target_pages:
            raise ValueError("visual table supplemental adapter received no target pages")
        batch = self.renderer_registry.render(source, pages=target_pages)
        return self._build_ir(
            source,
            batch,
            primary_ir=context.primary_document_ir,
            target_pages=target_pages,
            format_name="visual-table-supplement",
        )


__all__ = [
    "VisualTableProvider",
    "RuledGridVisualTableProvider",
    "VisualTableSupplementalAdapter",
]
