from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    implementation_binding_governance as governance,
)


def test_governance_replaces_raw_database_binding_with_approved_observer(monkeypatch) -> None:
    interface_id = "api:POST:/orders"
    raw_binding = {
        "binding_id": "behavior-binding:create-order",
        "behavior_ref": "behavior:create-order",
        "api_operation_bindings": [
            {
                "interface_id": interface_id,
                "status": "BOUND",
                "authoritative": True,
            }
        ],
        "condition_observer_bindings": [],
        "effect_observer_bindings": [
            {
                "slot_ref": "data_effect:0",
                "purpose": "EFFECT_OBSERVER",
                "source_field_candidate": "amount",
                "status": "BOUND",
                "bindings": [
                    {
                        "binding_kind": "DATABASE_FIELD",
                        "field_id": "raw-field:amount",
                        "table_id": "table:legacy.orders",
                        "table": "legacy.orders",
                        "field": "amount",
                        "authoritative": True,
                        "derivation": "exact_field_identity",
                    }
                ],
            }
        ],
        "response_observer_bindings": [],
        "ui_action_bindings": [],
    }
    monkeypatch.setattr(
        governance,
        "build_behavior_implementation_bindings",
        lambda asset, behaviors: ([raw_binding], [], [], {}),
    )
    monkeypatch.setattr(
        governance,
        "_authoritative_interface_ids",
        lambda asset, behavior: {interface_id},
    )

    asset = {
        "interfaces": [{"interface_id": interface_id}],
        "tables": [
            {
                "table_id": "table:main.Order",
                "name": "Order",
                "schema_name": "main",
            }
        ],
        "api_operation_database_table_candidates": [
            {"candidate_id": "table-candidate", "interface_id": interface_id}
        ],
        "api_operation_database_field_candidates": [
            {"candidate_id": "field-candidate", "interface_id": interface_id}
        ],
        "approved_database_observer_field_bindings": [
            {
                "field_binding_id": "approved-binding:amount",
                "observer_id": "database-observer:create-order",
                "observer_status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
                "runtime_observer_authoritative": True,
                "interface_id": interface_id,
                "database_table_id": "table:main.Order",
                "database_field_id": "database-field:main.Order:amount",
                "database_field_name": "amount",
                "api_field_id": "api-field:Order:amount",
                "api_field_name": "amount",
                "api_property_path": ["amount"],
                "value_source": "request.body.amount",
                "mapping_decision_id": "decision:amount",
                "evidence": [{"kind": "OPERATOR_DATABASE_MAPPING_AUTHORITY"}],
            }
        ],
    }
    behaviors = [
        {
            "behavior_id": "behavior:create-order",
            "status": "CONFIRMED",
            "source_kind": "ACCEPTED_BUSINESS_FACT",
            "operation_ref": "createOrder",
            "object_refs": ["Order"],
            "preconditions": [],
            "data_effects": [
                {
                    "action": "update",
                    "field": "amount",
                    "object": "Order",
                    "statement": "update order amount",
                }
            ],
        }
    ]

    bindings, unknowns, conflicts, gate = (
        governance.build_governed_behavior_implementation_bindings(asset, behaviors)
    )

    assert conflicts == []
    assert unknowns == []
    assert gate["status"] == "PASS"
    assert gate["scenario_planning_allowed"] is True
    assert gate["execution_allowed"] is False
    assert gate["metrics"]["approved_database_observer_slot_count"] == 1
    assert gate["raw_database_fields_cannot_bypass_mapping_authority"] is True

    binding = bindings[0]
    assert binding["scenario_planning_ready"] is True
    assert binding["execution_ready"] is False
    assert binding["database_mapping_authority_gate_enforced"] is True
    slot = binding["effect_observer_bindings"][0]
    assert slot["status"] == "BOUND"
    assert slot["approved_database_observer_used"] is True
    assert slot["raw_database_field_candidates_removed"] is True
    database_rows = [
        row for row in slot["bindings"] if row.get("binding_kind") == "DATABASE_FIELD"
    ]
    assert len(database_rows) == 1
    assert database_rows[0]["field_id"] == "database-field:main.Order:amount"
    assert database_rows[0]["observer_id"] == "database-observer:create-order"
    assert database_rows[0]["derivation"] == (
        "operator_approved_database_observer_contract"
    )
    assert database_rows[0]["write_target_allowed"] is False
    assert database_rows[0]["oracle_authority_allowed"] is False
