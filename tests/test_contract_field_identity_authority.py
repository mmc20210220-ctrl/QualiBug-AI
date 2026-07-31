from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.contract_field_identity_audit import (
    enforce_exact_contract_field_identity,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.contract_field_identity_policy import (
    exact_contract_field_identity,
)


INTERFACE_ID = "api:PATCH:/orders/{id}"
BODY_REF = "field-binding:body:order-status"
QUERY_REF = "field-binding:query:status"
VALUE_REF = "runtime-value:status"


def _field(ref: str, location: str, schema_path: str, field: str) -> dict:
    return {
        "contract_field_binding_id": ref,
        "interface_id": INTERFACE_ID,
        "location": location,
        "schema_path": schema_path,
        "field": field,
    }


def test_nested_contract_field_requires_complete_schema_path() -> None:
    nested = _field(BODY_REF, "BODY", "order.status", "status")
    flat = _field(QUERY_REF, "QUERY", "status", "status")

    assert exact_contract_field_identity(
        nested,
        interface_id=INTERFACE_ID,
        location="BODY",
        source_field="order.status",
    )
    assert not exact_contract_field_identity(
        nested,
        interface_id=INTERFACE_ID,
        location="BODY",
        source_field="status",
    )
    assert exact_contract_field_identity(
        flat,
        interface_id=INTERFACE_ID,
        location="QUERY",
        source_field="status",
    )


def _asset_and_model(source_field: str, selected_ref: str) -> tuple[dict, dict]:
    selected_location = "BODY" if selected_ref == BODY_REF else "QUERY"
    selected_schema = "order.status" if selected_ref == BODY_REF else "status"
    slot = {
        "slot_ref": "slot:status",
        "source_field_candidate": source_field,
        "runtime_value_binding_id": VALUE_REF,
        "contract_field_binding_refs": [selected_ref],
        "bindings": [
            {
                "binding_kind": "API_CONTRACT_FIELD",
                "interface_id": INTERFACE_ID,
                "field": source_field,
                "contract_field_binding_ref": selected_ref,
                "contract_field_location": selected_location,
                "contract_field_schema_path": selected_schema,
            }
        ],
    }
    binding = {
        "binding_id": "binding:update-order",
        "primary_api_interface_ref": INTERFACE_ID,
        "condition_observer_bindings": [slot],
        "effect_observer_bindings": [],
    }
    asset = {
        "binding_identity_graph": {
            "contract_field_bindings": [
                _field(BODY_REF, "BODY", "order.status", "status"),
                _field(QUERY_REF, "QUERY", "status", "status"),
            ],
            "runtime_value_bindings": [
                {
                    "runtime_value_binding_id": VALUE_REF,
                    "slot_ref": "slot:status",
                    "contract_field_binding_refs": [selected_ref],
                }
            ],
        },
        "binding_identity_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "binding_identity_ready": True,
            "metrics": {},
        },
        "behavior_implementation_bindings": [binding],
        "scenario_execution_contracts": [
            {
                "contract_id": "contract:update-order",
                "status": "READY",
                "formal_execution_contract": True,
                "action_contract": {"interface_id": INTERFACE_ID},
                "request_contract": {
                    "path_parameter_requirements": [],
                    "request_field_requirements": [
                        {
                            "source_slot_ref": "slot:status",
                            "runtime_value_binding_ref": VALUE_REF,
                            "field": selected_schema,
                            "location": selected_location,
                            "contract_field_binding_ref": selected_ref,
                        }
                    ],
                },
            }
        ],
        "scenario_execution_contract_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "execution_contract_ready": True,
            "metrics": {},
        },
        "runtime_plans": [
            {
                "plan_id": "plan:update-order",
                "status": "TEMPLATE_READY",
                "formal_runtime_plan": True,
                "action_entry": {"interface_id": INTERFACE_ID},
                "request_template": {
                    "path_parameters": [],
                    "query_parameters": [],
                    "header_parameters": [],
                    "cookie_parameters": [],
                    "body_fields": [
                        {
                            "slot_id": "request-slot:status",
                            "field": selected_schema,
                            "location": selected_location,
                            "contract_field_binding_ref": selected_ref,
                            "runtime_value_binding_ref": VALUE_REF,
                            "value_source": {"source_slot_ref": "slot:status"},
                        }
                    ],
                    "form_fields": [],
                },
                "binding_identity_refs": {
                    "contract_field_binding_refs": [selected_ref]
                },
            }
        ],
        "runtime_plan_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "runtime_plan_ready": True,
            "metrics": {},
        },
        "runtime_materializations": [
            {
                "materialization_id": "materialization:update-order",
                "runtime_plan_ref": "plan:update-order",
                "status": "DRAFT_READY",
                "formal_runtime_materialization": True,
                "request_value_bindings": [
                    {
                        "slot_id": "request-slot:status",
                        "contract_field_binding_ref": selected_ref,
                    }
                ],
                "binding_identity_refs": {
                    "contract_field_binding_refs": [selected_ref]
                },
            }
        ],
        "runtime_materialization_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "runtime_materialization_ready": True,
            "metrics": {},
        },
    }
    model = {
        "behavior_implementation_bindings": [binding],
        "binding_identity_graph": dict(asset["binding_identity_graph"]),
    }
    return asset, model


def test_leaf_only_nested_ref_is_removed_across_every_runtime_layer() -> None:
    asset, model = _asset_and_model("status", BODY_REF)

    enforce_exact_contract_field_identity(asset, model)

    candidate = model["behavior_implementation_bindings"][0][
        "condition_observer_bindings"
    ][0]["bindings"][0]
    assert "contract_field_binding_ref" not in candidate
    assert candidate["contract_field_identity_status"] == "REJECTED_NOT_EXACT"
    assert asset["binding_identity_gate"]["entry_allowed"] is False

    requirement = asset["scenario_execution_contracts"][0]["request_contract"][
        "request_field_requirements"
    ][0]
    assert "contract_field_binding_ref" not in requirement
    assert asset["scenario_execution_contract_gate"]["entry_allowed"] is False

    runtime_slot = asset["runtime_plans"][0]["request_template"]["body_fields"][0]
    assert "contract_field_binding_ref" not in runtime_slot
    assert asset["runtime_plan_gate"]["entry_allowed"] is False

    value = asset["runtime_materializations"][0]["request_value_bindings"][0]
    assert "contract_field_binding_ref" not in value
    assert asset["runtime_materialization_gate"]["entry_allowed"] is False
    assert any(
        row["reason_code"] == "IMPLEMENTATION_CONTRACT_FIELD_IDENTITY_NOT_EXACT"
        for row in asset["binding_identity_unknowns"]
    )


def test_exact_flat_parameter_identity_remains_admitted() -> None:
    asset, model = _asset_and_model("status", QUERY_REF)
    plan = asset["runtime_plans"][0]
    slot = plan["request_template"]["body_fields"].pop()
    plan["request_template"]["query_parameters"].append(slot)

    enforce_exact_contract_field_identity(asset, model)

    candidate = model["behavior_implementation_bindings"][0][
        "condition_observer_bindings"
    ][0]["bindings"][0]
    assert candidate["contract_field_binding_ref"] == QUERY_REF
    assert candidate["contract_field_identity_status"] == "BOUND_EXACT"
    assert asset.get("binding_identity_unknowns", []) == []
    assert asset["binding_identity_gate"]["entry_allowed"] is True
    assert asset["scenario_execution_contract_gate"]["entry_allowed"] is True
    assert asset["runtime_plan_gate"]["entry_allowed"] is True
    assert asset["runtime_materialization_gate"]["entry_allowed"] is True
