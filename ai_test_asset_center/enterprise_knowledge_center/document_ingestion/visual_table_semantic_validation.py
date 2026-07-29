"""Validate table semantic candidates without promoting them to business meaning."""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from .contract import text

SEMANTIC_VALIDATION_SCHEMA = "qualibug.visual-table-semantic-validation.v1"
_SYMBOL_TOKENS = {"√", "✓", "✔", "×", "✕", "✖", "○", "●", "◎", "◇", "◆", "△", "▲", "☆", "★", "y", "n"}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _norm(value: Any) -> str:
    return re.sub(r"\s+", "", text(value)).lower()


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(text(part) for part in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def _safe_match(row: dict[str, Any]) -> bool:
    keyword = text(row.get("keyword"))
    mode = text(row.get("match_mode"))
    if mode != "CONTAINED_EXPLICIT_HEADER_KEYWORD":
        return True
    # Short ASCII control words such as IF must be exact.  This prevents accidental matches
    # inside unrelated labels such as GIFT or SHIFT.
    return not (keyword.isascii() and len(keyword) < 4)


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
    receipt["critical_unsupported_content_count"] = sum(int(row.get("count") or 0) for row in critical)
    merge_receipt = dict(result.get("adapter_merge_receipt") or {})
    if merge_receipt:
        merge_receipt["status"] = status
        receipt["adapter_merge_receipt"] = merge_receipt
        result["adapter_merge_receipt"] = merge_receipt
    result["structure_receipt"] = receipt
    result["unsupported_content"] = unsupported


def validate_visual_table_semantic_candidates(document_ir: dict[str, Any]) -> dict[str, Any]:
    prior = _dict(document_ir.get("visual_table_semantic_validation_receipt"))
    if text(prior.get("schema")) == SEMANTIC_VALIDATION_SCHEMA:
        return dict(document_ir or {})
    result = dict(document_ir or {})
    blocks = [dict(row) for row in _list(result.get("blocks")) if isinstance(row, dict)]
    table_blocks = {
        text(row.get("block_id")): row
        for row in blocks
        if text(row.get("type")) == "TABLE" and text(row.get("block_id"))
    }
    unsupported = [
        dict(row)
        for row in _list(result.get("unsupported_content"))
        if isinstance(row, dict)
        and text(row.get("reason_code"))
        not in {
            "TABLE_LEGEND_TOKEN_AMBIGUOUS",
            "TABLE_SYMBOL_LEGEND_MISSING",
            "TABLE_COLOR_LEGEND_VISUAL_SAMPLE_UNVERIFIED",
        }
    ]

    kept_roles: list[dict[str, Any]] = []
    rejected_role_ids: set[str] = set()
    for raw in _list(result.get("table_column_role_candidates")):
        if not isinstance(raw, dict):
            continue
        role = dict(raw)
        matches = [
            dict(row)
            for row in _list(role.get("matched_explicit_keywords"))
            if isinstance(row, dict) and _safe_match(row)
        ]
        if not matches:
            rejected_role_ids.add(text(role.get("candidate_id")))
            continue
        role["matched_explicit_keywords"] = matches
        kept_roles.append(role)

    # Re-evaluate ambiguity rows after unsafe short-ASCII contained matches are removed.
    filtered_unsupported: list[dict[str, Any]] = []
    for gap in unsupported:
        if text(gap.get("reason_code")) != "DECISION_COLUMN_ROLE_AMBIGUOUS":
            filtered_unsupported.append(gap)
            continue
        conditions = [
            dict(row)
            for row in _list(gap.get("condition_matches"))
            if isinstance(row, dict) and _safe_match(row)
        ]
        results = [
            dict(row)
            for row in _list(gap.get("result_matches"))
            if isinstance(row, dict) and _safe_match(row)
        ]
        if conditions and results:
            gap["condition_matches"] = conditions
            gap["result_matches"] = results
            filtered_unsupported.append(gap)
    unsupported = filtered_unsupported

    kept_role_ids = {text(row.get("candidate_id")) for row in kept_roles if text(row.get("candidate_id"))}
    for block in blocks:
        candidates = [
            dict(row)
            for row in _list(block.get("structural_role_candidates"))
            if isinstance(row, dict)
        ]
        if candidates:
            block["structural_role_candidates"] = [
                row
                for row in candidates
                if not text(row.get("candidate_id"))
                or text(row.get("candidate_id")) in kept_role_ids
            ]

    roles_by_owner: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for role in kept_roles:
        owner = text(role.get("logical_table_id")) or text(role.get("table_block_id"))
        roles_by_owner[owner].append(role)
    decisions: list[dict[str, Any]] = []
    decisions_by_owner: dict[str, dict[str, Any]] = {}
    for owner, roles in roles_by_owner.items():
        table_id = text(roles[0].get("table_block_id"))
        conditions = sorted(
            {int(row.get("column_index") or 0) for row in roles if text(row.get("role")) == "CONDITION_COLUMN_CANDIDATE"}
        )
        results = sorted(
            {int(row.get("column_index") or 0) for row in roles if text(row.get("role")) == "RESULT_COLUMN_CANDIDATE"}
        )
        if not conditions or not results:
            continue
        candidate = {
            "schema": "qualibug.visual-table-semantic-candidates.v1",
            "candidate_id": _stable_id("decision_matrix_candidate", owner, conditions, results),
            "table_block_id": table_id,
            "logical_table_id": text(roles[0].get("logical_table_id")),
            "condition_column_candidates": conditions,
            "result_column_candidates": results,
            "evidence_code": "VALIDATED_EXPLICIT_HEADER_KEYWORDS_SUPPORT_CONDITION_AND_RESULT_COLUMNS",
            "candidate_only": True,
            "formal_business_rule": False,
            "business_semantics_added": False,
        }
        decisions.append(candidate)
        decisions_by_owner[owner] = candidate

    for table_id, table in table_blocks.items():
        owner = text(table.get("logical_table_id")) or table_id
        owner_table_id = text(table.get("semantic_candidate_owner_table_id")) or table_id
        role_ids = [
            row.get("candidate_id")
            for row in roles_by_owner.get(owner, [])
            if text(row.get("candidate_id"))
        ]
        table["column_role_candidate_ids"] = role_ids
        decision = decisions_by_owner.get(owner)
        table["decision_matrix_candidate"] = bool(decision)
        table["decision_matrix_candidate_id"] = decision.get("candidate_id") if decision else ""
        table["semantic_candidate_owner_table_id"] = owner_table_id

    legends = [
        dict(row) for row in _list(result.get("table_legend_candidates")) if isinstance(row, dict)
    ]
    legend_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for legend in legends:
        legend_groups[(text(legend.get("table_block_id")), _norm(legend.get("token")))].append(legend)
    ambiguous_tokens: set[tuple[str, str]] = set()
    for (table_id, token), rows in legend_groups.items():
        meanings = sorted({_norm(row.get("meaning_text")) for row in rows if _norm(row.get("meaning_text"))})
        if len(meanings) <= 1:
            continue
        ambiguous_tokens.add((table_id, token))
        unsupported.append(
            {
                "kind": "TABLE_LEGEND_TOKEN_AMBIGUOUS",
                "reason_code": "TABLE_LEGEND_TOKEN_AMBIGUOUS",
                "count": 1,
                "status": "SAME_EXPLICIT_LEGEND_TOKEN_HAS_MULTIPLE_MEANINGS",
                "severity": "P1",
                "blocks_formal_understanding": False,
                "included_in_plain_text_authority": True,
                "table_block_id": table_id,
                "token": token,
                "legend_candidate_ids": [row.get("legend_candidate_id") for row in rows],
                "meaning_candidates": [row.get("meaning_text") for row in rows],
            }
        )

    missing_symbol_cells: list[dict[str, Any]] = []
    for block in blocks:
        if text(block.get("type")) != "TABLE_CELL":
            continue
        token = _norm(block.get("text"))
        table_id = text(block.get("table_block_id"))
        if token not in _SYMBOL_TOKENS:
            continue
        if (table_id, token) in legend_groups and (table_id, token) not in ambiguous_tokens:
            continue
        if _list(block.get("legend_candidate_refs")):
            continue
        missing_symbol_cells.append(block)
    if missing_symbol_cells:
        unsupported.append(
            {
                "kind": "TABLE_SYMBOL_LEGEND_MISSING",
                "reason_code": "TABLE_SYMBOL_LEGEND_MISSING",
                "count": len(missing_symbol_cells),
                "status": "SYMBOL_CELLS_HAVE_NO_UNIQUE_EXPLICIT_LEGEND",
                "severity": "P1",
                "blocks_formal_understanding": False,
                "included_in_plain_text_authority": True,
                "cell_block_ids": [row.get("block_id") for row in missing_symbol_cells],
                "pages": sorted({int(row.get("page") or 0) for row in missing_symbol_cells if int(row.get("page") or 0) > 0}),
            }
        )

    color_legends = [row for row in legends if text(row.get("kind")) == "COLOR_LEGEND_CANDIDATE"]
    if color_legends:
        unsupported.append(
            {
                "kind": "TABLE_COLOR_LEGEND_VISUAL_SAMPLE_UNVERIFIED",
                "reason_code": "TABLE_COLOR_LEGEND_VISUAL_SAMPLE_UNVERIFIED",
                "count": len(color_legends),
                "status": "EXPLICIT_COLOR_LEGEND_TEXT_FOUND_BUT_CELL_COLOR_SAMPLES_NOT_BOUND",
                "severity": "P1",
                "blocks_formal_understanding": False,
                "included_in_plain_text_authority": True,
                "legend_candidate_ids": [row.get("legend_candidate_id") for row in color_legends],
            }
        )

    result["blocks"] = blocks
    result["table_column_role_candidates"] = kept_roles
    result["decision_matrix_candidates"] = decisions
    result["unsupported_content"] = unsupported
    receipt = dict(result.get("structure_receipt") or {})
    receipt["table_condition_column_candidate_count"] = sum(
        1 for row in kept_roles if text(row.get("role")) == "CONDITION_COLUMN_CANDIDATE"
    )
    receipt["table_result_column_candidate_count"] = sum(
        1 for row in kept_roles if text(row.get("role")) == "RESULT_COLUMN_CANDIDATE"
    )
    receipt["decision_matrix_candidate_count"] = len(decisions)
    receipt["rejected_unsafe_column_role_candidate_count"] = len(rejected_role_ids)
    receipt["table_legend_token_ambiguity_count"] = len(ambiguous_tokens)
    receipt["table_symbol_legend_missing_cell_count"] = len(missing_symbol_cells)
    receipt["table_color_legend_unverified_count"] = len(color_legends)
    result["structure_receipt"] = receipt
    candidate_receipt = dict(result.get("visual_table_semantic_candidate_receipt") or {})
    candidate_receipt["column_role_candidate_count"] = len(kept_roles)
    candidate_receipt["decision_matrix_candidate_count"] = len(decisions)
    candidate_receipt["formal_business_rules_created"] = 0
    candidate_receipt["business_semantics_added"] = False
    result["visual_table_semantic_candidate_receipt"] = candidate_receipt
    result["visual_table_semantic_validation_receipt"] = {
        "schema": SEMANTIC_VALIDATION_SCHEMA,
        "rejected_unsafe_role_candidate_count": len(rejected_role_ids),
        "legend_token_ambiguity_count": len(ambiguous_tokens),
        "symbol_legend_missing_cell_count": len(missing_symbol_cells),
        "color_legend_unverified_count": len(color_legends),
        "candidate_only": True,
        "formal_business_rules_created": 0,
        "business_semantics_added": False,
    }
    _refresh_status(result)
    return result


__all__ = ["SEMANTIC_VALIDATION_SCHEMA", "validate_visual_table_semantic_candidates"]
