from __future__ import annotations

from pathlib import Path

import pytest


def test_target_endpoint_resolution_fails_closed_without_declared_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_test_asset_center.target_endpoint import (
        TargetEndpointError,
        resolve_target_base_url,
    )

    monkeypatch.delenv("QUALIBUG_TARGET_BASE_URL", raising=False)
    monkeypatch.delenv("QUALIBUG_DEFAULT_BASE_URL", raising=False)

    with pytest.raises(TargetEndpointError, match="target_base_url_required"):
        resolve_target_base_url(None)


def test_target_endpoint_resolution_accepts_explicit_or_target_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_test_asset_center.target_endpoint import resolve_target_base_url

    monkeypatch.setenv("QUALIBUG_TARGET_BASE_URL", "https://qa.example.test/api/")
    assert resolve_target_base_url(None) == "https://qa.example.test/api"
    assert resolve_target_base_url("http://127.0.0.1:9010/") == (
        "http://127.0.0.1:9010"
    )


def test_active_discovery_paths_never_default_target_to_qualibug_backend() -> None:
    root = Path(__file__).resolve().parents[1] / "ai_test_asset_center"
    active_target_modules = (
        "bug_engine_autorun.py",
        "bug_engine_reporter.py",
        "frontend_runtime_discovery_adapter.py",
        "frontend_ux_discovery_adapter.py",
        "self_improving_loop.py",
        "sweep_loop.py",
        "fixture_auto_constructor.py",
    )
    # discovery_engine retired its single-file facade into a package
    # (discovery_engine/_engine.py et al). The whole package stays on the
    # active discovery path, so every module inside it is scanned.
    discovery_engine_dir = root / "discovery_engine"
    scanned = [
        *(root / name for name in active_target_modules),
        *sorted(discovery_engine_dir.glob("*.py")),
    ]

    for module_path in scanned:
        source = module_path.read_text(encoding="utf-8")
        assert "127.0.0.1:8088" not in source, module_path.name
        assert "localhost:8088" not in source, module_path.name

    assert not (root / "scenario_runner.py").exists(), (
        "the retired MES-specific scenario runner must not re-enter the "
        "industry-neutral product path"
    )
