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


def test_acceptance_action_returns_failed_verdict_as_completed_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured = {}

    def run(project, connector, **kwargs):
        captured.update(kwargs)
        return {
            "verdict": "FAIL",
            "acceptance_ready": False,
            "report_path": (
                "platform_workspace/enterprise-project/enterprise_knowledge_center/"
                "connector_acceptance_reports/feishu-prod/"
                "20260801T100100Z_aaaaaaaaaaaa.json"
            ),
            "content": "MUST-NOT-RETURN",
            "next_cursor": "MUST-NOT-RETURN",
        }

    monkeypatch.setattr(handlers, "run_feishu_tenant_acceptance", run)
    monkeypatch.setattr(
        handlers,
        "load_feishu_tenant_acceptance_report",
        lambda project, connector, report_id, root: _public_report(ready=False),
    )

    response = DummyHandler()._handle_knowledge_connector_action(
        PROJECT,
        CONNECTOR,
        "acceptance",
        {"profile": "pilot"},
        tmp_path,
        {"name": "operator", "role": "knowledge_admin"},
    )

    assert response["status"] == 200
    assert response["payload"]["ok"] is True
    assert response["payload"]["accepted"] is False
    assert response["payload"]["data"]["verdict"] == "FAIL"
    assert captured["profile"] == "pilot"
    assert captured["actor"]["name"] == "operator"
    encoded = json.dumps(response, ensure_ascii=False)
    assert "MUST-NOT-RETURN" not in encoded
    assert "report_path" not in encoded
    assert "next_cursor" not in encoded


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
