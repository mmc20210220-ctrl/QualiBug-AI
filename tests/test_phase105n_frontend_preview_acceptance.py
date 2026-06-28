from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.phase105_frontend_preview_acceptance import (
    FRONTEND_PREVIEW_ACCEPTANCE_JSON,
    FRONTEND_PREVIEW_ACCEPTANCE_MANIFEST,
    FRONTEND_PREVIEW_ACCEPTANCE_MD,
    main,
    run_frontend_preview_acceptance,
    validate_frontend_preview_acceptance,
)
from ai_test_asset_center.phase105_frontend_preview_server import Phase105FrontendPreviewSite


def _check(report, key: str):
    return next(check for check in report.checks if check.key == key)


def test_phase105n_build_first_preview_acceptance(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    output_dir = tmp_path / "acceptance"

    report = run_frontend_preview_acceptance(
        bundle_dir=bundle_dir,
        output_dir=output_dir,
        scenario="manufacturing",
        build_first=True,
    )

    assert report.passed is True
    assert report.score >= 90
    assert _check(report, "health_api").passed is True
    assert _check(report, "static_page_routes").passed is True
    assert _check(report, "readonly_method_guard").passed is True
    assert _check(report, "path_traversal_guard").passed is True
    assert _check(report, "redaction_guard").passed is True
    assert (output_dir / FRONTEND_PREVIEW_ACCEPTANCE_JSON).exists()
    assert (output_dir / FRONTEND_PREVIEW_ACCEPTANCE_MD).exists()
    assert (output_dir / FRONTEND_PREVIEW_ACCEPTANCE_MANIFEST).exists()


def test_phase105n_detects_missing_static_page(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    Phase105FrontendPreviewSite(bundle_dir=bundle_dir, scenario="manufacturing")
    (bundle_dir / "hub_v2" / "pages" / "test_execution" / "test_execution.html").unlink()

    report = validate_frontend_preview_acceptance(
        bundle_dir,
        output_dir=tmp_path / "acceptance",
        scenario="manufacturing",
        build_bundle=False,
    )

    assert report.passed is False
    assert _check(report, "static_page_routes").passed is False
    assert "test_execution" in _check(report, "static_page_routes").detail


def test_phase105n_cli_build_first_and_existing_bundle_mode(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    output_dir = tmp_path / "acceptance"

    assert main(["--build-first", "--bundle-dir", str(bundle_dir), "--output-dir", str(output_dir)]) == 0
    assert (output_dir / FRONTEND_PREVIEW_ACCEPTANCE_JSON).exists()

    recheck_dir = tmp_path / "acceptance_recheck"
    assert main(["--bundle-dir", str(bundle_dir), "--output-dir", str(recheck_dir)]) == 0
    assert (recheck_dir / FRONTEND_PREVIEW_ACCEPTANCE_MANIFEST).exists()
