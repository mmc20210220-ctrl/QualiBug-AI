from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.binding_identity_projection import (
    project_binding_identities_to_execution_contracts,
    project_binding_identities_to_materializations,
    project_binding_identities_to_runtime_plans,
    project_binding_identities_to_scenario_ir,
)


def _binding_model() -> tuple[dict, dict]:
    interface_id = "api:POST:/orders/{id}"
    binding_id = "binding:order:update"
    behavior_id = "behavior:order:update"
    asset = {
        "interfaces": [
            {
                "interface_id": interface_id,
                "source_id": "openapi",
                "method": "POST",
                "path": "/orders/{id}",
                "operation_id": "updateOrder",
                "parameter_contracts": [
                    {
                        "name": "id",
                        "field": "id",
                        "location": "PATH",
                        "required": True,
                        "schema_type": "string",
                    },
                    {
                        "name": "status",
                        "field": "status",
                        "location": "QUERY",
                        "required": False,
                        "schema_type": "string",
                    },
                ],
                "request_body_fields": [
                    {
                        "name": "order.status",
                        "field": "order.status",
                        "field_path": "order.status",
                        "location": "BODY",
                        "required": True,
                        "schema_type": "string",
                        "media_type": "application/json",
                    }
                ],
            }
        ],
        "scenario_ir": [
            {
                "scenario_id": "scenario:update",
                "scenario_type": "POSITIVE",
                "implementation_binding_ref": binding_id,
                "action_entry": {
                    "interface_id": interface_id,
                    "method": "POST",
                    "path": "/orders/{id}",
                    "operation_id": "updateOrder",
                    "authoritative": True,
                },
            }
        ],
    }
    model = {
        "actors": [],
        "business_behaviors": [
            {
                "behavior_id": behavior_id,
                "actor_refs": [],
            }
        ],
        "behavior_implementation_bindings": [
            {
                "binding_id": binding_id,
                "behavior_ref": behavior_id,
                "primary_api_interface_ref": interface_id,
                "scenario_planning_ready": True,
                "api_operation_bindings": [
                    {
                        "binding_id": "api-binding:update",
                        "interface_id": interface_id,
                        "method": "POST",
                        "path": "/orders/{id}",
                        "operation_id": "updateOrder",
                        "status": "BOUND",
                        "authoritative": True,
                        "derivation": "authoritative_relationship",
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
                                "interface_id": interface_id,
                                "field": "order.status",
                                "authoritative": True,
                            },
                            {
                                "binding_kind": "DATABASE_FIELD",
                                "field_id": "field:orders:status",
                                "table_id": "table:orders",
                                "field": "status",
                                "authoritative": True,
                            },
                        ],
                    }
                ],
                "effect_observer_bindings": [],
                "response_observer_bindings": [],
            }
        ],
        "scenario_ir": [dict(asset["scenario_ir"][0])],
    }
    return asset, model


def test_binding_identity_graph_preserves_full_field_location() -> None:
    asset, model = _binding_model()

    project_binding_identities_to_scenario_ir(asset, model)

    graph = asset["binding_identity_graph"]
    fields = graph["contract_field_bindings"]
    assert len(fields) == 3
    assert len({row["contract_field_binding_id"] for row in fields}) == 3
    body = next(row for row in fields if row["schema_path"] == "order.status")
    query = next(
        row
        for row in fields
        if row["schema_path"] == "status" and row["location"] == "QUERY"
    )
    assert body["location"] == "BODY"
    assert body["json_pointer"] == "/order/status"
    assert body["contract_field_binding_id"] != query["contract_field_binding_id"]

    binding = model["behavior_implementation_bindings"][0]
    slot = binding["condition_observer_bindings"][0]
    api_field = next(
        row for row in slot["bindings"] if row["binding_kind"] == "API_CONTRACT_FIELD"
    )
    assert api_field["contract_field_identity_status"] == "BOUND"
    assert api_field["contract_field_binding_ref"] == body["contract_field_binding_id"]
    assert slot["runtime_value_binding_id"]
    assert slot["contract_field_binding_refs"] == [body["contract_field_binding_id"]]

    action = asset["scenario_ir"][0]["action_entry"]
    assert action["action_surface_binding_ref"]
    assert action["binding_identity_locked"] is True


def test_identity_refs_flow_through_contract_plan_and_materialization() -> None:
    asset, model = _binding_model()
    project_binding_identities_to_scenario_ir(asset, model)
    binding = model["behavior_implementation_bindings"][0]
    slot = binding["condition_observer_bindings"][0]
    field_ref = slot["contract_field_binding_refs"][0]
    value_ref = slot["runtime_value_binding_id"]
    surface_ref = binding["primary_action_surface_binding_ref"]

    asset["scenario_execution_contracts"] = [
        {
            "contract_id": "contract:update",
            "implementation_binding_ref": binding["binding_id"],
            "action_contract": {
                "interface_id": "api:POST:/orders/{id}",
                "authoritative": True,
            },
            "request_contract": {
                "path_parameter_requirements": [],
                "request_field_requirements": [
                    {
                        "source_slot_ref": "slot:status",
                        "field": "order.status",
                        "location": "UNRESOLVED_CONTRACT_LOCATION",
                    }
                ],
            },
        }
    ]
    project_binding_identities_to_execution_contracts(asset, model)
    requirement = asset["scenario_execution_contracts"][0]["request_contract"][
        "request_field_requirements"
    ][0]
    assert requirement["contract_field_binding_ref"] == field_ref
    assert requirement["runtime_value_binding_ref"] == value_ref
    assert requirement["location"] == "BODY"

    asset["runtime_plan_gate"] = {"status": "PASS", "entry_allowed": True, "metrics": {}}
    asset["runtime_plan_unknowns"] = []
    asset["runtime_plans"] = [
        {
            "plan_id": "plan:update",
            "execution_contract_ref": "contract:update",
            "status": "TEMPLATE_READY",
            "formal_runtime_plan": True,
            "action_entry": {
                "interface_id": "api:POST:/orders/{id}",
                "authoritative": True,
            },
            "request_template": {
                "path_parameters": [],
                "query_parameters": [],
                "header_parameters": [],
                "cookie_parameters": [],
                "body_fields": [
                    {
                        "slot_id": "request-slot:status",
                        "field": "order.status",
                        "location": "BODY",
                        "value_source": {"source_slot_ref": "slot:status"},
                    }
                ],
                "form_fields": [],
            },
        }
    ]
    project_binding_identities_to_runtime_plans(asset, model)
    plan = asset["runtime_plans"][0]
    runtime_slot = plan["request_template"]["body_fields"][0]
    assert plan["action_entry"]["action_surface_binding_ref"] == surface_ref
    assert runtime_slot["contract_field_binding_ref"] == field_ref
    assert runtime_slot["runtime_value_binding_ref"] == value_ref

    asset["runtime_materialization_gate"] = {
        "status": "PASS",
        "entry_allowed": True,
        "metrics": {},
    }
    asset["runtime_materialization_unknowns"] = []
    asset["runtime_materializations"] = [
        {
            "materialization_id": "materialization:update",
            "runtime_plan_ref": "plan:update",
            "status": "DRAFT_READY",
            "formal_runtime_materialization": True,
            "request_value_bindings": [
                {
                    "slot_id": "request-slot:status",
                    "field": "order.status",
                    "location": "BODY",
                }
            ],
            "request_draft": {},
        }
    ]
    project_binding_identities_to_materializations(asset, model)
    materialization = asset["runtime_materializations"][0]
    value_binding = materialization["request_value_bindings"][0]
    assert materialization["request_draft"]["action_surface_binding_ref"] == surface_ref
    assert value_binding["contract_field_binding_ref"] == field_ref
    assert value_binding["runtime_value_binding_ref"] == value_ref


def test_runtime_plan_location_drift_fails_closed() -> None:
    asset, model = _binding_model()
    project_binding_identities_to_scenario_ir(asset, model)
    binding = model["behavior_implementation_bindings"][0]
    asset["scenario_execution_contracts"] = [
        {
            "contract_id": "contract:update",
            "implementation_binding_ref": binding["binding_id"],
            "action_contract": {
                "action_surface_binding_ref": binding[
                    "primary_action_surface_binding_ref"
                ]
            },
            "request_contract": {
                "path_parameter_requirements": [],
                "request_field_requirements": [
                    {
                        "source_slot_ref": "slot:status",
                        "field": "order.status",
                        "location": "BODY",
                        "contract_field_binding_ref": binding[
                            "condition_observer_bindings"
                        ][0]["contract_field_binding_refs"][0],
                        "runtime_value_binding_ref": binding[
                            "condition_observer_bindings"
                        ][0]["runtime_value_binding_id"],
                    }
                ],
            },
        }
    ]
    asset["runtime_plan_gate"] = {"status": "PASS", "entry_allowed": True, "metrics": {}}
    asset["runtime_plan_unknowns"] = []
    asset["runtime_plans"] = [
        {
            "plan_id": "plan:drift",
            "execution_contract_ref": "contract:update",
            "status": "TEMPLATE_READY",
            "formal_runtime_plan": True,
            "action_entry": {"authoritative": True},
            "request_template": {
                "path_parameters": [],
                "query_parameters": [
                    {
                        "slot_id": "request-slot:status",
                        "field": "order.status",
                        "location": "QUERY",
                        "value_source": {"source_slot_ref": "slot:status"},
                    }
                ],
                "header_parameters": [],
                "cookie_parameters": [],
                "body_fields": [],
                "form_fields": [],
            },
        }
    ]

    project_binding_identities_to_runtime_plans(asset, model)

    assert asset["runtime_plan_gate"]["entry_allowed"] is False
    assert (
        asset["runtime_plan_gate"]["status"]
        == "BLOCKED_RUNTIME_PLAN_BINDING_IDENTITY_DRIFT"
    )
    assert asset["runtime_plans"][0]["formal_runtime_plan"] is False
    assert any(
        row["reason_code"] == "RUNTIME_PLAN_BINDING_IDENTITY_DRIFT"
        for row in asset["runtime_plan_unknowns"]
    )


def test_formal_ui_contract_reuses_existing_source_contract_authority() -> None:
    asset, model = _binding_model()
    asset["interfaces"][0]["method"] = "GET"
    model["behavior_implementation_bindings"][0]["api_operation_bindings"][0][
        "method"
    ] = "GET"
    model["actors"] = [
        {
            "actor_id": "actor:admin",
            "name": "管理员",
            "credential_ref": "credential:admin",
        }
    ]
    model["business_behaviors"][0]["actor_refs"] = ["actor:admin"]
    asset["ui_formal_contracts"] = [
        {
            "contract_id": "ui-contract:order",
            "operation_ref": "api:POST:/orders/{id}",
            "actor_ref": "actor:admin",
            "ui_request": {
                "provider": "playwright_browser_plan",
                "start_url": "https://test.example/orders/1",
                "browser_plan": {
                    "steps": [
                        {
                            "action": "expect_text",
                            "selector": "[data-testid=order-status]",
                            "text": "approved",
                        }
                    ]
                },
            },
        }
    ]

    project_binding_identities_to_scenario_ir(asset, model)

    ui_surfaces = asset["binding_identity_graph"]["formal_ui_surface_bindings"]
    assert len(ui_surfaces) == 1
    assert ui_surfaces[0]["ui_contract_ref"] == "ui-contract:order"
    assert ui_surfaces[0]["locator_authority"] == "SOURCE_DECLARED_BROWSER_PLAN"
    assert ui_surfaces[0]["automatic_locator_generation_allowed"] is False
