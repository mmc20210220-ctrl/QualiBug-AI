from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_downstream import (
    refresh_chinese_business_downstream,
)


def _rule() -> dict:
    return {
        "rule_id": "rule:ship",
        "source_id": "prd",
        "source_locator": "prd.md#ship",
        "statement": "已审核订单允许发货",
        "risk_type": "business_logic",
        "severity": "P1",
        "derivation": "chinese_first_business_comprehension",
        "semantic_contract": {"status": "ACCEPTED", "fact_id": "fact:ship"},
    }


def _interface() -> dict:
    return {
        "interface_id": "api:POST:/orders/{id}/ship",
        "source_id": "openapi",
        "method": "POST",
        "path": "/orders/{id}/ship",
        "operation_id": "发货",
        "summary": "发货",
        "source_excerpt": "业务约束：已审核订单允许发货",
        "parameters": ["status"],
        "field_dictionary": ["status"],
    }


def _asset(binding_status: str | None) -> dict:
    asset = {
        "phase": "enterprise_knowledge_center",
        "asset_id": "asset:test",
        "rule_library": [_rule()],
        "interfaces": [_interface()],
        "relationships": [],
        "risk_domains": [
            {
                "risk_id": "risk:rule:ship",
                "source_rule_id": "rule:ship",
                "derivation": "chinese_first_business_comprehension",
            }
        ],
        "oracle_library": [
            {
                "oracle_id": "oracle:rule:ship",
                "rule_id": "rule:ship",
                "derivation": "chinese_first_business_comprehension",
            }
        ],
        "enterprise_comprehension_gate": {
            "status": "PASS",
            "entry_allowed": True,
        },
        "coverage_gaps": [],
        "summary": {},
    }
    if binding_status is not None:
        asset["implementation_binding_gate"] = {
            "schema": "qualibug.business-behavior-implementation-binding-gate.v1",
            "status": binding_status,
            "entry_allowed": binding_status == "PASS",
            "scenario_planning_allowed": binding_status == "PASS",
            "execution_allowed": False,
            "metrics": {},
        }
    return asset


def test_partial_implementation_binding_blocks_chinese_probe_mainline() -> None:
    asset, probes = refresh_chinese_business_downstream(
        _asset("PARTIAL_IMPLEMENTATION_BINDING")
    )

    assert probes == []
    assert asset["scenario_planning_gate"]["status"] == "BLOCKED_IMPLEMENTATION_BINDING_GATE"
    assert asset["scenario_planning_gate"]["entry_allowed"] is False
    assert asset["scenario_planning_gate"]["execution_allowed"] is False
    assert asset["enterprise_comprehension_gate"]["status"] == "PASS"
    assert asset["enterprise_comprehension_gate"]["entry_allowed"] is True
    assert asset["enterprise_comprehension_gate"]["scenario_planning_allowed"] is False
    assert asset["rule_library"][0]["downstream_binding_status"] == "BLOCKED_IMPLEMENTATION_BINDING_GATE"
    assert not any(
        row.get("source_rule_id") == "rule:ship"
        and row.get("derivation") == "chinese_first_business_comprehension"
        for row in asset["risk_domains"]
    )
    assert not any(
        row.get("rule_id") == "rule:ship"
        and row.get("derivation") == "chinese_first_business_comprehension"
        for row in asset["oracle_library"]
    )
    assert any(
        row["kind"] == "BLOCKED_IMPLEMENTATION_BINDING_GATE"
        for row in asset["coverage_gaps"]
    )


def test_missing_implementation_gate_cannot_bypass_new_mainline() -> None:
    asset, probes = refresh_chinese_business_downstream(_asset(None))

    assert probes == []
    assert asset["scenario_planning_gate"]["status"] == "BLOCKED_IMPLEMENTATION_BINDING_GATE"
    assert asset["scenario_planning_gate"]["implementation_binding_gate_present"] is False
    assert asset["scenario_planning_gate"]["implementation_binding_status"] == "NOT_BUILT"


def test_passed_implementation_binding_allows_scenario_asset_generation() -> None:
    asset, _probes = refresh_chinese_business_downstream(_asset("PASS"))

    assert asset["scenario_planning_gate"]["status"] == "PASS"
    assert asset["scenario_planning_gate"]["entry_allowed"] is True
    assert asset["scenario_planning_gate"]["scenario_planning_allowed"] is True
    assert asset["scenario_planning_gate"]["execution_allowed"] is False
    assert asset["rule_library"][0]["downstream_binding_status"] == "READY_AUTHORITATIVE_OPERATION_BOUND"
    assert any(row.get("source_rule_id") == "rule:ship" for row in asset["risk_domains"])
    assert any(row.get("rule_id") == "rule:ship" for row in asset["oracle_library"])
    assert asset["enterprise_comprehension_gate"]["status"] == "PASS"
    assert asset["enterprise_comprehension_gate"]["entry_allowed"] is True
