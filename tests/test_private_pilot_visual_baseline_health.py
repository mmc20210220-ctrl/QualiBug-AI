from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_health_contract as health
from ai_test_asset_center.private_pilot_visual_baseline_health_patch import (
    install_visual_baseline_health_patch,
    visual_baseline_health_status,
)


class _Handler:
    def __init__(self, root: Path) -> None:
        self._test_root = root

    def _root(self) -> Path:
        return self._test_root

    def _llm_health(self) -> dict[str, Any]:
        return {"available": True, "status": "online", "label": "online"}


def test_visual_baseline_health_is_additive_and_does_not_rewrite_overall_status(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    install_visual_baseline_health_patch()
    original = getattr(
        health,
        "_qualibug_health_builder_before_visual_baseline",
    )

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
        "_qualibug_health_builder_before_visual_baseline",
        base_payload,
    )
    # Rebind the installed wrapper so this test uses the controlled base payload.
    monkeypatch.setattr(health, "build_private_pilot_health_payload", base_payload)
    setattr(health, "_qualibug_visual_baseline_health_patch_installed", False)
    install_visual_baseline_health_patch()

    payload = health.build_private_pilot_health_payload(
        _Handler(tmp_path),
        fallback_root=tmp_path,
        patch_source="test",
    )

    assert payload["ok"] is True
    assert payload["status"] == "healthy"
    assert payload["components"]["api"]["status"] == "healthy"
    visual = payload["visual_baseline"]
    assert visual["schema_version"] == "qualibug.visual-baseline-health.v1"
    assert visual["governance"]["active_registry_identity_required"] is True
    assert visual["governance"]["baseline_auto_update_supported"] is False
    assert visual["governance"]["ai_visual_judgement_used"] is False
    assert visual["evidence_policy"]["har_persisted"] is False
    assert visual["evidence_policy"]["trace_persisted"] is False
    assert payload["components"]["visual_baseline"]["status"] in {
        "healthy",
        "degraded",
    }

    monkeypatch.setattr(health, "build_private_pilot_health_payload", original)


def test_visual_baseline_health_reports_explicit_runtime_installation_state() -> None:
    status = visual_baseline_health_status()

    assert status["runtime_installation"] in {
        "installed",
        "lazy_on_discovery_runtime_import",
    }
    assert isinstance(status["checks"]["pillow_available"], bool)
    assert isinstance(status["checks"]["playwright_python_available"], bool)
    assert status["renderer_profile"]
    assert status["comparison_method"]
