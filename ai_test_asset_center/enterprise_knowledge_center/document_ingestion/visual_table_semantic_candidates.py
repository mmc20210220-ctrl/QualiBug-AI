"""Project source-backed structural-semantic candidates for formal visual tables.

This stage does not create business rules.  It exposes auditable candidates for header trees,
row headers, decision-matrix column roles and explicit legends.  All lexical roles require
source text evidence; spatial order alone is never business meaning.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Iterable

from .contract import text

TABLE_SEMANTIC_CANDIDATE_SCHEMA = "qualibug.visual-table-semantic-candidates.v1"

# Generic document vocabulary only.  These are candidate triggers, not domain definitions.
_CONDITION_KEYWORDS = (
    "条件",
    "判断条件",
    "适用条件",
    "前提",
    "输入",
    "输入项",
    "状态",
    "场景",
    "condition",
    "conditions",
    "input",
    "inputs",
    "when",
    "if",
)
_RESULT_KEYWORDS = (
    "结果",
    "输出",
    "输出项",
    "动作",
    "处理",
    "结论",
    "决策",
    "是否允许",
    "result",
    "results",
    "output",
    "outputs",
    "action",
    "decision",
    "then",
)
_COLOR_WORDS = (
    "红色",
    "橙色",
    "黄色",
    "绿色",
    "蓝色",
    "紫色",
    "灰色",
    "黑色",
    "白色",
    "红",
    "橙",
    "黄",
    "绿",
    "蓝",
    "紫",
    "灰",
    "黑",
    "白",
    "red",
    "orange",
    "yellow",
    "green",
    "blue",
    "purple",
    "grey",
    "gray",
    "black",
    "white",
)
_SYMBOL_TOKENS = ("√", "✓", "✔", "×", "✕", "✖", "○", "●", "◎", "◇", "◆", "△", "▲", "☆", "★", "Y", "N")
_LEGEND_PATTERN = re.compile(
    r"^\s*(?P<token>[^\s:=：表示代表含义]{1,12})\s*(?:=|:|：|表示|代表|含义为)\s*(?P<meaning>.+?)\s*$",
    re.IGNORECASE,
)


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _norm(value: Any) -> str:
    return re.sub(r"[\s_\-—–:：/\\（）()【】\[\]]+", "", text(value)).lower()


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _bbox(value: Any) -> list[float]:
    values = list(value or [])
    if len(values) != 4:
        return []
    try:
        return [float(item) for item in values]
    except (TypeError, ValueError):
        return []


def _cells_by_table(blocks: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        if text(block.get("type")) != "TABLE_CELL":
            continue
        table_id = text(block.get("table_block_id"))
        if table_id:
            result[table_id].append(block)
    for rows in result.values():
        rows.sort(
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
    for rows in result.values():
        rows.sort(key=lambda row: int(row.get("column_index") or 0))
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


def _candidate_keyword(value: str, keywords: Iterable[str]) -> tuple[str, str]:
    normalized = _norm(value)
    if not normalized:
        return "", ""
    for keyword in keywords:
        key = _norm(keyword)
        if normalized == key:
            return keyword, "EXACT_EXPLICIT_HEADER_KEYWORD"
    for keyword in keywords:
        key = _norm(keyword)
        if len(key) >= 2 and key in normalized:
            return keyword, "CONTAINED_EXPLICIT_HEADER_KEYWORD"
    return "", ""


def _header_depth(table: dict[str, Any], cells: list[dict[str, Any]]) -> int:
    explicit = int(table.get("header_row_count") or 0)
    if explicit > 0:
        return explicit
    rows = _rows(cells)
    explicit_roles = [
        int(cell.get("row_index") or 0)
        for cell in cells
        if text(cell.get("table_header_role")) in {"CANONICAL_HEADER", "REPEATED_HEADER"}
    ]
    if explicit_roles:
        return max(explicit_roles) + 1
    if not rows or 0 not in rows:
        return 0
    first = rows[0]
    if any(
        int(cell.get("column_span") or 1) > 1 or int(cell.get("row_span") or 1) > 1
        for cell in first
    ):
        depth = 1
        while depth in rows and depth < 4:
            prior_spans = any(
                int(cell.get("column_span") or 1) > 1 or int(cell.get("row_span") or 1) > 1
                for cell in rows[depth - 1]
            )
            if not prior_spans or not all(text(cell.get("text")) for cell in rows[depth]):
                break
            depth += 1
        return depth
    # A single-row header is accepted only when explicit generic role vocabulary is present.
    if len(rows) >= 3 and all(text(cell.get("text")) for cell in first):
        if any(
            _candidate_keyword(text(cell.get("text")), _CONDITION_KEYWORDS)[0]
            or _candidate_keyword(text(cell.get("text")), _RESULT_KEYWORDS)[0]
            for cell in first
        ):
            return 1
    return 0


def _header_nodes(
    table_id: str,
    logical_table_id: str,
    cells: list[dict[str, Any]],
    header_depth: int,
) -> list[dict[str, Any]]:
    headers = [cell for cell in cells if int(cell.get("row_index") or 0) < header_depth]
    nodes: list[dict[str, Any]] = []
    for cell in headers:
        start = int(cell.get("column_index") or 0)
        span = max(1, int(cell.get("column_span") or 1))
        row_index = int(cell.get("row_index") or 0)
        node_id = _stable_id(
            "table_header_node",
            logical_table_id or table_id,
            row_index,
            start,
            span,
            cell.get("block_id"),
        )
        node = {
            "schema": TABLE_SEMANTIC_CANDIDATE_SCHEMA,
            "header_node_id": node_id,
            "table_block_id": table_id,
            "logical_table_id": logical_table_id,
            "source_cell_block_id": cell.get("block_id"),
            "level": row_index + 1,
            "start_column": start,
            "end_column": start + span - 1,
            "column_span": span,
            "row_span": max(1, int(cell.get("row_span") or 1)),
            "text": text(cell.get("text")),
            "parent_header_node_id": "",
            "child_header_node_ids": [],
            "node_kind": "HEADER_GROUP" if span > 1 else "HEADER_LEAF",
            "source_locator": cell.get("source_locator"),
            "candidate_only": True,
            "business_semantics_added": False,
        }
        nodes.append(node)
        cell["table_header_node_id"] = node_id
        cell["table_header_node_kind"] = node["node_kind"]
    for node in nodes:
        if int(node.get("level") or 0) <= 1:
            continue
        parents = [
            candidate
            for candidate in nodes
            if int(candidate.get("level") or 0) == int(node.get("level") or 0) - 1
            and int(candidate.get("start_column") or 0) <= int(node.get("start_column") or 0)
            and int(candidate.get("end_column") or 0) >= int(node.get("end_column") or 0)
        ]
        if len(parents) == 1:
            parent = parents[0]
            node["parent_header_node_id"] = parent["header_node_id"]
            parent["child_header_node_ids"].append(node["header_node_id"])
            parent["node_kind"] = "HEADER_GROUP"
    return nodes


def _header_paths(nodes: list[dict[str, Any]], column_count: int) -> dict[int, list[dict[str, Any]]]:
    result: dict[int, list[dict[str, Any]]] = {column: [] for column in range(column_count)}
    for column in range(column_count):
        result[column] = sorted(
            [
                node
                for node in nodes
                if int(node.get("start_column") or 0) <= column <= int(node.get("end_column") or 0)
                and text(node.get("text"))
            ],
            key=lambda node: int(node.get("level") or 0),
        )
    return result


def _append_candidate(cell: dict[str, Any], candidate: dict[str, Any]) -> None:
    existing = [
        dict(row)
        for row in _list(cell.get("structural_role_candidates"))
        if isinstance(row, dict)
    ]
    identity = (text(candidate.get("role")), text(candidate.get("evidence_code")))
    if not any(
        (text(row.get("role")), text(row.get("evidence_code"))) == identity
        for row in existing
    ):
        existing.append(candidate)
    cell["structural_role_candidates"] = existing


def _row_header_candidates(
    table_id: str,
    cells: list[dict[str, Any]],
    header_depth: int,
    column_count: int,
) -> list[dict[str, Any]]:
    if column_count < 2:
        return []
    body = [cell for cell in cells if int(cell.get("row_index") or 0) >= header_depth]
    rows = _rows(body)
    if len(rows) < 2:
        return []
    leftmost = [
        next(
            (
                cell
                for cell in row_cells
                if int(cell.get("column_index") or 0) == 0
                and max(1, int(cell.get("column_span") or 1)) == 1
            ),
            None,
        )
        for row_cells in rows.values()
    ]
    candidates = [cell for cell in leftmost if isinstance(cell, dict) and text(cell.get("text"))]
    support = len(candidates) / max(1, len(rows))
    if support < 0.6:
        return []
    result: list[dict[str, Any]] = []
    for cell in candidates:
        candidate = {
            "role": "ROW_HEADER_CANDIDATE",
            "confidence": round(min(0.94, 0.62 + support * 0.30), 4),
            "evidence_code": "LEFTMOST_BODY_COLUMN_WITH_REPEATED_NONEMPTY_LABELS",
            "table_block_id": table_id,
            "source_cell_block_id": cell.get("block_id"),
            "candidate_only": True,
            "business_semantics_added": False,
        }
        if int(cell.get("row_span") or 1) > 1:
            candidate["subtype"] = "ROW_HEADER_GROUP_CANDIDATE"
            candidate["evidence_code"] = "LEFTMOST_BODY_COLUMN_ROW_SPAN_GROUP"
        _append_candidate(cell, candidate)
        result.append(candidate)
    return result


def _column_role_candidates(
    table_id: str,
    logical_table_id: str,
    cells: list[dict[str, Any]],
    header_nodes: list[dict[str, Any]],
    column_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    paths = _header_paths(header_nodes, column_count)
    candidates: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for column, nodes in paths.items():
        condition_matches: list[dict[str, str]] = []
        result_matches: list[dict[str, str]] = []
        for node in nodes:
            value = text(node.get("text"))
            keyword, match_mode = _candidate_keyword(value, _CONDITION_KEYWORDS)
            if keyword:
                condition_matches.append(
                    {"header_node_id": node["header_node_id"], "keyword": keyword, "match_mode": match_mode}
                )
            keyword, match_mode = _candidate_keyword(value, _RESULT_KEYWORDS)
            if keyword:
                result_matches.append(
                    {"header_node_id": node["header_node_id"], "keyword": keyword, "match_mode": match_mode}
                )
        roles: list[str] = []
        if condition_matches:
            roles.append("CONDITION_COLUMN_CANDIDATE")
        if result_matches:
            roles.append("RESULT_COLUMN_CANDIDATE")
        for role in roles:
            matches = condition_matches if role.startswith("CONDITION") else result_matches
            candidate = {
                "schema": TABLE_SEMANTIC_CANDIDATE_SCHEMA,
                "candidate_id": _stable_id("table_column_role", logical_table_id or table_id, column, role),
                "table_block_id": table_id,
                "logical_table_id": logical_table_id,
                "column_index": column,
                "role": role,
                "header_path": [text(node.get("text")) for node in nodes if text(node.get("text"))],
                "header_node_ids": [node["header_node_id"] for node in nodes],
                "matched_explicit_keywords": matches,
                "confidence": 0.95 if all(row["match_mode"].startswith("EXACT") for row in matches) else 0.82,
                "candidate_only": True,
                "business_semantics_added": False,
            }
            candidates.append(candidate)
            for cell in cells:
                start = int(cell.get("column_index") or 0)
                span = max(1, int(cell.get("column_span") or 1))
                if start <= column < start + span:
                    _append_candidate(
                        cell,
                        {
                            "role": role,
                            "confidence": candidate["confidence"],
                            "evidence_code": "EXPLICIT_HEADER_ROLE_KEYWORD",
                            "candidate_id": candidate["candidate_id"],
                            "candidate_only": True,
                            "business_semantics_added": False,
                        },
                    )
        if condition_matches and result_matches:
            gaps.append(
                {
                    "kind": "DECISION_COLUMN_ROLE_AMBIGUOUS",
                    "reason_code": "DECISION_COLUMN_ROLE_AMBIGUOUS",
                    "count": 1,
                    "status": "SAME_HEADER_PATH_MATCHES_CONDITION_AND_RESULT_VOCABULARY",
                    "severity": "P1",
                    "blocks_formal_understanding": False,
                    "included_in_plain_text_authority": True,
                    "table_block_id": table_id,
                    "logical_table_id": logical_table_id,
                    "column_index": column,
                    "header_path": [text(node.get("text")) for node in nodes if text(node.get("text"))],
                    "condition_matches": condition_matches,
                    "result_matches": result_matches,
                }
            )
    return candidates, gaps


def _legend_kind(token: str) -> str:
    normalized = _norm(token)
    if normalized in {_norm(value) for value in _COLOR_WORDS}:
        return "COLOR_LEGEND_CANDIDATE"
    if token.strip() in _SYMBOL_TOKENS or len(token.strip()) <= 2:
        return "SYMBOL_LEGEND_CANDIDATE"
    return "TEXT_LEGEND_CANDIDATE"


def _legend_candidates(
    table: dict[str, Any],
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    page = int(table.get("page") or 0)
    table_id = text(table.get("block_id"))
    table_box = _bbox(table.get("bbox"))
    result: list[dict[str, Any]] = []
    for block in blocks:
        if int(block.get("page") or 0) != page:
            continue
        if text(block.get("type")) not in {"PARAGRAPH", "NOTE", "CAPTION", "KEY_VALUE"}:
            continue
        value = text(block.get("text"))
        match = _LEGEND_PATTERN.match(value)
        if not match:
            continue
        block_box = _bbox(block.get("bbox"))
        if table_box and block_box:
            # Same-page explicit legends must be reasonably close to the table region.
            vertical_gap = min(abs(block_box[1] - table_box[3]), abs(table_box[1] - block_box[3]))
            table_height = max(1.0, table_box[3] - table_box[1])
            if vertical_gap > table_height * 0.8:
                continue
        token = match.group("token").strip()
        meaning = match.group("meaning").strip()
        if not token or not meaning:
            continue
        kind = _legend_kind(token)
        result.append(
            {
                "schema": TABLE_SEMANTIC_CANDIDATE_SCHEMA,
                "legend_candidate_id": _stable_id("table_legend", table_id, block.get("block_id"), token, meaning),
                "table_block_id": table_id,
                "kind": kind,
                "token": token,
                "meaning_text": meaning,
                "source_block_id": block.get("block_id"),
                "source_locator": block.get("source_locator"),
                "evidence_code": "EXPLICIT_SOURCE_TEXT_LEGEND_DECLARATION",
                "visual_sample_verified": False,
                "candidate_only": True,
                "business_semantics_added": False,
            }
        )
    return result


def _attach_legends(cells: list[dict[str, Any]], legends: list[dict[str, Any]]) -> int:
    by_token: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for legend in legends:
        by_token[_norm(legend.get("token"))].append(legend)
    count = 0
    for cell in cells:
        token = _norm(cell.get("text"))
        matches = by_token.get(token) or []
        if not token or not matches:
            continue
        cell["legend_candidate_refs"] = [row["legend_candidate_id"] for row in matches]
        cell["legend_meaning_candidates"] = [
            {
                "legend_candidate_id": row["legend_candidate_id"],
                "meaning_text": row["meaning_text"],
                "kind": row["kind"],
                "candidate_only": True,
                "business_semantics_added": False,
            }
            for row in matches
        ]
        count += 1
    return count


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
        receipt["adapter_merge_receipt"] = merge_receipt
        result["adapter_merge_receipt"] = merge_receipt
    result["unsupported_content"] = unsupported
    result["structure_receipt"] = receipt


def apply_visual_table_semantic_candidates(document_ir: dict[str, Any]) -> dict[str, Any]:
    """Attach source-backed table-role candidates without creating business facts."""
    prior = _dict(document_ir.get("visual_table_semantic_candidate_receipt"))
    if text(prior.get("schema")) == TABLE_SEMANTIC_CANDIDATE_SCHEMA:
        return dict(document_ir or {})
    result = dict(document_ir or {})
    blocks = [dict(row) for row in _list(result.get("blocks")) if isinstance(row, dict)]
    cells_by_table = _cells_by_table(blocks)
    tables = [
        row
        for row in blocks
        if text(row.get("type")) == "TABLE"
        and bool(row.get("formal_table_structure"))
        and text(row.get("block_id"))
    ]
    unsupported = [
        dict(row) for row in _list(result.get("unsupported_content")) if isinstance(row, dict)
    ]
    header_nodes: list[dict[str, Any]] = []
    column_candidates: list[dict[str, Any]] = []
    row_header_candidates: list[dict[str, Any]] = []
    legend_candidates: list[dict[str, Any]] = []
    decision_matrix_candidates: list[dict[str, Any]] = []
    legend_mapped_cell_count = 0

    for table in tables:
        table_id = text(table.get("block_id"))
        logical_table_id = text(table.get("logical_table_id"))
        cells = cells_by_table.get(table_id) or []
        column_count = _column_count(cells)
        header_depth = _header_depth(table, cells)
        table["semantic_candidate_header_row_count"] = header_depth
        nodes = _header_nodes(table_id, logical_table_id, cells, header_depth)
        header_nodes.extend(nodes)
        row_candidates = _row_header_candidates(table_id, cells, header_depth, column_count)
        row_header_candidates.extend(row_candidates)
        roles, role_gaps = _column_role_candidates(
            table_id,
            logical_table_id,
            cells,
            nodes,
            column_count,
        )
        column_candidates.extend(roles)
        unsupported.extend(role_gaps)
        legends = _legend_candidates(table, blocks)
        legend_candidates.extend(legends)
        legend_mapped_cell_count += _attach_legends(cells, legends)
        condition_columns = sorted(
            {
                int(row.get("column_index") or 0)
                for row in roles
                if text(row.get("role")) == "CONDITION_COLUMN_CANDIDATE"
            }
        )
        result_columns = sorted(
            {
                int(row.get("column_index") or 0)
                for row in roles
                if text(row.get("role")) == "RESULT_COLUMN_CANDIDATE"
            }
        )
        if condition_columns and result_columns:
            candidate_id = _stable_id(
                "decision_matrix_candidate",
                logical_table_id or table_id,
                condition_columns,
                result_columns,
            )
            decision = {
                "schema": TABLE_SEMANTIC_CANDIDATE_SCHEMA,
                "candidate_id": candidate_id,
                "table_block_id": table_id,
                "logical_table_id": logical_table_id,
                "condition_column_candidates": condition_columns,
                "result_column_candidates": result_columns,
                "evidence_code": "EXPLICIT_HEADER_KEYWORDS_SUPPORT_CONDITION_AND_RESULT_COLUMNS",
                "candidate_only": True,
                "formal_business_rule": False,
                "business_semantics_added": False,
            }
            decision_matrix_candidates.append(decision)
            table["decision_matrix_candidate_id"] = candidate_id
            table["decision_matrix_candidate"] = True
        else:
            table["decision_matrix_candidate"] = False
        table["header_node_ids"] = [node["header_node_id"] for node in nodes]
        table["column_role_candidate_ids"] = [
            row["candidate_id"] for row in roles if text(row.get("candidate_id"))
        ]
        table["legend_candidate_ids"] = [
            row["legend_candidate_id"] for row in legends if text(row.get("legend_candidate_id"))
        ]
        table["candidate_only"] = True
        table["business_semantics_added"] = False

    result["blocks"] = blocks
    result["table_header_nodes"] = header_nodes
    result["table_column_role_candidates"] = column_candidates
    result["table_row_header_candidates"] = row_header_candidates
    result["table_legend_candidates"] = legend_candidates
    result["decision_matrix_candidates"] = decision_matrix_candidates
    result["unsupported_content"] = unsupported
    receipt = dict(result.get("structure_receipt") or {})
    receipt["table_header_node_count"] = len(header_nodes)
    receipt["table_header_group_node_count"] = sum(
        1 for row in header_nodes if text(row.get("node_kind")) == "HEADER_GROUP"
    )
    receipt["table_header_leaf_node_count"] = sum(
        1 for row in header_nodes if text(row.get("node_kind")) == "HEADER_LEAF"
    )
    receipt["table_row_header_candidate_count"] = len(row_header_candidates)
    receipt["table_condition_column_candidate_count"] = sum(
        1 for row in column_candidates if text(row.get("role")) == "CONDITION_COLUMN_CANDIDATE"
    )
    receipt["table_result_column_candidate_count"] = sum(
        1 for row in column_candidates if text(row.get("role")) == "RESULT_COLUMN_CANDIDATE"
    )
    receipt["decision_matrix_candidate_count"] = len(decision_matrix_candidates)
    receipt["table_legend_candidate_count"] = len(legend_candidates)
    receipt["table_color_legend_candidate_count"] = sum(
        1 for row in legend_candidates if text(row.get("kind")) == "COLOR_LEGEND_CANDIDATE"
    )
    receipt["table_symbol_legend_candidate_count"] = sum(
        1 for row in legend_candidates if text(row.get("kind")) == "SYMBOL_LEGEND_CANDIDATE"
    )
    receipt["legend_mapped_cell_count"] = legend_mapped_cell_count
    receipt["decision_column_role_ambiguity_count"] = sum(
        int(row.get("count") or 0)
        for row in unsupported
        if text(row.get("reason_code")) == "DECISION_COLUMN_ROLE_AMBIGUOUS"
    )
    receipt["table_semantic_candidate_contract"] = {
        "candidate_only": True,
        "explicit_source_header_text_required_for_condition_result_roles": True,
        "fuzzy_semantic_header_matching": False,
        "filename_is_business_context": False,
        "document_order_is_business_flow": False,
        "legend_visual_sample_verified_by_default": False,
        "business_semantics_added": False,
    }
    result["structure_receipt"] = receipt
    result["visual_table_semantic_candidate_receipt"] = {
        "schema": TABLE_SEMANTIC_CANDIDATE_SCHEMA,
        "header_node_count": len(header_nodes),
        "row_header_candidate_count": len(row_header_candidates),
        "column_role_candidate_count": len(column_candidates),
        "decision_matrix_candidate_count": len(decision_matrix_candidates),
        "legend_candidate_count": len(legend_candidates),
        "legend_mapped_cell_count": legend_mapped_cell_count,
        "candidate_only": True,
        "formal_business_rules_created": 0,
        "business_semantics_added": False,
    }
    _refresh_status(result)
    return result


__all__ = [
    "TABLE_SEMANTIC_CANDIDATE_SCHEMA",
    "apply_visual_table_semantic_candidates",
]
