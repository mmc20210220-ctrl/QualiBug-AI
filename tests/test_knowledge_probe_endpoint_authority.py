from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center import _linking


def _interface(interface_id: str = "api:GET:/orders") -> dict:
    return {
        "interface_id": interface_id,
        "method": "GET",
        "path": "/orders",
        "operation_id": "listOrders",
    }


def _risk() -> dict:
    return {
        "risk_id": "risk:orders",
        "source_rule_id": "rule:orders",
        "risk_type": "business_rule",
        "severity": "P1",
        "title": "订单规则",
        "expected": "订单结果必须符合规则",
        "evidence": ["prd"],
    }


def _relationship(
    *,
    status: str = "accepted",
    derivation: str = "exact_source_section",
    evidence_gate: str = "exact_source_section",
) -> dict:
    return {
        "edge_id": "edge:orders",
        "from": "rule:orders",
        "to": "api:GET:/orders",
        "relation": "rule_to_interface",
        "status": status,
        "derivation": derivation,
        "evidence_gate": evidence_gate,
        "evidence": {"operation_locator": "GET /orders"},
    }


def _asset() -> dict:
    return {
        "asset_id": "asset:test",
        "interfaces": [_interface()],
        "risk_domains": [_risk()],
        "relationships": [],
    }


def test_probe_compiler_does_not_use_first_interface_fallback() -> None:
    asset = _asset()

    # The legacy implementation still emits one candidate from list(interfaces)[:1].
    assert len(_linking._impl._probes_from_asset(asset, 10)) == 1
    assert _linking._probes_from_asset(asset, 10) == []


def test_probe_requires_accepted_rule_to_interface_authority() -> None:
    asset = _asset()
    asset["relationships"] = [_relationship()]

    probes = _linking._probes_from_asset(asset, 10)

    assert len(probes) == 1
    lineage = probes[0]["knowledge_lineage"]
    assert lineage["rule_id"] == "rule:orders"
    assert lineage["interface_id"] == "api:GET:/orders"
    assert lineage["binding_authority"] == "accepted_rule_to_interface_relationship"
    assert lineage["arbitrary_endpoint_fallback_used"] is False
    assert lineage["token_overlap_is_authoritative"] is False


def test_token_overlap_candidate_cannot_become_a_probe() -> None:
    asset = _asset()
    asset["relationships"] = [
        _relationship(
            status="candidate",
            derivation="token_overlap",
            evidence_gate="token_overlap_only_requires_explicit_source_relation",
        )
    ]

    assert _linking._probes_from_asset(asset, 10) == []


def test_runtime_plan_gate_restricts_probe_to_formal_action_interfaces() -> None:
    asset = _asset()
    asset["relationships"] = [_relationship()]
    asset["runtime_plan_gate"] = {"status": "PASS", "entry_allowed": True}
    asset["runtime_plans"] = [
        {
            "plan_id": "plan:other",
            "formal_runtime_plan": True,
            "action_entry": {
                "interface_id": "api:GET:/customers",
                "authoritative": True,
            },
        }
    ]

    assert _linking._probes_from_asset(asset, 10) == []


def test_closed_runtime_plan_gate_cannot_be_bypassed() -> None:
    asset = _asset()
    asset["relationships"] = [_relationship()]
    asset["runtime_plan_gate"] = {
        "status": "PARTIAL_RUNTIME_PLAN",
        "entry_allowed": False,
    }
    asset["runtime_plans"] = []

    assert _linking._probes_from_asset(asset, 10) == []


def test_formal_runtime_plan_preserves_authoritative_probe() -> None:
    asset = _asset()
    asset["relationships"] = [_relationship()]
    asset["runtime_plan_gate"] = {"status": "PASS", "entry_allowed": True}
    asset["runtime_plans"] = [
        {
            "plan_id": "plan:orders",
            "formal_runtime_plan": True,
            "action_entry": {
                "interface_id": "api:GET:/orders",
                "authoritative": True,
            },
        }
    ]

    probes = _linking._probes_from_asset(asset, 10)

    assert len(probes) == 1
    assert probes[0]["knowledge_lineage"]["runtime_plan_interface_admitted"] is True
