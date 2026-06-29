
from __future__ import annotations

import json
import shutil
from pathlib import Path

from ai_test_asset_center.phase106_frontend_risk_evidence_detail_runtime import (
    FRONTEND_APP_DIR,
    PHASE106H_VERSION,
    RISK_EVIDENCE_DETAIL_MANIFEST_JSON,
    build_frontend_risk_evidence_detail_runtime,
    run_frontend_risk_evidence_detail_runtime_export,
    scan_frontend_risk_evidence_detail_for_secret_leaks,
    validate_frontend_risk_evidence_detail_runtime,
    verify_frontend_risk_evidence_detail_checksums,
)


def test_phase106h_builds_risk_evidence_detail_route_and_models(tmp_path: Path) -> None:
    report = build_frontend_risk_evidence_detail_runtime(tmp_path, scenario="manufacturing")

    assert report.passed
    assert report.score == 100
    app_dir = tmp_path / FRONTEND_APP_DIR
    assert (app_dir / "src/pages/RiskEvidenceDetailRuntimePage.tsx").exists()
    assert (app_dir / "src/hooks/useRiskEvidenceDetailRuntime.ts").exists()
    assert (app_dir / "src/services/riskEvidenceDetailRuntime.ts").exists()
    assert (app_dir / "src/app/riskEvidenceDetailTypes.ts").exists()
    assert (tmp_path / "phase106_frontend_risk_evidence_detail_runtime.zip").exists()

    routes = (app_dir / "src/routes.ts").read_text(encoding="utf-8")
    assert "'/risk-evidence-runtime'" in routes
    assert "RiskEvidenceDetailRuntimePage" in routes

    service = (app_dir / "src/services/riskEvidenceDetailRuntime.ts").read_text(encoding="utf-8")
    for keyword in ("RiskEvidenceDetailRuntime", "loadEvidenceDetail", "loadEvidenceFromRun", "loadReproductionSteps", "loadRemediationAdvice", "riskId", "evidenceId", "runId", "demo-fallback"):
        assert keyword in service

    page_text = "\n".join(
        (app_dir / relative).read_text(encoding="utf-8")
        for relative in (
            "src/pages/RiskEvidenceDetailRuntimePage.tsx",
            "src/components/EvidenceIdentityPanel.tsx",
            "src/components/EvidenceRequestResponsePanel.tsx",
            "src/components/EvidenceBusinessSnapshotPanel.tsx",
            "src/components/EvidenceReproductionSteps.tsx",
            "src/components/EvidenceRemediationPanel.tsx",
        )
    )
    for keyword in ("风险证据链真实详情", "riskId", "evidenceId", "runId", "请求摘要", "响应摘要", "业务状态快照", "复现步骤", "修复建议", "关闭条件", "证据可信度"):
        assert keyword in page_text

    manifest = json.loads((tmp_path / RISK_EVIDENCE_DETAIL_MANIFEST_JSON).read_text(encoding="utf-8"))
    assert manifest["version"] == PHASE106H_VERSION
    assert manifest["route"] == "/risk-evidence-runtime"
    assert len(manifest["runtime_endpoints"]) >= 4
    assert not verify_frontend_risk_evidence_detail_checksums(tmp_path)

    core_page_text = "\n".join(
        (app_dir / relative).read_text(encoding="utf-8")
        for relative in (
            "src/pages/CustomerIntakePage.tsx",
            "src/pages/EnvironmentDiagnosisPage.tsx",
            "src/pages/BusinessFlowMapPage.tsx",
            "src/pages/TestExecutionPage.tsx",
            "src/pages/RiskEvidencePage.tsx",
            "src/pages/ReportRoiPage.tsx",
        )
    )
    assert "JSON.stringify(" not in core_page_text
    assert "<pre>" not in core_page_text


def test_phase106h_validate_only_detects_missing_risk_evidence_route(tmp_path: Path) -> None:
    build_frontend_risk_evidence_detail_runtime(tmp_path, scenario="ecommerce")
    routes = tmp_path / FRONTEND_APP_DIR / "src/routes.ts"
    routes.write_text(routes.read_text(encoding="utf-8").replace("'/risk-evidence-runtime'", "'/risk-evidence-missing'"), encoding="utf-8")

    report = validate_frontend_risk_evidence_detail_runtime(tmp_path, scenario="ecommerce")

    assert not report.passed
    failed_keys = {check.key for check in report.checks if not check.passed}
    assert "risk_evidence_detail_route" in failed_keys
    assert "checksums" in failed_keys


def test_phase106h_secret_scan_and_validate_only_failure(tmp_path: Path) -> None:
    build_frontend_risk_evidence_detail_runtime(tmp_path, scenario="manufacturing")
    readme = tmp_path / FRONTEND_APP_DIR / "README_FRONTEND_RISK_EVIDENCE_DETAIL_RUNTIME.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nclient_secret=raw\n", encoding="utf-8")

    leaks = scan_frontend_risk_evidence_detail_for_secret_leaks(tmp_path)
    assert any("client_secret=" in leak for leak in leaks)
    assert verify_frontend_risk_evidence_detail_checksums(tmp_path)

    shutil.rmtree(tmp_path / FRONTEND_APP_DIR)
    report = run_frontend_risk_evidence_detail_runtime_export(tmp_path, scenario="manufacturing", validate_only=True)
    assert not report.passed
