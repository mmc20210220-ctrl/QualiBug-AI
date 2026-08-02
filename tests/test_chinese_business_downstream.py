from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_downstream import (
    refresh_chinese_business_downstream,
)


def _rule(statement: str) -> dict:
    return {
        "rule_id": "zh_business:test-rule",
        "source_id": "prd-1",
        "source_locator": "订单PRD.md#section=权限;chars=0-20",
        "source_type": "chinese_business_semantic_contract",
        "statement": statement,
        "rule_type": "permission",
        "risk_type": "authorization",
        "severity": "P0",
        "tokens": ["管理员", "订单", "查看"],
        "semantic_contract": {
            "fact_id": "fact:test-rule",
            "status": "ACCEPTED",
            "source_spans": [
                {
                    "source_id": "prd-1",
                    "locator": "订单PRD.md#section=权限;chars=0-20",
                    "quote": statement,
                }
            ],
        },
        "derivation": "chinese_first_business_comprehension",
    }


def _asset(statement: str, source_excerpt: str) -> dict:
    return {
        "phase": "test",
        "asset_id": "knowledge_asset:test",
        "rule_library": [_rule(statement)],
        "interfaces": [
            {
                "interface_id": "interface:GET:/orders",
                "method": "GET",
                "path": "/orders",
                "operation_id": "listOrders",
                "summary": "查看订单",
                "source_id": "api-1",
                "source_excerpt": source_excerpt,
                "tokens": ["订单", "查看"],
            }
        ],
        "relationships": [],
        "risk_domains": [],
        "oracle_library": [],
        "coverage_gaps": [],
        "enterprise_comprehension_gate": {
            "status": "PASS",
            "entry_allowed": True,
        },
        "implementation_binding_gate": {
            "status": "PASS",
            "entry_allowed": True,
            "scenario_planning_allowed": True,
            "metrics": {},
        },
        "summary": {},
    }


def test_authoritative_source_section_closes_rule_to_probe_chain() -> None:
    statement = "管理员可以查看订单"
    asset, probes = refresh_chinese_business_downstream(
        _asset(statement, f"接口权限：{statement}。"),
        max_probe_count=20,
    )

    rule = asset["rule_library"][0]
    assert rule["downstream_binding_status"] == "READY_AUTHORITATIVE_OPERATION_BOUND"
    assert rule["authoritative_operation_refs"] == ["interface:GET:/orders"]
    assert any(
        edge["relation"] == "rule_to_interface" and edge["status"] == "accepted"
        for edge in asset["relationships"]
    )
    assert asset["risk_domains"][0]["source_rule_id"] == rule["rule_id"]
    assert asset["oracle_library"][0]["linked_interfaces"] == ["interface:GET:/orders"]
    assert len(probes) == 1
    assert probes[0]["path"] == "/orders"
    assert (
        probes[0]["knowledge_lineage"]["fact_authority"]
        == "original_chinese_source_span"
    )
    assert asset["enterprise_comprehension_gate"]["downstream"]["status"] == "PASS"


def test_short_cjk_statement_exact_source_section_binds() -> None:
    """CJK rules shorter than the old ASCII 8-char floor must still bind exactly."""
    statement = "取消订单"
    asset, _probes = refresh_chinese_business_downstream(
        _asset(
            statement,
            f"### POST /api/orders/:id/cancel\n\n{statement}。buyer 可用。",
        ),
        max_probe_count=20,
    )

    rule = asset["rule_library"][0]
    assert rule["downstream_binding_status"] == "READY_AUTHORITATIVE_OPERATION_BOUND"
    assert rule["authoritative_operation_refs"] == ["interface:GET:/orders"]
    assert "interface:GET:/orders" in rule["operation_refs"]
    assert asset["chinese_rule_downstream_gate"]["behavior_ir_bound_rule_count"] == 1


def test_unbound_chinese_rule_never_falls_back_to_first_endpoint() -> None:
    statement = "管理员可以查看订单"
    asset, probes = refresh_chinese_business_downstream(
        _asset(statement, "该接口仅返回订单列表。"),
        max_probe_count=20,
    )

    rule = asset["rule_library"][0]
    assert rule["downstream_binding_status"] == "BLOCKED_NO_AUTHORITATIVE_OPERATION_LINK"
    assert rule["authoritative_operation_refs"] == []
    assert asset["risk_domains"] == []
    assert asset["oracle_library"] == []
    assert probes == []
    downstream = asset["enterprise_comprehension_gate"]["downstream"]
    assert downstream["entry_allowed"] is False
    assert downstream["arbitrary_endpoint_fallback_allowed"] is False
    assert downstream["blocked_rule_count"] == 1
    assert any(
        gap["kind"] == "BLOCKED_BUSINESS_COMPREHENSION_DOWNSTREAM_UNBOUND"
        for gap in asset["coverage_gaps"]
    )
