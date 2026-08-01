from __future__ import annotations

import json
from pathlib import Path

import pytest

import ai_test_asset_center.private_pilot_connector_handlers as handlers
from ai_test_asset_center.private_pilot_connector_handlers import (
    KnowledgeConnectorHandlersMixin,
)

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"
JOB_ID = "ftaj_aaaaaaaaaaaaaaaaaaaaaaaa"


class DummyHandler(KnowledgeConnectorHandlersMixin):
    def _json(self, payload, status=200):
        return {"status": status, "payload": payload}


def _public_report(*, ready: bool = True):
    return {
        "schema": "qualibug.feishu-tenant-acceptance.v1",
        "report_id": "20260801T100100Z_aaaaaaaaaaaa",
        "acceptance_id": "fta_123",
        "project_id": PROJECT,
        "connector_instance_id": CONNECTOR,
        "profile": "pilot",
        "verdict": "PASS" if ready else "FAIL",
        "acceptance_ready": ready,
        "started_at_utc": "2026-08-01T10:00:00Z",
        "completed_at_utc": "2026-08-01T10:01:00Z",
        "summary": {
            "blocker_failure_count": 0 if ready else 1,
            "executed_run_count": 2,
            "required_run_count": 2,
            "minimum_coverage_ratio": 1.0 if ready else 0.9,
        },
        "runs": [],
        "checks": [],
        "source_content_returned": False,
        "raw_cursor_returned": False,
        "credential_values_returned": False,
        "filesystem_path_returned": False,
    }


def _public_job(status: str = "RUNNING"):
    return {
        "schema": "qualibug.feishu-tenant-acceptance-job.v1",
        "job_id": JOB_ID,
        "project_id": PROJECT,
        "connector_instance_id": CONNECTOR,
        "profile": "pilot",
        "status": status,
        "requested_at_utc": "2026-08-01T10:00:00Z",
        "started_at_utc": "2026-08-01T10:00:01Z",
        "completed_at_utc": "" if status == "RUNNING" else "2026-08-01T10:01:00Z",
        "report_id": "" if status == "RUNNING" else "20260801T100100Z_aaaaaaaaaaaa",
        "verdict": "" if status == "RUNNING" else "PASS",
        "acceptance_ready": status == "COMPLETE",
        "error_type": "",
        "terminal": status in {"COMPLETE", "FAILED", "INTERRUPTED"},
        "governance": {
            "source_content_returned": False,
            "raw_cursor_returned": False,
            "credential_values_returned": False,
            "filesystem_path_returned": False,
            "background_execution": True,
        },
    }


def test_get_acceptance_report_inventory_uses_safe_projection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        handlers,
        "_connector_inventory",
        lambda project, root: {"connectors": []},
    )
    expected = {
        "schema": "qualibug.feishu-tenant-acceptance-report-inventory.v1",
        "project_id": PROJECT,
        "connector_instance_id": CONNECTOR,
        "reports": [_public_report()],
        "governance": {
            "source_content_returned": False,
            "raw_cursor_returned": False,
            "credential_values_returned": False,
        },
    }
    monkeypatch.setattr(
        handlers,
        "list_feishu_tenant_acceptance_reports",
        lambda project, connector, root, limit: expected,
    )

    response = DummyHandler()._handle_knowledge_connector_get(
        PROJECT,
        [CONNECTOR, "acceptance-reports"],
        tmp_path,
    )

    assert response["status"] == 200
    assert response["payload"] == {"ok": True, "data": expected}
    encoded = json.dumps(response, ensure_ascii=False)
    assert "next_cursor" not in encoded
    assert "report_path" not in encoded
    assert "access_token" not in encoded


def test_get_acceptance_report_detail_never_returns_private_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        handlers,
        "_connector_inventory",
        lambda project, root: {"connectors": []},
    )
    report = _public_report()
    monkeypatch.setattr(
        handlers,
        "load_feishu_tenant_acceptance_report",
        lambda project, connector, report_id, root: report,
    )

    response = DummyHandler()._handle_knowledge_connector_get(
        PROJECT,
        [CONNECTOR, "acceptance-reports", report["report_id"]],
        tmp_path,
    )

    assert response["status"] == 200
    assert response["payload"]["data"]["acceptance_ready"] is True
    encoded = json.dumps(response, ensure_ascii=False)
    assert "filesystem_path" in encoded
    assert "/private/" not in encoded
    assert "report_path" not in encoded


def test_acceptance_action_starts_background_job_and_returns_202(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured = {}

    def start(project, connector, **kwargs):
        captured.update(kwargs)
        return _public_job("RUNNING")

    monkeypatch.setattr(handlers, "start_feishu_tenant_acceptance_job", start)

    response = DummyHandler()._handle_knowledge_connector_action(
        PROJECT,
        CONNECTOR,
        "acceptance",
        {"profile": "pilot"},
        tmp_path,
        {"name": "operator", "role": "knowledge_admin"},
    )

    assert response["status"] == 202
    assert response["payload"]["ok"] is True
    assert response["payload"]["data"]["status"] == "RUNNING"
    assert response["payload"]["data"]["job_id"] == JOB_ID
    assert captured["profile"] == "pilot"
    assert captured["actor"]["name"] == "operator"
    assert captured["options"]["allow_raw_text_fallback"] is False
    encoded = json.dumps(response, ensure_ascii=False)
    assert "report_path" not in encoded
    assert "next_cursor" not in encoded
    assert "access_token" not in encoded


def test_get_current_and_specific_acceptance_jobs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        handlers,
        "_connector_inventory",
        lambda project, root: {"connectors": []},
    )
    monkeypatch.setattr(
        handlers,
        "get_current_feishu_tenant_acceptance_job",
        lambda project, connector, root: _public_job("RUNNING"),
    )
    monkeypatch.setattr(
        handlers,
        "get_feishu_tenant_acceptance_job",
        lambda project, connector, job_id, root: _public_job("COMPLETE"),
    )

    current = DummyHandler()._handle_knowledge_connector_get(
        PROJECT,
        [CONNECTOR, "acceptance-jobs", "current"],
        tmp_path,
    )
    completed = DummyHandler()._handle_knowledge_connector_get(
        PROJECT,
        [CONNECTOR, "acceptance-jobs", JOB_ID],
        tmp_path,
    )

    assert current["payload"]["data"]["status"] == "RUNNING"
    assert completed["payload"]["data"]["status"] == "COMPLETE"
    assert completed["payload"]["data"]["report_id"]
    encoded = json.dumps({"current": current, "completed": completed})
    assert "report_path" not in encoded
    assert "owner_pid" not in encoded
    assert "process_token" not in encoded


def test_connector_inventory_projects_latest_acceptance_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        handlers,
        "list_connector_instances",
        lambda *args, **kwargs: {
            "connector_instances": [
                {
                    "connector_instance_id": CONNECTOR,
                    "connector_type": "feishu",
                    "status": "ACTIVE",
                    "resource_scope": "wiki-all-accessible",
                }
            ],
            "summary": {"connector_instance_count": 1},
            "governance": {},
        },
    )
    monkeypatch.setattr(
        handlers,
        "_profile_index",
        lambda project, root: {
            CONNECTOR: {
                "connector_instance_id": CONNECTOR,
                "credentials_configured": True,
            }
        },
    )
    monkeypatch.setattr(
        handlers,
        "connector_auto_sync_status",
        lambda *args, **kwargs: {"enabled": True, "state": "idle"},
    )
    monkeypatch.setattr(
        handlers,
        "_coverage_projection",
        lambda *args, **kwargs: {"status": "COMPLETE"},
    )
    monkeypatch.setattr(
        handlers,
        "latest_feishu_tenant_acceptance_summary",
        lambda *args, **kwargs: {
            "status": "PASS",
            "acceptance_ready": True,
            "latest_report": _public_report(),
        },
    )

    inventory = handlers._connector_inventory(PROJECT, tmp_path)

    connector = inventory["connectors"][0]
    assert connector["acceptance"]["status"] == "PASS"
    assert connector["acceptance"]["acceptance_ready"] is True
    assert inventory["summary"]["acceptance_ready_connector_count"] == 1
    assert inventory["summary"]["acceptance_not_run_connector_count"] == 0
    assert inventory["governance"][
        "acceptance_projection_uses_allowlisted_report_fields"
    ] is True
    assert inventory["governance"][
        "acceptance_runs_as_persistent_background_job"
    ] is True
