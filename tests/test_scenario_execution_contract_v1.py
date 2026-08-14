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


def _interface(
    *, method: str = "POST", path: str = "/orders/{order_id}/ship"
) -> dict:
    return {
        "interface_id": "interface:ship",
        "source_id": "openapi",
        "method": method,
        "path": path,
        "operation_id": "shipOrder",
        "parameter_contracts": [
            {
                "name": "order_id",
                "field": "order_id",
                "location": "PATH",
                "required": True,
                "schema_type": "string",
            }
        ],
        "request_body_fields": [
            {
                "name": "status",
                "field": "status",
                "field_path": "status",
                "location": "BODY",
                "required": True,
                "schema_type": "string",
                "media_type": "application/json",
            },
            {
                "name": "reason",
                "field": "reason",
                "field_path": "reason",
                "location": "BODY",
                "required": False,
                "schema_type": "string",
                "media_type": "application/json",
            },
        ],
    }


def _scenario(
    *,
    scenario_id: str = "scenario:ship",
    method: str = "POST",
    path: str = "/orders/{order_id}/ship",
    observers: bool = True,
    canonical: bool = True,
) -> dict:
    predicate = {
        "schema": "qualibug.condition-expression.v1",
        "node_type": "PREDICATE",
        "predicate_id": "predicate:status",
        "slot_ref": "slot:status",
        "raw_value": "status=approved",
        "field_candidate": "status",
        "operator_candidate": "EQUALS",
        "value_candidate": {"raw": "approved", "value_type": "TEXT"},
        "status": "CONFIRMED",
        "source_backed": True,
    }
    outcome = {
        "schema": "qualibug.outcome-contract.v1",
        "outcome_id": "outcome:permission",
        "outcome_type": "PERMISSION_DECISION",
        "target_object_refs": ["订单"],
        "expected_decision": "ALLOW",
        "mandatory": True,
        "observation_phase": "RESPONSE",
        "caused_by_operation_ref": "发货",
        "status": "CONFIRMED",
        "evidence": _evidence(),
    }
    row = {
        "schema": "qualibug.enterprise-test-scenario-ir.v1",
        "scenario_id": scenario_id,
        "scenario_type": "POSITIVE",
        "behavior_ref": "behavior:ship",
        "implementation_binding_ref": "binding:ship",
        "canonical_semantics_version": "operation-condition-outcome.v1",
        "operation_clause": {
            "schema": "qualibug.operation-clause.v1",
            "operation_ref": "发货",
            "actor_refs": ["仓管员"],
            "object_refs": ["订单"],
            "source_refs": ["fact:ship"],
            "evidence": _evidence(),
            "status": "CONFIRMED",
            "source_backed": True,
        },
        "condition_expression": predicate,
        "outcome_contracts": [outcome],
        "actor_refs": ["仓管员"],
        "object_refs": ["订单"],
        "operation_ref": "发货",
        "preconditions": [deepcopy(predicate)],
        "action_entry": {
            "interface_id": "interface:ship",
            "method": method,
            "path": path,
            "operation_id": "shipOrder",
            "contract_fields": ["order_id", "status", "reason"],
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
            "outcome_observers": (
                [
                    {
                        "outcome_ref": "outcome:permission",
                        "outcome_type": "PERMISSION_DECISION",
                        "status": "BOUND",
                        "binding_kind": "API_RESPONSE_OUTCOME_CHANNEL",
                    }
                ]
                if observers
                else []
            ),
            "effect_observers": [],
            "response_observers": [],
        },
        "expected_outcome": {
            "outcome_contracts": [outcome],
            "permission_decision": "ALLOW",
            "expected_effects": ["ALLOW"],
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
    if not canonical:
        for key in (
            "canonical_semantics_version",
            "operation_clause",
            "condition_expression",
            "outcome_contracts",
        ):
            row.pop(key, None)
    return row


def _binding(*, method: str = "POST", path: str = "/orders/{order_id}/ship") -> dict:
    return {
        "binding_id": "binding:ship",
        "behavior_ref": "behavior:ship",
        "scenario_planning_ready": True,
        "status": "BOUND",
        "api_operation_bindings": [
            {
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
        "condition_observer_bindings": [
            {
                "slot_ref": "slot:status",
                "purpose": "PRECONDITION_OBSERVER",
                "status": "BOUND",
                "bindings": [
                    {
                        "binding_kind": "API_CONTRACT_FIELD",
                        "interface_id": "interface:ship",
                        "field": "status",
                        "authoritative": True,
                    }
                ],
            }
        ],
        "evidence": _evidence(),
    }


def _asset(scenario: dict, *, gate_pass: bool = True) -> dict:
    return {
        "interfaces": [
            _interface(
                method=scenario["action_entry"]["method"],
                path=scenario["action_entry"]["path"],
            )
        ],
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


def test_canonical_projection_compiles_predicate_and_outcome_requirements() -> None:
    asset = _asset(_scenario())
    project_governed_scenario_execution_contracts(asset, _model(_binding()))

    assert asset["scenario_execution_contract_gate"]["status"] == "PASS"
    contract = asset["scenario_execution_contracts"][0]
    assert contract["status"] == "REQUIREMENTS_READY"
    assert contract["operation_clause"]["operation_ref"] == "发货"
    assert contract["condition_expression"]["node_type"] == "PREDICATE"
    assert contract["outcome_contracts"][0]["outcome_id"] == "outcome:permission"
    assert contract["execution_allowed"] is False

    path_requirement = contract["request_contract"]["path_parameter_requirements"][0]
    assert path_requirement["field"] == "order_id"
    assert path_requirement["runtime_value_source"] == "RUNTIME_ENTITY_IDENTIFIER"
    request_requirement = contract["request_contract"]["request_field_requirements"][0]
    assert request_requirement["field"] == "status"
    assert request_requirement["predicate_ref"] == "predicate:status"
    assertion = contract["oracle_plan"]["outcome_assertion_requirements"][0]
    assert assertion["outcome_ref"] == "outcome:permission"
    assert assertion["observer_binding_complete"] is True


def test_legacy_scenario_fields_cannot_override_canonical_contract_semantics() -> None:
    scenario = _scenario()
    scenario["operation_ref"] = "取消订单"
    scenario["preconditions"][0]["field_candidate"] = "伪造字段"
    scenario["expected_outcome"]["permission_decision"] = "DENY"
    scenario["expected_outcome"]["expected_effects"] = ["伪造结果"]

    asset = _asset(scenario)
    project_governed_scenario_execution_contracts(asset, _model(_binding()))

    contract = asset["scenario_execution_contracts"][0]
    assert contract["status"] == "REQUIREMENTS_READY"
    assert contract["operation_clause"]["operation_ref"] == "发货"
    requirement = contract["request_contract"]["request_field_requirements"][0]
    assert requirement["field_candidate"] == "status"
    assert contract["oracle_plan"]["permission_decision_requirement"] == "ALLOW"
    assert "伪造结果" not in contract["oracle_plan"]["semantic_effect_requirements"]


def test_read_only_action_does_not_require_cleanup() -> None:
    scenario = _scenario(method="GET", path="/orders/{order_id}")
    asset = _asset(scenario)
    project_governed_scenario_execution_contracts(
        asset, _model(_binding(method="GET", path="/orders/{order_id}"))
    )
    cleanup = asset["scenario_execution_contracts"][0]["cleanup_requirements"]
    assert cleanup["write_action"] is False
    assert cleanup["cleanup_required"] is False
    assert cleanup["strategy_requirement"] == "NOT_REQUIRED_READ_ONLY_ACTION"


def test_missing_mandatory_outcome_observer_blocks_contract() -> None:
    asset = _asset(_scenario(observers=False))
    project_governed_scenario_execution_contracts(asset, _model(_binding()))
    gate = asset["scenario_execution_contract_gate"]
    contract = asset["scenario_execution_contracts"][0]
    assert gate["status"] == "BLOCKED_EXECUTION_CONTRACT_INCOMPLETE"
    assert contract["status"] == "INCOMPLETE"
    assert "EXECUTION_CONTRACT_OUTCOME_OBSERVER_UNRESOLVED" in contract[
        "unresolved_contract_semantics"
    ]


def test_legacy_only_scenario_is_not_reparsed() -> None:
    asset = _asset(_scenario(canonical=False))
    contracts, unknowns, gate = build_scenario_execution_contracts(
        asset, _model(_binding())
    )
    assert gate["status"] == "BLOCKED_EXECUTION_CONTRACT_INCOMPLETE"
    assert contracts[0]["status"] == "INCOMPLETE"
    assert any(
        row["kind"] == "EXECUTION_CONTRACT_CANONICAL_SEMANTICS_MISSING"
        for row in unknowns
    )
    assert contracts[0]["downstream_raw_text_reparse_allowed"] is False


def test_upstream_scenario_ir_gate_closed_builds_no_contract() -> None:
    contracts, unknowns, gate = build_scenario_execution_contracts(
        _asset(_scenario(), gate_pass=False), _model(_binding())
    )
    assert contracts == []
    assert unknowns == []
    assert gate["status"] == "BLOCKED_EXECUTION_CONTRACT_UPSTREAM_SCENARIO_IR_GATE"


def test_projection_is_idempotent_and_declares_canonical_governance() -> None:
    asset = _asset(_scenario())
    model = _model(_binding())
    project_governed_scenario_execution_contracts(asset, model)
    first_contracts = deepcopy(asset["scenario_execution_contracts"])
    first_relationships = deepcopy(asset["scenario_execution_contract_relationships"])
    project_governed_scenario_execution_contracts(asset, model)
    assert asset["scenario_execution_contracts"] == first_contracts
    assert asset["scenario_execution_contract_relationships"] == first_relationships
    assert asset["governance"]["execution_contract_raw_text_reparse_allowed"] is False


def test_final_scenario_projection_automatically_compiles_canonical_contract() -> None:
    behavior = ensure_canonical_behavior_semantics(
        {
            "behavior_id": "behavior:ship",
            "behavior_family_id": "behavior-family:ship",
            "source_kind": "ACCEPTED_BUSINESS_FACT",
            "source_refs": ["fact:ship"],
            "status": "CONFIRMED",
            "permission_decision": "ALLOW",
            "actor_refs": ["仓管员"],
            "object_refs": ["订单"],
            "operation_ref": "发货",
            "preconditions": [
                {
                    "slot_id": "slot:status",
                    "raw_value": "status=approved",
                    "field_candidate": "status",
                    "operator_candidate": "EQUALS",
                    "value_candidate": {"raw": "approved", "value_type": "TEXT"},
                }
            ],
            "condition_combinator": "SINGLE_CONDITION",
            "state_effects": [],
            "data_effects": [],
            "expected_effects": [],
            "exceptions": [],
            "compensations": [],
            "evidence": _evidence(),
            "unresolved_semantics": [],
        }
    )
    predicates = iter_condition_predicates(behavior["condition_expression"])
    outcomes = mandatory_outcomes(behavior)
    binding = {
        **_binding(),
        "condition_observer_bindings": [
            {
                "slot_ref": row["slot_ref"],
                "source_field_candidate": row["field_candidate"],
                "status": "BOUND",
                "bindings": [
                    {
                        "binding_kind": "API_CONTRACT_FIELD",
                        "interface_id": "interface:ship",
                        "field": row["field_candidate"],
                        "authoritative": True,
                    }
                ],
            }
            for row in predicates
        ],
        "effect_observer_bindings": [],
        "response_observer_bindings": [],
        "outcome_observer_bindings": [
            {
                "outcome_ref": row["outcome_id"],
                "outcome_type": row["outcome_type"],
                "status": "BOUND",
                "binding_kind": "API_RESPONSE_OUTCOME_CHANNEL",
            }
            for row in outcomes
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
    asset = {
        "summary": {},
        "governance": {},
        "coverage_gaps": [],
        "relationships": [],
        "interfaces": [_interface()],
    }
    project_final_scenario_planning_gate(asset, model)
    assert asset["scenario_ir_gate"]["status"] == "PASS"
    assert asset["scenario_execution_contract_gate"]["status"] == "PASS"
    contract = asset["scenario_execution_contracts"][0]
    assert contract["canonical_semantics_version"] == "operation-condition-outcome.v1"
    assert contract["execution_allowed"] is False
