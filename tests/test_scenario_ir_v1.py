from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.behavior_ir_logic_gate import (
    ensure_canonical_behavior_semantics,
    iter_condition_predicates,
    mandatory_outcomes,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding_projection import (
    project_final_scenario_planning_gate,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.scenario_ir import (
    build_scenario_ir_v1,
    project_scenario_ir_to_asset,
)


def _evidence() -> list[dict]:
    return [
        {
            "source_id": "prd",
            "source_locator": "prd.md#ship",
            "quote": "订单满足条件后执行发货",
            "fact_id": "fact:ship",
            "derivation": "source_span",
        }
    ]


def _behavior(
    *,
    behavior_id: str = "behavior:ship",
    permission: str = "ALLOW",
    actors: list[str] | None = None,
    operator: str = "GREATER_THAN_OR_EQUAL",
    value: int = 10000,
    state: bool = False,
    authorization_explicit: bool = False,
    modality: str = "ASSERTS",
) -> dict:
    row = {
        "schema": "qualibug.enterprise-business-behavior.v1",
        "behavior_id": behavior_id,
        "behavior_family_id": "behavior-family:ship",
        "source_kind": "ACCEPTED_BUSINESS_FACT",
        "source_refs": ["fact:ship"],
        "actor_refs": list(actors or []),
        "operation_ref": "发货",
        "object_refs": ["订单"],
        "trigger": {},
        "preconditions": [
            {
                "slot_id": "slot:amount",
                "raw_value": "金额至少1万元",
                "field_candidate": "金额",
                "operator_candidate": operator,
                "value_candidate": {
                    "raw": "1万元",
                    "value_type": "NUMBER",
                    "normalized_value": value,
                    "unit": "元",
                },
                "status": "CONFIRMED_SOURCE_TEXT",
            }
        ],
        "condition_combinator": "SINGLE_CONDITION",
        "expected_effects": [],
        "state_effects": (
            [
                {
                    "field": "status",
                    "from_state": "已审核",
                    "to_state": "已发货",
                    "raw": "订单状态从已审核变为已发货",
                }
            ]
            if state
            else []
        ),
        "data_effects": [],
        "permission_decision": permission,
        "business_modality": modality,
        "authorization_semantics_explicit": authorization_explicit,
        "exceptions": [],
        "compensations": [],
        "evidence": _evidence(),
        "unresolved_semantics": [],
        "status": "CONFIRMED",
        "candidate_only": False,
        "formal_business_rule": True,
    }
    return ensure_canonical_behavior_semantics(row)


def _binding(behavior: dict) -> dict:
    predicates = iter_condition_predicates(behavior["condition_expression"])
    outcomes = mandatory_outcomes(behavior)
    state_outcomes = [
        row for row in outcomes if row["outcome_type"] == "STATE_TRANSITION"
    ]
    return {
        "schema": "qualibug.business-behavior-implementation-binding.v1",
        "binding_id": f"binding:{behavior['behavior_id']}",
        "behavior_ref": behavior["behavior_id"],
        "primary_api_interface_ref": "api:POST:/orders/{id}/ship",
        "api_operation_bindings": [
            {
                "interface_id": "api:POST:/orders/{id}/ship",
                "method": "POST",
                "path": "/orders/{id}/ship",
                "operation_id": "shipOrder",
                "contract_fields": ["id", "金额", "status"],
                "status": "BOUND",
                "authoritative": True,
                "derivation": "authoritative_relationship",
            }
        ],
        "ui_action_bindings": [],
        "condition_observer_bindings": [
            {
                "slot_ref": row["slot_ref"],
                "source_field_candidate": row["field_candidate"],
                "status": "BOUND",
                "bindings": [
                    {
                        "binding_kind": "DATABASE_FIELD",
                        "table_id": "table:订单",
                        "field": "amount",
                        "authoritative": True,
                    }
                ],
            }
            for row in predicates
        ],
        "effect_observer_bindings": [
            {
                "slot_ref": "effect:status",
                "source_field_candidate": "status",
                "status": "BOUND",
                "bindings": [
                    {
                        "binding_kind": "DATABASE_FIELD",
                        "table_id": "table:订单",
                        "field": "status",
                        "authoritative": True,
                    }
                ],
            }
            for _row in state_outcomes
        ],
        "response_observer_bindings": [
            {
                "binding_kind": "API_RESPONSE_OUTCOME_CHANNEL",
                "interface_id": "api:POST:/orders/{id}/ship",
                "status": "BOUND_CHANNEL_ONLY",
                "authoritative": True,
            }
        ],
        "outcome_observer_bindings": [
            {
                "outcome_ref": row["outcome_id"],
                "outcome_type": row["outcome_type"],
                "status": "BOUND",
                "binding_kind": (
                    "API_RESPONSE_OUTCOME_CHANNEL"
                    if row["outcome_type"] == "PERMISSION_DECISION"
                    else "DATABASE_FIELD"
                ),
            }
            for row in outcomes
        ],
        "status": "BOUND",
        "scenario_planning_ready": True,
        "execution_ready": False,
        "evidence": _evidence(),
    }


def _model(behaviors: list[dict], bindings: list[dict]) -> dict:
    return {
        "gate": {"status": "PASS", "entry_allowed": True},
        "implementation_binding_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "scenario_planning_allowed": True,
            "execution_allowed": False,
            "metrics": {},
        },
        "business_behaviors": behaviors,
        "behavior_implementation_bindings": bindings,
    }


def _asset(*, allowed: bool = True) -> dict:
    return {
        "scenario_planning_gate": {
            "status": "PASS" if allowed else "PARTIAL_SCENARIO_PLANNING",
            "entry_allowed": allowed,
            "scenario_planning_allowed": allowed,
            "execution_allowed": False,
        },
        "summary": {},
        "governance": {},
        "coverage_gaps": [],
    }


def test_canonical_behavior_projects_positive_boundary_and_state_transition() -> None:
    behavior = _behavior(actors=["仓管员"], state=True)
    scenarios, unknowns, gate = build_scenario_ir_v1(
        _asset(), _model([behavior], [_binding(behavior)])
    )

    assert unknowns == []
    assert gate["status"] == "PASS"
    assert {row["scenario_type"] for row in scenarios} == {
        "POSITIVE",
        "BOUNDARY",
        "STATE_TRANSITION",
    }
    assert all(row["operation_clause"] == behavior["operation_clause"] for row in scenarios)
    assert all(row["condition_expression"] == behavior["condition_expression"] for row in scenarios)
    assert all(row["legacy_semantic_fields_are_projections"] is True for row in scenarios)
    base = next(row for row in scenarios if row["scenario_type"] == "POSITIVE")
    assert base["outcome_contracts"] == behavior["outcome_contracts"]
    assert base["observer_plan"]["outcome_observers"]
    boundary = next(row for row in scenarios if row["scenario_type"] == "BOUNDARY")
    assert boundary["boundary"]["threshold"] == 10000
    transition = next(row for row in scenarios if row["scenario_type"] == "STATE_TRANSITION")
    assert transition["state_transition_outcome_refs"]


def test_legacy_behavior_fields_cannot_override_canonical_scenario_semantics() -> None:
    behavior = _behavior(actors=["仓管员"])
    behavior["operation_ref"] = "取消订单"
    behavior["permission_decision"] = "DENY"
    behavior["preconditions"][0]["field_candidate"] = "伪造字段"
    behavior["expected_effects"] = ["伪造结果"]

    scenarios, unknowns, gate = build_scenario_ir_v1(
        _asset(), _model([behavior], [_binding(behavior)])
    )

    assert unknowns == []
    assert gate["status"] == "PASS"
    base = next(row for row in scenarios if row["scenario_type"] == "POSITIVE")
    assert base["operation_clause"]["operation_ref"] == "发货"
    assert base["operation_ref"] == "发货"
    assert base["condition_expression"]["field_candidate"] == "金额"
    assert base["expected_outcome"]["permission_decision"] == "ALLOW"
    assert "伪造结果" not in base["expected_outcome"]["expected_effects"]


def test_strict_boundary_changes_only_scenario_outcome_contract() -> None:
    behavior = _behavior(operator="GREATER_THAN")
    scenarios, unknowns, gate = build_scenario_ir_v1(
        _asset(), _model([behavior], [_binding(behavior)])
    )

    boundary = next(row for row in scenarios if row["scenario_type"] == "BOUNDARY")
    assert boundary["status"] == "INCOMPLETE"
    assert boundary["expected_outcome"]["permission_decision"] == "UNRESOLVED"
    assert behavior["outcome_contracts"][0]["status"] == "CONFIRMED"
    assert any(row["kind"] == "BOUNDARY_COMPLEMENT_OUTCOME_UNRESOLVED" for row in unknowns)
    assert gate["status"] == "PASS"


def test_authorization_denial_and_business_rejection_remain_distinct() -> None:
    denied_actor = _behavior(
        behavior_id="behavior:deny-role",
        permission="DENY",
        actors=["访客"],
        authorization_explicit=True,
    )
    generic_deny = _behavior(
        behavior_id="behavior:deny-business",
        permission="DENY",
        modality="MUST_NOT",
    )
    scenarios, _unknowns, gate = build_scenario_ir_v1(
        _asset(),
        _model(
            [denied_actor, generic_deny],
            [_binding(denied_actor), _binding(generic_deny)],
        ),
    )

    base_types = {
        row["behavior_ref"]: row["scenario_type"]
        for row in scenarios
        if row["scenario_type"] in {"UNAUTHORIZED", "REJECTION"}
    }
    assert base_types["behavior:deny-role"] == "UNAUTHORIZED"
    assert base_types["behavior:deny-business"] == "REJECTION"
    assert gate["status"] == "PASS"


def test_upstream_gate_closed_compiles_no_scenario() -> None:
    behavior = _behavior()
    scenarios, unknowns, gate = build_scenario_ir_v1(
        _asset(allowed=False), _model([behavior], [_binding(behavior)])
    )
    assert scenarios == []
    assert unknowns == []
    assert gate["status"] == "BLOCKED_SCENARIO_IR_UPSTREAM_GATE"


def test_missing_canonical_outcome_blocks_scenario_ir() -> None:
    behavior = _behavior(permission="UNSPECIFIED")
    binding = _binding(behavior)
    scenarios, unknowns, gate = build_scenario_ir_v1(
        _asset(), _model([behavior], [binding])
    )
    base = next(row for row in scenarios if row["scenario_type"] == "POSITIVE")
    assert base["status"] == "INCOMPLETE"
    assert any(row["kind"] == "SCENARIO_OUTCOME_CONTRACTS_UNRESOLVED" for row in unknowns)
    assert gate["status"] == "BLOCKED_SCENARIO_IR_INCOMPLETE"


def test_projection_is_idempotent_and_declares_canonical_governance() -> None:
    behavior = _behavior()
    asset = _asset()
    model = _model([behavior], [_binding(behavior)])
    project_scenario_ir_to_asset(asset, model)
    first = deepcopy(asset["scenario_ir"])
    project_scenario_ir_to_asset(asset, model)
    assert asset["scenario_ir"] == first
    assert asset["scenario_ir_gate"]["status"] == "PASS"
    assert asset["governance"]["scenario_ir_raw_text_reparse_allowed"] is False


def test_final_planning_projection_compiles_canonical_scenario_ir() -> None:
    behavior = _behavior()
    asset = {"summary": {}, "governance": {}, "coverage_gaps": [], "relationships": []}
    model = _model([behavior], [_binding(behavior)])
    project_final_scenario_planning_gate(asset, model)
    assert asset["scenario_planning_gate"]["status"] == "PASS"
    assert asset["scenario_ir_gate"]["status"] == "PASS"
    assert asset["scenario_ir"][0]["canonical_semantics_version"]
    assert asset["scenario_ir"][0]["execution_ready"] is False
