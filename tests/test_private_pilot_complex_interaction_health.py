from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_health_contract as health
from ai_test_asset_center.private_pilot_complex_interaction_health_patch import (
    complex_interaction_health_status,
    install_complex_interaction_health_patch,
)


class _Handler:
    def __init__(self, root: Path) -> None:
        self._test_root = root

    def _root(self) -> Path:
        return self._test_root

    def _llm_health(self) -> dict[str, Any]:
        return {"available": True, "status": "online", "label": "online"}


def test_complex_interaction_health_is_additive(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    install_complex_interaction_health_patch()
    original = getattr(health, "_qualibug_health_builder_before_complex_interactions")

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
        "_qualibug_health_builder_before_complex_interactions",
        base_payload,
    )
    monkeypatch.setattr(health, "build_private_pilot_health_payload", base_payload)
    setattr(health, "_qualibug_complex_interaction_health_patch_installed", False)
    install_complex_interaction_health_patch()

    payload = health.build_private_pilot_health_payload(
        _Handler(tmp_path),
        fallback_root=tmp_path,
        patch_source="test",
    )

    assert payload["ok"] is True
    assert payload["status"] == "healthy"
    assert payload["components"]["api"]["status"] == "healthy"
    status = payload["complex_ui_interactions"]
    assert status["schema_version"] == "qualibug.complex-interaction-health.v1"
    assert status["supported_actions"] == [
        "click_download",
        "click_popup",
        "set_input_files",
    ]
    assert status["runtime_installation"] in {
        "installed",
        "lazy_on_discovery_runtime_import",
    }
    governance = status["governance"]
    assert governance["approved_sandbox_write_required"] is True
    assert governance["persistent_cleanup_equivalence_required"] is True
    assert governance["literal_upload_paths_supported"] is False
    assert governance["download_raw_content_persisted"] is False
    assert governance["popup_closed_after_observation"] is True
    assert governance["mismatch_promoted_to_formal_violation_v1"] is False
    assert governance["browser_execution_verified_by_health"] is False

    monkeypatch.setattr(health, "build_private_pilot_health_payload", original)


def test_complex_interaction_health_reports_code_readiness() -> None:
    status = complex_interaction_health_status()

    assert status["status"] in {"healthy", "degraded"}
    assert isinstance(status["ready"], bool)
    assert isinstance(status["checks"]["modules_available"], bool)
    assert status["runtime_installation"] in {
        "installed",
        "lazy_on_discovery_runtime_import",
    }
