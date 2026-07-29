from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding_governance import (
    build_governed_behavior_implementation_bindings,
)


def test_same_named_field_on_unrelated_object_is_not_an_observer() -> None:
    behavior = {
        "behavior_id": "behavior:ship",
        "source_refs": ["fact:ship"],
        "operation_ref": "发货",
        "object_refs": ["订单"],
        "preconditions": [
            {
                "slot_id": "slot:status",
                "field_candidate": "status",
                "operator_candidate": "EQUALS",
                "value_candidate": {"raw": "approved"},
            }
        ],
        "permission_decision": "ALLOW",
        "state_effects": [],
        "data_effects": [],
        "status": "CONFIRMED",
        "condition_combinator": "SINGLE_CONDITION",
    }
    interface_id = "api:POST:/orders/{id}/ship"
    asset = {
        "interfaces": [
            {
                "interface_id": interface_id,
                "source_id": "openapi",
                "method": "POST",
                "path": "/orders/{id}/ship",
                "operation_id": "发货",
                "summary": "发货",
                "parameters": ["status"],
                "field_dictionary": ["status"],
            }
        ],
        "relationships": [
            {
                "edge_id": "edge:ship",
                "from": "fact:ship",
                "to": interface_id,
                "relation": "behavior_to_interface",
                "status": "accepted",
                "derivation": "exact_source_section",
                "evidence_gate": "exact_source_section",
            }
        ],
        "data_tables": [
            {
                "table_id": "table:发票",
                "source_id": "schema",
                "name": "发票",
                "columns": ["status"],
            }
        ],
        "field_dictionary": [
            {
                "field_id": "field:invoice:status",
                "source_id": "schema",
                "table_id": "table:发票",
                "table": "发票",
                "field": "status",
                "field_path": "发票.status",
            }
        ],
        "ui_design_specs": [],
        "rule_library": [],
    }

    bindings, unknowns, conflicts, gate = build_governed_behavior_implementation_bindings(
        asset, [behavior]
    )

    assert conflicts == []
    slot = bindings[0]["condition_observer_bindings"][0]
    assert slot["status"] == "OBJECT_TABLE_UNRESOLVED"
    assert slot["object_table_identity_confirmed"] is False
    assert slot["reason_code"] == "IMPLEMENTATION_FIELD_OBJECT_TABLE_UNRESOLVED"
    assert bindings[0]["scenario_planning_ready"] is False
    assert any(
        row["kind"] == "IMPLEMENTATION_CONDITION_OBSERVER_UNRESOLVED"
        and row["reason_code"] == "IMPLEMENTATION_FIELD_OBJECT_TABLE_UNRESOLVED"
        for row in unknowns
    )
    assert gate["status"] == "PARTIAL_IMPLEMENTATION_BINDING"
    assert gate["object_table_identity_required_for_database_observers"] is True
