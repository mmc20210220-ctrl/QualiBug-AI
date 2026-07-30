from __future__ import annotations

import importlib


def _open_gates() -> dict:
    return {
        "scenario_planning_gate": {"scenario_planning_allowed": True},
        "scenario_ir_gate": {"entry_allowed": True},
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
    assert build_gated_probes(_open_gates(), 12, compiler=compiler) == [{"probe_id": "p1"}]
    assert calls == [12]


def test_secure_installer_no_longer_replaces_resolver() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import runtime_materialization, runtime_materialization_security

    resolver = runtime_materialization._resolve_slot
    runtime_materialization_security.install_secure_runtime_value_resolver()
    assert runtime_materialization._resolve_slot is resolver
