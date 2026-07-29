from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.runtime_materialization_security import (
    project_secure_runtime_materializations_to_asset,
)


def _slot() -> dict:
    return {
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


def _plan(*, test_data: list[dict] | None = None) -> dict:
    return {
        "schema": "qualibug.runtime-plan.v1",
        "plan_id": "runtime-plan:read-order",
        "execution_contract_ref": "execution-contract:read-order",
        "scenario_ref": "scenario:read-order",
        "behavior_ref": "behavior:read-order",
        "implementation_binding_ref": "binding:read-order",
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
            "path_parameters": [_slot()],
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
        "test_data_setup_templates": list(test_data or []),
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


def _asset(binding: dict, *, plan: dict | None = None) -> dict:
    return {
        "runtime_plan_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "runtime_plan_ready": True,
            "execution_allowed": False,
        },
        "runtime_plans": [plan or _plan()],
        "environment_ref": "env:test",
        "runtime_environment": {
            "environment_ref": "env:test",
            "environment_kind": "TEST",
            "is_production": False,
            "base_url": "https://sit.example.internal",
        },
        "runtime_input_bindings": [binding],
        "summary": {},
        "governance": {},
        "coverage_gaps": [],
        "relationships": [],
    }


def test_unapproved_runtime_value_is_scrubbed_from_request_draft() -> None:
    asset = _asset(
        {
            "binding_id": "binding:unapproved-order",
            "slot_id": "runtime-slot:order-id",
            "field": "order_id",
            "location": "PATH",
            "value": "UNAPPROVED-ORDER-SECRET",
            "status": "DRAFT",
        }
    )
    model = {"source_summary": {}, "metrics": {}}

    project_secure_runtime_materializations_to_asset(asset, model)

    assert asset["runtime_materialization_gate"]["status"] == (
        "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
    )
    materialization = asset["runtime_materializations"][0]
    assert "UNAPPROVED-ORDER-SECRET" not in str(materialization)
    binding = materialization["request_value_bindings"][0]
    assert binding["resolution_status"] == "BLOCKED_VALUE_BINDING_NOT_APPROVED"
    assert binding["draft_value_present"] is False
    assert any(
        row["kind"] == "RUNTIME_MATERIALIZATION_VALUE_BINDING_NOT_APPROVED"
        for row in asset["runtime_materialization_unknowns"]
    )


def test_required_null_value_is_not_treated_as_materialized() -> None:
    asset = _asset(
        {
            "binding_id": "binding:null-order",
            "slot_id": "runtime-slot:order-id",
            "field": "order_id",
            "location": "PATH",
            "value": None,
            "approved_for_materialization": True,
        }
    )
    model = {"source_summary": {}, "metrics": {}}

    project_secure_runtime_materializations_to_asset(asset, model)

    assert asset["runtime_materialization_gate"]["status"] == (
        "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
    )
    assert any(
        row["kind"] == "RUNTIME_MATERIALIZATION_REQUIRED_VALUE_IS_NULL"
        for row in asset["runtime_materialization_unknowns"]
    )
    binding = asset["runtime_materializations"][0]["request_value_bindings"][0]
    assert binding["draft_value_present"] is False


def test_test_data_binding_requires_actual_value_source() -> None:
    plan = _plan(
        test_data=[
            {
                "requirement_kind": "EXISTING_ENTITY_OR_SYSTEM_STATE",
                "slot_ref": "condition:status",
                "field_candidate": "status",
                "runtime_value_materialized": False,
            }
        ]
    )
    asset = _asset(
        {
            "binding_id": "binding:order-id",
            "slot_id": "runtime-slot:order-id",
            "field": "order_id",
            "location": "PATH",
            "value": "ORD-1001",
            "approved_for_materialization": True,
        },
        plan=plan,
    )
    asset["runtime_input_bindings"].append(
        {
            "binding_id": "binding:empty-test-data",
            "slot_ref": "condition:status",
            "field": "status",
            "approved_for_materialization": True,
        }
    )
    model = {"source_summary": {}, "metrics": {}}

    project_secure_runtime_materializations_to_asset(asset, model)

    assert asset["runtime_materialization_gate"]["status"] == (
        "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
    )
    assert any(
        row["kind"]
        == "RUNTIME_MATERIALIZATION_TEST_DATA_BINDING_HAS_NO_VALUE_SOURCE"
        for row in asset["runtime_materialization_unknowns"]
    )
    assert asset["governance"][
        "runtime_materialization_test_data_requires_actual_value_source"
    ] is True
