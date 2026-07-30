from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.database_observer_runtime_plan_projection import (
    project_database_observers_into_runtime_plans,
)


def _observer_contract() -> dict:
    return {
        "schema": "qualibug.database-observer-contract.v1",
        "observer_id": "database_observer:create-order",
        "operation_schema_binding_id": "binding:create-order:request",
        "interface_id": "api:POST:/orders",
        "database_table_id": "table:main.orders",
        "database_schema_name": "main",
        "database_table_name": "orders",
        "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
        "runtime_observer_authoritative": True,
        "read_only": True,
        "mutation_allowed": False,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
        "selected_identity_key": ["id"],
        "identity_predicates": [
            {
                "database_field_name": "id",
                "database_field_id": "field:orders:id",
                "operator": "=",
                "value_source": "request.body.id",
                "field_binding_id": "observer-field:id",
            }
        ],
        "query_plan": {
            "operation": "SELECT_ONE",
            "projection": ["id", "status"],
            "predicates": [],
            "parameterized": True,
            "maximum_rows": 2,
            "raw_sql": "",
        },
    }


def _binding() -> dict:
    return {
        "binding_id": "implementation:create-order",
        "condition_observer_bindings": [],
        "effect_observer_bindings": [
            {
                "slot_ref": "effect:status",
                "purpose": "EFFECT_OBSERVER",
                "status": "BOUND",
                "bindings": [
                    {
                        "binding_kind": "DATABASE_FIELD",
                        "observer_id": "database_observer:create-order",
                        "field_binding_id": "observer-field:status",
                        "field_id": "field:orders:status",
                        "table_id": "table:main.orders",
                        "table": "table:main.orders",
                        "field": "status",
                        "authoritative": True,
                        "read_only": True,
                        "write_target_allowed": False,
                        "oracle_authority_allowed": False,
                        "derivation": "operator_approved_database_observer_contract",
                        "mapping_decision_id": "decision:status",
                    }
                ],
            }
        ],
    }


def _runtime_plan() -> dict:
    return {
        "plan_id": "runtime-plan:create-order",
        "implementation_binding_ref": "implementation:create-order",
        "status": "TEMPLATE_READY",
        "formal_runtime_plan": True,
        "oracle_query_templates": {
            "templates": [
                {
                    "template_id": "generic-db-field",
                    "template_kind": "DATABASE_FIELD_SNAPSHOT",
                    "phase": "BEFORE_AND_AFTER",
                    "table_ref": "table:main.orders",
                    "table": "table:main.orders",
                    "field": "status",
                },
                {
                    "template_id": "unrelated-same-field",
                    "template_kind": "DATABASE_FIELD_SNAPSHOT",
                    "phase": "BEFORE",
                    "table_ref": "table:main.shipments",
                    "table": "table:main.shipments",
                    "field": "status",
                },
                {
                    "template_id": "http-response",
                    "template_kind": "HTTP_RESPONSE_CAPTURE",
                    "phase": "AFTER",
                },
            ]
        },
        "snapshot_template": {
            "before_oracle_template_refs": [
                "generic-db-field",
                "unrelated-same-field",
            ],
            "after_oracle_template_refs": ["generic-db-field", "http-response"],
        },
    }


def _asset() -> dict:
    return {
        "database_observer_contracts": [_observer_contract()],
        "behavior_implementation_bindings": [_binding()],
        "runtime_plans": [_runtime_plan()],
        "runtime_plan_unknowns": [],
        "runtime_plan_gate": {"status": "PASS", "metrics": {}},
        "relationships": [
            {
                "edge_id": "edge:contract-plan",
                "from": "execution-contract:create-order",
                "to": "runtime-plan:create-order",
                "relation": "execution_contract_to_runtime_plan",
                "status": "accepted",
                "confidence": 1.0,
            },
            {
                "edge_id": "edge:plan-interface",
                "from": "runtime-plan:create-order",
                "to": "api:POST:/orders",
                "relation": "runtime_plan_to_interface",
                "status": "accepted",
                "confidence": 1.0,
            },
        ],
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }


def test_exact_approved_observer_replaces_only_its_generic_snapshot() -> None:
    asset = _asset()
    model = {"behavior_implementation_bindings": [_binding()]}

    result = project_database_observers_into_runtime_plans(asset, model)

    plan = result["runtime_plans"][0]
    templates = plan["oracle_query_templates"]["templates"]
    assert not any(row["template_id"] == "generic-db-field" for row in templates)
    assert any(row["template_id"] == "unrelated-same-field" for row in templates)
    exact = next(
        row
        for row in templates
        if row["template_kind"] == "APPROVED_DATABASE_OBSERVER_SNAPSHOT"
    )
    assert exact["observer_handler_id"] == "approved_database_readback"
    assert exact["observer_contract_ref"] == "database_observer:create-order"
    assert exact["adapter"] == "db_sql"
    assert exact["phase"] == "BEFORE_AND_AFTER"
    assert exact["projection"] == ["id", "status"]
    assert exact["identity_predicates"][0]["value_source"] == "request.body.id"
    assert exact["database_connection_ref"] == ""
    assert exact["runtime_connection_binding_required"] is True
    assert exact["query_template_compiled"] is True
    assert exact["raw_sql_compiled"] is False
    assert exact["database_connection_opened"] is False
    assert exact["query_executed"] is False
    assert exact["oracle_verdict_emitted"] is False
    assert exact["write_target_allowed"] is False
    assert exact["mutation_allowed"] is False
    embedded = exact["database_observer_contract"]
    assert embedded["runtime_connection_bound"] is False
    assert embedded["runtime_values_materialized"] is False
    assert embedded["secret_values_retained"] is False
    assert embedded["raw_sql_retained"] is False
    assert plan["database_queries_executable"] is False
    assert plan["database_observer_runtime_template_count"] == 1
    assert exact["template_id"] in plan["snapshot_template"]["before_oracle_template_refs"]
    assert exact["template_id"] in plan["snapshot_template"]["after_oracle_template_refs"]
    assert "unrelated-same-field" in plan["snapshot_template"]["before_oracle_template_refs"]
    assert "http-response" in plan["snapshot_template"]["after_oracle_template_refs"]
    assert result["runtime_plan_gate"]["status"] == "PASS"
    assert all(row["status"] == "accepted" for row in result["runtime_plan_relationships"])
    receipt = result["database_observer_runtime_plan_projection"]
    assert receipt["runtime_template_count"] == 1
    assert receipt["runtime_connection_open_count"] == 0
    assert receipt["query_execution_count"] == 0
    assert receipt["oracle_verdict_count"] == 0
    assert receipt["stale_runtime_plan_authority_retained"] is False


def test_missing_current_contract_blocks_plan_and_downgrades_relationships() -> None:
    asset = _asset()
    asset["database_observer_contracts"] = []
    model = {"behavior_implementation_bindings": [_binding()]}

    result = project_database_observers_into_runtime_plans(asset, model)

    plan = result["runtime_plans"][0]
    assert plan["status"] == "INCOMPLETE"
    assert plan["formal_runtime_plan"] is False
    assert result["runtime_plan_gate"]["status"] == "BLOCKED_RUNTIME_PLAN_INCOMPLETE"
    assert result["runtime_plan_gate"]["entry_allowed"] is False
    assert any(
        row["reason_code"]
        == "RUNTIME_PLAN_APPROVED_DATABASE_OBSERVER_CONTRACT_MISSING"
        for row in result["runtime_plan_unknowns"]
    )
    assert all(row["status"] == "candidate" for row in result["runtime_plan_relationships"])
    assert all(row["confidence"] == 0.0 for row in result["runtime_plan_relationships"])
    assert any(
        row["kind"] == "RUNTIME_PLAN_APPROVED_DATABASE_OBSERVER_CONTRACT_MISSING"
        for row in result["coverage_gaps"]
    )
    projection = result["database_observer_runtime_plan_projection"]
    assert projection["status"] == "PARTIAL"
    assert projection["missing_contract_count"] == 1


def test_projection_rebuilds_templates_and_resolved_unknowns_from_current_contract() -> None:
    asset = _asset()
    asset["runtime_plans"][0]["oracle_query_templates"]["templates"].append(
        {
            "template_id": "old-approved-template",
            "template_kind": "APPROVED_DATABASE_OBSERVER_SNAPSHOT",
            "observer_contract_ref": "revoked-observer",
            "phase": "AFTER",
        }
    )
    asset["runtime_plan_unknowns"].append(
        {
            "unknown_id": "old-missing-contract",
            "reason_code": "RUNTIME_PLAN_APPROVED_DATABASE_OBSERVER_CONTRACT_MISSING",
        }
    )
    asset["coverage_gaps"].append(
        {
            "kind": "RUNTIME_PLAN_APPROVED_DATABASE_OBSERVER_CONTRACT_MISSING",
            "missing_contract_count": 1,
        }
    )
    model = {"behavior_implementation_bindings": [_binding()]}

    result = project_database_observers_into_runtime_plans(asset, model)

    templates = result["runtime_plans"][0]["oracle_query_templates"]["templates"]
    assert not any(row["template_id"] == "old-approved-template" for row in templates)
    assert sum(
        row["template_kind"] == "APPROVED_DATABASE_OBSERVER_SNAPSHOT"
        for row in templates
    ) == 1
    assert not any(
        row.get("unknown_id") == "old-missing-contract"
        for row in result["runtime_plan_unknowns"]
    )
    assert not any(
        row.get("kind") == "RUNTIME_PLAN_APPROVED_DATABASE_OBSERVER_CONTRACT_MISSING"
        for row in result["coverage_gaps"]
    )
