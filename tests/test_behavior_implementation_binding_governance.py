from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.closure import (
    apply_minimum_understanding_closure,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding_governance import (
    build_governed_behavior_implementation_bindings,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.schema import (
    empty_model,
)


def _behavior() -> dict:
    return {
        "schema": "qualibug.enterprise-business-behavior.v1",
        "behavior_id": "behavior:ship",
        "behavior_family_id": "family:ship-order",
        "source_kind": "ACCEPTED_BUSINESS_FACT",
        "source_refs": ["fact:ship"],
        "actor_refs": ["仓管员"],
        "operation_ref": "发货",
        "object_refs": ["订单"],
        "trigger": {},
        "preconditions": [
            {
                "slot_id": "slot:status",
                "field_candidate": "status",
                "operator_candidate": "EQUALS",
                "value_candidate": {"raw": "approved", "value_type": "TEXT"},
                "status": "CONFIRMED_SOURCE_TEXT",
            }
        ],
        "state_preconditions": [],
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
                "fact_id": "fact:ship",
                "derivation": "source_span",
            }
        ],
        "unresolved_semantics": [],
        "condition_combinator": "SINGLE_CONDITION",
        "status": "CONFIRMED",
        "candidate_only": False,
        "formal_business_rule": True,
    }


def _interface(interface_id: str, path: str) -> dict:
    return {
        "interface_id": interface_id,
        "source_id": "openapi",
        "source_kind": "openapi",
        "method": "POST",
        "path": path,
        "operation_id": "发货",
        "summary": "发货",
        "parameters": ["status", "id"],
        "field_dictionary": ["status", "id"],
        "tokens": ["发货", "status"],
    }


def _field_assets() -> dict:
    return {
        "data_tables": [
            {
                "table_id": "table:订单",
                "source_id": "schema",
                "name": "订单",
                "columns": ["id", "status"],
            }
        ],
        "field_dictionary": [
            {
                "field_id": "field:order:status",
                "source_id": "schema",
                "table_id": "table:订单",
                "table": "订单",
                "field": "status",
                "field_path": "订单.status",
                "description": "订单状态",
            }
        ],
    }


def _relationship(target: str, edge_id: str = "edge:ship") -> dict:
    return {
        "edge_id": edge_id,
        "from": "fact:ship",
        "to": target,
        "relation": "behavior_to_interface",
        "status": "accepted",
        "derivation": "exact_source_section",
        "evidence_gate": "exact_source_section",
        "evidence": {"operation_locator": target},
    }


def test_multiple_authoritative_action_interfaces_remain_ambiguous() -> None:
    first = "api:POST:/orders/{id}/ship"
    second = "api:POST:/orders/{id}/dispatch"
    asset = {
        "interfaces": [
            _interface(first, "/orders/{id}/ship"),
            _interface(second, "/orders/{id}/dispatch"),
        ],
        **_field_assets(),
        "ui_design_specs": [],
        "relationships": [
            _relationship(first, "edge:ship:1"),
            _relationship(second, "edge:ship:2"),
        ],
        "rule_library": [],
    }

    bindings, unknowns, conflicts, gate = build_governed_behavior_implementation_bindings(
        asset, [_behavior()]
    )

    assert conflicts == []
    assert bindings[0]["authoritative_api_interface_count"] == 2
    assert bindings[0]["primary_api_interface_ref"] == ""
    assert bindings[0]["status"] == "AMBIGUOUS"
    assert bindings[0]["scenario_planning_ready"] is False
    assert any(
        row["kind"] == "BEHAVIOR_API_BINDING_MULTIPLE_AUTHORITATIVE"
        for row in unknowns
    )
    assert gate["status"] == "PARTIAL_IMPLEMENTATION_BINDING"
    assert gate["single_primary_action_interface_required"] is True


def test_api_request_field_alone_is_not_a_runtime_observer() -> None:
    interface_id = "api:POST:/orders/{id}/ship"
    asset = {
        "interfaces": [_interface(interface_id, "/orders/{id}/ship")],
        "data_tables": [],
        "field_dictionary": [],
        "ui_design_specs": [],
        "relationships": [_relationship(interface_id)],
        "rule_library": [],
    }

    bindings, unknowns, conflicts, gate = build_governed_behavior_implementation_bindings(
        asset, [_behavior()]
    )

    assert conflicts == []
    condition = bindings[0]["condition_observer_bindings"][0]
    assert condition["status"] == "CONTRACT_FIELD_ONLY"
    assert condition["request_contract_field_available"] is True
    assert condition["runtime_observer_available"] is False
    assert condition["api_request_field_is_runtime_observer"] is False
    assert bindings[0]["scenario_planning_ready"] is False
    assert any(
        row["kind"] == "IMPLEMENTATION_CONDITION_OBSERVER_UNRESOLVED"
        and row["reason_code"] == "API_CONTRACT_FIELD_IS_NOT_RUNTIME_OBSERVER"
        for row in unknowns
    )
    assert gate["status"] == "PARTIAL_IMPLEMENTATION_BINDING"


def test_missing_authoritative_interface_cannot_fall_back_to_exact_name() -> None:
    real_interface = "api:POST:/orders/{id}/ship"
    missing_interface = "api:POST:/legacy/orders/{id}/ship"
    asset = {
        "interfaces": [_interface(real_interface, "/orders/{id}/ship")],
        **_field_assets(),
        "ui_design_specs": [],
        "relationships": [_relationship(missing_interface)],
        "rule_library": [],
    }

    bindings, unknowns, conflicts, gate = build_governed_behavior_implementation_bindings(
        asset, [_behavior()]
    )

    assert conflicts == []
    assert bindings[0]["primary_api_interface_ref"] == real_interface
    assert bindings[0]["scenario_planning_ready"] is False
    assert any(
        row["kind"] == "BEHAVIOR_AUTHORITATIVE_INTERFACE_MISSING"
        and missing_interface in row["interface_refs"]
        for row in unknowns
    )
    assert gate["status"] == "PARTIAL_IMPLEMENTATION_BINDING"


def test_closure_projects_implementation_gate_to_asset_without_changing_semantic_gate() -> None:
    fact = {
        "fact_id": "fact:ship",
        "status": "ACCEPTED",
        "kind": "RULE",
        "raw_statement": "已审核订单允许发货",
        "subject": {"entity_refs": ["订单"], "actor_refs": ["仓管员"]},
        "object": {"entity_refs": ["订单"]},
        "action": {"canonical": "发货", "raw": "发货"},
        "conditions": ["status=approved"],
        "modality": "MAY",
        "polarity": "POSITIVE",
        "source_spans": [
            {
                "source_id": "prd",
                "locator": "prd.md#ship",
                "quote": "已审核订单允许发货",
            }
        ],
        "state_effects": [],
        "data_effects": [],
        "postconditions": [],
        "exceptions": [],
    }
    model = empty_model()
    model["operations"] = [
        {
            "operation_id": "operation:ship",
            "name": "发货",
            "raw_action_names": ["发货"],
            "object_refs": ["订单"],
            "actor_refs": ["仓管员"],
            "evidence": [
                {
                    "source_id": "prd",
                    "source_locator": "prd.md#ship",
                    "fact_id": "fact:ship",
                    "derivation": "source_span",
                }
            ],
        }
    ]
    asset = {
        "business_fact_ledger": {"items": [fact]},
        "source_inventory": [],
        "enterprise_comprehension_gate": {"entry_allowed": True, "status": "PASS"},
        "interfaces": [],
        "data_tables": [],
        "field_dictionary": [],
        "ui_design_specs": [],
        "relationships": [],
        "rule_library": [],
        "document_structure_assets": {"items": [], "errors": []},
        "summary": {},
        "coverage_gaps": [],
        "governance": {},
    }

    result = apply_minimum_understanding_closure(model, asset)

    assert result["gate"]["status"] == "PASS"
    assert result["gate"]["entry_allowed"] is True
    assert asset["implementation_binding_gate"]["status"] == "PARTIAL_IMPLEMENTATION_BINDING"
    assert asset["summary"]["scenario_planning_allowed"] is False
    assert asset["summary"]["implementation_execution_allowed"] is False
    assert any(row["kind"] == "IMPLEMENTATION_BINDING_PARTIAL" for row in asset["coverage_gaps"])
    assert asset["governance"]["semantic_and_implementation_gates_are_separate"] is True
