from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.phase103_delivery_acceptance import main, validate_delivery_bundle
from ai_test_asset_center.phase103_delivery_bundle import build_delivery_bundle


def test_phase103y_validates_delivery_bundle(tmp_path: Path) -> None:
    bundle = tmp_path / "delivery"
    build_delivery_bundle(scenarios=("manufacturing",), output_dir=bundle)

    report = validate_delivery_bundle(bundle, output_dir=tmp_path / "acceptance", require_zip=True)

    assert report["passed"] is True
    assert report["score"] >= 90
    assert not report["failed_checks"]
    assert (tmp_path / "acceptance" / "delivery_acceptance_report.json").exists()
    assert (tmp_path / "acceptance" / "delivery_acceptance_report.md").exists()
    keys = {check["key"] for check in report["checks"]}
    assert "manifest_passed" in keys
    assert "redaction_guard" in keys
    assert any(key.endswith(":risk_evidence") for key in keys)


def test_phase103y_detects_raw_secret_leak(tmp_path: Path) -> None:
    bundle = tmp_path / "leaky_delivery"
    build_delivery_bundle(scenarios=("ecommerce",), output_dir=bundle, create_zip=False)
    leak_path = bundle / "commercial" / "leak.md"
    leak_path.write_text("Authorization: Bearer raw-customer-secret\n", encoding="utf-8")

    report = validate_delivery_bundle(bundle)

    assert report["passed"] is False
    failed_keys = {check["key"] for check in report["failed_checks"]}
    assert "redaction_guard" in failed_keys
    serialized = json.dumps(report, ensure_ascii=False)
    assert "Bearer raw" in serialized


def test_phase103y_cli_build_first_and_write_report(tmp_path: Path, capsys) -> None:
    bundle = tmp_path / "cli_bundle"
    out = tmp_path / "cli_acceptance"

    code = main(["--build-first", "--scenario", "saas", "--bundle-dir", str(bundle), "--output-dir", str(out), "--require-zip"])
    captured = capsys.readouterr().out

    assert code == 0
    assert "score" in captured
    report = json.loads((out / "delivery_acceptance_report.json").read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert (bundle / "delivery_manifest.json").exists()
    assert bundle.with_suffix(".zip").exists()
