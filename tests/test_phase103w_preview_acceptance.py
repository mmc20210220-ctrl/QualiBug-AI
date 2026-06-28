from __future__ import annotations

import json

from ai_test_asset_center.phase103_preview_acceptance import main, validate_preview_site, validate_scenarios


def test_phase103w_validates_manufacturing_preview(tmp_path) -> None:
    report = validate_preview_site(scenario="manufacturing", static_dir=tmp_path / "site")

    assert report.passed is True
    assert report.score == 100
    assert report.project_id == "demo_manufacturing_erp_v3"
    assert any(check.key == "redaction_gate" and check.passed for check in report.checks)
    assert "通过" in report.to_markdown()


def test_phase103w_writes_multi_scenario_reports(tmp_path) -> None:
    summary = validate_scenarios(["manufacturing", "ecommerce", "saas"], output_dir=tmp_path)

    assert summary["passed"] is True
    assert summary["scenario_count"] == 3
    assert summary["average_score"] == 100
    assert (tmp_path / "acceptance_summary.json").exists()
    assert (tmp_path / "acceptance_summary.md").exists()
    assert (tmp_path / "manufacturing" / "acceptance_report.json").exists()

    payload = json.loads((tmp_path / "saas" / "acceptance_report.json").read_text(encoding="utf-8"))
    combined = json.dumps(payload, ensure_ascii=False)
    assert payload["passed"] is True
    assert "raw-saas-token" not in combined
    assert "DemoPasswordShouldBeRedacted" not in combined


def test_phase103w_detects_custom_secret_pattern(tmp_path) -> None:
    report = validate_preview_site(
        scenario="ecommerce",
        static_dir=tmp_path / "site",
        secret_patterns=("QualiBug AI",),
    )

    redaction = next(check for check in report.checks if check.key == "redaction_gate")
    assert report.passed is False
    assert redaction.passed is False
    assert "QualiBug AI" in redaction.detail


def test_phase103w_cli_returns_success_and_writes_reports(tmp_path, capsys) -> None:
    exit_code = main(["--scenario", "manufacturing", "--output-dir", str(tmp_path)])
    captured = capsys.readouterr().out

    assert exit_code == 0
    assert "average_score" in captured
    assert (tmp_path / "acceptance_summary.json").exists()
