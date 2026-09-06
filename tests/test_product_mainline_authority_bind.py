from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center import policy_registry as policy_registry_module
from ai_test_asset_center.policy_wiring import bind_product_installed_mainline_authority


def _fresh_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(policy_registry_module, "_registry", None)
    path = tmp_path / "policy_registry.json"
    return policy_registry_module.get_policy_registry(path), path


def test_bind_rewrites_persisted_legacy_default_to_installed_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, path = _fresh_registry(tmp_path, monkeypatch)
    active = registry.get_active()
    assert active is not None
    active.strategy.execution.mainline_authority = "legacy_champion"
    active.signature = active._compute_signature()
    registry._save()
    monkeypatch.delenv("QUALIBUG_MAINLINE_AUTHORITY", raising=False)

    result = bind_product_installed_mainline_authority()

    assert result["changed"] is True
    assert result["mainline_authority"] == "experiment_candidate"
    assert result["previous_mainline_authority"] == "legacy_champion"
    monkeypatch.setattr(policy_registry_module, "_registry", None)
    restored = policy_registry_module.PolicyRegistry(path).get_active_strategy().execution
    assert restored.mainline_authority == "experiment_candidate"


def test_bind_respects_explicit_legacy_only_in_compatibility_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry, path = _fresh_registry(tmp_path, monkeypatch)
    assert registry.get_active_strategy().execution.mainline_authority == (
        "experiment_candidate"
    )
    monkeypatch.setenv("QUALIBUG_MAINLINE_AUTHORITY", "legacy_champion")
    monkeypatch.setenv("QUALIBUG_AUTHORITY_MODE", "COMPATIBILITY")

    result = bind_product_installed_mainline_authority()

    assert result["changed"] is True
    assert result["mainline_authority"] == "legacy_champion"
    monkeypatch.setattr(policy_registry_module, "_registry", None)
    assert policy_registry_module.PolicyRegistry(path).get_active_strategy().execution.mainline_authority == (
        "legacy_champion"
    )


def test_private_pilot_http_routing_serves_spa_not_legacy_html() -> None:
    root = Path(__file__).resolve().parents[1]
    source = (root / "ai_test_asset_center" / "private_pilot_http_routing.py").read_text(
        encoding="utf-8"
    )

    assert "_serve_frontend(parsed, root)" in source
    assert "def _serve_public_frontend" in source
    assert 'aliases = {' in source
    assert '"/knowledge": "/materials"' in source
    assert "render_enterprise_business_knowledge_center" not in source
    assert "render_multi_industry_benchmark_report" not in source
    assert "render_release_risk_dashboard_html" not in source
    assert "_render_onboard" not in source
    assert "_legacy_served" not in source
