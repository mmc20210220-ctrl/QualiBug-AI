from __future__ import annotations

from typing import Any

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.behavior_ir_logic_gate import (
    build_business_behavior_ir_v1,
)


def _operation() -> dict[str, Any]:
    return {
        "operation_id": "operation-ship",
        "name": "发货",
        "raw_action_names": ["发货"],
        "object_refs": ["订单"],
        "evidence": [
            {
                "source_id": "source-rule",
                "source_locator": "rules.md#line=1",
                "quote": "发货",
                "derivation": "test",
            }
        ],
    }


def _fact(fact_id: str, *, combinator: str = "") -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "status": "ACCEPTED",
        "kind": "RULE",
        "raw_statement": "订单状态为已审核或待发货时可以发货",
        "subject": {"entity_refs": ["订单"], "actor_refs": []},
        "object": {"entity_refs": ["订单"]},
        "action": {"canonical": "发货", "raw": "发货"},
        "conditions": ["状态=已审核", "状态=待发货"],
        "trigger": {"condition_combinator": combinator} if combinator else {},
        "modality": "MAY",
        "source_spans": [
            {
                "source_id": "source-rule",
                "source_locator": f"rules.md#fact={fact_id}",
                "quote": "订单状态为已审核或待发货时可以发货",
            }
        ],
        "state_effects": [],
        "data_effects": [],
        "postconditions": [],
        "exceptions": [],
    }


def _asset(facts: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "business_fact_ledger": {"items": facts},
        "document_structure_assets": {"items": [], "errors": []},
    }


def test_multiple_conditions_without_explicit_logic_remain_incomplete_not_conflicted() -> None:
    fact = _fact("fact-unresolved")
    _rows, behaviors, conflicts, unknowns, gate = build_business_behavior_ir_v1(
        _asset([fact]), [fact], [_operation()]
    )

    assert conflicts == []
    assert len(behaviors) == 1
    behavior = behaviors[0]
    assert behavior["condition_combinator"] == "UNRESOLVED"
    assert behavior["status"] == "INCOMPLETE"
    assert "BEHAVIOR_CONDITION_COMBINATOR_UNRESOLVED" in behavior["unresolved_semantics"]
    assert any(
        row["reason_code"] == "BEHAVIOR_CONDITION_COMBINATOR_UNRESOLVED"
        for row in unknowns
    )
    assert gate["status"] == "PARTIAL_BUSINESS_BEHAVIOR_IR"
    assert gate["metrics"]["unresolved_condition_combinator_count"] == 1
    assert gate["multiple_conditions_are_implicitly_and"] is False


def test_explicit_or_restores_confirmed_behavior_and_avoids_false_contradiction() -> None:
    fact = _fact("fact-or", combinator="OR")
    _rows, behaviors, conflicts, unknowns, gate = build_business_behavior_ir_v1(
        _asset([fact]), [fact], [_operation()]
    )

    assert conflicts == []
    assert behaviors[0]["condition_combinator"] == "OR"
    assert behaviors[0]["status"] == "CONFIRMED"
    assert behaviors[0]["formal_business_rule"] is True
    assert not any(
        row["kind"] == "BUSINESS_BEHAVIOR_INCOMPLETE"
        for row in unknowns
    )
    assert gate["status"] == "PASS"


def test_explicit_and_with_incompatible_equalities_remains_conflicted() -> None:
    fact = _fact("fact-and", combinator="AND")
    _rows, behaviors, conflicts, _unknowns, gate = build_business_behavior_ir_v1(
        _asset([fact]), [fact], [_operation()]
    )

    assert behaviors[0]["condition_combinator"] == "AND"
    assert behaviors[0]["status"] == "CONFLICTED"
    assert len(conflicts) == 1
    assert conflicts[0]["kind"] == "BEHAVIOR_CONDITION_CONTRADICTION"
    assert gate["status"] == "BLOCKED_BUSINESS_BEHAVIOR_CONFLICT"
