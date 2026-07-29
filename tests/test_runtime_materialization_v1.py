from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.runtime_materialization import (
    build_runtime_materializations_v1,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.runtime_materialization_governance import (
    project_governed_runtime_materializations_to_asset,
)


def _evidence() -> list[dict]:
    return [
        {
            "source_id": "source:policy",
            "source_locator": "policy.pdf#page=3;table=1;row=2",
            "quote": "已审核订单允许仓管员发货",
            "derivation": "source_span",
        }
    ]


def _slot(
    slot_id: str,
    field: str,
    location: str,
    source_kind: str,
    *,
    raw: object = None,
    required: bool = True,
    schema_type: str = "STRING",
    media_type_candidates: list[str] | None = None,
) -> dict:
    source = {
        "source_kind": source_kind,
        "source_slot_ref": slot_id,
        "runtime_value_materialized": False,
    }
    if source_kind == "SOURCE_BACKED_SEMANTIC_VALUE":
        source.update({"raw": raw, "value_type": schema_type})
    row = {
        "slot_id": slot_id,
        "field": field,
        "location": location,
        "required": required,
        "schema_type": schema_type,
        "value_source": source,
        "runtime_value_materialized": False,
    }
    if media_type_candidates:
        row["media_type_candidates"] = list(media_type_candidates)
    return row


def _plan(*, write: bool = True, body_fields: list[dict] | None = None) -> dict:
    return {
        "schema": "qualibug.runtime-plan.v1",
        "plan_id": "runtime-plan:ship",
        "execution_contract_ref": "execution-contract:ship",
        "scenario_ref": "scenario:ship",
        "behavior_ref": "behavior:ship",
        "implementation_binding_ref": "binding:ship",
        "scenario_type": "POSITIVE",
        "status": "TEMPLATE_READY",
        "formal_runtime_plan": True,
        "action_entry": {
            "interface_id": "api:POST:/orders/{order_id}/ship",
            "method": "POST" if write else "GET",
            "path": "/orders/{order_id}/ship",
            "operation_id": "shipOrder",
            "authoritative": True,
        },
        "request_template": {
            "method": "POST" if write else "GET",
            "interface_id": "api:POST:/orders/{order_id}/ship",
            "operation_id": "shipOrder",
            "path_template": "/orders/{order_id}/ship",
            "path_parameters": [
                _slot(
                    "runtime-slot:order-id",
                    "order_id",
                    "PATH",
                    "RUNTIME_ENTITY_IDENTIFIER",
                )
            ],
            "query_parameters": [
                _slot(
                    "condition:status",
                    "status",
                    "QUERY",
                    "SOURCE_BACKED_SEMANTIC_VALUE",
                    raw="approved",
                )
            ],
            "header_parameters": [
                _slot(
                    "runtime-slot:tenant",
                    "X-Tenant",
                    "HEADER",
                    "RUNTIME_REQUIRED_INPUT",
                )
            ],
            "cookie_parameters": [],
            "body_fields": list(body_fields or []),
            "form_fields": [],
            "request_body_media_types": sorted(
                {
                    media
                    for row in body_fields or []
                    for media in row.get("media_type_candidates", [])
                }
            ),
            "field_locations_resolved": True,
            "request_template_compiled": True,
            "concrete_request_compiled": False,
            "runtime_values_materialized": False,
        },
        "credential_template": {
            "credential_slots": [
                {
                    "slot_id": "credential-slot:warehouse",
                    "actor_ref": "仓管员",
                    "credential_ref": "credential-ref:warehouse-user",
                    "environment_ref": "env:test",
                    "resolution_status": "CREDENTIAL_REF_RESOLVED",
                    "credential_value_loaded": False,
                }
            ],
            "security_requirements": [
                {
                    "scheme": "bearerAuth",
                    "type": "HTTP",
                    "scheme_name": "bearer",
                }
            ],
            "credential_refs_only": True,
            "plaintext_credentials_allowed": False,
            "credential_values_loaded": False,
        },
        "test_data_setup_templates": [],
        "oracle_query_templates": {
            "templates": [
                {
                    "template_id": "oracle:db-status",
                    "template_kind": "DATABASE_FIELD_SNAPSHOT",
                    "phase": "BEFORE_AND_AFTER",
                    "table_ref": "table:orders",
                    "table": "orders",
                    "field": "status",
                    "entity_identity_source": "SAME_SCENARIO_ENTITY_IDENTITY",
                    "query_template_compiled": True,
                    "sql_compiled": False,
                },
                {
                    "template_id": "oracle:http-response",
                    "template_kind": "HTTP_RESPONSE_CAPTURE",
                    "phase": "AFTER",
                    "interface_id": "api:POST:/orders/{order_id}/ship",
                    "declared_response_contracts": [{"status": "200"}],
                    "permission_decision_requirement": "ALLOW",
                    "capture_status": True,
                    "capture_headers": True,
                    "capture_body": True,
                },
            ],
            "concrete_assertions_compiled": False,
        },
        "snapshot_template": {
            "before_snapshot_required": True,
            "after_snapshot_required": True,
            "snapshot_templates_compiled": True,
            "snapshots_materialized": False,
        },
        "cleanup_step_templates": {
            "write_action": write,
            "strategy_requirement": (
                "REVERSIBLE_CLEANUP_OR_ISOLATED_SANDBOX_REQUIRED"
                if write
                else "NOT_REQUIRED_READ_ONLY_ACTION"
            ),
            "steps": (
                [
                    {
                        "step_index": 1,
                        "step_kind": "CAPTURE_MUTATED_ENTITY_IDENTITY",
                        "template_compiled": True,
                    },
                    {
                        "step_index": 2,
                        "step_kind": "REQUIRE_ISOLATED_SANDBOX_RESET_OR_BOUND_REVERSAL",
                        "environment_capability_required": "DISPOSABLE_SANDBOX_OR_REVERSIBLE_WRITE",
                        "template_compiled": True,
                    },
                    {
                        "step_index": 3,
                        "step_kind": "VERIFY_CLEANUP_RESTORED_STATE",
                        "template_compiled": True,
                    },
                ]
                if write
                else [
                    {
                        "step_index": 1,
                        "step_kind": "NO_CLEANUP_REQUIRED",
                        "reason": "READ_ONLY_ACTION",
                        "template_compiled": True,
                    }
                ]
            ),
            "cleanup_step_templates_compiled": True,
            "cleanup_executed": False,
        },
        "environment_template": {
            "environment_ref": "env:test",
            "environment_ref_resolution_status": "RESOLVED",
            "non_production_required": write,
            "network_access_allowed": False,
            "runtime_environment_validated": False,
        },
        "evidence": _evidence(),
        "execution_allowed": False,
        "network_calls_allowed": False,
    }


def _asset(*, plan: dict | None = None) -> dict:
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
            "capabilities": ["RESETTABLE"],
            "reset_ref": "sandbox-reset:test",
        },
        "connectors": [
            {
                "connector_id": "connector:orders",
                "enabled": True,
                "endpoint_ref": "https://sit.example.internal",
            }
        ],
        "runtime_input_bindings": [
            {
                "binding_id": "binding:order-id",
                "runtime_plan_ref": "runtime-plan:ship",
                "slot_id": "runtime-slot:order-id",
                "field": "order_id",
                "location": "PATH",
                "value": "ORD-1001",
                "approved_for_materialization": True,
            },
            {
                "binding_id": "binding:tenant",
                "runtime_plan_ref": "runtime-plan:ship",
                "slot_id": "runtime-slot:tenant",
                "field": "X-Tenant",
                "location": "HEADER",
                "value": "tenant-a",
                "approved_for_materialization": True,
            },
        ],
        "summary": {},
        "governance": {},
        "coverage_gaps": [],
        "relationships": [],
    }


def test_governed_materialization_builds_non_sendable_request_and_assertion_drafts() -> None:
    asset = _asset()
    model = {"source_summary": {}, "metrics": {}}

    project_governed_runtime_materializations_to_asset(asset, model)

    assert asset["runtime_materialization_gate"]["status"] == "PASS"
    assert len(asset["runtime_materializations"]) == 1
    materialization = asset["runtime_materializations"][0]
    assert materialization["status"] == "DRAFT_READY"
    request = materialization["request_draft"]
    assert request["url_draft"] == "https://sit.example.internal/orders/ORD-1001/ship"
    assert request["query_draft"] == [{"field": "status", "value": "approved"}]
    assert request["header_draft"] == [
        {"field": "X-Tenant", "value": "tenant-a", "sensitive": False}
    ]
    assert request["request_sendable"] is False
    assert request["network_call_allowed"] is False

    credential = materialization["credential_binding"]
    assert credential["credential_slots"][0]["credential_ref"] == (
        "credential-ref:warehouse-user"
    )
    assert credential["security_placeholders"][0]["placeholder"] == (
        "{{secret_ref:credential-ref:warehouse-user}}"
    )
    assert credential["secret_values_loaded"] is False
    assert "password" not in str(credential).lower()

    db_assertion = next(
        row
        for row in materialization["assertion_drafts"]
        if row["draft_kind"] == "DATABASE_SNAPSHOT_QUERY_AST"
    )
    assert db_assertion["entity_identity_binding_ref"] == "runtime-slot:order-id"
    assert db_assertion["query_ast_compiled"] is True
    assert db_assertion["sql_compiled"] is False
    assert db_assertion["assertion_executable"] is False
    assert materialization["cleanup_draft"]["cleanup_binding_resolved"] is True
    assert materialization["cleanup_draft"]["cleanup_executable"] is False

    for key in (
        "execution_allowed",
        "request_sendable",
        "request_serialized",
        "network_calls_allowed",
        "secret_values_loaded",
        "credential_injection_executed",
        "generators_executed",
        "test_data_setup_executed",
        "database_queries_executable",
        "assertions_executable",
        "snapshots_materialized",
        "cleanup_executable",
        "cleanup_executed",
        "bug_classification_allowed",
    ):
        assert materialization[key] is False


def test_missing_dynamic_value_binding_blocks_materialization() -> None:
    asset = _asset()
    asset["runtime_input_bindings"] = [
        row for row in asset["runtime_input_bindings"] if row["slot_id"] != "runtime-slot:order-id"
    ]
    contracts, unknowns, gate = build_runtime_materializations_v1(asset, {})

    assert gate["status"] == "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
    assert contracts[0]["status"] == "INCOMPLETE"
    assert any(
        row["kind"] == "RUNTIME_MATERIALIZATION_REQUIRED_VALUE_BINDING_MISSING"
        and row["field"] == "order_id"
        for row in unknowns
    )


def test_production_write_is_blocked_even_with_reset_capability() -> None:
    asset = _asset()
    asset["runtime_environment"].update(
        {"environment_kind": "PRODUCTION", "is_production": True}
    )
    asset["runtime_environment"]["base_url"] = "https://prod.example.internal"
    contracts, unknowns, gate = build_runtime_materializations_v1(asset, {})

    assert gate["status"] == "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
    assert contracts[0]["status"] == "INCOMPLETE"
    assert any(
        row["kind"] == "RUNTIME_MATERIALIZATION_PRODUCTION_WRITE_FORBIDDEN"
        for row in unknowns
    )


def test_sensitive_request_field_cannot_receive_plain_source_literal() -> None:
    plan = _plan()
    plan["request_template"]["header_parameters"].append(
        _slot(
            "condition:authorization",
            "Authorization",
            "HEADER",
            "SOURCE_BACKED_SEMANTIC_VALUE",
            raw="Bearer should-not-be-copied",
        )
    )
    asset = _asset(plan=plan)
    contracts, unknowns, gate = build_runtime_materializations_v1(asset, {})

    assert gate["status"] == "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
    assert contracts[0]["credential_binding"]["secret_values_loaded"] is False
    assert any(
        row["kind"]
        == "RUNTIME_MATERIALIZATION_SENSITIVE_FIELD_REQUIRES_CREDENTIAL_REF"
        for row in unknowns
    )
    assert "should-not-be-copied" not in str(contracts[0]["request_draft"])


def test_multiple_body_media_types_require_approved_selection() -> None:
    body_slot = _slot(
        "condition:body-status",
        "status",
        "BODY",
        "SOURCE_BACKED_SEMANTIC_VALUE",
        raw="approved",
        media_type_candidates=["application/json", "application/xml"],
    )
    asset = _asset(plan=_plan(body_fields=[body_slot]))
    contracts, unknowns, gate = build_runtime_materializations_v1(asset, {})
    assert gate["status"] == "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
    assert any(
        row["kind"] == "RUNTIME_MATERIALIZATION_MEDIA_TYPE_SELECTION_MISSING"
        for row in unknowns
    )

    asset["request_media_type_bindings"] = [
        {
            "binding_id": "media-binding:json",
            "runtime_plan_ref": "runtime-plan:ship",
            "media_type": "application/json",
            "approved_for_materialization": True,
        }
    ]
    contracts, unknowns, gate = build_runtime_materializations_v1(asset, {})
    assert gate["status"] == "PASS"
    assert unknowns == []
    assert contracts[0]["request_draft"]["body_media_type"] == "application/json"
    assert contracts[0]["request_draft"]["body_draft"] == {"status": "approved"}


def test_allowed_generator_is_bound_but_not_executed() -> None:
    asset = _asset()
    for row in asset["runtime_input_bindings"]:
        if row["slot_id"] == "runtime-slot:order-id":
            row.pop("value", None)
            row["generator"] = {"kind": "UUID", "namespace": "test-order"}
    plan = asset["runtime_plans"][0]
    plan["oracle_query_templates"]["templates"] = [
        row
        for row in plan["oracle_query_templates"]["templates"]
        if row["template_kind"] != "DATABASE_FIELD_SNAPSHOT"
    ]
    contracts, unknowns, gate = build_runtime_materializations_v1(asset, {})

    assert gate["status"] == "PASS"
    assert unknowns == []
    binding = next(
        row
        for row in contracts[0]["request_value_bindings"]
        if row["slot_id"] == "runtime-slot:order-id"
    )
    assert binding["binding_kind"] == "RUNTIME_GENERATOR_DESCRIPTOR"
    assert binding["generator_executed"] is False
    assert "{{generator:uuid}}" in contracts[0]["request_draft"]["path_draft"]


def test_multiple_connectors_do_not_get_guessed() -> None:
    asset = _asset()
    asset["connectors"].append(
        {
            "connector_id": "connector:other",
            "enabled": True,
            "endpoint_ref": "https://other-sit.example.internal",
        }
    )
    model = {"source_summary": {}, "metrics": {}}

    project_governed_runtime_materializations_to_asset(asset, model)

    assert asset["runtime_materialization_gate"]["status"] == (
        "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
    )
    assert any(
        row["kind"] == "RUNTIME_MATERIALIZATION_BASE_URL_UNRESOLVED"
        for row in asset["runtime_materialization_unknowns"]
    )


def test_credential_ref_is_required_when_security_scheme_exists() -> None:
    asset = _asset()
    asset["runtime_plans"][0]["credential_template"]["credential_slots"][0][
        "credential_ref"
    ] = None
    contracts, unknowns, gate = build_runtime_materializations_v1(asset, {})

    assert gate["status"] == "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
    assert contracts[0]["credential_binding"]["secret_values_loaded"] is False
    assert any(
        row["kind"] == "RUNTIME_MATERIALIZATION_CREDENTIAL_REF_UNRESOLVED"
        for row in unknowns
    )


def test_upstream_runtime_plan_gate_closed_builds_no_materialization() -> None:
    asset = _asset()
    asset["runtime_plan_gate"] = {
        "status": "BLOCKED_RUNTIME_PLAN_INCOMPLETE",
        "entry_allowed": False,
    }
    contracts, unknowns, gate = build_runtime_materializations_v1(asset, {})

    assert contracts == []
    assert unknowns == []
    assert gate["status"] == "BLOCKED_RUNTIME_MATERIALIZATION_UPSTREAM_PLAN_GATE"
    assert gate["execution_allowed"] is False


def test_governed_projection_is_idempotent() -> None:
    asset = _asset()
    model = {"source_summary": {}, "metrics": {}}

    project_governed_runtime_materializations_to_asset(asset, model)
    first_contracts = deepcopy(asset["runtime_materializations"])
    first_relationships = deepcopy(asset["runtime_materialization_relationships"])
    project_governed_runtime_materializations_to_asset(asset, model)

    assert asset["runtime_materializations"] == first_contracts
    assert asset["runtime_materialization_relationships"] == first_relationships
    assert asset["summary"]["materialized_execution_allowed"] is False
    assert asset["governance"]["runtime_materialization_plaintext_credentials_allowed"] is False
