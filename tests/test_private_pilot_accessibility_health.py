from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_health_contract as health
from ai_test_asset_center.private_pilot_accessibility_health_patch import (
    accessibility_health_status,
    install_accessibility_health_patch,
)


class _Handler:
    def __init__(self, root: Path) -> None:
        self._test_root = root

    def _root(self) -> Path:
        return self._test_root

    def _llm_health(self) -> dict[str, Any]:
        return {"available": True, "status": "online", "label": "online"}


def test_accessibility_health_is_additive(tmp_path: Path, monkeypatch: Any) -> None:
    install_accessibility_health_patch()
    original = getattr(health, "_qualibug_health_builder_before_accessibility_rules")

    def base_payload(
        handler: Any,
        *,
        fallback_root: Path,
        patch_source: str,
    ) -> dict[str, Any]:
        return {
            "ok": True,
            "status": "healthy",
            "components": {
                "api": {"status": "healthy"},
                "llm": {"status": "healthy"},
            },
        }

    monkeypatch.setattr(
        health,
        "_qualibug_health_builder_before_accessibility_rules",
        base_payload,
    )
    monkeypatch.setattr(health, "build_private_pilot_health_payload", base_payload)
    setattr(health, "_qualibug_accessibility_health_patch_installed", False)
    install_accessibility_health_patch()

    payload = health.build_private_pilot_health_payload(
        _Handler(tmp_path),
        fallback_root=tmp_path,
        patch_source="test",
    )

    assert payload["ok"] is True
    assert payload["status"] == "healthy"
    assert payload["components"]["api"]["status"] == "healthy"
    assert payload["components"]["llm"]["status"] == "healthy"
    accessibility = payload["accessibility_rules"]
    assert accessibility["schema_version"] == "qualibug.accessibility-health.v1"
    assert accessibility["standard"] == "wcag22-aa-deterministic"
    assert accessibility["wcag_version"] == "2.2"
    assert accessibility["supported_rule_count"] >= (
        accessibility["default_standard_rule_count"]
    )
    assert accessibility["custom_only_rule_count"] >= 0
    assert accessibility["governance"][
        "complete_observation_required_for_property_held"
    ] is True
    assert accessibility["governance"]["complex_contrast_promoted_to_pass"] is False
    assert accessibility["governance"]["truncated_scan_promoted_to_pass"] is False
    assert accessibility["governance"]["raw_dom_persisted"] is False
    assert accessibility["governance"][
        "ai_accessibility_opinion_used_as_defect"
    ] is False
    assert payload["components"]["accessibility_rules"]["status"] in {
        "healthy",
        "degraded",
    }

    monkeypatch.setattr(health, "build_private_pilot_health_payload", original)


def test_accessibility_health_reports_lazy_or_installed_runtime() -> None:
    status = accessibility_health_status()

    assert status["runtime_installation"] in {
        "installed",
        "lazy_on_discovery_runtime_import",
    }
    assert isinstance(status["checks"]["rule_module_available"], bool)
    assert isinstance(status["checks"]["action_available"], bool)
    assert isinstance(status["checks"]["source_guard_available"], bool)
    assert status["supported_rule_count"] >= status["default_standard_rule_count"]
