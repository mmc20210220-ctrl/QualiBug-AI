"""Cross-page visual table continuation and structural header inheritance.

This stage runs after all document adapters are merged.  It links only formally recovered
visual table fragments with strong source-backed layout evidence.  Page adjacency is never
interpreted as business flow, and header inheritance never changes cell text.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from statistics import median
from typing import Any, Iterable

from .contract import text

TABLE_CONTINUATION_SCHEMA = "qualibug.visual-table-continuation.v1"
_CONTINUATION_MARKER = re.compile(
    r"(?:^|[\s（(【\[])"
    r"(?:续表|表\s*[（(]?\s*续\s*[）)]?|continued|cont\.?\s*$)",
    re.IGNORECASE,
)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", text(value)).strip().lower()


def _bbox(value: Any) -> list[float]:
    values = list(value or [])
    if len(values) != 4:
        return []
    try:
        return [float(item) for item in values]
    except (TypeError, ValueError):
        return []


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _page_size(table: dict[str, Any], pages: list[dict[str, Any]]) -> tuple[float, float]:
    evidence = _dict(table.get("structure_evidence"))
    rendering = _dict(evidence.get("page_rendering"))
    try:
        width = float(rendering.get("width_px") or 0.0)
        height = float(rendering.get("height_px") or 0.0)
    except (TypeError, ValueError):
        width = height = 0.0
    if width > 0 and height > 0:
        return width, height
    page_number = int(table.get("page") or 0)
    for row in pages:
        if not isinstance(row, dict) or int(row.get("page") or 0) != page_number:
            continue
        renderings = [
            dict(item)
            for item in _list(row.get("page_rendering"))
            if isinstance(item, dict)
        ]
        for item in renderings:
            try:
                width = float(item.get("width_px") or 0.0)
                height = float(item.get("height_px") or 0.0)
            except (TypeError, ValueError):
                continue
            if width > 0 and height > 0:
                return width, height
    return 0.0, 0.0


def _table_cells(blocks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        if text(block.get("type")) != "TABLE_CELL":
            continue
        table_id = text(block.get("table_block_id"))
        if table_id:
            result[table_id].append(block)
    for values in result.values():
        values.sort(
            key=lambda row: (
                int(row.get("row_index") or 0),
                int(row.get("column_index") or 0),
            )
        )
    return result


def _rows(cells: Iterable[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for cell in cells:
        result[int(cell.get("row_index") or 0)].append(cell)
    for values in result.values():
        values.sort(key=lambda row: int(row.get("column_index") or 0))
    return dict(result)


def _column_count(cells: Iterable[dict[str, Any]]) -> int:
    return max(
        (
            int(cell.get("column_index") or 0)
            + max(1, int(cell.get("column_span") or 1))
            for cell in cells
        ),
        default=0,
    )


def _row_signature(cells: Iterable[dict[str, Any]]) -> tuple[tuple[int, int, int, str], ...]:
    return tuple(
        (
            int(cell.get("column_index") or 0),
            max(1, int(cell.get("column_span") or 1)),
            max(1, int(cell.get("row_span") or 1)),
            _norm(cell.get("text")),
        )
        for cell in sorted(cells, key=lambda row: int(row.get("column_index") or 0))
    )


def _row_layout_signature(cells: Iterable[dict[str, Any]]) -> tuple[tuple[int, int, int], ...]:
    return tuple(
        (
            int(cell.get("column_index") or 0),
            max(1, int(cell.get("column_span") or 1)),
            max(1, int(cell.get("row_span") or 1)),
        )
        for cell in sorted(cells, key=lambda row: int(row.get("column_index") or 0))
    )


def _top_signatures(cells: list[dict[str, Any]], limit: int = 4) -> list[tuple[tuple[int, int, int, str], ...]]:
    rows = _rows(cells)
    return [_row_signature(rows[index]) for index in sorted(rows)[:limit]]


def _repeated_header_depth(
    left_cells: list[dict[str, Any]], right_cells: list[dict[str, Any]], limit: int = 4
) -> int:
    left = _top_signatures(left_cells, limit)
    right = _top_signatures(right_cells, limit)
    depth = 0
    for left_row, right_row in zip(left, right):
        if not left_row or left_row != right_row:
            break
        if not any(value[3] for value in left_row):
            break
        depth += 1
    return depth


def _structural_header_depth(cells: list[dict[str, Any]], limit: int = 3) -> int:
    """Return a conservative header projection based only on spans and complete top rows."""
    rows = _rows(cells)
    ordered = sorted(rows)
    if not ordered or ordered[0] != 0:
        return 0
    first = rows[0]
    if not first or not all(_norm(cell.get("text")) for cell in first):
        return 0
    has_span = any(
        int(cell.get("column_span") or 1) > 1 or int(cell.get("row_span") or 1) > 1
        for cell in first
    )
    if not has_span:
        return 0
    depth = 1
    for row_index in ordered[1:limit]:
        if row_index != depth:
            break
        values = rows[row_index]
        if not values or not all(_norm(cell.get("text")) for cell in values):
            break
        depth += 1
        if not any(
            int(cell.get("column_span") or 1) > 1 or int(cell.get("row_span") or 1) > 1
            for cell in values
        ):
            break
    return depth


def _column_boundaries(cells: list[dict[str, Any]], width: float) -> list[float]:
    count = _column_count(cells)
    if count <= 0 or width <= 0:
        return []
    candidates: dict[int, list[float]] = defaultdict(list)
    for cell in cells:
        box = _bbox(cell.get("bbox"))
        if len(box) != 4:
            continue
        column = int(cell.get("column_index") or 0)
        span = max(1, int(cell.get("column_span") or 1))
        candidates[column].append(box[0] / width)
        candidates[min(count, column + span)].append(box[2] / width)
    boundaries: list[float] = []
    for index in range(count + 1):
        values = candidates.get(index) or []
        if not values:
            return []
        boundaries.append(float(median(values)))
    return boundaries


def _geometry_similarity(
    left: dict[str, Any],
    right: dict[str, Any],
    cells_by_table: dict[str, list[dict[str, Any]]],
    pages: list[dict[str, Any]],
) -> tuple[float, list[float], list[float]]:
    left_cells = cells_by_table.get(text(left.get("block_id"))) or []
    right_cells = cells_by_table.get(text(right.get("block_id"))) or []
    if _column_count(left_cells) != _column_count(right_cells):
        return 0.0, [], []
    left_width, _left_height = _page_size(left, pages)
    right_width, _right_height = _page_size(right, pages)
    left_boundaries = _column_boundaries(left_cells, left_width)
    right_boundaries = _column_boundaries(right_cells, right_width)
    if not left_boundaries or len(left_boundaries) != len(right_boundaries):
        return 0.0, left_boundaries, right_boundaries
    differences = [abs(a - b) for a, b in zip(left_boundaries, right_boundaries)]
    maximum = max(differences or [1.0])
    mean = sum(differences) / max(1, len(differences))
    similarity = max(0.0, 1.0 - maximum * 12.0 - mean * 8.0)
    return round(similarity, 4), left_boundaries, right_boundaries


def _edge_evidence(table: dict[str, Any], pages: list[dict[str, Any]], *, position: str) -> bool:
    box = _bbox(table.get("bbox"))
    _width, height = _page_size(table, pages)
    if len(box) != 4 or height <= 0:
        return False
    if position == "bottom":
        return box[3] / height >= 0.60
    return box[1] / height <= 0.40


def _continuation_marker(
    table: dict[str, Any], blocks: list[dict[str, Any]], pages: list[dict[str, Any]]
) -> dict[str, Any] | None:
    table_box = _bbox(table.get("bbox"))
    page = int(table.get("page") or 0)
    _width, height = _page_size(table, pages)
    for block in blocks:
        if int(block.get("page") or 0) != page:
            continue
        if text(block.get("type")) not in {"PARAGRAPH", "NOTE", "CAPTION", "HEADING"}:
            continue
        value = text(block.get("text"))
        if not value or not _CONTINUATION_MARKER.search(value):
            continue
        block_box = _bbox(block.get("bbox"))
        if table_box and block_box and block_box[1] > table_box[1]:
            continue
        if height > 0 and block_box and block_box[1] / height > 0.45:
            continue
        return {
            "block_id": block.get("block_id"),
            "source_locator": block.get("source_locator"),
            "text": value,
        }
    return None


def _candidate_header_conflict(
    canonical_cells: list[dict[str, Any]], candidate_cells: list[dict[str, Any]], depth: int
) -> bool:
    if depth <= 0:
        return False
    left_rows = _rows(canonical_cells)
    right_rows = _rows(candidate_cells)
    for row_index in range(depth):
        left = left_rows.get(row_index) or []
        right = right_rows.get(row_index) or []
        if not left or not right:
            return False
        if _row_layout_signature(left) != _row_layout_signature(right):
            return False
        left_text = tuple(value[3] for value in _row_signature(left))
        right_text = tuple(value[3] for value in _row_signature(right))
        if left_text != right_text and all(left_text) and all(right_text):
            return True
    return False


def _header_paths(header_cells: list[dict[str, Any]], column_count: int) -> dict[int, list[str]]:
    rows = _rows(header_cells)
    result: dict[int, list[str]] = {index: [] for index in range(column_count)}
    for row_index in sorted(rows):
        for cell in rows[row_index]:
            value = text(cell.get("text"))
            if not value:
                continue
            start = int(cell.get("column_index") or 0)
            span = max(1, int(cell.get("column_span") or 1))
            for column in range(start, min(column_count, start + span)):
                if not result[column] or result[column][-1] != value:
                    result[column].append(value)
    return result


def _refresh_status(result: dict[str, Any]) -> None:
    unsupported = [
        dict(row) for row in _list(result.get("unsupported_content")) if isinstance(row, dict)
    ]
    critical = [row for row in unsupported if bool(row.get("blocks_formal_understanding"))]
    prior = text(_dict(result.get("structure_receipt")).get("status"))
    status = "BLOCKED" if critical or prior == "BLOCKED" else "PARTIAL" if unsupported else "COMPLETE"
    receipt = dict(result.get("structure_receipt") or {})
    receipt["status"] = status
    receipt["unsupported_content"] = unsupported
    receipt["unsupported_content_count"] = sum(int(row.get("count") or 0) for row in unsupported)
    receipt["critical_unsupported_content_count"] = sum(
        int(row.get("count") or 0) for row in critical
    )
    merge_receipt = dict(result.get("adapter_merge_receipt") or {})
    if merge_receipt:
        merge_receipt["status"] = status
        result["adapter_merge_receipt"] = merge_receipt
        receipt["adapter_merge_receipt"] = merge_receipt
    result["unsupported_content"] = unsupported
    result["structure_receipt"] = receipt


def apply_visual_table_continuations(document_ir: dict[str, Any]) -> dict[str, Any]:
    """Link cross-page table fragments and project exact structural header inheritance."""
    result = dict(document_ir or {})
    blocks = [dict(row) for row in _list(result.get("blocks")) if isinstance(row, dict)]
    pages = [dict(row) for row in _list(result.get("pages")) if isinstance(row, dict)]
    cells_by_table = _table_cells(blocks)
    tables = [
        row
        for row in blocks
        if text(row.get("type")) == "TABLE"
        and bool(row.get("formal_table_structure"))
        and int(row.get("page") or 0) > 0
        and text(row.get("block_id"))
    ]
    tables.sort(
        key=lambda row: (
            int(row.get("page") or 0),
            (_bbox(row.get("bbox")) or [0.0, 0.0])[1],
            text(row.get("block_id")),
        )
    )
    table_by_id = {text(row.get("block_id")): row for row in tables}
    unsupported = [
        dict(row) for row in _list(result.get("unsupported_content")) if isinstance(row, dict)
    ]
    links: list[dict[str, Any]] = []
    used_as_previous: set[str] = set()
    used_as_next: set[str] = set()

    for previous in tables:
        previous_id = text(previous.get("block_id"))
        if previous_id in used_as_previous or not _edge_evidence(previous, pages, position="bottom"):
            continue
        next_page = int(previous.get("page") or 0) + 1
        candidates: list[dict[str, Any]] = []
        for following in tables:
            following_id = text(following.get("block_id"))
            if following_id in used_as_next or int(following.get("page") or 0) != next_page:
                continue
            if not _edge_evidence(following, pages, position="top"):
                continue
            similarity, left_boundaries, right_boundaries = _geometry_similarity(
                previous, following, cells_by_table, pages
            )
            if similarity < 0.86:
                continue
            previous_cells = cells_by_table.get(previous_id) or []
            following_cells = cells_by_table.get(following_id) or []
            repeated_depth = _repeated_header_depth(previous_cells, following_cells)
            marker = _continuation_marker(following, blocks, pages)
            structural_depth = _structural_header_depth(previous_cells)
            header_depth = repeated_depth or structural_depth
            conflict = bool(
                marker
                and header_depth > 0
                and repeated_depth == 0
                and _candidate_header_conflict(previous_cells, following_cells, header_depth)
            )
            score = similarity + min(0.20, repeated_depth * 0.06) + (0.08 if marker else 0.0)
            candidates.append(
                {
                    "previous_table_id": previous_id,
                    "following_table_id": following_id,
                    "geometry_similarity": similarity,
                    "left_column_boundaries": left_boundaries,
                    "right_column_boundaries": right_boundaries,
                    "repeated_header_row_count": repeated_depth,
                    "structural_header_row_count": structural_depth,
                    "header_row_count": header_depth,
                    "continuation_marker": marker,
                    "header_conflict": conflict,
                    "score": round(score, 4),
                }
            )
        if not candidates:
            continue
        candidate = max(candidates, key=lambda row: float(row.get("score") or 0.0))
        previous_cells = cells_by_table.get(previous_id) or []
        following_cells = cells_by_table.get(text(candidate.get("following_table_id"))) or []
        strong = bool(candidate.get("repeated_header_row_count")) or bool(
            candidate.get("continuation_marker")
        )
        if candidate.get("header_conflict"):
            unsupported.append(
                {
                    "kind": "VISUAL_TABLE_CONTINUATION_HEADER_CONFLICT",
                    "reason_code": "VISUAL_TABLE_CONTINUATION_HEADER_CONFLICT",
                    "count": 1,
                    "pages": [int(previous.get("page") or 0), next_page],
                    "table_block_ids": [previous_id, candidate.get("following_table_id")],
                    "status": "CONTINUATION_MARKER_PRESENT_BUT_HEADER_LAYOUT_TEXT_CONFLICTS",
                    "severity": "P0",
                    "blocks_formal_understanding": True,
                    "included_in_plain_text_authority": True,
                    "evidence": candidate,
                }
            )
            continue
        if not strong:
            unsupported.append(
                {
                    "kind": "VISUAL_TABLE_CONTINUATION_AMBIGUOUS",
                    "reason_code": "VISUAL_TABLE_CONTINUATION_AMBIGUOUS",
                    "count": 1,
                    "pages": [int(previous.get("page") or 0), next_page],
                    "table_block_ids": [previous_id, candidate.get("following_table_id")],
                    "status": "ADJACENT_EDGE_TABLES_HAVE_SIMILAR_COLUMNS_WITHOUT_REPEAT_OR_MARKER",
                    "severity": "P1",
                    "blocks_formal_understanding": False,
                    "included_in_plain_text_authority": True,
                    "evidence": candidate,
                }
            )
            continue
        if not previous_cells or not following_cells:
            continue
        links.append(candidate)
        used_as_previous.add(previous_id)
        used_as_next.add(text(candidate.get("following_table_id")))

    previous_map = {text(row.get("following_table_id")): text(row.get("previous_table_id")) for row in links}
    next_map = {text(row.get("previous_table_id")): text(row.get("following_table_id")) for row in links}
    groups: list[dict[str, Any]] = []
    visited: set[str] = set()
    repeated_header_cell_count = 0
    inherited_header_cell_count = 0
    multi_level_group_count = 0

    for table in tables:
        table_id = text(table.get("block_id"))
        if table_id in visited or table_id in previous_map:
            continue
        fragment_ids = [table_id]
        while fragment_ids[-1] in next_map:
            fragment_ids.append(next_map[fragment_ids[-1]])
        if len(fragment_ids) < 2:
            continue
        visited.update(fragment_ids)
        link_rows = [row for row in links if text(row.get("previous_table_id")) in fragment_ids]
        header_depth = max(
            [int(row.get("header_row_count") or 0) for row in link_rows]
            + [_structural_header_depth(cells_by_table.get(fragment_ids[0]) or [])]
        )
        canonical_cells = cells_by_table.get(fragment_ids[0]) or []
        canonical_header_cells = [
            cell for cell in canonical_cells if int(cell.get("row_index") or 0) < header_depth
        ]
        column_count = _column_count(canonical_cells)
        header_paths = _header_paths(canonical_header_cells, column_count)
        logical_id = _stable_id("logical_visual_table", *fragment_ids)
        if header_depth > 1:
            multi_level_group_count += 1

        group_evidence: list[dict[str, Any]] = []
        for fragment_index, fragment_id in enumerate(fragment_ids):
            fragment = table_by_id[fragment_id]
            fragment_cells = cells_by_table.get(fragment_id) or []
            incoming = next(
                (row for row in links if text(row.get("following_table_id")) == fragment_id),
                None,
            )
            repeated_depth = int(_dict(incoming).get("repeated_header_row_count") or 0)
            fragment["logical_table_id"] = logical_id
            fragment["table_fragment_index"] = fragment_index
            fragment["table_fragment_count"] = len(fragment_ids)
            fragment["continued_from_table_id"] = (
                fragment_ids[fragment_index - 1] if fragment_index > 0 else ""
            )
            fragment["continued_to_table_id"] = (
                fragment_ids[fragment_index + 1] if fragment_index + 1 < len(fragment_ids) else ""
            )
            fragment["cross_page_relation_status"] = "CONFIRMED_STRUCTURAL_CONTINUATION"
            fragment["header_row_count"] = header_depth
            fragment["header_source_table_id"] = fragment_ids[0]
            fragment["repeated_header_row_count"] = repeated_depth
            fragment["document_order_is_business_flow"] = False

            for cell in fragment_cells:
                row_index = int(cell.get("row_index") or 0)
                if fragment_index == 0 and row_index < header_depth:
                    cell["table_header_role"] = "CANONICAL_HEADER"
                    cell["table_header_level"] = row_index + 1
                    continue
                if fragment_index > 0 and row_index < repeated_depth:
                    cell["table_header_role"] = "REPEATED_HEADER"
                    cell["table_header_level"] = row_index + 1
                    cell["excluded_from_plain_text_projection"] = True
                    cell["header_source_table_id"] = fragment_ids[0]
                    repeated_header_cell_count += 1
                    continue
                covered_columns = range(
                    int(cell.get("column_index") or 0),
                    min(
                        column_count,
                        int(cell.get("column_index") or 0)
                        + max(1, int(cell.get("column_span") or 1)),
                    ),
                )
                paths = [header_paths.get(column) or [] for column in covered_columns]
                paths = [path for path in paths if path]
                if paths:
                    cell["column_header_paths"] = paths
                    cell["header_source_table_id"] = fragment_ids[0]
                    cell["header_inheritance_evidence"] = "EXACT_REPEATED_OR_STRUCTURAL_HEADER_CHAIN"
                    inherited_header_cell_count += 1
                    if len(paths) == 1:
                        cell["column_header_path"] = paths[0]
            if incoming:
                group_evidence.append(dict(incoming))

        groups.append(
            {
                "schema": TABLE_CONTINUATION_SCHEMA,
                "logical_table_id": logical_id,
                "fragment_table_ids": fragment_ids,
                "pages": [int(table_by_id[value].get("page") or 0) for value in fragment_ids],
                "header_row_count": header_depth,
                "multi_level_header": header_depth > 1,
                "canonical_header_table_id": fragment_ids[0],
                "column_count": column_count,
                "column_header_paths": {str(key): value for key, value in header_paths.items()},
                "continuation_evidence": group_evidence,
                "document_order_is_business_flow": False,
                "business_semantics_added": False,
            }
        )

    row_blocks = [row for row in blocks if text(row.get("type")) == "TABLE_ROW"]
    for row_block in row_blocks:
        table_id = text(row_block.get("parent_id"))
        table = table_by_id.get(table_id)
        if not table or not text(table.get("logical_table_id")):
            continue
        row_index = int(row_block.get("row_index") or 0)
        repeated_depth = int(table.get("repeated_header_row_count") or 0)
        header_depth = int(table.get("header_row_count") or 0)
        fragment_index = int(table.get("table_fragment_index") or 0)
        if fragment_index == 0 and row_index < header_depth:
            row_block["table_header_role"] = "CANONICAL_HEADER"
            row_block["table_header_level"] = row_index + 1
        elif fragment_index > 0 and row_index < repeated_depth:
            row_block["table_header_role"] = "REPEATED_HEADER"
            row_block["table_header_level"] = row_index + 1
            row_block["excluded_from_plain_text_projection"] = True

    result["blocks"] = blocks
    result["table_groups"] = groups
    result["table_continuations"] = links
    result["unsupported_content"] = unsupported
    receipt = dict(result.get("structure_receipt") or {})
    receipt["logical_visual_table_group_count"] = len(groups)
    receipt["continued_visual_table_fragment_count"] = sum(
        max(0, len(_list(group.get("fragment_table_ids"))) - 1) for group in groups
    )
    receipt["multi_level_header_group_count"] = multi_level_group_count
    receipt["repeated_header_cell_count"] = repeated_header_cell_count
    receipt["header_inherited_data_cell_count"] = inherited_header_cell_count
    receipt["ambiguous_table_continuation_count"] = sum(
        int(row.get("count") or 0)
        for row in unsupported
        if text(row.get("reason_code")) == "VISUAL_TABLE_CONTINUATION_AMBIGUOUS"
    )
    receipt["table_continuation_header_conflict_count"] = sum(
        int(row.get("count") or 0)
        for row in unsupported
        if text(row.get("reason_code")) == "VISUAL_TABLE_CONTINUATION_HEADER_CONFLICT"
    )
    receipt["table_continuation_contract"] = {
        "adjacent_pages_required": True,
        "page_edge_evidence_required": True,
        "column_geometry_similarity_required": True,
        "repeated_header_or_explicit_marker_required": True,
        "exact_header_text_matching_only": True,
        "document_order_is_business_flow": False,
        "business_semantics_added": False,
    }
    result["structure_receipt"] = receipt
    result["visual_table_continuation_receipt"] = {
        "schema": TABLE_CONTINUATION_SCHEMA,
        "logical_table_group_count": len(groups),
        "continuation_link_count": len(links),
        "multi_level_header_group_count": multi_level_group_count,
        "repeated_header_cell_count": repeated_header_cell_count,
        "header_inherited_data_cell_count": inherited_header_cell_count,
        "groups": groups,
        "links": links,
        "document_order_is_business_flow": False,
        "business_semantics_added": False,
    }
    _refresh_status(result)
    return result


__all__ = ["TABLE_CONTINUATION_SCHEMA", "apply_visual_table_continuations"]
