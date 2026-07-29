"""Enforce structural separation for decision-matrix candidates."""
from __future__ import annotations

from typing import Any

from .contract import text

DECISION_MATRIX_GATE_SCHEMA = "qualibug.decision-matrix-candidate-gate.v1"


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def apply_decision_matrix_candidate_gate(document_ir: dict[str, Any]) -> dict[str, Any]:
    prior = _dict(document_ir.get("decision_matrix_candidate_gate_receipt"))
    if text(prior.get("schema")) == DECISION_MATRIX_GATE_SCHEMA:
        return dict(document_ir or {})
    result = dict(document_ir or {})
    blocks = [dict(row) for row in _list(result.get("blocks")) if isinstance(row, dict)]
    kept: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for raw in _list(result.get("decision_matrix_candidates")):
        if not isinstance(raw, dict):
            continue
        candidate = dict(raw)
        conditions = {int(value) for value in _list(candidate.get("condition_column_candidates"))}
        results = {int(value) for value in _list(candidate.get("result_column_candidates"))}
        overlap = sorted(conditions & results)
        if overlap or not conditions or not results:
            rejected.append(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "table_block_id": candidate.get("table_block_id"),
                    "logical_table_id": candidate.get("logical_table_id"),
                    "overlapping_columns": overlap,
                    "reason": (
                        "CONDITION_AND_RESULT_COLUMNS_OVERLAP"
                        if overlap
                        else "CONDITION_OR_RESULT_COLUMNS_EMPTY"
                    ),
                }
            )
            continue
        kept.append(candidate)
    kept_by_owner = {
        text(row.get("logical_table_id")) or text(row.get("table_block_id")): row
        for row in kept
    }
    for block in blocks:
        if text(block.get("type")) != "TABLE":
            continue
        owner = text(block.get("logical_table_id")) or text(block.get("block_id"))
        candidate = kept_by_owner.get(owner)
        block["decision_matrix_candidate"] = bool(candidate)
        block["decision_matrix_candidate_id"] = candidate.get("candidate_id") if candidate else ""
    result["blocks"] = blocks
    result["decision_matrix_candidates"] = kept
    receipt = dict(result.get("structure_receipt") or {})
    receipt["decision_matrix_candidate_count"] = len(kept)
    receipt["rejected_overlapping_decision_matrix_candidate_count"] = len(rejected)
    result["structure_receipt"] = receipt
    candidate_receipt = dict(result.get("visual_table_semantic_candidate_receipt") or {})
    candidate_receipt["decision_matrix_candidate_count"] = len(kept)
    candidate_receipt["formal_business_rules_created"] = 0
    candidate_receipt["business_semantics_added"] = False
    result["visual_table_semantic_candidate_receipt"] = candidate_receipt
    result["decision_matrix_candidate_gate_receipt"] = {
        "schema": DECISION_MATRIX_GATE_SCHEMA,
        "accepted_candidate_count": len(kept),
        "rejected_candidate_count": len(rejected),
        "rejected_candidates": rejected,
        "distinct_condition_and_result_columns_required": True,
        "candidate_only": True,
        "formal_business_rules_created": 0,
        "business_semantics_added": False,
    }
    return result


__all__ = ["DECISION_MATRIX_GATE_SCHEMA", "apply_decision_matrix_candidate_gate"]
