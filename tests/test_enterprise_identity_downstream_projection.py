from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_downstream_projection import (
    project_identity_to_downstream,
)


def test_governed_api_binding_projects_entity_through_runtime_layers() -> None:
    entity_id = "enterprise_entity:order"
    behavior_id = "behavior:order-view"
    asset = {
        "business_fact_ledger": {
            "items": [
                {
                    "fact_id": "fact:view-order",
                    "subject": {"resolved_entity_refs": [entity_id]},
                    "object": {"resolved_entity_refs": [entity_id]},
                }
            ]
        },
        "enterprise_identity_resolution": {"bindings": []},
    }
    model = {
        "term_resolution": {"alias_to_entity": {"Order": entity_id}},
        "identity_bindings": [],
        "business_behaviors": [
            {
                "behavior_id": behavior_id,
                "object_refs": ["Order"],
                "source_refs": ["fact:view-order"],
                "unresolved_semantics": [],
            }
        ],
        "behavior_ir_gate": {"status": "PASS", "entry_allowed": True, "metrics": {}},
        "behavior_implementation_bindings": [
            {
                "binding_id": "implementation:order-view",
                "behavior_ref": behavior_id,
                "api_operation_bindings": [
                    {
                        "interface_id": "interface:get-order",
                        "status": "BOUND",
                        "authoritative": True,
                        "evidence": [{"source_id": "openapi", "source_locator": "GET /orders/{id}", "quote": "getOrder"}],
                    }
                ],
                "ui_action_bindings": [],
                "condition_observer_bindings": [],
                "effect_observer_bindings": [],
                "response_observer_bindings": [],
                "status": "BOUND",
                "scenario_planning_ready": True,
            }
        ],
        "implementation_binding_gate": {"status": "PASS", "entry_allowed": True, "metrics": {}},
        "scenario_ir": [
            {"scenario_id": "scenario:1", "behavior_ref": behavior_id}
        ],
        "scenario_execution_contracts": [
            {"contract_id": "contract:1", "scenario_ref": "scenario:1"}
        ],
        "runtime_plans": [
            {"runtime_plan_id": "plan:1", "execution_contract_ref": "contract:1"}
        ],
        "runtime_materializations": [
            {"runtime_materialization_id": "materialization:1", "runtime_plan_ref": "plan:1"}
        ],
    }

    projected = project_identity_to_downstream(asset, model)

    behavior = projected["business_behaviors"][0]
    assert behavior["business_entity_refs"] == [entity_id]
    assert behavior["identity_execution_allowed"] is True
    identity_binding = projected["identity_bindings"][0]
    assert identity_binding["artifact_ref"] == "interface:get-order"
    assert identity_binding["entity_id"] == entity_id
    implementation = projected["behavior_implementation_bindings"][0]
    assert implementation["business_entity_refs"] == [entity_id]
    assert implementation["identity_binding_refs"] == [identity_binding["binding_id"]]
    assert projected["scenario_ir"][0]["business_entity_refs"] == [entity_id]
    assert projected["scenario_execution_contracts"][0]["business_entity_refs"] == [entity_id]
    assert projected["runtime_plans"][0]["business_entity_refs"] == [entity_id]
    assert projected["runtime_materializations"][0]["business_entity_refs"] == [entity_id]
    assert projected["identity_execution_admission"]["entry_allowed"] is True


def test_name_only_behavior_is_not_execution_admitted() -> None:
    asset = {"business_fact_ledger": {"items": []}}
    model = {
        "term_resolution": {"alias_to_entity": {}},
        "identity_bindings": [],
        "business_behaviors": [
            {
                "behavior_id": "behavior:unknown",
                "object_refs": ["UnknownObject"],
                "source_refs": [],
                "unresolved_semantics": [],
            }
        ],
        "behavior_ir_gate": {"status": "PASS", "entry_allowed": True, "metrics": {}},
        "behavior_implementation_bindings": [
            {
                "binding_id": "implementation:unknown",
                "behavior_ref": "behavior:unknown",
                "status": "BOUND",
                "scenario_planning_ready": True,
            }
        ],
        "implementation_binding_gate": {"status": "PASS", "entry_allowed": True, "metrics": {}},
        "scenario_ir": [],
        "scenario_execution_contracts": [],
        "runtime_plans": [],
        "runtime_materializations": [],
    }

    projected = project_identity_to_downstream(asset, model)

    behavior = projected["business_behaviors"][0]
    assert behavior["identity_resolution_status"] == "UNRESOLVED"
    assert behavior["identity_execution_allowed"] is False
    assert "BEHAVIOR_IDENTITY_UNRESOLVED" in behavior["unresolved_semantics"]
    assert projected["behavior_ir_gate"]["entry_allowed"] is False
    implementation = projected["behavior_implementation_bindings"][0]
    assert implementation["scenario_planning_ready"] is False
    assert projected["implementation_binding_gate"]["scenario_planning_allowed"] is False
    assert projected["identity_execution_admission"]["entry_allowed"] is False
    assert projected["identity_execution_admission"]["name_only_execution_allowed"] is False
