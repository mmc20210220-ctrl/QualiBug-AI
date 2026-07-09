import json
from pathlib import Path

from ai_test_asset_center.private_pilot_regression_suite_refresh_patch import (
    refresh_regression_suite_after_scan,
)


def test_refresh_regression_suite_after_scan_uses_confirmed_ledger(tmp_path: Path) -> None:
    project = "demo_project"
    workspace = tmp_path / "platform_workspace" / project / "defect_discovery"
    workspace.mkdir(parents=True)
    (workspace / "confirmed_findings.json").write_text(
        json.dumps({
            "evidence_001": {
                "id": "BUG-001",
                "title": "普通用户可读取其他租户订单",
                "severity": "P1",
                "risk_type": "tenant_isolation",
                "reproduction": {"method": "GET", "path": "/api/orders/other-tenant-order"},
            }
        }),
        encoding="utf-8",
    )

    result = {
        "success": True,
        "scan_id": "scan_demo_1",
        "project": project,
        "execution_status": "completed",
        "findings": [],
    }

    enriched = refresh_regression_suite_after_scan(result, project=project, root=tmp_path)

    assert enriched["regression_suite_refresh"]["status"] == "refreshed"
    assert enriched["regression_suite_refresh"]["summary"]["confirmed_ledger_probe_count"] == 1
    assert enriched["regression_suite"]["release_count"] == 1
    assert enriched["regression_suite"]["ci_gate_recommendation"] == "block_on_p0_p1_regression_failure"
    assert (tmp_path / "platform_outputs" / project / "regression_suite" / "regression_suite.json").exists()
    persisted = json.loads((tmp_path / "platform_outputs" / project / "scan_result.json").read_text(encoding="utf-8"))
    assert persisted["regression_suite_refresh"]["status"] == "refreshed"


def test_refresh_regression_suite_after_scan_skips_without_confirmed_sources(tmp_path: Path) -> None:
    result = {
        "success": True,
        "scan_id": "scan_demo_2",
        "project": "empty_project",
        "execution_status": "completed",
        "findings": [],
    }

    enriched = refresh_regression_suite_after_scan(result, project="empty_project", root=tmp_path)

    assert enriched["regression_suite_refresh"]["status"] == "skipped"
    assert enriched["regression_suite_refresh"]["reason"] == "no_confirmed_findings_or_confirmed_ledger"
    assert "regression_suite" not in enriched
