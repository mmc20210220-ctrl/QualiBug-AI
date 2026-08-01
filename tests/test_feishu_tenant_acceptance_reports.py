from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.feishu_tenant_acceptance import (
    FEISHU_TENANT_ACCEPTANCE_SCHEMA,
)
from ai_test_asset_center.feishu_tenant_acceptance_reports import (
    FeishuTenantAcceptanceReportError,
    latest_feishu_tenant_acceptance_summary,
    list_feishu_tenant_acceptance_reports,
    load_feishu_tenant_acceptance_report,
)

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"


def _write_report(
    root: Path,
    report_id: str,
    *,
    verdict: str = "PASS",
    ready: bool = True,
) -> Path:
    directory = (
        root
        / "platform_workspace"
        / PROJECT
        / "enterprise_knowledge_center"
        / "connector_acceptance_reports"
        / CONNECTOR
    )
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{report_id}.json"
    payload = {
        "schema": FEISHU_TENANT_ACCEPTANCE_SCHEMA,
        "acceptance_id": f"fta_{report_id}",
        "project_id": PROJECT,
        "connector_instance_id": CONNECTOR,
        "profile": "pilot",
        "verdict": verdict,
        "acceptance_ready": ready,
        "started_at_utc": "2026-08-01T10:00:00Z",
        "completed_at_utc": "2026-08-01T10:01:00Z",
        "thresholds": {
            "runs": 2,
            "min_discovered_resources": 20,
            "min_coverage_ratio": 0.95,
            "max_unsupported_ratio": 0.05,
            "max_run_duration_seconds": 900.0,
        },
        "connection": {
            "status": "AVAILABLE",
            "connector_type": "feishu",
            "auth_mode": "internal_app",
            "space_count": 2,
            "network_side_effect": "READ_ONLY",
            "credentials_persisted": False,
            "access_token_persisted": False,
            "app_secret": "MUST-NOT-RETURN",
        },
        "runs": [
            {
                "sync_epoch_id": "sync-1",
                "status": "COMPLETE",
                "duration_seconds": 12.5,
                "discovered_resource_count": 20,
                "covered_resource_count": 20,
                "materialized_resource_count": 20,
                "unchanged_resource_count": 0,
                "unsupported_resource_count": 0,
                "unknown_gap_count": 0,
                "failure_count": 0,
                "degraded_resource_count": 0,
                "export_avoided_count": 0,
                "knowledge_coverage_ratio": 1.0,
                "knowledge_coverage_status": "COMPLETE",
                "remote_discovery_complete": True,
                "supported_materialization_complete": True,
                "cursor_checkpoint_committed": True,
                "checkpoint_commit_protocol": "RECOVERABLE_TWO_STAGE",
                "customer_material_mutation_executed": False,
                "source_content_persisted_in_adapter_receipt": False,
                "next_cursor_fingerprint": "a" * 64,
                "next_cursor": "RAW-CURSOR-MUST-NOT-RETURN",
                "content": "CUSTOMER-CONTENT-MUST-NOT-RETURN",
                "run_receipt_path": "/private/server/path.json",
            }
        ],
        "checks": [
            {
                "check_id": "NO_CUSTOMER_MUTATION",
                "status": "PASS",
                "severity": "BLOCKER",
                "observed": {
                    "mutation": False,
                    "access_token": "MUST-NOT-RETURN",
                    "content": "MUST-NOT-RETURN",
                },
                "expected": False,
                "detail": "CUSTOMER-DIAGNOSTIC-MUST-NOT-RETURN",
            },
            {
                "check_id": "UNSTRUCTURED_VALUE",
                "status": "FAIL",
                "severity": "BLOCKER",
                "observed": "CUSTOMER-CONTRACT-CONTENT-MUST-NOT-RETURN",
                "expected": ">= 20",
                "detail": "CUSTOMER-ERROR-DETAIL-MUST-NOT-RETURN",
            },
        ],
        "summary": {
            "check_count": 2,
            "blocker_failure_count": 0,
            "executed_run_count": 2,
            "required_run_count": 2,
            "maximum_run_duration_seconds": 12.5,
            "minimum_coverage_ratio": 1.0,
            "maximum_discovered_resource_count": 20,
        },
        "execution_error": None,
        "governance": {
            "customer_material_access": "NON_MUTATING_READ_ONLY",
            "customer_material_mutation_executed": False,
            "deletion_policy": "RETAIN",
            "customer_source_content_in_report": False,
            "raw_cursor_values_in_report": False,
            "credential_values_in_report": False,
            "source_occurrence_content_loaded_by_acceptance": False,
            "existing_managed_sync_authority_reused": True,
        },
        "report_path": "/private/server/report.json",
        "tenant_access_token": "MUST-NOT-RETURN",
        "content": "CUSTOMER-CONTENT-MUST-NOT-RETURN",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_report_projection_is_allowlisted_and_redacted(tmp_path: Path) -> None:
    report_id = "20260801T100100Z_aaaaaaaaaaaa"
    _write_report(tmp_path, report_id)

    report = load_feishu_tenant_acceptance_report(
        PROJECT,
        CONNECTOR,
        report_id,
        root=tmp_path,
    )

    encoded = json.dumps(report, ensure_ascii=False)
    assert report["verdict"] == "PASS"
    assert report["acceptance_ready"] is True
    assert report["runs"][0]["next_cursor_fingerprint"] == "a" * 64
    assert report["runs"][0]["customer_material_mutation_executed"] is False
    assert report["checks"][0]["observed"] == {}
    assert report["checks"][0]["detail"] == ""
    assert report["checks"][1]["observed"] == "REDACTED_UNSTRUCTURED_VALUE"
    assert report["checks"][1]["expected"] == ">= 20"
    assert report["checks"][1]["detail"] == ""
    assert report["arbitrary_diagnostic_text_returned"] is False
    assert report["source_content_returned"] is False
    assert report["raw_cursor_returned"] is False
    assert report["credential_values_returned"] is False
    assert "RAW-CURSOR-MUST-NOT-RETURN" not in encoded
    assert "CUSTOMER-CONTENT-MUST-NOT-RETURN" not in encoded
    assert "CUSTOMER-DIAGNOSTIC-MUST-NOT-RETURN" not in encoded
    assert "CUSTOMER-CONTRACT-CONTENT-MUST-NOT-RETURN" not in encoded
    assert "CUSTOMER-ERROR-DETAIL-MUST-NOT-RETURN" not in encoded
    assert "MUST-NOT-RETURN" not in encoded
    assert "/private/server" not in encoded
    assert "next_cursor\"" not in encoded
    assert "run_receipt_path" not in encoded


def test_report_lookup_rejects_path_traversal(tmp_path: Path) -> None:
    with pytest.raises(
        FeishuTenantAcceptanceReportError,
        match="acceptance_report_id_invalid",
    ):
        load_feishu_tenant_acceptance_report(
            PROJECT,
            CONNECTOR,
            "../../outside",
            root=tmp_path,
        )


def test_report_inventory_is_newest_first_and_bounded(tmp_path: Path) -> None:
    _write_report(
        tmp_path,
        "20260801T100100Z_aaaaaaaaaaaa",
        verdict="FAIL",
        ready=False,
    )
    _write_report(
        tmp_path,
        "20260801T100200Z_bbbbbbbbbbbb",
        verdict="PASS",
        ready=True,
    )

    inventory = list_feishu_tenant_acceptance_reports(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        limit=1,
    )

    assert inventory["summary"]["report_count"] == 1
    assert inventory["reports"][0]["report_id"] == (
        "20260801T100200Z_bbbbbbbbbbbb"
    )
    assert inventory["reports"][0]["acceptance_ready"] is True
    assert inventory["governance"]["arbitrary_diagnostic_text_returned"] is False
    encoded = json.dumps(inventory, ensure_ascii=False)
    assert "CUSTOMER-CONTENT-MUST-NOT-RETURN" not in encoded
    assert "CUSTOMER-ERROR-DETAIL-MUST-NOT-RETURN" not in encoded
    assert "/private/server" not in encoded


def test_latest_summary_distinguishes_not_run_pass_and_fail(tmp_path: Path) -> None:
    empty = latest_feishu_tenant_acceptance_summary(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    )
    assert empty == {
        "status": "NOT_RUN",
        "acceptance_ready": False,
        "latest_report": None,
    }

    _write_report(
        tmp_path,
        "20260801T100100Z_aaaaaaaaaaaa",
        verdict="FAIL",
        ready=False,
    )
    failed = latest_feishu_tenant_acceptance_summary(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    )
    assert failed["status"] == "FAIL"
    assert failed["acceptance_ready"] is False

    _write_report(
        tmp_path,
        "20260801T100200Z_bbbbbbbbbbbb",
        verdict="PASS",
        ready=True,
    )
    passed = latest_feishu_tenant_acceptance_summary(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    )
    assert passed["status"] == "PASS"
    assert passed["acceptance_ready"] is True
