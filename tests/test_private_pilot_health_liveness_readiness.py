from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import private_pilot_health_contract as health


class _Handler:
    def __init__(self, root: Path, llm: dict) -> None:
        self._health_root = root
        self._health_llm = llm

    def _root(self) -> Path:
        return self._health_root

    def _llm_health(self) -> dict:
        return dict(self._health_llm)


def test_llm_offline_blocks_readiness_without_killing_liveness(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        health,
        "system_behavior_runtime_status",
        lambda: {"ready": True},
    )
    payload = health.build_private_pilot_health_payload(
        _Handler(
            tmp_path,
            {"available": False, "status": "offline", "label": "offline"},
        ),
        fallback_root=tmp_path,
        patch_source="test",
    )

    assert payload["ok"] is True
    assert payload["live"] is True
    assert payload["ready"] is False
    assert payload["status"] == "degraded"
    assert payload["readiness_blockers"] == ["llm_offline"]
    assert payload["offline_reasons"] == []


def test_missing_private_root_is_a_real_liveness_failure(
    monkeypatch, tmp_path: Path
) -> None:
    missing = tmp_path / "missing"
    monkeypatch.setattr(
        health,
        "system_behavior_runtime_status",
        lambda: {"ready": True},
    )
    payload = health.build_private_pilot_health_payload(
        _Handler(missing, {"available": True, "status": "healthy"}),
        fallback_root=missing,
        patch_source="test",
    )

    assert payload["ok"] is False
    assert payload["live"] is False
    assert payload["ready"] is False
    assert payload["status"] == "offline"
    assert payload["offline_reasons"] == ["private_root_missing"]
