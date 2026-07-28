from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_health_contract as health
from ai_test_asset_center.private_pilot_browser_matrix_health_patch import (
    browser_matrix_health_status,
    install_browser_matrix_health_patch,
)


class _Handler:
    def __init__(self, root: Path) -> None:
        self._test_root = root

    def _root(self) -> Path:
        return self._test_root

    def _llm_health(self) -> dict[str, Any]:
        return {"available": True, "status": "online", "label": "online"}


def test_browser_matrix_health_is_additive(tmp_path: Path, monkeypatch: Any) -> None:
    install_browser_matrix_health_patch()
    original = getattr(health, "_qualibug_health_builder_before_browser_matrix")

    def base_payload(
        handler: Any,
        *,
        fallback_root: Path,
        patch_source: str,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "status": "healthy",
            "components": {"api": {"status": "healthy"}},
        }

    monkeypatch.setattr(
        health,
        "_qualibug_health_builder_before_browser_matrix",
        base_payload,
    )
    monkeypatch.setattr(health, "build_private_pilot_health_payload", base_payload)
    setattr(health, "_qualibug_browser_matrix_health_patch_installed", False)
    install_browser_matrix_health_patch()

    payload = health.build_private_pilot_health_payload(
        _Handler(tmp_path),
        fallback_root=tmp_path,
        patch_source="test",
    )

    assert payload["ok"] is True
    assert payload["status"] == "healthy"
    assert payload["components"]["api"]["status"] == "healthy"
    matrix = payload["browser_matrix"]
    assert matrix["schema_version"] == "qualibug.browser-matrix-health.v1"
    assert matrix["supported_engines"] == ["chromium", "firefox", "webkit"]
    assert matrix["engine_binary_verification"]["status"] == (
        "not_launched_by_health_check"
    )
    assert matrix["governance"]["property_held_requires_all_profiles"] is True
    assert matrix["governance"]["runtime_failure_is_formal_violation"] is False
    assert matrix["governance"]["interactive_matrix_supported"] is False
    assert matrix["governance"]["cross_engine_visual_baseline_supported"] is False
    assert payload["components"]["browser_matrix"]["status"] in {
        "healthy",
        "degraded",
    }

    monkeypatch.setattr(health, "build_private_pilot_health_payload", original)


def test_browser_matrix_health_reports_lazy_or_installed_runtime() -> None:
    status = browser_matrix_health_status()

    assert status["runtime_installation"] in {
        "installed",
        "lazy_on_discovery_runtime_import",
    }
    assert isinstance(status["checks"]["playwright_python_available"], bool)
    assert isinstance(status["checks"]["contract_guard_available"], bool)
    assert isinstance(status["checks"]["execution_module_available"], bool)
