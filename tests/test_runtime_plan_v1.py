from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.runtime_plan import (
    build_runtime_plans_v1,
    project_runtime_plans_to_asset,
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


def _interface(*, ambiguous_status: bool = False, include_status: bool = True) -> dict:
    parameter_contracts = [
        {
            "name": "order_id",
            "field": "order_id",
            "location": "PATH",
            "required": True,
            "schema_type": "STRING",
            "source": "OPENAPI_PARAMETER",
        },
        {
            "name": "X-Tenant",
            "field": "X-Tenant",
            "location": "HEADER",
            "required": True,
            "schema_type": "STRING",
            "source": "OPENAPI_PARAMETER",
        },
    ]
    if include_status:
        parameter_contracts.append(
            {
                "name": "status",
                "field": "status",
                "location": "QUERY",
                "required": True,
                "schema_type": "STRING",
                "source": "OPENAPI_PARAMETER",
            }
        )
    if ambiguous_status:
        parameter_contracts.append(
            {
                "name": "status",
                "field": "status",
                "location": "HEADER",
                "required": False,
                "schema_type": "STRING",
                "source": "OPENAPI_PARAMETER",
            }
        )
    return {
        "interface_id": "api:POST:/orders/{order_id}/ship",
        "source_id": "source:openapi",
        "source_kind": "openapi",
        "method": "POST",
        "path": "/orders/{order_id}/ship",
        "operation_id": "shipOrder",
        "summary": "发货",
        "parameter_contracts": parameter_contracts,
        "request_body_fields": [],
        "request_body_media_types": ["application/json"],
        "response_contracts": [
            {
                "status": "200",
                "description": "发货成功",
                "media_types": ["application/json"],
                "fields": [
                    {
                        "field": "success",
                        "location": "RESPONSE_BODY",
                        "schema_type": "BOOLEAN",
                    }
                ],
            }
        ],
        "security_requirements": [
            {
                "scheme": "bearerAuth",
                "type": "HTTP",
                "scheme_name": "bearer",
                "credential_value_retained": False,
            }
        ],
        "request_contract_locations_preserved": True,
        "credential_values_retained": False,
    }


def _contract(*, method: str = "POST") -> dict:
    return {
        "contract_id": "execution-contract:ship",
        "scenario_ref": "scenario:ship",
        "behavior_ref": "behavior:ship",
        "implementation_binding_ref": "binding:ship",
        "scenario_type": "POSITIVE",
        "status": "REQUIREMENTS_READY",
        "action_contract": {
            "interface_id": "api:POST:/orders/{order_id}/ship",
            "method": method,
            "path": "/orders/{order_id}/ship",
            "operation_id": "shipOrder",
            "authoritative": True,
        },
        "request_contract": {
            "path_parameter_requirements": [
                {
                    "field": "order_id",
                    "location": "PATH",
                    "required": True,
                    "runtime_value_source": "RUNTIME_ENTITY_IDENTIFIER",
                    "runtime_value_materialized": False,
                }
            ],
            "request_field_requirements": [
                {
                    "slot_ref": "condition:status",
                    "field": "status",
                    "field_candidate": "status",
                    "operator": "EQUALS",
                    "semantic_value_requirement": {
                        "raw": "approved",
                        "value_type": "TEXT",
                        "source_backed_semantic_value": True,
                        "runtime_value_materialized": False,
                    },
                    "required": True,
                }
            ],
        },
        "credential_requirements": [
            {
                "requirement_kind": "ACTOR_IDENTITY",
                "actor_ref": "仓管员",
                "credential_selection_required": True,
                "credential_selected": False,
            }
        ],
        "test_data_requirements": [
            {
                "requirement_kind": "EXISTING_ENTITY_OR_SYSTEM_STATE",
                "field_candidate": "订单.status",
                "runtime_value_materialized": False,
            }
        ],
        "oracle_plan": {
            "permission_decision_requirement": "ALLOW",
            "condition_observers": [
                {
                    "slot_ref": "condition:status",
                    "purpose": "PRECONDITION_OBSERVER",
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
            "effect_observers": [
                {
                    "slot_ref": "effect:status",
                    "purpose": "STATE_EFFECT_OBSERVER",
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
            "response_observers": [
                {
                    "binding_kind": "API_RESPONSE_OUTCOME_CHANNEL",
                    "interface_id": "api:POST:/orders/{order_id}/ship",
                    "authoritative": True,
                }
            ],
            "concrete_assertion_compiled": False,
        },
        "snapshot_plan": {
            "before_snapshot_required": True,
            "after_snapshot_required": True,
            "snapshot_consistency_scope": "SAME_SCENARIO_ENTITY_IDENTITY",
        },
        "cleanup_requirements": {
            "write_action": method in {"POST", "PUT", "PATCH", "DELETE"},
            "cleanup_required": method in {"POST", "PUT", "PATCH", "DELETE"},
            "strategy_requirement": (
                "REVERSIBLE_CLEANUP_OR_ISOLATED_SANDBOX_REQUIRED"
                if method in {"POST", "PUT", "PATCH", "DELETE"}
                else "NOT_REQUIRED_READ_ONLY_ACTION"
            ),
            "source_backed_compensation_candidates": [],
        },
        "evidence": _evidence(),
        "execution_allowed": False,
    }


def _asset(interface: dict, contract: dict | None = None) -> dict:
    return {
        "scenario_execution_contract_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "execution_contract_ready": True,
            "execution_allowed": False,
        },
        "scenario_execution_contracts": [contract or _contract()],
        "interfaces": [interface],
        "credential_refs": [
            {
                "credential_ref": "credential-ref:warehouse-user",
                "actor_ref": "仓管员",
                "environment_ref": "env:test",
                "username": "must-not-be-copied",
                "password": "must-not-be-copied",
            }
        ],
        "environment_ref": "env:test",
        "summary": {},
        "governance": {},
        "coverage_gaps": [],
        "relationships": [],
    }


def test_runtime_plan_compiles_locations_sources_oracles_and_cleanup_templates() -> None:
    asset = _asset(_interface())
    plans, unknowns, gate = build_runtime_plans_v1(asset, {})

    assert unknowns == []
    assert gate["status"] == "PASS"
    assert len(plans) == 1
    plan = plans[0]
    assert plan["status"] == "TEMPLATE_READY"
    assert plan["execution_allowed"] is False
    assert plan["network_calls_allowed"] is False
    assert plan["http_request_compiled"] is False

    request = plan["request_template"]
    assert request["path_parameters"][0]["field"] == "order_id"
    assert request["path_parameters"][0]["value_source"]["source_kind"] == "RUNTIME_ENTITY_IDENTIFIER"
    status = next(row for row in request["query_parameters"] if row["field"] == "status")
    assert status["value_source"]["raw"] == "approved"
    tenant = next(row for row in request["header_parameters"] if row["field"] == "X-Tenant")
    assert tenant["value_source"]["source_kind"] == "RUNTIME_REQUIRED_INPUT"
    assert all(row["runtime_value_materialized"] is False for row in request["header_parameters"])

    credentials = plan["credential_template"]
    assert credentials["credential_slots"][0]["credential_ref"] == "credential-ref:warehouse-user"
    assert credentials["credential_values_loaded"] is False
    assert "username" not in str(credentials)
    assert "password" not in str(credentials)

    oracle_kinds = {
        row["template_kind"] for row in plan["oracle_query_templates"]["templates"]
    }
    assert oracle_kinds == {"DATABASE_FIELD_SNAPSHOT", "HTTP_RESPONSE_CAPTURE"}
    assert plan["oracle_query_templates"]["concrete_assertions_compiled"] is False
    assert plan["snapshot_template"]["snapshots_materialized"] is False
    cleanup = plan["cleanup_step_templates"]
    assert cleanup["cleanup_step_templates_compiled"] is True
    assert cleanup["cleanup_actions_executable"] if "cleanup_actions_executable" in cleanup else True
    assert cleanup["cleanup_executed"] is False
    assert cleanup["steps"][1]["step_kind"] == "REQUIRE_ISOLATED_SANDBOX_RESET_OR_BOUND_REVERSAL"


def test_request_field_without_source_declared_location_blocks_runtime_plan() -> None:
    asset = _asset(_interface(include_status=False))
    plans, unknowns, gate = build_runtime_plans_v1(asset, {})

    assert gate["status"] == "BLOCKED_RUNTIME_PLAN_INCOMPLETE"
    assert plans[0]["status"] == "INCOMPLETE"
    assert any(
        row["kind"] == "RUNTIME_PLAN_REQUEST_FIELD_LOCATION_UNRESOLVED"
        for row in unknowns
    )


def test_same_field_in_multiple_locations_is_ambiguous() -> None:
    asset = _asset(_interface(ambiguous_status=True))
    plans, unknowns, gate = build_runtime_plans_v1(asset, {})

    assert gate["status"] == "BLOCKED_RUNTIME_PLAN_INCOMPLETE"
    assert plans[0]["request_template"]["field_locations_resolved"] is False
    ambiguous = next(
        row
        for row in unknowns
        if row["kind"] == "RUNTIME_PLAN_REQUEST_FIELD_LOCATION_AMBIGUOUS"
    )
    assert ambiguous["candidate_locations"] == ["HEADER", "QUERY"]


def test_missing_credential_ref_is_runtime_slot_not_plaintext_inference() -> None:
    asset = _asset(_interface())
    asset["credential_refs"] = []
    plans, unknowns, gate = build_runtime_plans_v1(asset, {})

    assert gate["status"] == "PASS"
    assert unknowns == []
    slot = plans[0]["credential_template"]["credential_slots"][0]
    assert slot["resolution_status"] == "RUNTIME_CREDENTIAL_REF_REQUIRED"
    assert not slot.get("credential_ref")
    assert slot["credential_value_loaded"] is False


def test_multiple_credential_refs_for_same_actor_block_runtime_plan() -> None:
    asset = _asset(_interface())
    asset["credential_refs"].append(
        {
            "credential_ref": "credential-ref:warehouse-user-2",
            "actor_ref": "仓管员",
        }
    )
    plans, unknowns, gate = build_runtime_plans_v1(asset, {})

    assert gate["status"] == "BLOCKED_RUNTIME_PLAN_INCOMPLETE"
    assert plans[0]["status"] == "INCOMPLETE"
    assert any(row["kind"] == "RUNTIME_PLAN_CREDENTIAL_REF_AMBIGUOUS" for row in unknowns)


def test_read_only_runtime_plan_has_no_cleanup_action() -> None:
    contract = _contract(method="GET")
    interface = _interface()
    interface["method"] = "GET"
    asset = _asset(interface, contract)
    plans, _unknowns, gate = build_runtime_plans_v1(asset, {})

    assert gate["status"] == "PASS"
    cleanup = plans[0]["cleanup_step_templates"]
    assert cleanup["write_action"] is False
    assert cleanup["steps"][0]["step_kind"] == "NO_CLEANUP_REQUIRED"


def test_upstream_execution_contract_gate_closed_builds_no_runtime_plan() -> None:
    asset = _asset(_interface())
    asset["scenario_execution_contract_gate"] = {
        "status": "BLOCKED_EXECUTION_CONTRACT_INCOMPLETE",
        "entry_allowed": False,
    }
    plans, unknowns, gate = build_runtime_plans_v1(asset, {})

    assert plans == []
    assert unknowns == []
    assert gate["status"] == "BLOCKED_RUNTIME_PLAN_UPSTREAM_EXECUTION_CONTRACT_GATE"
    assert gate["execution_allowed"] is False


def test_runtime_plan_projection_is_idempotent_and_adds_relationships() -> None:
    asset = _asset(_interface())
    model = {"source_summary": {}, "metrics": {}}

    project_runtime_plans_to_asset(asset, model)
    first_plans = deepcopy(asset["runtime_plans"])
    first_relationships = deepcopy(asset["runtime_plan_relationships"])
    project_runtime_plans_to_asset(asset, model)

    assert asset["runtime_plans"] == first_plans
    assert asset["runtime_plan_relationships"] == first_relationships
    assert {
        row["relation"] for row in asset["runtime_plan_relationships"]
    } == {
        "execution_contract_to_runtime_plan",
        "runtime_plan_to_interface",
    }
    assert asset["summary"]["runtime_execution_allowed"] is False
    assert asset["governance"]["runtime_plan_plaintext_credentials_allowed"] is False
