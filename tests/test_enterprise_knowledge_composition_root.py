from __future__ import annotations

import importlib


def _open_gates() -> dict:
    return {
        "scenario_planning_gate": {"scenario_planning_allowed": True},
        "scenario_ir_gate": {"entry_allowed": True},
        "binding_identity_gate": {"entry_allowed": True},
        "scenario_execution_contract_gate": {"entry_allowed": True},
        "runtime_plan_gate": {"entry_allowed": True},
        "runtime_materialization_gate": {"entry_allowed": True},
    }


def test_import_does_not_replace_shared_authorities() -> None:
    from ai_test_asset_center import enterprise_knowledge_center as center
    from ai_test_asset_center.enterprise_knowledge_center import _api, _linking
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import runtime_materialization

    builder = _api.build_enterprise_business_knowledge_asset
    api_probe = _api._probes_from_asset
    linking_probe = _linking._probes_from_asset
    resolver = runtime_materialization._resolve_slot
    importlib.reload(center)
    assert _api.build_enterprise_business_knowledge_asset is builder
    assert _api._probes_from_asset is api_probe
    assert _linking._probes_from_asset is linking_probe
    assert runtime_materialization._resolve_slot is resolver


def test_probe_policy_is_pure_and_fail_closed() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.probe_policy import build_gated_probes

    calls: list[int] = []

    def compiler(asset: dict, max_count: int):
        calls.append(max_count)
        return [{"probe_id": "p1"}]

    blocked = _open_gates()
    blocked["runtime_materialization_gate"] = {"entry_allowed": False}
    assert build_gated_probes(blocked, 12, compiler=compiler) == []
    assert calls == []
    assert build_gated_probes(_open_gates(), 0, compiler=compiler) == []
    assert calls == []
    assert build_gated_probes(_open_gates(), 12, compiler=compiler) == [{"probe_id": "p1"}]
    assert calls == [12]


def test_zero_probe_budget_is_strict_at_base_authority() -> None:
    from ai_test_asset_center.enterprise_knowledge_center import _api, _linking

    asset = {
        "asset_id": "asset-1",
        "interfaces": [
            {
                "interface_id": "api:GET:/orders",
                "method": "GET",
                "path": "/orders",
            }
        ],
        "risk_domains": [
            {
                "risk_id": "risk:r1",
                "source_rule_id": "r1",
                "risk_type": "business_rule",
            }
        ],
        "relationships": [
            {
                "edge_id": "edge:r1",
                "from": "r1",
                "to": "api:GET:/orders",
                "relation": "rule_to_interface",
                "status": "accepted",
                "derivation": "exact_source_section",
                # _relationship_is_authoritative requires non-empty, non-token
                # evidence: a rule→interface edge without structured evidence is
                # no longer authoritative (evidence-completeness contract).
                "evidence": {
                    "exact_source_section": "prd-source#L12",
                },
            }
        ],
    }
    assert _linking._probes_from_asset(asset, 0) == []
    assert _api._probes_from_asset(asset, 0) == []
    assert len(_linking._probes_from_asset(asset, 1)) == 1


def test_explicit_zero_is_not_replaced_by_default_budget() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.composition import _probe_limit

    assert _probe_limit(None) == 140
    assert _probe_limit(0) == 0
    assert _probe_limit("0") == 0
    assert _probe_limit(7) == 7


def test_secure_installer_no_longer_replaces_resolver() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import runtime_materialization, runtime_materialization_security

    resolver = runtime_materialization._resolve_slot
    runtime_materialization_security.install_secure_runtime_value_resolver()
    assert runtime_materialization._resolve_slot is resolver
