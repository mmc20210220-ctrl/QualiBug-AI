from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding_projection import (
    project_final_scenario_planning_gate,
)


def test_final_scenario_mainline_compiles_non_executable_runtime_plan() -> None:
    behavior = {
        "behavior_id": "behavior:ship-order",
        "behavior_family_id": "behavior-family:ship-order",
        "status": "CONFIRMED",
        "permission_decision": "ALLOW",
        "actor_refs": ["仓管员"],
        "object_refs": ["订单"],
        "operation_ref": "发货",
        "preconditions": [
            {
                "slot_id": "condition:status",
                "field_candidate": "status",
                "operator_candidate": "EQUALS",
                "value_candidate": {
                    "raw": "approved",
                    "value_type": "TEXT",
                },
            }
        ],
        "condition_combinator": "SINGLE",
        "expected_effects": [],
        "state_effects": [],
        "data_effects": [],
        "exceptions": [],
        "compensations": [],
        "evidence": [
            {
                "source_id": "source:policy",
                "source_locator": "policy.pdf#page=3;table=1;row=2",
                "quote": "已审核订单允许仓管员发货",
                "derivation": "source_span",
            }
        ],
    }
    binding = {
        "binding_id": "binding:ship-order",
        "behavior_ref": "behavior:ship-order",
        "behavior_status": "CONFIRMED",
        "operation_ref": "发货",
        "object_refs": ["订单"],
        "status": "BOUND",
        "scenario_planning_ready": True,
        "primary_api_interface_ref": "api:POST:/orders/{order_id}/ship",
        "api_operation_bindings": [
            {
                "binding_id": "api-binding:ship-order",
                "interface_id": "api:POST:/orders/{order_id}/ship",
                "method": "POST",
                "path": "/orders/{order_id}/ship",
                "operation_id": "shipOrder",
                "status": "BOUND",
                "authoritative": True,
                "derivation": "authoritative_relationship",
                "contract_fields": ["order_id", "status"],
                "evidence": [
                    {
                        "source_id": "source:openapi",
                        "source_locator": "POST /orders/{order_id}/ship",
                        "derivation": "behavior_action_binding",
                    }
                ],
            }
        ],
        "ui_action_bindings": [],
        "condition_observer_bindings": [
            {
                "slot_ref": "condition:status",
                "purpose": "PRECONDITION_OBSERVER",
                "source_field_candidate": "status",
                "status": "BOUND",
                "bindings": [
                    {
                        "binding_kind": "DATABASE_FIELD",
                        "table_id": "table:orders",
                        "table": "orders",
                        "field": "status",
                        "authoritative": True,
                    }
                ],
            }
        ],
        "effect_observer_bindings": [],
        "response_observer_bindings": [
            {
                "binding_kind": "API_RESPONSE_OUTCOME_CHANNEL",
                "interface_id": "api:POST:/orders/{order_id}/ship",
                "status": "BOUND_CHANNEL_ONLY",
                "authoritative": True,
                "expected_assertion_compiled": False,
            }
        ],
        "evidence": [
            {
                "source_id": "source:openapi",
                "source_locator": "POST /orders/{order_id}/ship",
                "derivation": "behavior_action_binding",
            }
        ],
        "execution_ready": False,
    }
    model = {
        "gate": {"status": "PASS", "entry_allowed": True},
        "implementation_binding_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "scenario_planning_allowed": True,
            "execution_allowed": False,
            "metrics": {"scenario_ready_binding_count": 1},
        },
        "business_behaviors": [behavior],
        "behavior_implementation_bindings": [binding],
        "source_summary": {},
        "metrics": {},
    }
    asset = {
        "interfaces": [
            {
                "interface_id": "api:POST:/orders/{order_id}/ship",
                "source_id": "source:openapi",
                "method": "POST",
                "path": "/orders/{order_id}/ship",
                "operation_id": "shipOrder",
                "summary": "发货",
                "parameter_contracts": [
                    {
                        "name": "order_id",
                        "field": "order_id",
                        "location": "PATH",
                        "required": True,
                        "schema_type": "STRING",
                        "source": "OPENAPI_PARAMETER",
                    },
                    {
                        "name": "status",
                        "field": "status",
                        "location": "QUERY",
                        "required": True,
                        "schema_type": "STRING",
                        "source": "OPENAPI_PARAMETER",
                    },
                ],
                "request_body_fields": [],
                "response_contracts": [{"status": "200"}],
                "security_requirements": [{"scheme": "bearerAuth", "type": "HTTP"}],
                "request_contract_locations_preserved": True,
                "credential_values_retained": False,
            }
        ],
        "credential_refs": [
            {
                "credential_ref": "credential-ref:warehouse-user",
                "actor_ref": "仓管员",
                "environment_ref": "env:test",
            }
        ],
        "environment_ref": "env:test",
        "summary": {},
        "governance": {},
        "coverage_gaps": [],
        "relationships": [],
    }

    project_final_scenario_planning_gate(asset, model)

    assert asset["scenario_planning_gate"]["status"] == "PASS"
    assert asset["scenario_ir_gate"]["status"] == "PASS"
    assert asset["scenario_execution_contract_gate"]["status"] == "PASS"
    assert asset["runtime_plan_gate"]["status"] == "PASS"
    assert len(asset["runtime_plans"]) >= 1
    plan = asset["runtime_plans"][0]
    assert plan["status"] == "TEMPLATE_READY"
    assert plan["request_template"]["query_parameters"][0]["field"] == "status"
    assert plan["credential_template"]["credential_slots"][0]["credential_ref"] == (
        "credential-ref:warehouse-user"
    )
    assert plan["execution_allowed"] is False
    assert plan["network_calls_allowed"] is False
    assert plan["http_request_compiled"] is False
    assert plan["credentials_loaded"] is False
    assert plan["database_queries_executable"] is False
    assert plan["oracle_assertions_compiled"] is False
    assert plan["cleanup_actions_executable"] is False
    assert asset["summary"]["runtime_execution_allowed"] is False
    assert asset["governance"]["legacy_probe_generation_requires_runtime_plan_gate"] is True
