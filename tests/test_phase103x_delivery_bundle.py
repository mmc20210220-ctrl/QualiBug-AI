from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ai_test_asset_center.phase103_delivery_bundle import build_delivery_bundle, main


def test_phase103x_build_single_scenario_delivery_bundle(tmp_path: Path) -> None:
    out = tmp_path / "delivery"
    manifest = build_delivery_bundle(scenarios=("manufacturing",), output_dir=out)

    assert manifest["passed"] is True
    assert manifest["scenario_count"] == 1
    assert manifest["redaction_status"] == "safe"
    assert manifest["average_acceptance_score"] >= 90
    assert Path(manifest["zip_path"]).exists()

    assert (out / "delivery_manifest.json").exists()
    assert (out / "README_DELIVERY_BUNDLE.md").read_text(encoding="utf-8").startswith("# Phase103X")
    assert (out / "commercial" / "01_one_pager.md").exists()
    assert (out / "commercial" / "02_sales_demo_script.md").exists()
    assert (out / "commercial" / "03_customer_handoff_checklist.md").exists()

    scenario = manifest["scenarios"][0]
    assert scenario["scenario"] == "manufacturing"
    assert scenario["acceptance_passed"] is True
    assert (out / scenario["entrypoint"]).exists()
    assert (out / scenario["acceptance_report"]).exists()
    assert (out / "scenarios" / "manufacturing" / "data" / "command_center.json").exists()

    serialized = json.dumps(manifest, ensure_ascii=False)
    assert "raw-manufacturing-token" not in serialized
    assert "DemoPasswordShouldBeRedacted" not in serialized


def test_phase103x_zip_contains_static_site_and_commercial_assets(tmp_path: Path) -> None:
    out = tmp_path / "delivery_zip"
    manifest = build_delivery_bundle(scenarios=("ecommerce",), output_dir=out)
    zip_path = Path(manifest["zip_path"])

    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    assert "delivery_manifest.json" in names
    assert "commercial/01_one_pager.md" in names
    assert "scenarios/ecommerce/site/index.html" in names
    assert "scenarios/ecommerce/site/dashboard.html" in names
    assert "scenarios/ecommerce/acceptance_report.md" in names


def test_phase103x_cli_no_zip(tmp_path: Path, capsys) -> None:
    out = tmp_path / "cli_delivery"
    code = main(["--scenario", "saas", "--output-dir", str(out), "--no-zip"])
    captured = capsys.readouterr().out
    assert code == 0
    assert "average_acceptance_score" in captured
    manifest = json.loads((out / "delivery_manifest.json").read_text(encoding="utf-8"))
    assert manifest["passed"] is True
    assert manifest["zip_path"] is None
    assert (out / "scenarios" / "saas" / "site" / "index.html").exists()
