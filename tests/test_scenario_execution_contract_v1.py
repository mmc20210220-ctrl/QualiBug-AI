from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding_projection import (
    project_final_scenario_planning_gate,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.scenario_execution_contract import (
    build_scenario_execution_contracts,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.scenario_execution_contract_projection import (
    project_governed_scenario_execution_contracts,
)


def _evidence() -> list[dict]:
    return [
        {
            "source_id": "source:order-policy",
            "source_locator": "订单制度.pdf#page=8",
            "quote": "已审核订单允许仓管员发货",
            "derivation": "source_span",
        }
    ]


def _scenario(
    *,
    scenario_id: str = "scenario:ship",
    method: str = "POST",
    path: str = "/orders/{order_id}/ship",
    observers: bool = True,
) -> dict:
    return {
        "schema": "qualibug.enterprise-test-scenario-ir.v1",
        "scenario_id": scenario_id,
        "scenario_type": "POSITIVE",
        "behavior_ref": "behavior:ship",
        "implementation_binding_ref": "binding:ship",
        "actor_refs": ["仓管员"],
        "object_refs": ["订单"],
        "operation_ref": "发货",
        "preconditions": [
            {
                "slot_id": "slot:status",
                "field_candidate": "status",
                "operator_candidate": "EQUALS",
                "value_candidate": {
                    "raw": "approved",
                    "value_type": "TEXT",
                },
                "header_path": ["条件", "status"],
            }
        ],
        "action_entry": {
            "interface_id": "interface:ship",
            "method": method,
            "path": path,
            "operation_id": "shipOrder",
            "derivation": "authoritative_relationship",
            "authoritative": True,
        },
        "observer_plan": {
            "condition_observers": [
                {
                    "slot_ref": "slot:status",
                    "status": "BOUND",
                    "bindings": [
                        {
                            "binding_kind": "DATABASE_FIELD",
                            "table": "orders",
                            "field": "status",
                            "authoritative": True,
                        }
                    ],
                }
            ],
            "effect_observers": [],
            "response_observers": (
                [
                    {
                        "binding_kind": "API_RESPONSE_OUTCOME_CHANNEL",
                        "interface_id": "interface:ship",
                        "status": "BOUND_CHANNEL_ONLY",
                        "authoritative": True,
                    }
                ]
                if observers
                else []
            ),
        },
        "expected_outcome": {
            "permission_decision": "ALLOW",
            "expected_effects": [],
            "state_effects": [],
            "data_effects": [],
            "concrete_assertion_compiled": False,
        },
        "exceptions": [],
        "compensations": [],
        "evidence": _evidence(),
        "unresolved_semantics": [],
        "status": "PLANNABLE",
        "formal_scenario_ir": True,
        "execution_ready": False,
    }


def _binding(*, method: str = "POST", path: str = "/orders/{order_id}/ship") -> dict:
    return {
        "binding_id": "binding:ship",
        "behavior_ref": "behavior:ship",
        "scenario_planning_ready": True,
        "status": "BOUND",
        "api_operation_bindings": [
            {
                "binding_id": "api-binding:ship",
                "interface_id": "interface:ship",
                "method": method,
                "path": path,
                "operation_id": "shipOrder",
                "status": "BOUND",
                "authoritative": True,
                "derivation": "authoritative_relationship",
                "contract_fields": ["order_id", "status", "reason"],
                "evidence": _evidence(),
            }
        ],
        "evidence": _evidence(),
    }


def _asset(scenario: dict, *, gate_pass: bool = True) -> dict:
    return {
        "scenario_ir": [scenario],
        "scenario_ir_gate": {
            "status": "PASS" if gate_pass else "BLOCKED_SCENARIO_IR_UPSTREAM_GATE",
            "entry_allowed": gate_pass,
            "scenario_ir_ready": gate_pass,
            "execution_allowed": False,
        },
        "summary": {},
        "governance": {},
        "coverage_gaps": [],
        "relationships": [],
    }


def _model(binding: dict) -> dict:
    return {
        "behavior_implementation_bindings": [binding],
        "source_summary": {},
        "metrics": {},
    }


def test_governed_projection_compiles_runtime_requirements_without_values() -> None:
    asset = _asset(_scenario())
    model = _model(_binding())

    project_governed_scenario_execution_contracts(asset, model)

    assert asset["scenario_execution_contract_gate"]["status"] == "PASS"
    assert len(asset["scenario_execution_contracts"]) == 1
    contract = asset["scenario_execution_contracts"][0]
    assert contract["status"] == "REQUIREMENTS_READY"
    assert contract["execution_allowed"] is False
    assert contract["request_payload_compiled"] is False
    assert contract["credentials_selected"] is False
    assert contract["expected_assertions_compiled"] is False

    path_requirement = contract["request_contract"]["path_parameter_requirements"][0]
    assert path_requirement["field"] == "order_id"
    assert path_requirement["runtime_value_source"] == "RUNTIME_ENTITY_IDENTIFIER"
    assert path_requirement["runtime_value_materialized"] is False

    request_requirement = contract["request_contract"]["request_field_requirements"][0]
    assert request_requirement["field"] == "status"
    assert request_requirement["location"] == "UNRESOLVED_CONTRACT_LOCATION"
    assert request_requirement["semantic_value_requirement"]["raw"] == "approved"
    assert request_requirement["runtime_value_materialized"] is False

    assert contract["credential_requirements"][0]["actor_ref"] == "仓管员"
    assert contract["credential_requirements"][0]["credential_selected"] is False
    assert contract["cleanup_requirements"]["cleanup_required"] is True
    assert (
        contract["cleanup_requirements"]["strategy_requirement"]
        == "REVERSIBLE_CLEANUP_OR_ISOLATED_SANDBOX_REQUIRED"
    )


def test_read_only_action_does_not_require_cleanup() -> None:
    scenario = _scenario(method="GET", path="/orders/{order_id}")
    binding = _binding(method="GET", path="/orders/{order_id}")
    asset = _asset(scenario)

    project_governed_scenario_execution_contracts(asset, _model(binding))

    contract = asset["scenario_execution_contracts"][0]
    cleanup = contract["cleanup_requirements"]
    assert cleanup["write_action"] is False
    assert cleanup["cleanup_required"] is False
    assert cleanup["strategy_requirement"] == "NOT_REQUIRED_READ_ONLY_ACTION"


def test_missing_permission_response_observer_blocks_execution_contract() -> None:
    asset = _asset(_scenario(observers=False))
    model = _model(_binding())

    project_governed_scenario_execution_contracts(asset, model)

    gate = asset["scenario_execution_contract_gate"]
    assert gate["status"] == "BLOCKED_EXECUTION_CONTRACT_INCOMPLETE"
    contract = asset["scenario_execution_contracts"][0]
    assert contract["status"] == "INCOMPLETE"
    assert "EXECUTION_CONTRACT_PERMISSION_RESPONSE_OBSERVER_UNRESOLVED" in contract[
        "unresolved_contract_semantics"
    ]
    assert asset["scenario_execution_contract_unknowns"][0][
        "blocks_execution_contract"
    ] is True


def test_upstream_scenario_ir_gate_closed_builds_no_contract() -> None:
    scenario = _scenario()
    asset = _asset(scenario, gate_pass=False)

    contracts, unknowns, gate = build_scenario_execution_contracts(
        asset, _model(_binding())
    )

    assert contracts == []
    assert unknowns == []
    assert gate["status"] == "BLOCKED_EXECUTION_CONTRACT_UPSTREAM_SCENARIO_IR_GATE"
    assert gate["execution_allowed"] is False


def test_projection_is_idempotent_and_creates_relationships() -> None:
    asset = _asset(_scenario())
    model = _model(_binding())

    project_governed_scenario_execution_contracts(asset, model)
    first_contracts = deepcopy(asset["scenario_execution_contracts"])
    first_relationships = deepcopy(asset["scenario_execution_contract_relationships"])
    project_governed_scenario_execution_contracts(asset, model)

    assert asset["scenario_execution_contracts"] == first_contracts
    assert asset["scenario_execution_contract_relationships"] == first_relationships
    assert {
        row["relation"] for row in asset["scenario_execution_contract_relationships"]
    } == {
        "scenario_ir_to_execution_contract",
        "execution_contract_to_interface",
    }
    assert asset["summary"]["scenario_execution_allowed"] is False
    assert asset["governance"][
        "scenario_execution_contract_does_not_enable_execution"
    ] is True


def test_final_scenario_projection_automatically_compiles_execution_contract() -> None:
    behavior = {
        "behavior_id": "behavior:ship",
        "behavior_family_id": "behavior-family:ship",
        "status": "CONFIRMED",
        "permission_decision": "ALLOW",
        "actor_refs": ["仓管员"],
        "object_refs": ["订单"],
        "operation_ref": "发货",
        "preconditions": _scenario()["preconditions"],
        "state_effects": [],
        "data_effects": [],
        "expected_effects": [],
        "exceptions": [],
        "compensations": [],
        "evidence": _evidence(),
    }
    binding = {
        **_binding(),
        "condition_observer_bindings": _scenario()["observer_plan"][
            "condition_observers"
        ],
        "effect_observer_bindings": [],
        "response_observer_bindings": _scenario()["observer_plan"][
            "response_observers"
        ],
    }
    model = {
        "gate": {"status": "PASS", "entry_allowed": True},
        "implementation_binding_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "scenario_planning_allowed": True,
            "execution_allowed": False,
            "metrics": {},
        },
        "business_behaviors": [behavior],
        "behavior_implementation_bindings": [binding],
        "source_summary": {},
        "metrics": {},
    }
    asset = {"summary": {}, "governance": {}, "coverage_gaps": [], "relationships": []}

    project_final_scenario_planning_gate(asset, model)

    assert asset["scenario_ir_gate"]["status"] == "PASS"
    assert asset["scenario_execution_contract_gate"]["status"] == "PASS"
    assert asset["scenario_execution_contracts"]
    assert asset["scenario_execution_contracts"][0]["execution_allowed"] is False
