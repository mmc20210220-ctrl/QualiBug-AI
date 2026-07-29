"""Normalize table semantic candidates at logical-table scope.

Continuation fragments may repeat the same headers.  Candidate identities are therefore owned
by the canonical first fragment and inherited by later fragments without deleting local source
evidence.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .contract import text
from .visual_table_semantic_candidates import TABLE_SEMANTIC_CANDIDATE_SCHEMA

SEMANTIC_NORMALIZATION_SCHEMA = "qualibug.visual-table-semantic-normalization.v1"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _covered_columns(cell: dict[str, Any]) -> range:
    start = int(cell.get("column_index") or 0)
    span = max(1, int(cell.get("column_span") or 1))
    return range(start, start + span)


def normalize_visual_table_semantic_candidates(document_ir: dict[str, Any]) -> dict[str, Any]:
    prior = _dict(document_ir.get("visual_table_semantic_normalization_receipt"))
    if text(prior.get("schema")) == SEMANTIC_NORMALIZATION_SCHEMA:
        return dict(document_ir or {})
    result = dict(document_ir or {})
    blocks = [dict(row) for row in _list(result.get("blocks")) if isinstance(row, dict)]
    table_blocks = {
        text(row.get("block_id")): row
        for row in blocks
        if text(row.get("type")) == "TABLE" and text(row.get("block_id"))
    }
    groups = [dict(row) for row in _list(result.get("table_groups")) if isinstance(row, dict)]
    canonical_by_table: dict[str, str] = {}
    logical_by_table: dict[str, str] = {}
    group_fragments: dict[str, list[str]] = {}
    for group in groups:
        logical_id = text(group.get("logical_table_id"))
        fragments = [text(value) for value in _list(group.get("fragment_table_ids")) if text(value)]
        if not logical_id or not fragments:
            continue
        canonical = text(group.get("canonical_header_table_id")) or fragments[0]
        group_fragments[logical_id] = fragments
        for fragment in fragments:
            canonical_by_table[fragment] = canonical
            logical_by_table[fragment] = logical_id
    for table_id, table in table_blocks.items():
        logical_id = text(table.get("logical_table_id"))
        if logical_id and table_id not in logical_by_table:
            logical_by_table[table_id] = logical_id
            canonical_by_table[table_id] = table_id

    raw_nodes = [
        dict(row) for row in _list(result.get("table_header_nodes")) if isinstance(row, dict)
    ]
    kept_nodes: list[dict[str, Any]] = []
    nodes_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for node in raw_nodes:
        table_id = text(node.get("table_block_id"))
        canonical = canonical_by_table.get(table_id, table_id)
        if canonical != table_id:
            continue
        node["logical_table_id"] = logical_by_table.get(table_id, text(node.get("logical_table_id")))
        kept_nodes.append(node)
        nodes_by_table[table_id].append(node)

    raw_roles = [
        dict(row)
        for row in _list(result.get("table_column_role_candidates"))
        if isinstance(row, dict)
    ]
    kept_roles: list[dict[str, Any]] = []
    roles_by_table_column: dict[tuple[str, int, str], dict[str, Any]] = {}
    for role in raw_roles:
        table_id = text(role.get("table_block_id"))
        canonical = canonical_by_table.get(table_id, table_id)
        if canonical != table_id:
            continue
        role["logical_table_id"] = logical_by_table.get(table_id, text(role.get("logical_table_id")))
        kept_roles.append(role)
        roles_by_table_column[
            (table_id, int(role.get("column_index") or 0), text(role.get("role")))
        ] = role

    raw_decisions = [
        dict(row)
        for row in _list(result.get("decision_matrix_candidates"))
        if isinstance(row, dict)
    ]
    kept_decisions: list[dict[str, Any]] = []
    decision_by_canonical: dict[str, dict[str, Any]] = {}
    for decision in raw_decisions:
        table_id = text(decision.get("table_block_id"))
        canonical = canonical_by_table.get(table_id, table_id)
        if canonical != table_id or canonical in decision_by_canonical:
            continue
        decision["logical_table_id"] = logical_by_table.get(table_id, text(decision.get("logical_table_id")))
        decision_by_canonical[canonical] = decision
        kept_decisions.append(decision)

    inherited_table_count = 0
    inherited_cell_candidate_count = 0
    for logical_id, fragments in group_fragments.items():
        canonical = canonical_by_table.get(fragments[0], fragments[0])
        canonical_nodes = nodes_by_table.get(canonical) or []
        canonical_roles = [
            role for role in kept_roles if text(role.get("table_block_id")) == canonical
        ]
        canonical_decision = decision_by_canonical.get(canonical)
        for fragment in fragments:
            table = table_blocks.get(fragment)
            if not table:
                continue
            table["semantic_candidate_owner_table_id"] = canonical
            table["header_node_ids"] = [row.get("header_node_id") for row in canonical_nodes]
            table["column_role_candidate_ids"] = [row.get("candidate_id") for row in canonical_roles]
            if canonical_decision:
                table["decision_matrix_candidate"] = True
                table["decision_matrix_candidate_id"] = canonical_decision.get("candidate_id")
            if fragment != canonical:
                table["semantic_candidates_inherited_from_table_id"] = canonical
                inherited_table_count += 1

    nodes_by_canonical_column: dict[tuple[str, int], list[str]] = defaultdict(list)
    for node in kept_nodes:
        table_id = text(node.get("table_block_id"))
        start = int(node.get("start_column") or 0)
        end = int(node.get("end_column") or start)
        for column in range(start, end + 1):
            nodes_by_canonical_column[(table_id, column)].append(text(node.get("header_node_id")))

    for block in blocks:
        if text(block.get("type")) != "TABLE_CELL":
            continue
        table_id = text(block.get("table_block_id"))
        canonical = canonical_by_table.get(table_id, table_id)
        if canonical != table_id:
            block.pop("table_header_node_id", None)
            block.pop("table_header_node_kind", None)
            refs = [
                node_id
                for column in _covered_columns(block)
                for node_id in nodes_by_canonical_column.get((canonical, column), [])
                if node_id
            ]
            if refs:
                block["canonical_header_node_refs"] = list(dict.fromkeys(refs))
                block["semantic_candidates_inherited_from_table_id"] = canonical
        candidates = [
            dict(row)
            for row in _list(block.get("structural_role_candidates"))
            if isinstance(row, dict)
        ]
        normalized: list[dict[str, Any]] = []
        for candidate in candidates:
            role = text(candidate.get("role"))
            if role not in {"CONDITION_COLUMN_CANDIDATE", "RESULT_COLUMN_CANDIDATE"}:
                normalized.append(candidate)
                continue
            replacement = None
            for column in _covered_columns(block):
                replacement = roles_by_table_column.get((canonical, column, role))
                if replacement:
                    break
            if replacement:
                candidate["candidate_id"] = replacement.get("candidate_id")
                candidate["semantic_candidate_owner_table_id"] = canonical
                if canonical != table_id:
                    inherited_cell_candidate_count += 1
            normalized.append(candidate)
        if normalized:
            unique: dict[tuple[str, str], dict[str, Any]] = {}
            for candidate in normalized:
                unique[(text(candidate.get("role")), text(candidate.get("candidate_id")))] = candidate
            block["structural_role_candidates"] = list(unique.values())

    result["blocks"] = blocks
    result["table_header_nodes"] = kept_nodes
    result["table_column_role_candidates"] = kept_roles
    result["decision_matrix_candidates"] = kept_decisions
    receipt = dict(result.get("structure_receipt") or {})
    receipt["table_header_node_count"] = len(kept_nodes)
    receipt["table_header_group_node_count"] = sum(
        1 for row in kept_nodes if text(row.get("node_kind")) == "HEADER_GROUP"
    )
    receipt["table_header_leaf_node_count"] = sum(
        1 for row in kept_nodes if text(row.get("node_kind")) == "HEADER_LEAF"
    )
    receipt["table_condition_column_candidate_count"] = sum(
        1 for row in kept_roles if text(row.get("role")) == "CONDITION_COLUMN_CANDIDATE"
    )
    receipt["table_result_column_candidate_count"] = sum(
        1 for row in kept_roles if text(row.get("role")) == "RESULT_COLUMN_CANDIDATE"
    )
    receipt["decision_matrix_candidate_count"] = len(kept_decisions)
    receipt["semantic_candidate_inherited_fragment_count"] = inherited_table_count
    receipt["semantic_candidate_inherited_cell_count"] = inherited_cell_candidate_count
    result["structure_receipt"] = receipt
    result["visual_table_semantic_normalization_receipt"] = {
        "schema": SEMANTIC_NORMALIZATION_SCHEMA,
        "logical_table_group_count": len(group_fragments),
        "canonical_header_node_count": len(kept_nodes),
        "canonical_column_role_candidate_count": len(kept_roles),
        "canonical_decision_matrix_candidate_count": len(kept_decisions),
        "inherited_fragment_count": inherited_table_count,
        "inherited_cell_candidate_count": inherited_cell_candidate_count,
        "repeated_fragment_candidates_removed": True,
        "source_evidence_deleted": False,
        "business_semantics_added": False,
    }
    return result


__all__ = ["SEMANTIC_NORMALIZATION_SCHEMA", "normalize_visual_table_semantic_candidates"]
