from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.closure import (
    apply_minimum_understanding_closure,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.schema import (
    empty_model,
)


def test_governed_binding_projects_authoritative_and_candidate_edges() -> None:
    fact = {
        "fact_id": "fact:ship",
        "status": "ACCEPTED",
        "kind": "RULE",
        "raw_statement": "已审核订单允许发货",
        "subject": {"entity_refs": ["订单"], "actor_refs": ["仓管员"]},
        "object": {"entity_refs": ["订单"]},
        "action": {"canonical": "发货", "raw": "发货"},
        "conditions": ["status=approved"],
        "modality": "MAY",
        "polarity": "POSITIVE",
        "source_spans": [
            {
                "source_id": "prd",
                "locator": "prd.md#ship",
                "quote": "已审核订单允许发货",
            }
        ],
        "state_effects": [],
        "data_effects": [],
        "postconditions": [],
        "exceptions": [],
    }
    model = empty_model()
    model["operations"] = [
        {
            "operation_id": "operation:ship",
            "name": "发货",
            "raw_action_names": ["发货"],
            "object_refs": ["订单"],
            "actor_refs": ["仓管员"],
            "evidence": [
                {
                    "source_id": "prd",
                    "source_locator": "prd.md#ship",
                    "fact_id": "fact:ship",
                    "derivation": "source_span",
                }
            ],
        }
    ]
    interface_id = "api:POST:/orders/{id}/ship"
    asset = {
        "business_fact_ledger": {"items": [fact]},
        "source_inventory": [],
        "enterprise_comprehension_gate": {"entry_allowed": True, "status": "PASS"},
        "interfaces": [
            {
                "interface_id": interface_id,
                "source_id": "openapi",
                "method": "POST",
                "path": "/orders/{id}/ship",
                "operation_id": "发货",
                "summary": "发货",
                "parameters": ["status", "id"],
                "field_dictionary": ["status", "id"],
            }
        ],
        "data_tables": [
            {
                "table_id": "table:订单",
                "source_id": "schema",
                "name": "订单",
                "columns": ["id", "status"],
            }
        ],
        "field_dictionary": [
            {
                "field_id": "field:order:status",
                "source_id": "schema",
                "table_id": "table:订单",
                "table": "订单",
                "field": "status",
                "field_path": "订单.status",
                "description": "订单状态",
            }
        ],
        "ui_design_specs": [
            {
                "ui_spec_id": "ui:order-detail",
                "source_id": "ui-spec",
                "name": "订单详情",
                "description": "订单详情页",
                "components": ["发货"],
                "text_labels": ["发货"],
            }
        ],
        "relationships": [
            {
                "edge_id": "edge:existing",
                "from": "fact:ship",
                "to": interface_id,
                "relation": "behavior_to_interface",
                "status": "accepted",
                "derivation": "exact_source_section",
                "evidence_gate": "exact_source_section",
            }
        ],
        "rule_library": [],
        "document_structure_assets": {"items": [], "errors": []},
        "summary": {},
        "coverage_gaps": [],
        "governance": {},
    }

    result = apply_minimum_understanding_closure(model, asset)

    assert result["implementation_binding_gate"]["status"] == "PASS"
    assert result["implementation_binding_gate"]["scenario_planning_allowed"] is True
    assert result["implementation_binding_gate"]["execution_allowed"] is False
    edges = asset["implementation_binding_relationships"]
    by_relation = {}
    for edge in edges:
        by_relation.setdefault(edge["relation"], []).append(edge)

    action = by_relation["behavior_to_interface"][0]
    assert action["to"] == interface_id
    assert action["status"] == "accepted"
    assert action["evidence_gate"] == "governed_authoritative_action_binding"

    observer = by_relation["behavior_condition_observed_by_field"][0]
    assert observer["to"] == "field:order:status"
    assert observer["status"] == "accepted"
    assert observer["evidence_gate"] == "exact_field_and_object_table_identity"

    contract_candidate = by_relation["behavior_field_candidate_in_interface"][0]
    assert contract_candidate["status"] == "candidate"
    assert contract_candidate["evidence_gate"] == "request_contract_field_is_not_runtime_observer"

    response = by_relation["behavior_effect_observed_by_api_response"][0]
    assert response["status"] == "accepted"
    assert response["evidence"]["expected_assertion_compiled"] is False

    ui = by_relation["behavior_to_ui_design_candidate"][0]
    assert ui["status"] == "candidate"
    assert ui["evidence"]["executable_locator_available"] is False

    assert any(edge["edge_id"] == "edge:existing" for edge in asset["relationships"])
    assert asset["summary"]["implementation_binding_relationship_count"] == len(edges)
    assert asset["governance"]["implementation_binding_relationship_graph_enabled"] is True
