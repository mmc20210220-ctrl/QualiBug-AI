from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center import _linking


def _formal_asset() -> dict:
    return {
        "asset_id": "asset:formal",
        "scenario_ir_gate": {"status": "PASS", "entry_allowed": True},
        "scenario_execution_contract_gate": {
            "status": "PASS",
            "entry_allowed": True,
        },
        "runtime_plan_gate": {"status": "PASS", "entry_allowed": True},
        "runtime_materialization_gate": {
            "status": "PASS",
            "entry_allowed": True,
        },
        "scenario_ir": [
            {
                "scenario_id": "scenario:ship",
                "scenario_type": "STATE_TRANSITION",
                "title": "订单发货",
                "actor_refs": ["仓管员"],
                "expected_outcome": {
                    "permission_decision": "ALLOW",
                    "expected_effects": ["订单状态变为 shipped"],
                },
                "formal_ui_surface_bindings": [
                    {
                        "action_surface_binding_id": "surface:ui:ship",
                        "surface_kind": "UI_BROWSER",
                    }
                ],
            }
        ],
        "runtime_plans": [
            {
                "plan_id": "plan:ship",
                "scenario_ref": "scenario:ship",
                "behavior_ref": "behavior:ship",
                "implementation_binding_ref": "binding:ship",
                "scenario_type": "STATE_TRANSITION",
                "status": "TEMPLATE_READY",
                "formal_runtime_plan": True,
                "action_entry": {
                    "interface_id": "api:POST:/orders/{id}/ship",
                    "method": "POST",
                    "path": "/orders/{id}/ship",
                    "operation_id": "shipOrder",
                    "authoritative": True,
                    "action_surface_binding_ref": "surface:http:ship",
                },
                "binding_identity_refs": {
                    "action_surface_binding_ref": "surface:http:ship",
                    "contract_field_binding_refs": ["field-binding:id"],
                    "runtime_value_binding_refs": ["value-binding:id"],
                },
                "oracle_query_templates": {},
            }
        ],
        "runtime_materializations": [
            {
                "materialization_id": "materialization:ship",
                "runtime_plan_ref": "plan:ship",
                "status": "DRAFT_READY",
                "formal_runtime_materialization": True,
                "binding_identity_refs": {
                    "action_surface_binding_ref": "surface:http:ship"
                },
                "request_draft": {
                    "action_surface_binding_ref": "surface:http:ship"
                },
            }
        ],
        # Deliberately wrong legacy input: the formal compiler must ignore it.
        "interfaces": [
            {
                "interface_id": "api:GET:/wrong",
                "method": "GET",
                "path": "/wrong",
            }
        ],
        "risk_domains": [
            {
                "risk_id": "risk:wrong",
                "source_rule_id": "rule:wrong",
                "risk_type": "business_rule",
                "title": "错误旧风险",
            }
        ],
        "relationships": [
            {
                "edge_id": "edge:wrong",
                "from": "rule:wrong",
                "to": "api:GET:/wrong",
                "relation": "rule_to_interface",
                "status": "accepted",
                "derivation": "exact_source_section",
                "evidence_gate": "exact_source_section",
                "evidence": {"operation_locator": "GET /wrong"},
            }
        ],
    }


def test_formal_runtime_chain_is_the_only_probe_source() -> None:
    probes = _linking._probes_from_asset(_formal_asset(), 10)

    assert len(probes) == 1
    probe = probes[0]
    assert probe["source"] == "enterprise_understanding_runtime_plan"
    assert probe["method"] == "POST"
    assert probe["path"] == "/orders/{id}/ship"
    assert probe["runtime_plan_ref"] == "plan:ship"
    assert probe["runtime_materialization_ref"] == "materialization:ship"
    assert probe["action_surface_binding_ref"] == "surface:http:ship"
    assert probe["formal_ui_surface_binding_refs"] == ["surface:ui:ship"]
    lineage = probe["knowledge_lineage"]
    assert (
        lineage["binding_authority"]
        == "formal_runtime_plan_and_materialization_identity"
    )
    assert lineage["legacy_risk_domain_endpoint_selection_used"] is False
    assert lineage["interface_id"] == "api:POST:/orders/{id}/ship"


def test_formal_stage_never_falls_back_when_gate_is_closed() -> None:
    asset = _formal_asset()
    asset["runtime_materialization_gate"] = {
        "status": "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE",
        "entry_allowed": False,
    }

    assert _linking._probes_from_asset(asset, 10) == []


def test_materialization_identity_mismatch_blocks_probe() -> None:
    asset = _formal_asset()
    asset["runtime_materializations"][0]["binding_identity_refs"][
        "action_surface_binding_ref"
    ] = "surface:http:other"
    asset["runtime_materializations"][0]["request_draft"][
        "action_surface_binding_ref"
    ] = "surface:http:other"

    assert _linking._probes_from_asset(asset, 10) == []


def test_formal_plan_without_action_binding_identity_is_not_admitted() -> None:
    asset = _formal_asset()
    asset["runtime_plans"][0]["action_entry"].pop(
        "action_surface_binding_ref"
    )
    asset["runtime_plans"][0]["binding_identity_refs"].pop(
        "action_surface_binding_ref"
    )

    assert _linking._probes_from_asset(asset, 10) == []


def test_legacy_asset_remains_source_relationship_governed() -> None:
    asset = {
        "asset_id": "asset:legacy",
        "interfaces": [
            {
                "interface_id": "api:GET:/orders",
                "method": "GET",
                "path": "/orders",
                "operation_id": "listOrders",
            }
        ],
        "risk_domains": [
            {
                "risk_id": "risk:orders",
                "source_rule_id": "rule:orders",
                "risk_type": "business_rule",
                "severity": "P1",
                "title": "订单规则",
                "expected": "订单结果符合规则",
                "evidence": ["prd"],
            }
        ],
        "relationships": [
            {
                "edge_id": "edge:orders",
                "from": "rule:orders",
                "to": "api:GET:/orders",
                "relation": "rule_to_interface",
                "status": "accepted",
                "derivation": "exact_source_section",
                "evidence_gate": "exact_source_section",
                "evidence": {"operation_locator": "GET /orders"},
            }
        ],
    }

    probes = _linking._probes_from_asset(asset, 10)

    assert len(probes) == 1
    assert (
        probes[0]["knowledge_lineage"]["binding_authority"]
        == "accepted_rule_to_interface_relationship"
    )
    assert (
        probes[0]["knowledge_lineage"][
            "legacy_risk_domain_endpoint_selection_used"
        ]
        is True
    )
