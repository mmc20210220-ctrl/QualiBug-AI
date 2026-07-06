from __future__ import annotations

import json

from ai_test_asset_center.phase105_risk_evidence_experience import (
    CORE_RISK_EVIDENCE_LABELS,
    build_risk_evidence_experience,
    run_risk_evidence_experience_export,
    scan_risk_evidence_for_secret_leaks,
    validate_risk_evidence_experience,
)


def test_phase105f_generates_risk_evidence_page(tmp_path) -> None:
    manifest = build_risk_evidence_experience(tmp_path, scenario="manufacturing", api_base_url="http://127.0.0.1:8088")

    assert manifest["version"].startswith("phase105f")
    assert manifest["redaction_status"] == "safe"
    assert manifest["entrypoint"] == "risk_evidence.html"
    assert manifest["risk_count"] >= 3
    assert manifest["launch_blocking_count"] >= 1
    assert (tmp_path / "risk_evidence.html").exists()
    assert (tmp_path / "assets" / "qualibug_risk_evidence.css").exists()
    assert (tmp_path / "assets" / "qualibug_risk_evidence.js").exists()

    html = (tmp_path / "risk_evidence.html").read_text(encoding="utf-8")
    for label in ["风险与 Bug 列表", "证据链详情", "业务影响", "阻断上线", "复现步骤", "请求响应摘要", "快照对比", "默认脱敏"]:
        assert label in html

    data = json.loads((tmp_path / "data" / "risk_evidence_experience_data.json").read_text(encoding="utf-8"))
    assert data["risk_summary"]["total"] >= 3
    assert data["risk_summary"]["launch_blocking"] >= 1
    assert data["risks"]
    assert data["selected_risk"]["evidence"]["reproduction_steps"]
    assert data["selected_risk"]["evidence"]["request_summary"]["headers_redacted"] is True
    assert data["selected_risk"]["evidence"]["response_summary"]["body_redacted"] is True
    assert data["selected_risk"]["evidence"]["snapshot_before"]
    assert data["phase104_actions"]["read_risk_detail"].endswith("/risks/{risk_id}")
    assert set(manifest["core_labels"]) == set(CORE_RISK_EVIDENCE_LABELS)


def test_phase105f_acceptance_report_and_validate_only(tmp_path) -> None:
    result = run_risk_evidence_experience_export(output_dir=tmp_path, scenario="ecommerce", api_base_url="http://127.0.0.1:8088")

    assert result["acceptance"]["passed"] is True
    assert result["acceptance"]["score"] == 100
    assert (tmp_path / "risk_evidence_experience_acceptance_report.json").exists()
    assert (tmp_path / "risk_evidence_experience_acceptance_report.md").exists()
    assert "Phase105F 风险与证据链详情体验验收报告" in (tmp_path / "risk_evidence_experience_acceptance_report.md").read_text(encoding="utf-8")

    validate_result = run_risk_evidence_experience_export(output_dir=tmp_path, validate_only=True)
    assert validate_result["acceptance"]["passed"] is True


def test_phase105f_detects_missing_page_and_secret_leak(tmp_path) -> None:
    build_risk_evidence_experience(tmp_path)
    (tmp_path / "assets" / "qualibug_risk_evidence.js").write_text("const bad = 'client_secret=raw';", encoding="utf-8")
    (tmp_path / "risk_evidence.html").unlink()

    report = validate_risk_evidence_experience(tmp_path)
    assert report.passed is False
    details = "\n".join(check.detail for check in report.checks)
    assert "risk_evidence.html" in details
    assert "client_secret=" in details
    assert scan_risk_evidence_for_secret_leaks(tmp_path)

