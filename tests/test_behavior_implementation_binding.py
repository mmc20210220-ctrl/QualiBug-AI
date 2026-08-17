from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.closure import (
    apply_minimum_understanding_closure,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding import (
    build_behavior_implementation_bindings,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.schema import (
    empty_model,
)


def _behavior(*, behavior_id: str = "behavior:ship", source_ref: str = "fact:ship") -> dict:
    return {
        "schema": "qualibug.enterprise-business-behavior.v1",
        "behavior_id": behavior_id,
        "behavior_family_id": "family:ship-order",
        "source_kind": "ACCEPTED_BUSINESS_FACT",
        "source_refs": [source_ref],
        "actor_refs": ["仓管员"],
        "operation_ref": "发货",
        "object_refs": ["订单"],
        "trigger": {},
        "preconditions": [
            {
                "slot_id": "slot:status",
                "field_candidate": "status",
                "operator_candidate": "EQUALS",
                "value_candidate": {"raw": "approved", "value_type": "TEXT"},
                "status": "CONFIRMED_SOURCE_TEXT",
            }
        ],
        "state_preconditions": [],
        "expected_effects": [],
        "state_effects": [],
        "data_effects": [],
        "permission_decision": "ALLOW",
        "exceptions": [],
        "compensations": [],
        "evidence": [
            {
                "source_id": "prd",
                "source_locator": "prd.md#ship",
                "quote": "已审核订单允许发货",
                "fact_id": source_ref,
                "derivation": "source_span",
            }
        ],
        "unresolved_semantics": [],
        "condition_combinator": "SINGLE_CONDITION",
        "status": "CONFIRMED",
        "candidate_only": False,
        "formal_business_rule": True,
    }


def _ship_interface(interface_id: str = "api:POST:/orders/{id}/ship") -> dict:
    return {
        "interface_id": interface_id,
        "source_id": "openapi",
        "source_kind": "openapi",
        "method": "POST",
        "path": "/orders/{id}/ship",
        "operation_id": "发货",
        "summary": "发货",
        "tags": ["orders"],
        "parameters": ["status", "id"],
        "field_dictionary": ["status", "id"],
        "tokens": ["发货", "orders", "ship", "status"],
    }


def _asset() -> dict:
    return {
        "interfaces": [_ship_interface()],
        "data_tables": [
            {
                "table_id": "table:订单",
                "source_id": "schema",
                "name": "订单",
                "columns": ["id", "status"],
                "aliases": [],
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
                "edge_id": "edge:ship",
                "from": "fact:ship",
                "to": "api:POST:/orders/{id}/ship",
                "relation": "behavior_to_interface",
                "status": "accepted",
                "derivation": "exact_source_section",
                "evidence_gate": "exact_source_section",
                "evidence": {"operation_locator": "POST /orders/{id}/ship"},
            }
        ],
        "rule_library": [],
    }


def test_confirmed_behavior_binds_api_field_and_response_channel() -> None:
    bindings, unknowns, conflicts, gate = build_behavior_implementation_bindings(
        _asset(), [_behavior()]
    )

    assert conflicts == []
    assert len(bindings) == 1
    binding = bindings[0]
    assert binding["status"] == "BOUND"
    assert binding["scenario_planning_ready"] is True
    assert binding["execution_ready"] is False
    assert binding["api_operation_bindings"][0]["interface_id"] == "api:POST:/orders/{id}/ship"
    assert binding["api_operation_bindings"][0]["authoritative"] is True
    assert binding["condition_observer_bindings"][0]["status"] == "BOUND"
    kinds = {
        row["binding_kind"]
        for row in binding["condition_observer_bindings"][0]["bindings"]
    }
    assert kinds == {"DATABASE_FIELD", "API_CONTRACT_FIELD"}
    assert binding["response_observer_bindings"][0]["status"] == "BOUND_CHANNEL_ONLY"
    assert binding["expected_assertion_compiled"] is False
    assert binding["ui_action_bindings"][0]["status"] == "CANDIDATE_DESIGN_BINDING"
    assert binding["ui_action_bindings"][0]["executable_locator_available"] is False
    assert gate["status"] == "PASS"
    assert gate["execution_allowed"] is False
    assert not any(row["kind"] == "BEHAVIOR_API_BINDING_UNRESOLVED" for row in unknowns)


def test_explicit_state_effect_field_creates_exact_effect_observer_slot() -> None:
    behavior = _behavior()
    behavior["preconditions"] = []
    behavior["permission_decision"] = "UNSPECIFIED"
    behavior["state_effects"] = [
        {
            "field": "status",
            "from_state": "APPROVED",
            "to_state": "SHIPPED",
            "raw": "订单状态变为SHIPPED",
        }
    ]

    bindings, unknowns, conflicts, _gate = build_behavior_implementation_bindings(
        _asset(), [behavior]
    )

    assert conflicts == []
    effect = bindings[0]["effect_observer_bindings"]
    assert len(effect) == 1
    assert effect[0]["slot_ref"] == "state_effect:0"
    assert effect[0]["source_field_candidate"] == "status"
    assert effect[0]["status"] == "BOUND"
    assert any(
        row["binding_kind"] == "DATABASE_FIELD"
        and row["field_id"] == "field:order:status"
        for row in effect[0]["bindings"]
    )
    assert not any(
        row["kind"] == "IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED"
        for row in unknowns
    )


def test_token_overlap_remains_diagnostic_and_cannot_select_endpoint() -> None:
    asset = _asset()
    asset["relationships"] = []
    asset["interfaces"] = [
        {
            "interface_id": "api:GET:/shipment-records",
            "source_id": "openapi",
            "method": "GET",
            "path": "/shipment-records",
            "operation_id": "queryShipmentRecords",
            "summary": "发货记录查询",
            "tokens": ["发货", "记录", "查询"],
            "parameters": ["status"],
        }
    ]

    bindings, unknowns, conflicts, gate = build_behavior_implementation_bindings(
        asset, [_behavior()]
    )

    assert conflicts == []
    api = bindings[0]["api_operation_bindings"]
    assert len(api) == 1
    assert api[0]["status"] == "CANDIDATE_ONLY"
    assert api[0]["authoritative"] is False
    assert bindings[0]["scenario_planning_ready"] is False
    assert any(row["kind"] == "BEHAVIOR_API_BINDING_UNRESOLVED" for row in unknowns)
    assert gate["status"] == "PARTIAL_IMPLEMENTATION_BINDING"
    assert gate["token_overlap_is_authoritative"] is False


def test_same_field_in_multiple_unrelated_tables_is_ambiguous() -> None:
    asset = _asset()
    asset["field_dictionary"] = [
        {
            "field_id": "field:a:status",
            "source_id": "schema",
            "table_id": "table:A",
            "table": "A",
            "field": "status",
        },
        {
            "field_id": "field:b:status",
            "source_id": "schema",
            "table_id": "table:B",
            "table": "B",
            "field": "status",
        },
    ]
    asset["data_tables"] = [
        {"table_id": "table:A", "name": "A"},
        {"table_id": "table:B", "name": "B"},
    ]

    bindings, unknowns, conflicts, gate = build_behavior_implementation_bindings(
        asset, [_behavior()]
    )

    assert conflicts == []
    condition = bindings[0]["condition_observer_bindings"][0]
    assert condition["status"] == "AMBIGUOUS"
    assert bindings[0]["status"] == "AMBIGUOUS"
    assert bindings[0]["scenario_planning_ready"] is False
    assert any(
        row["kind"] == "IMPLEMENTATION_CONDITION_OBSERVER_UNRESOLVED"
        and row["status"] == "AMBIGUOUS"
        for row in unknowns
    )
    assert gate["status"] == "PARTIAL_IMPLEMENTATION_BINDING"


def test_authoritative_relationship_disagreeing_with_exact_operation_is_conflict() -> None:
    asset = _asset()
    asset["interfaces"].append(
        {
            "interface_id": "api:POST:/orders/{id}/cancel",
            "source_id": "openapi",
            "method": "POST",
            "path": "/orders/{id}/cancel",
            "operation_id": "取消订单",
            "summary": "取消订单",
            "parameters": ["status"],
        }
    )
    asset["relationships"][0]["to"] = "api:POST:/orders/{id}/cancel"

    bindings, _unknowns, conflicts, gate = build_behavior_implementation_bindings(
        asset, [_behavior()]
    )

    assert bindings[0]["status"] == "CONFLICTED"
    assert bindings[0]["scenario_planning_ready"] is False
    assert len(conflicts) == 1
    assert conflicts[0]["kind"] == "BEHAVIOR_API_BINDING_CONFLICT"
    assert gate["status"] == "BLOCKED_IMPLEMENTATION_BINDING_CONFLICT"


def test_semantic_gate_and_implementation_gate_remain_separate() -> None:
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
    asset = {
        "business_fact_ledger": {"items": [fact]},
        "source_inventory": [],
        "enterprise_comprehension_gate": {"entry_allowed": True, "status": "PASS"},
        "interfaces": [],
        "data_tables": [],
        "field_dictionary": [],
        "ui_design_specs": [],
        "relationships": [],
        "rule_library": [],
        "document_structure_assets": {"items": [], "errors": []},
    }

    result = apply_minimum_understanding_closure(model, asset)

    assert result["business_behaviors"][0]["status"] == "CONFIRMED"
    assert result["implementation_binding_gate"]["status"] == "PARTIAL_IMPLEMENTATION_BINDING"
    assert result["implementation_binding_gate"]["entry_allowed"] is False
    assert result["gate"]["status"] == "PASS"
    assert result["gate"]["entry_allowed"] is True
