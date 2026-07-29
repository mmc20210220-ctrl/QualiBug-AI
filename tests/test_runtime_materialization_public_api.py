from __future__ import annotations

from copy import deepcopy

import ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding as understanding


def _asset() -> dict:
    plan = {
        "schema": "qualibug.runtime-plan.v1",
        "plan_id": "runtime-plan:public-api",
        "execution_contract_ref": "execution-contract:public-api",
        "scenario_ref": "scenario:public-api",
        "behavior_ref": "behavior:public-api",
        "implementation_binding_ref": "binding:public-api",
        "status": "TEMPLATE_READY",
        "formal_runtime_plan": True,
        "action_entry": {
            "interface_id": "api:GET:/orders/{order_id}",
            "method": "GET",
            "path": "/orders/{order_id}",
            "operation_id": "getOrder",
            "authoritative": True,
        },
        "request_template": {
            "method": "GET",
            "interface_id": "api:GET:/orders/{order_id}",
            "operation_id": "getOrder",
            "path_template": "/orders/{order_id}",
            "path_parameters": [
                {
                    "slot_id": "runtime-slot:order-id",
                    "field": "order_id",
                    "location": "PATH",
                    "required": True,
                    "schema_type": "STRING",
                    "value_source": {
                        "source_kind": "RUNTIME_ENTITY_IDENTIFIER",
                        "source_slot_ref": "runtime-slot:order-id",
                        "runtime_value_materialized": False,
                    },
                    "runtime_value_materialized": False,
                }
            ],
            "query_parameters": [],
            "header_parameters": [],
            "cookie_parameters": [],
            "body_fields": [],
            "form_fields": [],
            "request_body_media_types": [],
            "field_locations_resolved": True,
            "request_template_compiled": True,
            "concrete_request_compiled": False,
            "runtime_values_materialized": False,
        },
        "credential_template": {
            "credential_slots": [],
            "security_requirements": [],
            "credential_refs_only": True,
            "plaintext_credentials_allowed": False,
            "credential_values_loaded": False,
        },
        "test_data_setup_templates": [],
        "oracle_query_templates": {
            "templates": [
                {
                    "template_id": "oracle:http-order",
                    "template_kind": "HTTP_RESPONSE_CAPTURE",
                    "phase": "AFTER",
                    "interface_id": "api:GET:/orders/{order_id}",
                    "declared_response_contracts": [{"status": "200"}],
                    "permission_decision_requirement": "ALLOW",
                    "capture_status": True,
                    "capture_headers": True,
                    "capture_body": True,
                }
            ],
            "concrete_assertions_compiled": False,
        },
        "snapshot_template": {
            "before_snapshot_required": False,
            "after_snapshot_required": True,
            "snapshot_templates_compiled": True,
            "snapshots_materialized": False,
        },
        "cleanup_step_templates": {
            "write_action": False,
            "strategy_requirement": "NOT_REQUIRED_READ_ONLY_ACTION",
            "steps": [
                {
                    "step_index": 1,
                    "step_kind": "NO_CLEANUP_REQUIRED",
                    "reason": "READ_ONLY_ACTION",
                    "template_compiled": True,
                }
            ],
            "cleanup_step_templates_compiled": True,
            "cleanup_executed": False,
        },
        "environment_template": {
            "environment_ref": "env:test",
            "environment_ref_resolution_status": "RESOLVED",
            "non_production_required": False,
            "network_access_allowed": False,
            "runtime_environment_validated": False,
        },
        "evidence": [
            {
                "source_id": "source:openapi",
                "source_locator": "GET /orders/{order_id}",
                "derivation": "source_contract",
            }
        ],
        "execution_allowed": False,
        "network_calls_allowed": False,
    }
    return {
        "runtime_plan_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "runtime_plan_ready": True,
            "execution_allowed": False,
        },
        "runtime_plans": [plan],
        "environment_ref": "env:test",
        "runtime_environment": {
            "environment_ref": "env:test",
            "environment_kind": "TEST",
            "is_production": False,
            "base_url": "https://sit.example.internal",
        },
        "runtime_input_bindings": [
            {
                "binding_id": "binding:unapproved-order",
                "slot_id": "runtime-slot:order-id",
                "field": "order_id",
                "location": "PATH",
                "value": "UNAPPROVED-PUBLIC-VALUE",
                "status": "DRAFT",
            }
        ],
        "summary": {},
        "governance": {},
        "coverage_gaps": [],
        "relationships": [],
    }


def test_package_builder_name_routes_to_secure_authority_without_mutating_inputs() -> None:
    asset = _asset()
    model = {"source_summary": {}, "metrics": {}}
    original_asset = deepcopy(asset)
    original_model = deepcopy(model)

    contracts, unknowns, gate = understanding.build_runtime_materializations_v1(
        asset, model
    )

    assert understanding.build_runtime_materializations_v1.__name__ == (
        "build_secure_runtime_materializations_v1"
    )
    assert gate["status"] == "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
    assert any(
        row["kind"] == "RUNTIME_MATERIALIZATION_VALUE_BINDING_NOT_APPROVED"
        for row in unknowns
    )
    assert "UNAPPROVED-PUBLIC-VALUE" not in str(contracts)
    assert asset == original_asset
    assert model == original_model


def test_package_projection_name_routes_to_secure_authority() -> None:
    asset = _asset()
    model = {"source_summary": {}, "metrics": {}}

    understanding.project_runtime_materializations_to_asset(asset, model)

    assert understanding.project_runtime_materializations_to_asset.__name__ == (
        "project_secure_runtime_materializations_to_asset"
    )
    assert asset["runtime_materialization_gate"]["status"] == (
        "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
    )
    assert "UNAPPROVED-PUBLIC-VALUE" not in str(asset["runtime_materializations"])
    assert asset["governance"][
        "runtime_materialization_secure_projection_is_public_authority"
    ] is True


def test_core_materialization_primitives_are_not_star_exported() -> None:
    assert "build_runtime_materializations_core_v1" not in understanding.__all__
    assert "project_runtime_materializations_core_to_asset" not in understanding.__all__
    assert "project_governed_runtime_materializations_to_asset" not in understanding.__all__
