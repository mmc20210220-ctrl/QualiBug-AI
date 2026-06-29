from __future__ import annotations

from pathlib import Path

import pytest

from aitestops.playwright_offline_bundle import install_playwright_offline_bundle, verify_playwright_offline_bundle


def test_install_playwright_offline_bundle_requires_wheelhouse(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        install_playwright_offline_bundle(
            bundle_dir=tmp_path,
            requirements_file=tmp_path / "requirements-optional.txt",
            browsers_path=None,
            env_out=None,
        )


def test_verify_playwright_offline_bundle_detects_missing_assets(tmp_path: Path) -> None:
    report = verify_playwright_offline_bundle(bundle_dir=tmp_path)
    assert report["wheelhouse_ok"] is False
    assert report["browsers_ok"] is False
    assert report["issues"]


def test_verify_playwright_offline_bundle_detects_browser_cache(tmp_path: Path) -> None:
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    (wheelhouse / "playwright-1.40.0-py3-none-any.whl").write_text("x", encoding="utf-8")
    browsers_root = tmp_path / "browsers" / "ms-playwright"
    browsers_root.mkdir(parents=True)
    (browsers_root / "chromium-1234").mkdir()
    (browsers_root / "firefox-5678").mkdir()

    report = verify_playwright_offline_bundle(bundle_dir=tmp_path)
    assert report["wheelhouse_ok"] is True
    assert report["browsers_ok"] is True
    detected = report["browsers_detected"]
    assert detected["chromium"] >= 1
    assert detected["firefox"] >= 1
