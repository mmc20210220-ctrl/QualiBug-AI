from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding_projection import (
    project_final_scenario_planning_gate,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.scenario_ir import (
    build_scenario_ir_v1,
    project_scenario_ir_to_asset,
)


def _behavior(
    *,
    behavior_id: str = "behavior:ship",
    permission: str = "ALLOW",
    actors: list[str] | None = None,
    operator: str = "GREATER_THAN_OR_EQUAL",
    value: int = 10000,
    state_effects: list[dict] | None = None,
    expected_effects: list[str] | None = None,
) -> dict:
    return {
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
        "state_preconditions": [],
        "expected_effects": list(expected_effects or []),
        "state_effects": list(state_effects or []),
        "data_effects": [],
        "permission_decision": permission,
        "exceptions": [],
        "compensations": [],
        "evidence": [
            {
                "source_id": "prd",
                "source_locator": "prd.md#ship",
                "quote": "订单满足条件后执行发货",
                "fact_id": "fact:ship",
                "derivation": "source_span",
            }
        ],
        "unresolved_semantics": [],
        "status": "CONFIRMED",
        "candidate_only": False,
        "formal_business_rule": True,
    }


def _binding(behavior_id: str = "behavior:ship") -> dict:
    return {
        "schema": "qualibug.business-behavior-implementation-binding.v1",
        "binding_id": f"binding:{behavior_id}",
        "behavior_ref": behavior_id,
        "behavior_status": "CONFIRMED",
        "operation_ref": "发货",
        "object_refs": ["订单"],
        "primary_api_interface_ref": "api:POST:/orders/{id}/ship",
        "api_operation_bindings": [
            {
                "interface_id": "api:POST:/orders/{id}/ship",
                "method": "POST",
                "path": "/orders/{id}/ship",
                "operation_id": "shipOrder",
                "status": "BOUND",
                "authoritative": True,
                "derivation": "authoritative_relationship",
                "evidence": [
                    {
                        "source_id": "openapi",
                        "source_locator": "openapi.yaml#shipOrder",
                        "derivation": "behavior_action_binding",
                    }
                ],
            }
        ],
        "ui_action_bindings": [
            {
                "ui_spec_id": "ui:order-detail",
                "label": "发货",
                "status": "CANDIDATE_DESIGN_BINDING",
                "authoritative": False,
                "executable_locator_available": False,
            }
        ],
        "condition_observer_bindings": [
            {
                "slot_ref": "slot:amount",
                "purpose": "PRECONDITION_OBSERVER",
                "source_field_candidate": "金额",
                "status": "BOUND",
                "bindings": [
                    {
                        "binding_kind": "DATABASE_FIELD",
                        "field_id": "field:order:amount",
                        "table_id": "table:订单",
                        "field": "amount",
                        "authoritative": True,
                    }
                ],
            }
        ],
        "effect_observer_bindings": [],
        "response_observer_bindings": [
            {
                "binding_kind": "API_RESPONSE_OUTCOME_CHANNEL",
                "interface_id": "api:POST:/orders/{id}/ship",
                "status": "BOUND_CHANNEL_ONLY",
                "authoritative": True,
                "expected_assertion_compiled": False,
            }
        ],
        "status": "BOUND",
        "scenario_planning_ready": True,
        "execution_ready": False,
        "request_payload_compiled": False,
        "expected_assertion_compiled": False,
        "evidence": [
            {
                "source_id": "openapi",
                "source_locator": "openapi.yaml#shipOrder",
                "derivation": "behavior_action_binding",
            }
        ],
    }


def _model(behaviors: list[dict], bindings: list[dict]) -> dict:
    return {
        "gate": {"status": "PASS", "entry_allowed": True},
        "implementation_binding_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "scenario_planning_allowed": True,
            "execution_allowed": False,
            "metrics": {
                "behavior_binding_count": len(bindings),
                "scenario_ready_binding_count": len(bindings),
            },
        },
        "business_behaviors": behaviors,
        "behavior_implementation_bindings": bindings,
    }


def _asset(*, allowed: bool = True) -> dict:
    return {
        "scenario_planning_gate": {
            "status": "PASS" if allowed else "PARTIAL_SCENARIO_PLANNING_IMPLEMENTATION_BINDING",
            "entry_allowed": allowed,
            "scenario_planning_allowed": allowed,
            "execution_allowed": False,
        },
        "summary": {},
        "governance": {},
        "coverage_gaps": [],
    }


def test_allow_behavior_compiles_positive_boundary_and_state_transition() -> None:
    behavior = _behavior(
        actors=["仓管员"],
        state_effects=[{"from_state": "已审核", "to_state": "已发货"}],
    )
    scenarios, unknowns, gate = build_scenario_ir_v1(
        _asset(), _model([behavior], [_binding()])
    )

    assert unknowns == []
    assert gate["status"] == "PASS"
    assert {row["scenario_type"] for row in scenarios} == {
        "POSITIVE",
        "BOUNDARY",
        "STATE_TRANSITION",
    }
    boundary = next(row for row in scenarios if row["scenario_type"] == "BOUNDARY")
    assert boundary["status"] == "PLANNABLE"
    assert boundary["boundary"]["threshold"] == 10000
    assert boundary["expected_outcome"]["permission_decision"] == "ALLOW"
    transition = next(
        row for row in scenarios if row["scenario_type"] == "STATE_TRANSITION"
    )
    assert transition["state_transition_expectations"] == [
        {"from_state": "已审核", "to_state": "已发货"}
    ]
    assert all(row["execution_ready"] is False for row in scenarios)
    assert all(row["request_payload_compiled"] is False for row in scenarios)
    assert all(row["expected_assertion_compiled"] is False for row in scenarios)


def test_strict_boundary_keeps_complement_outcome_unresolved_without_blocking_base() -> None:
    behavior = _behavior(operator="GREATER_THAN")
    scenarios, unknowns, gate = build_scenario_ir_v1(
        _asset(), _model([behavior], [_binding()])
    )

    boundary = next(row for row in scenarios if row["scenario_type"] == "BOUNDARY")
    assert boundary["status"] == "INCOMPLETE"
    assert boundary["expected_outcome"]["permission_decision"] == "UNRESOLVED"
    assert boundary["boundary"]["adjacent_value_generation_allowed"] is False
    unresolved = next(
        row
        for row in unknowns
        if row["kind"] == "BOUNDARY_COMPLEMENT_OUTCOME_UNRESOLVED"
    )
    assert unresolved["blocks_scenario_ir"] is False
    assert gate["status"] == "PASS"
    assert gate["metrics"]["plannable_scenario_count"] == 1
    assert gate["metrics"]["incomplete_scenario_count"] == 1


def test_explicit_denied_actor_is_unauthorized_but_actorless_deny_is_rejection() -> None:
    denied_actor = _behavior(
        behavior_id="behavior:deny-ship-role",
        permission="DENY",
        actors=["访客"],
    )
    generic_deny = _behavior(
        behavior_id="behavior:deny-ship",
        permission="DENY",
        actors=[],
    )
    scenarios, _unknowns, gate = build_scenario_ir_v1(
        _asset(),
        _model(
            [denied_actor, generic_deny],
            [
                _binding("behavior:deny-ship-role"),
                _binding("behavior:deny-ship"),
            ],
        ),
    )

    base_types = {
        row["behavior_ref"]: row["scenario_type"]
        for row in scenarios
        if row["scenario_type"] in {"UNAUTHORIZED", "REJECTION"}
    }
    assert base_types["behavior:deny-ship-role"] == "UNAUTHORIZED"
    assert base_types["behavior:deny-ship"] == "REJECTION"
    assert gate["status"] == "PASS"


def test_upstream_gate_closed_compiles_no_scenario() -> None:
    scenarios, unknowns, gate = build_scenario_ir_v1(
        _asset(allowed=False), _model([_behavior()], [_binding()])
    )

    assert scenarios == []
    assert unknowns == []
    assert gate["status"] == "BLOCKED_SCENARIO_IR_UPSTREAM_GATE"
    assert gate["entry_allowed"] is False
    assert gate["execution_allowed"] is False


def test_missing_semantic_outcome_blocks_scenario_ir() -> None:
    behavior = _behavior(permission="UNSPECIFIED", expected_effects=[])
    scenarios, unknowns, gate = build_scenario_ir_v1(
        _asset(), _model([behavior], [_binding()])
    )

    base = next(row for row in scenarios if row["scenario_type"] == "POSITIVE")
    assert base["status"] == "INCOMPLETE"
    assert any(
        row["kind"] == "SCENARIO_EXPECTED_OUTCOME_UNRESOLVED"
        and row["blocks_scenario_ir"] is True
        for row in unknowns
    )
    assert gate["status"] == "BLOCKED_SCENARIO_IR_INCOMPLETE"


def test_projection_is_idempotent_and_updates_asset_and_model() -> None:
    asset = _asset()
    model = _model([_behavior()], [_binding()])

    project_scenario_ir_to_asset(asset, model)
    first = deepcopy(asset["scenario_ir"])
    project_scenario_ir_to_asset(asset, model)

    assert asset["scenario_ir"] == first
    assert model["scenario_ir"] == first
    assert asset["scenario_ir_gate"]["status"] == "PASS"
    assert asset["summary"]["scenario_ir_ready"] is True
    assert asset["summary"]["scenario_execution_allowed"] is False
    assert asset["governance"]["scenario_ir_is_non_executable"] is True


def test_final_planning_projection_compiles_scenario_ir_automatically() -> None:
    asset = {"summary": {}, "governance": {}, "coverage_gaps": []}
    model = _model([_behavior()], [_binding()])

    project_final_scenario_planning_gate(asset, model)

    assert asset["scenario_planning_gate"]["status"] == "PASS"
    assert asset["scenario_ir_gate"]["status"] == "PASS"
    assert asset["scenario_ir"]
    assert asset["scenario_ir"][0]["execution_ready"] is False
