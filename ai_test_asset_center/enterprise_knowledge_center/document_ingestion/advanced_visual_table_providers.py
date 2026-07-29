"""Advanced visual-table providers for borderless and merged-cell structures.

These providers consume the existing ``RenderedPage`` contract and emit the same geometry
shape accepted by ``VisualTableSupplementalAdapter``. They recover structure only; no
business meaning, header semantics or process order is inferred here.
"""
from __future__ import annotations

import io
import math
import os
import shutil
from collections import defaultdict
from statistics import median
from typing import Any, Iterable, Protocol

from .contract import text
from .page_rendering import RenderedPage
from .visual_table_adapter import RuledGridVisualTableProvider, VisualTableProvider


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _bbox(value: Any) -> list[int]:
    rows = list(value or [])
    if len(rows) != 4:
        return []
    try:
        return [int(round(float(row))) for row in rows]
    except (TypeError, ValueError):
        return []


def _bbox_union(values: Iterable[Iterable[Any]]) -> list[int]:
    rows = [_bbox(value) for value in values]
    rows = [row for row in rows if len(row) == 4]
    if not rows:
        return []
    return [
        min(row[0] for row in rows),
        min(row[1] for row in rows),
        max(row[2] for row in rows),
        max(row[3] for row in rows),
    ]


def _intersection_over_union(left: Iterable[Any], right: Iterable[Any]) -> float:
    a = _bbox(left)
    b = _bbox(right)
    if len(a) != 4 or len(b) != 4:
        return 0.0
    x0 = max(a[0], b[0])
    y0 = max(a[1], b[1])
    x1 = min(a[2], b[2])
    y1 = min(a[3], b[3])
    intersection = max(0, x1 - x0) * max(0, y1 - y0)
    area_a = max(0, a[2] - a[0]) * max(0, a[3] - a[1])
    area_b = max(0, b[2] - b[0]) * max(0, b[3] - b[1])
    union = area_a + area_b - intersection
    return intersection / union if union > 0 else 0.0


def _weighted_mean(rows: Iterable[tuple[float, float]]) -> float:
    numerator = 0.0
    denominator = 0.0
    for value, weight in rows:
        numerator += float(value) * max(0.0, float(weight))
        denominator += max(0.0, float(weight))
    return numerator / denominator if denominator > 0 else 0.0


class WordLayoutProvider(Protocol):
    name: str
    version: str

    def available(self) -> bool:
        ...

    def words(
        self,
        rendered_page: RenderedPage,
        *,
        region_bbox: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        """Return word-level text, confidence and rendered-page pixel coordinates."""
        ...


class TesseractWordLayoutProvider:
    """Optional Tesseract word-box provider used by borderless-table detection."""

    name = "tesseract-word-layout-provider"
    version = "1"

    def __init__(self, language: str | None = None, psm: int = 6) -> None:
        self.language = text(language or os.getenv("QUALIBUG_OCR_LANG") or "chi_sim+eng")
        self.psm = max(3, min(13, int(psm)))

    def available(self) -> bool:
        if not shutil.which("tesseract"):
            return False
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401
        except ImportError:
            return False
        return True

    def words(
        self,
        rendered_page: RenderedPage,
        *,
        region_bbox: list[int] | None = None,
    ) -> list[dict[str, Any]]:
        if not self.available():
            raise RuntimeError("Tesseract word-layout provider is unavailable")
        import pytesseract
        from PIL import Image
        from pytesseract import Output

        image = Image.open(io.BytesIO(rendered_page.image_bytes)).convert("RGB")
        width, height = int(image.width), int(image.height)
        crop = _bbox(region_bbox or [0, 0, width, height])
        if len(crop) != 4:
            crop = [0, 0, width, height]
        crop[0] = max(0, min(width, crop[0]))
        crop[2] = max(crop[0], min(width, crop[2]))
        crop[1] = max(0, min(height, crop[1]))
        crop[3] = max(crop[1], min(height, crop[3]))
        if crop[2] - crop[0] < 2 or crop[3] - crop[1] < 2:
            return []
        cropped = image.crop(tuple(crop))
        data = pytesseract.image_to_data(
            cropped,
            lang=self.language,
            output_type=Output.DICT,
            config=f"--psm {self.psm}",
        )
        result: list[dict[str, Any]] = []
        count = len(data.get("text") or [])
        for index in range(count):
            value = text((data.get("text") or [""])[index])
            if not value:
                continue
            try:
                confidence = float((data.get("conf") or ["-1"])[index]) / 100.0
            except (TypeError, ValueError):
                confidence = -1.0
            if confidence < 0:
                continue
            left = int((data.get("left") or [0])[index]) + crop[0]
            top = int((data.get("top") or [0])[index]) + crop[1]
            word_width = int((data.get("width") or [0])[index])
            word_height = int((data.get("height") or [0])[index])
            result.append(
                {
                    "text": value,
                    "confidence": round(confidence, 4),
                    "bbox": [left, top, left + word_width, top + word_height],
                    "line_key": (
                        int((data.get("block_num") or [0])[index]),
                        int((data.get("par_num") or [0])[index]),
                        int((data.get("line_num") or [0])[index]),
                    ),
                    "word_index": int((data.get("word_num") or [0])[index]),
                }
            )
        return result


class _UnionFind:
    def __init__(self, values: Iterable[tuple[int, int]]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: tuple[int, int]) -> tuple[int, int]:
        parent = self.parent[value]
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, left: tuple[int, int], right: tuple[int, int]) -> None:
        root_left = self.find(left)
        root_right = self.find(right)
        if root_left != root_right:
            self.parent[root_right] = root_left

    def components(self) -> list[list[tuple[int, int]]]:
        grouped: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
        for value in self.parent:
            grouped[self.find(value)].append(value)
        return list(grouped.values())


class MergedCellRuledGridVisualTableProvider:
    """Resolve rectangular row/column spans from missing internal ruled-grid borders."""

    name = "merged-cell-ruled-grid-visual-table-provider"
    version = "1"

    def __init__(
        self,
        base_provider: RuledGridVisualTableProvider | None = None,
        *,
        merge_boundary_threshold: float = 0.34,
        minimum_outer_border_support: float = 0.55,
    ) -> None:
        self.base_provider = base_provider or RuledGridVisualTableProvider()
        self.merge_boundary_threshold = max(0.05, min(0.6, float(merge_boundary_threshold)))
        self.minimum_outer_border_support = max(
            0.25, min(0.95, float(minimum_outer_border_support))
        )

    def available(self) -> bool:
        return self.base_provider.available()

    @staticmethod
    def _support(cell: dict[str, Any], side: str) -> float:
        try:
            return float((cell.get("border_support") or {}).get(side) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _strong_pair_context(
        self,
        left: dict[str, Any],
        right: dict[str, Any],
        *,
        orientation: str,
    ) -> bool:
        if orientation == "horizontal":
            values = [
                self._support(left, "top"),
                self._support(left, "bottom"),
                self._support(right, "top"),
                self._support(right, "bottom"),
            ]
        else:
            values = [
                self._support(left, "left"),
                self._support(left, "right"),
                self._support(right, "left"),
                self._support(right, "right"),
            ]
        return min(values or [0.0]) >= self.minimum_outer_border_support

    def _merged_cell(
        self,
        component: list[tuple[int, int]],
        atomic: dict[tuple[int, int], dict[str, Any]],
    ) -> tuple[dict[str, Any] | None, str]:
        rows = sorted({row for row, _column in component})
        columns = sorted({column for _row, column in component})
        expected = {(row, column) for row in range(rows[0], rows[-1] + 1) for column in range(columns[0], columns[-1] + 1)}
        if set(component) != expected:
            return None, "NON_RECTANGULAR_MERGE_COMPONENT"
        top_row = rows[0]
        bottom_row = rows[-1]
        left_column = columns[0]
        right_column = columns[-1]
        top_support = min(self._support(atomic[(top_row, column)], "top") for column in columns)
        bottom_support = min(
            self._support(atomic[(bottom_row, column)], "bottom") for column in columns
        )
        left_support = min(self._support(atomic[(row, left_column)], "left") for row in rows)
        right_support = min(self._support(atomic[(row, right_column)], "right") for row in rows)
        outer = {
            "top": round(top_support, 4),
            "bottom": round(bottom_support, 4),
            "left": round(left_support, 4),
            "right": round(right_support, 4),
        }
        border_complete = min(outer.values()) >= self.minimum_outer_border_support
        merged = len(component) > 1
        return (
            {
                "row_index": top_row,
                "column_index": left_column,
                "row_span": bottom_row - top_row + 1,
                "column_span": right_column - left_column + 1,
                "bbox": _bbox_union(atomic[value].get("bbox") or [] for value in component),
                "border_complete": border_complete,
                "border_support": outer,
                "boundary_evidence_mode": (
                    "RULED_GRID_MISSING_INTERNAL_BOUNDARY_MERGE"
                    if merged
                    else "RULED_GRID_VISIBLE_BOUNDARIES"
                ),
                "merged_from_atomic_cells": [
                    {"row_index": row, "column_index": column}
                    for row, column in sorted(component)
                ],
            },
            "",
        )

    def _resolve_table(self, table: dict[str, Any]) -> dict[str, Any]:
        result = dict(table)
        atomic = {
            (int(cell.get("row_index") or 0), int(cell.get("column_index") or 0)): dict(cell)
            for cell in _list(table.get("cells"))
            if isinstance(cell, dict)
        }
        if not atomic:
            return result
        union_find = _UnionFind(atomic)
        for (row, column), cell in atomic.items():
            right_key = (row, column + 1)
            if right_key in atomic:
                right = atomic[right_key]
                shared = min(self._support(cell, "right"), self._support(right, "left"))
                if shared < self.merge_boundary_threshold and self._strong_pair_context(
                    cell, right, orientation="horizontal"
                ):
                    union_find.union((row, column), right_key)
            below_key = (row + 1, column)
            if below_key in atomic:
                below = atomic[below_key]
                shared = min(self._support(cell, "bottom"), self._support(below, "top"))
                if shared < self.merge_boundary_threshold and self._strong_pair_context(
                    cell, below, orientation="vertical"
                ):
                    union_find.union((row, column), below_key)

        final_cells: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        merged_count = 0
        for component in union_find.components():
            merged_cell, error = self._merged_cell(component, atomic)
            if merged_cell is None:
                unresolved.append(
                    {
                        "reason": error,
                        "atomic_cells": [
                            {"row_index": row, "column_index": column}
                            for row, column in sorted(component)
                        ],
                    }
                )
                final_cells.extend(atomic[value] for value in component)
                continue
            merged_count += int(
                int(merged_cell.get("row_span") or 1) > 1
                or int(merged_cell.get("column_span") or 1) > 1
            )
            final_cells.append(merged_cell)

        final_cells.sort(
            key=lambda cell: (int(cell.get("row_index") or 0), int(cell.get("column_index") or 0))
        )
        geometry_formal = not unresolved and all(
            bool(cell.get("border_complete")) for cell in final_cells
        )
        outer_supports = [
            float(value)
            for cell in final_cells
            for value in (cell.get("border_support") or {}).values()
        ]
        support_projection = sum(outer_supports) / len(outer_supports) if outer_supports else 0.0
        base_confidence = float(table.get("confidence") or 0.0)
        result.update(
            {
                "cells": final_cells,
                "row_count": max(
                    (
                        int(cell.get("row_index") or 0) + int(cell.get("row_span") or 1)
                        for cell in final_cells
                    ),
                    default=0,
                ),
                "column_count": max(
                    (
                        int(cell.get("column_index") or 0)
                        + int(cell.get("column_span") or 1)
                        for cell in final_cells
                    ),
                    default=0,
                ),
                "confidence": round(min(1.0, base_confidence * 0.55 + support_projection * 0.45), 4),
                "geometry_formal": geometry_formal,
                "merged_cell_count": merged_count,
                "merged_cell_resolution": (
                    "UNRESOLVED" if unresolved else "RESOLVED" if merged_count else "NOT_PRESENT"
                ),
                "unresolved_merge_components": unresolved,
                "detection_method": (
                    text(table.get("detection_method"))
                    + "+rectangular_missing_internal_border_span_resolution"
                ).strip("+"),
            }
        )
        return result

    def detect(
        self,
        rendered_page: RenderedPage,
        *,
        region_bbox: list[int] | None = None,
        target_region_id: str = "",
    ) -> list[dict[str, Any]]:
        return [
            self._resolve_table(table)
            for table in self.base_provider.detect(
                rendered_page,
                region_bbox=region_bbox,
                target_region_id=target_region_id,
            )
        ]


class TextAlignedVisualTableProvider:
    """Recover borderless tables from repeated word-box column alignment."""

    name = "text-aligned-borderless-visual-table-provider"
    version = "1"

    def __init__(
        self,
        word_provider: WordLayoutProvider | None = None,
        *,
        minimum_word_confidence: float = 0.55,
        minimum_rows: int = 3,
        minimum_columns: int = 2,
        minimum_column_support_ratio: float = 0.55,
        minimum_geometry_confidence: float = 0.76,
    ) -> None:
        self.word_provider = word_provider or TesseractWordLayoutProvider()
        self.minimum_word_confidence = max(0.0, min(1.0, float(minimum_word_confidence)))
        self.minimum_rows = max(2, int(minimum_rows))
        self.minimum_columns = max(2, int(minimum_columns))
        self.minimum_column_support_ratio = max(
            0.25, min(0.95, float(minimum_column_support_ratio))
        )
        self.minimum_geometry_confidence = max(
            0.4, min(0.98, float(minimum_geometry_confidence))
        )

    def available(self) -> bool:
        return self.word_provider.available()

    @staticmethod
    def _word_height(word: dict[str, Any]) -> int:
        box = _bbox(word.get("bbox"))
        return max(1, box[3] - box[1]) if len(box) == 4 else 1

    @staticmethod
    def _word_width(word: dict[str, Any]) -> int:
        box = _bbox(word.get("bbox"))
        return max(1, box[2] - box[0]) if len(box) == 4 else 1

    def _group_rows(self, words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        keyed: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        unkeyed: list[dict[str, Any]] = []
        for word in words:
            key = word.get("line_key")
            if isinstance(key, (tuple, list)) and any(str(value) != "0" for value in key):
                keyed[tuple(key)].append(word)
            else:
                unkeyed.append(word)
        rows = list(keyed.values())
        if unkeyed:
            heights = [self._word_height(word) for word in unkeyed]
            tolerance = max(4.0, median(heights or [8]) * 0.7)
            for word in sorted(
                unkeyed,
                key=lambda row: (
                    (_bbox(row.get("bbox"))[1] + _bbox(row.get("bbox"))[3]) / 2.0,
                    _bbox(row.get("bbox"))[0],
                ),
            ):
                box = _bbox(word.get("bbox"))
                center = (box[1] + box[3]) / 2.0
                selected: list[dict[str, Any]] | None = None
                for row in rows:
                    centers = [
                        (_bbox(value.get("bbox"))[1] + _bbox(value.get("bbox"))[3]) / 2.0
                        for value in row
                    ]
                    if centers and abs(center - sum(centers) / len(centers)) <= tolerance:
                        selected = row
                        break
                if selected is None:
                    rows.append([word])
                else:
                    selected.append(word)
        return [
            sorted(row, key=lambda word: _bbox(word.get("bbox"))[0])
            for row in sorted(
                rows,
                key=lambda row: min(_bbox(word.get("bbox"))[1] for word in row),
            )
        ]

    def _segments(self, row: list[dict[str, Any]], median_height: float) -> list[dict[str, Any]]:
        if not row:
            return []
        gap_threshold = max(10.0, median_height * 1.15)
        groups: list[list[dict[str, Any]]] = [[row[0]]]
        for word in row[1:]:
            prior_box = _bbox(groups[-1][-1].get("bbox"))
            current_box = _bbox(word.get("bbox"))
            gap = current_box[0] - prior_box[2]
            if gap > gap_threshold:
                groups.append([word])
            else:
                groups[-1].append(word)
        result: list[dict[str, Any]] = []
        for group in groups:
            value = " ".join(text(word.get("text")) for word in group if text(word.get("text")))
            if not value:
                continue
            result.append(
                {
                    "text": value,
                    "bbox": _bbox_union(word.get("bbox") or [] for word in group),
                    "confidence": round(
                        _weighted_mean(
                            (
                                float(word.get("confidence") or 0.0),
                                max(1, len(text(word.get("text")))),
                            )
                            for word in group
                        ),
                        4,
                    ),
                    "words": [dict(word) for word in group],
                }
            )
        return result

    @staticmethod
    def _cluster(values: list[tuple[int, int]], tolerance: float) -> list[dict[str, Any]]:
        groups: list[list[tuple[int, int]]] = []
        for x, row_index in sorted(values):
            if not groups or abs(x - sum(value[0] for value in groups[-1]) / len(groups[-1])) > tolerance:
                groups.append([(x, row_index)])
            else:
                groups[-1].append((x, row_index))
        return [
            {
                "anchor": int(round(sum(value[0] for value in group) / len(group))),
                "rows": sorted({value[1] for value in group}),
            }
            for group in groups
        ]

    def detect(
        self,
        rendered_page: RenderedPage,
        *,
        region_bbox: list[int] | None = None,
        target_region_id: str = "",
    ) -> list[dict[str, Any]]:
        if not self.available():
            raise RuntimeError("word-layout provider is unavailable")
        words = [
            dict(word)
            for word in self.word_provider.words(rendered_page, region_bbox=region_bbox)
            if isinstance(word, dict)
            and text(word.get("text"))
            and float(word.get("confidence") or 0.0) >= self.minimum_word_confidence
            and len(_bbox(word.get("bbox"))) == 4
        ]
        if len(words) < self.minimum_rows * self.minimum_columns:
            return []
        heights = [self._word_height(word) for word in words]
        median_height = float(median(heights or [10]))
        row_words = self._group_rows(words)
        row_segments = [self._segments(row, median_height) for row in row_words]
        structural_rows = [segments for segments in row_segments if len(segments) >= 2]
        if len(structural_rows) < self.minimum_rows:
            return []

        anchor_inputs: list[tuple[int, int]] = []
        for row_index, segments in enumerate(row_segments):
            if len(segments) < 2:
                continue
            for segment in segments:
                anchor_inputs.append((_bbox(segment.get("bbox"))[0], row_index))
        clusters = self._cluster(anchor_inputs, max(10.0, median_height * 1.2))
        minimum_support = max(2, int(math.ceil(len(structural_rows) * self.minimum_column_support_ratio)))
        anchors = sorted(
            int(cluster["anchor"])
            for cluster in clusters
            if len(cluster.get("rows") or []) >= minimum_support
        )
        if len(anchors) < self.minimum_columns:
            return []

        boundaries: list[float] = [-float("inf")]
        boundaries.extend((left + right) / 2.0 for left, right in zip(anchors, anchors[1:]))
        boundaries.append(float("inf"))
        table_rows: list[list[dict[str, Any]]] = []
        for segments in row_segments:
            assigned: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
            for segment in segments:
                box = _bbox(segment.get("bbox"))
                center_left = box[0]
                center_right = box[2]
                start_column = next(
                    (
                        index
                        for index in range(len(anchors))
                        if boundaries[index] <= center_left < boundaries[index + 1]
                    ),
                    len(anchors) - 1,
                )
                end_column = next(
                    (
                        index
                        for index in range(len(anchors))
                        if boundaries[index] < center_right <= boundaries[index + 1]
                    ),
                    len(anchors) - 1,
                )
                end_column = max(start_column, end_column)
                assigned[(start_column, end_column)].append(segment)
            cells: list[dict[str, Any]] = []
            for (start_column, end_column), values in sorted(assigned.items()):
                cells.append(
                    {
                        "row_index": len(table_rows),
                        "column_index": start_column,
                        "row_span": 1,
                        "column_span": end_column - start_column + 1,
                        "bbox": _bbox_union(value.get("bbox") or [] for value in values),
                        "text": " ".join(text(value.get("text")) for value in values),
                        "text_confidence": round(
                            sum(float(value.get("confidence") or 0.0) for value in values)
                            / max(1, len(values)),
                            4,
                        ),
                        # Existing adapter formalizes structural completeness through this
                        # field. Here it means the inferred alignment boundaries are complete,
                        # not that visible borders exist.
                        "border_complete": True,
                        "border_support": {
                            "alignment": round(
                                sum(
                                    1.0
                                    - min(
                                        1.0,
                                        abs(_bbox(value.get("bbox"))[0] - anchors[start_column])
                                        / max(1.0, median_height * 2.0),
                                    )
                                    for value in values
                                )
                                / max(1, len(values)),
                                4,
                            )
                        },
                        "boundary_evidence_mode": "REPEATED_TEXT_COLUMN_ALIGNMENT",
                        "word_evidence": [
                            word
                            for value in values
                            for word in _list(value.get("words"))
                            if isinstance(word, dict)
                        ],
                    }
                )
            if cells:
                table_rows.append(cells)

        if len(table_rows) < self.minimum_rows:
            return []
        cells = [cell for row in table_rows for cell in row]
        occupied_slots = sum(int(cell.get("column_span") or 1) for cell in cells)
        coverage = min(1.0, occupied_slots / max(1, len(table_rows) * len(anchors)))
        supported_columns = len(anchors) / max(1, max(len(row) for row in structural_rows))
        word_confidence = sum(float(word.get("confidence") or 0.0) for word in words) / len(words)
        row_tops = [min(_bbox(cell.get("bbox"))[1] for cell in row) for row in table_rows]
        spacings = [right - left for left, right in zip(row_tops, row_tops[1:]) if right > left]
        if len(spacings) >= 2 and sum(spacings) > 0:
            mean_spacing = sum(spacings) / len(spacings)
            spacing_variation = (
                sum(abs(value - mean_spacing) for value in spacings) / len(spacings)
            ) / max(1.0, mean_spacing)
            row_regularity = max(0.0, 1.0 - spacing_variation)
        else:
            row_regularity = 0.75
        confidence = max(
            0.0,
            min(
                1.0,
                word_confidence * 0.35
                + min(1.0, supported_columns) * 0.25
                + coverage * 0.25
                + row_regularity * 0.15,
            ),
        )
        geometry_formal = (
            confidence >= self.minimum_geometry_confidence
            and coverage >= 0.62
            and len(table_rows) >= self.minimum_rows
            and len(anchors) >= self.minimum_columns
        )
        table_bbox = _bbox_union(cell.get("bbox") or [] for cell in cells)
        return [
            {
                "provider_table_index": 1,
                "bbox": table_bbox,
                "row_count": len(table_rows),
                "column_count": len(anchors),
                "cells": cells,
                "confidence": round(confidence, 4),
                "geometry_formal": geometry_formal,
                "complete_cell_border_ratio": 1.0,
                "target_region_id": target_region_id,
                "detection_method": "borderless_repeated_word_box_column_alignment",
                "boundary_evidence_mode": "TEXT_ALIGNMENT_NOT_VISIBLE_BORDERS",
                "column_anchors": anchors,
                "coverage_ratio": round(coverage, 4),
                "word_confidence": round(word_confidence, 4),
                "row_regularity": round(row_regularity, 4),
                "merged_cell_count": sum(
                    1
                    for cell in cells
                    if int(cell.get("row_span") or 1) > 1
                    or int(cell.get("column_span") or 1) > 1
                ),
                "merged_cell_resolution": "RESOLVED",
            }
        ]


class CompositeVisualTableProvider:
    """Combine multiple structural providers and remove overlapping duplicate tables."""

    name = "composite-visual-table-provider"
    version = "1"

    def __init__(self, providers: Iterable[VisualTableProvider]) -> None:
        self.providers = tuple(providers)
        self.last_errors: list[dict[str, Any]] = []

    def available(self) -> bool:
        return any(provider.available() for provider in self.providers)

    @staticmethod
    def _score(table: dict[str, Any]) -> tuple[int, float, int]:
        method = text(table.get("detection_method"))
        ruled = int("ruled_grid" in method)
        return (
            int(bool(table.get("geometry_formal"))),
            float(table.get("confidence") or 0.0),
            ruled,
        )

    def detect(
        self,
        rendered_page: RenderedPage,
        *,
        region_bbox: list[int] | None = None,
        target_region_id: str = "",
    ) -> list[dict[str, Any]]:
        self.last_errors = []
        candidates: list[dict[str, Any]] = []
        for provider in self.providers:
            if not provider.available():
                continue
            try:
                rows = provider.detect(
                    rendered_page,
                    region_bbox=region_bbox,
                    target_region_id=target_region_id,
                )
            except Exception as exc:
                self.last_errors.append(
                    {
                        "provider": text(getattr(provider, "name", "")),
                        "detail": f"{type(exc).__name__}: {exc}"[:500],
                    }
                )
                continue
            for row in rows:
                candidate = dict(row)
                candidate["contributing_provider"] = text(getattr(provider, "name", ""))
                candidate["contributing_provider_version"] = text(
                    getattr(provider, "version", "")
                )
                candidates.append(candidate)

        selected: list[dict[str, Any]] = []
        for candidate in sorted(candidates, key=self._score, reverse=True):
            duplicate = next(
                (
                    existing
                    for existing in selected
                    if text(existing.get("target_region_id"))
                    == text(candidate.get("target_region_id"))
                    and _intersection_over_union(
                        existing.get("bbox") or [], candidate.get("bbox") or []
                    )
                    >= 0.68
                ),
                None,
            )
            if duplicate is None:
                selected.append(candidate)
                continue
            duplicate.setdefault("alternative_provider_observations", []).append(
                {
                    "provider": candidate.get("contributing_provider"),
                    "provider_version": candidate.get("contributing_provider_version"),
                    "confidence": candidate.get("confidence"),
                    "detection_method": candidate.get("detection_method"),
                    "bbox": candidate.get("bbox"),
                }
            )
        return selected


def build_default_advanced_visual_table_provider() -> CompositeVisualTableProvider:
    return CompositeVisualTableProvider(
        [
            MergedCellRuledGridVisualTableProvider(),
            TextAlignedVisualTableProvider(),
        ]
    )


__all__ = [
    "WordLayoutProvider",
    "TesseractWordLayoutProvider",
    "MergedCellRuledGridVisualTableProvider",
    "TextAlignedVisualTableProvider",
    "CompositeVisualTableProvider",
    "build_default_advanced_visual_table_provider",
]
