import json
from pathlib import Path

from ai_test_asset_center.regression_suite_builder import (
    _load_confirmed_findings_regression_probes,
    build_regression_suite,
)


def test_confirmed_findings_become_regression_probes(tmp_path: Path) -> None:
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
                "confidence_score": 0.93,
                "reproduction": {
                    "method": "GET",
                    "path": "/api/orders/other-tenant-order",
                    "actor": "buyer_a",
                },
                "evidence_quality": {"score": 92, "evidence_strength": "runtime"},
                "current_campaign_scope": {
                    "campaign_id": "campaign_1",
                    "scope_id": "scope_1",
                    "environment_ref": "staging",
                },
            },
            "evidence_without_path": {
                "title": "没有可复现路径的缺陷不会生成自动回归探针",
                "reproduction": {},
            },
        }),
        encoding="utf-8",
    )

    probes = _load_confirmed_findings_regression_probes(project, tmp_path)

    assert len(probes) == 1
    assert probes[0]["regression_probe_id"] == "CONFIRMED_REG_evidence_001"
    assert probes[0]["issue_id"] == "BUG-001"
    assert probes[0]["source"] == "confirmed_findings_ledger"
    assert probes[0]["verification_badge"] == "confirmed_finding_regression"
    assert probes[0]["path"] == "/api/orders/other-tenant-order"
    assert probes[0]["current_campaign_scope"]["campaign_id"] == "campaign_1"


def test_build_regression_suite_includes_confirmed_findings_ledger(tmp_path: Path) -> None:
    project = "demo_project"
    workspace = tmp_path / "platform_workspace" / project / "defect_discovery"
    workspace.mkdir(parents=True)
    (workspace / "confirmed_findings.json").write_text(
        json.dumps({
            "evidence_002": {
                "id": "BUG-002",
                "title": "重复提交导致库存扣减两次",
                "severity": "P0",
                "risk_type": "idempotency",
                "reproduction": {"method": "POST", "path": "/api/orders/submit", "request_body": {"sku": "SKU-1", "qty": 1}},
            }
        }),
        encoding="utf-8",
    )

    suite = build_regression_suite(project, root=tmp_path, options={"allow_destructive_regression": True})

    assert suite["summary"]["confirmed_ledger_probe_count"] == 1
    assert suite["summary"]["source_distribution"]["confirmed_findings_ledger"] == 1
    release_items = suite["modes"]["release"]["items"]
    assert release_items[0]["regression_probe_id"] == "CONFIRMED_REG_evidence_002"
    assert release_items[0]["source"] == "confirmed_findings_ledger"
    assert release_items[0]["request_body"] == {"sku": "SKU-1", "qty": 1}
    assert suite["ci_gate"]["recommendation"] == "block_on_p0_p1_regression_failure"
