from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.behavior_ir_logic_gate import (
    build_condition_expression,
    condition_expression_combinator,
    condition_expression_complete,
    ensure_canonical_behavior_semantics,
    iter_condition_predicates,
    mandatory_outcomes,
    outcome_contracts_complete,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding_governance import (
    _outcome_observer_bindings,
)


def _slot(slot_id: str, field: str, value: str) -> dict:
    return {
        "slot_id": slot_id,
        "raw_value": f"{field}={value}",
        "field_candidate": field,
        "operator_candidate": "EQUALS",
        "value_candidate": {"raw": value, "value_type": "TEXT"},
        "status": "CONFIRMED_SOURCE_TEXT",
    }


def _permission_behavior() -> dict:
    return {
        "schema": "qualibug.enterprise-business-behavior.v1",
        "behavior_id": "behavior:ship",
        "behavior_family_id": "family:ship-order",
        "source_kind": "ACCEPTED_BUSINESS_FACT",
        "source_refs": ["fact:ship"],
        "actor_refs": ["仓管员"],
        "operation_ref": "发货",
        "object_refs": ["订单"],
        "preconditions": [_slot("slot:status", "status", "approved")],
        "condition_combinator": "SINGLE_CONDITION",
        "condition_frame": {},
        "expected_effects": [],
        "state_effects": [],
        "data_effects": [],
        "permission_decision": "ALLOW",
        "exceptions": [],
        "compensations": [],
        "evidence": [
            {
                "source_id": "prd",
                "source_locator": "prd.md#ship",
                "quote": "已审核订单允许发货",
            }
        ],
        "unresolved_semantics": [],
        "status": "CONFIRMED",
        "candidate_only": False,
        "formal_business_rule": True,
    }


def test_nested_branch_and_exception_are_part_of_one_condition_authority() -> None:
    behavior = _permission_behavior()
    behavior["preconditions"] = [
        _slot("slot:amount", "amount", "1000"),
        _slot("slot:status", "status", "draft"),
    ]
    behavior["condition_combinator"] = "AND"
    behavior["condition_frame"] = {
        "kind": "IF_THEN_ELSE",
        "branch": "THEN",
        "branch_index": 0,
        "exception_scopes": ["管理员"],
        "parent_conditions": [],
    }

    expression = build_condition_expression(behavior)

    assert expression["node_type"] == "BRANCH"
    assert expression["guard"]["node_type"] == "EXCEPT"
    assert expression["guard"]["child"]["node_type"] == "ALL"
    assert condition_expression_combinator(expression) == "AND"
    assert condition_expression_complete(expression) is True
    assert len(iter_condition_predicates(expression)) == 2


def test_multiple_conditions_without_explicit_combinator_never_default_to_and() -> None:
    behavior = _permission_behavior()
    behavior["preconditions"] = [
        _slot("slot:a", "status", "approved"),
        _slot("slot:b", "status", "pending"),
    ]
    behavior["condition_combinator"] = ""

    result = ensure_canonical_behavior_semantics(behavior)

    assert result["condition_expression"]["node_type"] == "UNRESOLVED"
    assert result["condition_combinator"] == "UNRESOLVED"
    assert "BEHAVIOR_CONDITION_COMBINATOR_UNRESOLVED" in result["unresolved_semantics"]
    assert result["status"] == "INCOMPLETE"
    assert result["formal_business_rule"] is False


def test_legacy_behavior_projects_operation_and_permission_outcome_once() -> None:
    behavior = ensure_canonical_behavior_semantics(_permission_behavior())

    assert behavior["operation_clause"]["operation_ref"] == "发货"
    assert behavior["operation_clause"]["status"] == "CONFIRMED"
    assert behavior["condition_expression"]["node_type"] == "PREDICATE"
    assert mandatory_outcomes(behavior)[0]["outcome_type"] == "PERMISSION_DECISION"
    assert outcome_contracts_complete(behavior) is True
    assert behavior["legacy_semantic_fields_are_projections"] is True
    assert behavior["status"] == "CONFIRMED"


def test_unstructured_mandatory_result_cannot_become_formal_oracle() -> None:
    behavior = _permission_behavior()
    behavior["permission_decision"] = "UNSPECIFIED"
    behavior["expected_effects"] = ["通知相关人员"]

    result = ensure_canonical_behavior_semantics(behavior)

    assert mandatory_outcomes(result)[0]["outcome_type"] == "ASSERTION_TEXT"
    assert mandatory_outcomes(result)[0]["status"] == "SOURCE_TEXT_ONLY"
    assert outcome_contracts_complete(result) is False
    assert "BEHAVIOR_MANDATORY_OUTCOME_UNRESOLVED" in result["unresolved_semantics"]
    assert result["status"] == "INCOMPLETE"


def test_every_mandatory_outcome_requires_its_own_observer_binding() -> None:
    behavior = _permission_behavior()
    behavior["state_effects"] = [
        {
            "field": "status",
            "from_state": "APPROVED",
            "to_state": "SHIPPED",
            "raw": "订单状态变为SHIPPED",
        }
    ]
    behavior = ensure_canonical_behavior_semantics(behavior)
    state_outcome = next(
        row
        for row in mandatory_outcomes(behavior)
        if row["outcome_type"] == "STATE_TRANSITION"
    )
    assert state_outcome["observer_slot_ref"] == "state_effect:0"
    binding = {
        "response_observer_bindings": [
            {
                "authoritative": True,
                "status": "BOUND_CHANNEL_ONLY",
            }
        ]
    }
    effect_slots = [
        {
            "slot_ref": "state_effect:0",
            "source_field_candidate": "status",
            "status": "BOUND",
        },
        {
            "slot_ref": "data_effect:0",
            "source_field_candidate": "status",
            "status": "BOUND",
        },
    ]

    rows = _outcome_observer_bindings(
        behavior,
        binding=binding,
        effect_slots=effect_slots,
    )

    assert len(rows) == 2
    assert {row["outcome_type"] for row in rows} == {
        "STATE_TRANSITION",
        "PERMISSION_DECISION",
    }
    assert all(row["status"] == "BOUND" for row in rows)

    only_permission = _outcome_observer_bindings(
        behavior,
        binding=binding,
        effect_slots=[],
    )
    assert any(
        row["outcome_type"] == "STATE_TRANSITION"
        and row["status"] == "UNBOUND"
        for row in only_permission
    )


def test_state_outcome_without_declared_field_never_invents_status() -> None:
    behavior = _permission_behavior()
    behavior["permission_decision"] = "UNSPECIFIED"
    behavior["state_effects"] = [
        {
            "from_state": "APPROVED",
            "to_state": "SHIPPED",
            "raw": "订单进入SHIPPED",
        }
    ]

    result = ensure_canonical_behavior_semantics(behavior)
    outcome = mandatory_outcomes(result)[0]

    assert outcome["outcome_type"] == "STATE_TRANSITION"
    assert outcome["field_ref"] == ""
    assert outcome["status"] == "UNRESOLVED"
    assert outcome["reason_code"] == "OUTCOME_STATE_FIELD_UNRESOLVED"
    assert result["status"] == "INCOMPLETE"
