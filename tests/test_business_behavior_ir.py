from __future__ import annotations

from typing import Any

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.behavior_ir_governance import (
    build_governed_business_behavior_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.closure import (
    apply_minimum_understanding_closure,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.schema import (
    empty_model,
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


def _matrix_structure(*, result_value: str = "允许发货", condition_value: str = "已审核") -> dict[str, Any]:
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
        _cell(table_id, 1, 0, condition_value),
        _cell(table_id, 1, 1, result_value),
    ]
    return {
        "source_id": "source-matrix",
        "filename": "matrix.pdf",
        "format": "pdf",
        "blocks": blocks,
        "decision_matrix_candidates": [
            {
                "candidate_id": "matrix-candidate-1",
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


def _asset(structure: dict[str, Any], facts: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "asset_id": "asset-behavior",
        "source_inventory": [
            {"source_id": "source-matrix", "status": "active"},
        ],
        "business_fact_ledger": {"items": list(facts or [])},
        "document_structure_assets": {
            "source_count": 1,
            "block_count": len(structure["blocks"]),
            "page_count": 1,
            "items": [structure],
            "errors": [],
        },
        "enterprise_comprehension_gate": {"entry_allowed": True, "status": "PASS"},
    }


def _fact(
    fact_id: str,
    *,
    modality: str,
    condition: str = "状态=已审核",
) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "status": "ACCEPTED",
        "kind": "RULE",
        "raw_statement": f"订单{condition}时{modality}发货",
        "subject": {"entity_refs": ["订单"], "actor_refs": []},
        "object": {"entity_refs": ["订单"]},
        "action": {"canonical": "发货", "raw": "发货"},
        "conditions": [condition],
        "modality": modality,
        "polarity": "",
        "source_spans": [
            {
                "source_id": "source-rule",
                "source_locator": f"rules.md#fact={fact_id}",
                "quote": f"订单{condition}时{modality}发货",
            }
        ],
        "state_effects": [],
        "data_effects": [],
        "postconditions": [],
        "exceptions": [],
    }


def test_decision_matrix_row_compiles_to_candidate_behavior() -> None:
    asset = _asset(_matrix_structure())
    rows, behaviors, conflicts, unknowns, gate = build_governed_business_behavior_ir(
        asset, [], [_operation()]
    )

    assert len(rows) == 1
    row = rows[0]
    assert row["status"] == "CANDIDATE"
    assert row["condition_slots"][0]["field_candidate"] == "状态"
    assert row["condition_slots"][0]["operator_candidate"] == "EQUALS"
    assert row["result_slots"][0]["operation_candidate"] == "发货"

    assert len(behaviors) == 1
    behavior = behaviors[0]
    assert behavior["operation_ref"] == "发货"
    assert behavior["object_refs"] == ["订单"]
    assert behavior["permission_decision"] == "ALLOW"
    assert behavior["status"] == "CANDIDATE"
    assert behavior["formal_business_rule"] is False
    assert conflicts == []
    assert any(row["kind"] == "BUSINESS_BEHAVIOR_CANDIDATE_UNCONFIRMED" for row in unknowns)
    assert gate["status"] == "PARTIAL_BUSINESS_BEHAVIOR_IR"


def test_negative_permission_has_precedence_over_allow_substring() -> None:
    asset = _asset(_matrix_structure(result_value="不允许发货"))
    _rows, behaviors, conflicts, _unknowns, _gate = build_governed_business_behavior_ir(
        asset, [], [_operation()]
    )
    assert behaviors[0]["permission_decision"] == "DENY"
    assert behaviors[0]["status"] == "CANDIDATE"
    assert conflicts == []


def test_numeric_condition_normalizes_chinese_scale() -> None:
    structure = _matrix_structure(condition_value="金额>1万元")
    structure["blocks"][-2]["column_header_path"] = ["条件", "金额"]
    asset = _asset(structure)
    rows, _behaviors, _conflicts, _unknowns, _gate = build_governed_business_behavior_ir(
        asset, [], [_operation()]
    )
    slot = rows[0]["condition_slots"][0]
    assert slot["field_candidate"] == "金额"
    assert slot["operator_candidate"] == "GREATER_THAN"
    assert slot["value_candidate"]["normalized_value"] == 10000
    assert slot["value_candidate"]["unit"] == "元"


def test_matching_fact_and_matrix_row_merge_into_confirmed_behavior() -> None:
    fact = _fact("fact-allow", modality="MAY")
    asset = _asset(_matrix_structure(), [fact])
    _rows, behaviors, conflicts, unknowns, gate = build_governed_business_behavior_ir(
        asset, [fact], [_operation()]
    )

    assert len(behaviors) == 1
    behavior = behaviors[0]
    assert behavior["status"] == "CONFIRMED"
    assert behavior["formal_business_rule"] is True
    assert len(behavior["source_refs"]) == 2
    assert len(behavior["evidence"]) == 3
    assert conflicts == []
    assert not any(row["kind"] == "BUSINESS_BEHAVIOR_CANDIDATE_UNCONFIRMED" for row in unknowns)
    assert gate["status"] == "PASS"


def test_same_conditions_allow_and_deny_create_blocking_behavior_conflict() -> None:
    allow = _fact("fact-allow", modality="MAY")
    deny = _fact("fact-deny", modality="MUST_NOT")
    asset = _asset(_matrix_structure(), [allow, deny])
    _rows, behaviors, conflicts, _unknowns, gate = build_governed_business_behavior_ir(
        asset, [allow, deny], [_operation()]
    )

    assert {row["status"] for row in behaviors} == {"CONFLICTED"}
    assert len(conflicts) == 1
    assert conflicts[0]["kind"] == "BEHAVIOR_PERMISSION_DECISION_CONFLICT"
    assert gate["status"] == "BLOCKED_BUSINESS_BEHAVIOR_CONFLICT"


def test_closure_surfaces_candidate_behavior_and_keeps_model_partial() -> None:
    structure = _matrix_structure()
    model = empty_model()
    model["operations"] = [_operation()]
    result = apply_minimum_understanding_closure(model, _asset(structure))

    assert len(result["decision_matrix_row_ledger"]) == 1
    assert len(result["business_behaviors"]) == 1
    assert result["business_behaviors"][0]["status"] == "CANDIDATE"
    assert result["behavior_ir_gate"]["status"] == "PARTIAL_BUSINESS_BEHAVIOR_IR"
    assert result["gate"]["status"] == "PARTIAL_ENTERPRISE_UNDERSTANDING"
    assert result["metrics"]["candidate_behavior_count"] == 1
