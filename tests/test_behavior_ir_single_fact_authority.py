from __future__ import annotations

from typing import Any

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.behavior_ir_governance import (
    build_governed_business_behavior_ir,
)


def _cell(table_id: str, row: int, column: int, value: str, *, header: bool = False) -> dict[str, Any]:
    return {
        "block_id": f"{table_id}:cell:{row}:{column}",
        "type": "TABLE_CELL",
        "table_block_id": table_id,
        "parent_id": f"{table_id}:row:{row}",
        "page": 1,
        "row_index": row,
        "column_index": column,
        "row_span": 1,
        "column_span": 1,
        "text": value,
        "source_locator": f"matrix.pdf#page=1;table={table_id};row={row};column={column}",
        "table_header_role": "CANONICAL_HEADER" if header else "",
        "column_header_path": ["条件" if column == 0 else "结果", "状态" if column == 0 else "动作"],
    }


def _matrix_structure() -> dict[str, Any]:
    table_id = "table-1"
    blocks = [
        {
            "block_id": table_id,
            "type": "TABLE",
            "page": 1,
            "formal_table_structure": True,
            "semantic_candidate_header_row_count": 1,
            "source_locator": "matrix.pdf#page=1;table=table-1",
        },
        _cell(table_id, 0, 0, "状态", header=True),
        _cell(table_id, 0, 1, "动作", header=True),
        _cell(table_id, 1, 0, "已审核"),
        _cell(table_id, 1, 1, "不允许发货"),
    ]
    return {
        "source_id": "source-matrix",
        "filename": "matrix.pdf",
        "format": "pdf",
        "blocks": blocks,
        "decision_matrix_candidates": [
            {
                "candidate_id": "matrix-candidate-deny",
                "table_block_id": table_id,
                "logical_table_id": "",
                "condition_column_candidates": [0],
                "result_column_candidates": [1],
                "candidate_only": True,
                "formal_business_rule": False,
            }
        ],
        "structure_receipt": {
            "status": "COMPLETE",
            "page_count": 1,
            "text_page_count": 1,
        },
        "unsupported_content": [],
    }


def _accepted_allow_fact() -> dict[str, Any]:
    return {
        "fact_id": "fact-allow-ship",
        "status": "ACCEPTED",
        "kind": "RULE",
        "raw_statement": "订单状态为已审核时可以发货",
        "subject": {"entity_refs": ["订单"], "actor_refs": []},
        "object": {"entity_refs": ["订单"]},
        "action": {"canonical": "发货", "raw": "发货"},
        "conditions": ["状态=已审核"],
        "modality": "MAY",
        "polarity": "POSITIVE",
        "source_spans": [
            {
                "source_id": "source-rule",
                "source_locator": "rules.md#fact=fact-allow-ship",
                "quote": "订单状态为已审核时可以发货",
            }
        ],
        "state_effects": [],
        "data_effects": [],
        "postconditions": [],
        "exceptions": [],
    }


def _operation() -> dict[str, Any]:
    return {
        "operation_id": "operation-ship-order",
        "name": "发货",
        "raw_action_names": ["发货"],
        "object_refs": ["订单"],
        "evidence": [
            {
                "source_id": "source-rule",
                "source_locator": "rules.md#line=1",
                "quote": "订单可以发货",
                "derivation": "test",
            }
        ],
    }


def test_matrix_candidate_cannot_conflict_with_accepted_fact_authority() -> None:
    fact = _accepted_allow_fact()
    structure = _matrix_structure()
    asset = {
        "asset_id": "asset-single-fact-authority",
        "source_inventory": [{"source_id": "source-matrix", "status": "active"}],
        "business_fact_ledger": {"items": [fact]},
        "document_structure_assets": {
            "source_count": 1,
            "block_count": len(structure["blocks"]),
            "page_count": 1,
            "items": [structure],
            "errors": [],
        },
        "enterprise_comprehension_gate": {"entry_allowed": True, "status": "PASS"},
    }

    _rows, behaviors, conflicts, _unknowns, gate = build_governed_business_behavior_ir(
        asset, [fact], [_operation()]
    )

    confirmed = [row for row in behaviors if row["source_kind"] == "ACCEPTED_BUSINESS_FACT"]
    candidates = [row for row in behaviors if row["source_kind"] == "DECISION_MATRIX_ROW"]

    assert len(confirmed) == 1
    assert confirmed[0]["permission_decision"] == "ALLOW"
    assert confirmed[0]["status"] == "CONFIRMED"
    assert confirmed[0]["formal_business_rule"] is True
    assert len(candidates) == 1
    assert candidates[0]["permission_decision"] == "DENY"
    assert candidates[0]["status"] == "CANDIDATE"
    assert candidates[0]["formal_business_rule"] is False
    assert conflicts == []
    assert gate["status"] == "PARTIAL_BUSINESS_BEHAVIOR_IR"
    assert gate["metrics"]["confirmed_behavior_count"] == 1
    assert gate["metrics"]["candidate_behavior_count"] == 1
