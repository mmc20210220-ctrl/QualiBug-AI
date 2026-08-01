from __future__ import annotations

import json
from pathlib import Path

import pytest

import ai_test_asset_center.feishu_tenant_acceptance_jobs as jobs
import ai_test_asset_center.private_pilot_connector_handlers as handlers
from ai_test_asset_center.feishu_tenant_acceptance import (
    CONNECTOR_TENANT_ACCEPTANCE_SCHEMA,
    run_connector_tenant_acceptance,
)
from ai_test_asset_center.feishu_tenant_acceptance_jobs import (
    CONNECTOR_TENANT_ACCEPTANCE_JOB_SCHEMA,
    start_connector_tenant_acceptance_job,
)
from ai_test_asset_center.feishu_tenant_acceptance_reports import (
    CONNECTOR_ACCEPTANCE_REPORT_INVENTORY_SCHEMA,
    list_connector_tenant_acceptance_reports,
    load_connector_tenant_acceptance_report,
)


PROJECT = "enterprise-project"
CONNECTOR = "gitlab-prod"


class _Handler(handlers.KnowledgeConnectorHandlersMixin):
    def _json(self, payload, status=200):
        return {"status": status, "payload": payload}


def _connection() -> dict[str, object]:
    return {
        "status": "AVAILABLE",
        "connector_type": "gitlab",
        "auth_mode": "token",
        "network_side_effect": "READ_ONLY_GET",
        "credentials_persisted": False,
        "access_token_persisted": False,
    }


def _sync_factory():
    count = 0

    def run(*args, **kwargs):
        nonlocal count
        count += 1
        stable = count > 1
        return {
            "sync_epoch_id": "epoch-without-customer-data",
            "status": "COMPLETE",
            "discovered_resource_count": 2,
            "covered_resource_count": 2,
            "materialized_resource_count": 0 if stable else 2,
            "unchanged_resource_count": 2 if stable else 0,
            "unsupported_resource_count": 0,
            "unknown_gap_count": 0,
            "failure_count": 0,
            "degraded_resource_count": 0,
            "export_avoided_count": 2 if stable else 0,
            "knowledge_coverage_ratio": 1.0,
            "knowledge_coverage_status": "COMPLETE",
            "remote_discovery_complete": True,
            "supported_materialization_complete": True,
            "cursor_checkpoint_committed": True,
            "checkpoint_commit_protocol": "RECOVERABLE_TWO_STAGE",
            "customer_material_mutation_executed": False,
            "source_content_persisted_in_adapter_receipt": False,
            "next_cursor": "secret-cursor-value",
            "run_receipt_path": "private/secret-receipt.json",
            "content": "CUSTOMER-SOURCE-CONTENT",
        }

    return run


def test_generic_acceptance_uses_registry_safe_network_contract(
    tmp_path: Path,
) -> None:
    report = run_connector_tenant_acceptance(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        profile="smoke",
        connection_tester=lambda *args, **kwargs: _connection(),
        sync_runner=_sync_factory(),
        sleeper=lambda _: None,
    )

    assert report["schema"] == CONNECTOR_TENANT_ACCEPTANCE_SCHEMA
    assert report["acceptance_id"].startswith("cta_")
    assert report["verdict"] == "PASS"
    assert report["checks"][-1]["status"] == "PASS"
    persisted = (tmp_path / report["report_path"]).read_text(encoding="utf-8")
    assert "secret-cursor-value" not in persisted
    assert "private/secret-receipt.json" not in persisted
    assert "CUSTOMER-SOURCE-CONTENT" not in persisted


def test_generic_acceptance_job_supports_non_feishu_connector(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        jobs,
        "list_connector_instances",
        lambda *args, **kwargs: {
            "connector_instances": [
                {
                    "connector_instance_id": CONNECTOR,
                    "connector_type": "gitlab",
                    "status": "ACTIVE",
                }
            ]
        },
    )

    result = start_connector_tenant_acceptance_job(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        runner=lambda *args, **kwargs: {
            "verdict": "PASS",
            "acceptance_ready": True,
            "report_path": (
                "connector_acceptance_reports/gitlab-prod/"
                "20260801T100100Z_aaaaaaaaaaaa.json"
            ),
        },
        thread_starter=lambda target, name: (target(), None)[1],
    )

    assert result["schema"] == CONNECTOR_TENANT_ACCEPTANCE_JOB_SCHEMA
    assert result["job_id"].startswith("ctaj_")
    assert result["status"] == "COMPLETE"


def test_generic_report_projection_has_generic_schema_and_no_raw_content(
    tmp_path: Path,
) -> None:
    report = run_connector_tenant_acceptance(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        profile="smoke",
        connection_tester=lambda *args, **kwargs: _connection(),
        sync_runner=_sync_factory(),
        sleeper=lambda _: None,
    )
    report_id = Path(report["report_path"]).stem

    inventory = list_connector_tenant_acceptance_reports(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    )
    projected = load_connector_tenant_acceptance_report(
        PROJECT,
        CONNECTOR,
        report_id,
        root=tmp_path,
    )

    assert inventory["schema"] == CONNECTOR_ACCEPTANCE_REPORT_INVENTORY_SCHEMA
    assert inventory["reports"][0]["report_id"] == report_id
    assert projected["schema"] == CONNECTOR_TENANT_ACCEPTANCE_SCHEMA
    encoded = json.dumps(projected, ensure_ascii=False)
    assert "secret-cursor-value" not in encoded
    assert "private/secret-receipt.json" not in encoded
    assert "CUSTOMER-SOURCE-CONTENT" not in encoded


def test_inventory_dispatches_registered_non_feishu_connector_to_generic_summary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: list[str] = []
    monkeypatch.setattr(
        handlers,
        "list_connector_instances",
        lambda *args, **kwargs: {
            "connector_instances": [
                {
                    "connector_instance_id": CONNECTOR,
                    "connector_type": "gitlab",
                    "status": "ACTIVE",
                    "resource_scope": "repository",
                }
            ],
            "summary": {"connector_instance_count": 1},
            "governance": {},
        },
    )
    monkeypatch.setattr(handlers, "_profile_index", lambda *args: {})
    monkeypatch.setattr(
        handlers,
        "connector_auto_sync_status",
        lambda *args, **kwargs: {"enabled": False, "state": "idle"},
    )
    monkeypatch.setattr(
        handlers,
        "_coverage_projection",
        lambda *args, **kwargs: {"status": "NOT_AVAILABLE"},
    )

    def generic_summary(*args, **kwargs):
        observed.append("generic")
        return {"status": "NOT_RUN", "acceptance_ready": False}

    monkeypatch.setattr(
        handlers,
        "latest_connector_tenant_acceptance_summary",
        generic_summary,
    )
    inventory = handlers._connector_inventory(PROJECT, tmp_path)

    assert observed == ["generic"]
    assert inventory["connectors"][0]["acceptance"]["status"] == "NOT_RUN"


def test_acceptance_action_dispatches_registered_non_feishu_connector(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    observed: list[str] = []
    monkeypatch.setattr(
        handlers,
        "list_connector_instances",
        lambda *args, **kwargs: {
            "connector_instances": [
                {
                    "connector_instance_id": CONNECTOR,
                    "connector_type": "gitlab",
                    "status": "ACTIVE",
                }
            ]
        },
    )

    def generic_start(*args, **kwargs):
        observed.append("generic")
        return {"status": "PENDING", "acceptance_ready": False}

    monkeypatch.setattr(
        handlers,
        "start_connector_tenant_acceptance_job",
        generic_start,
    )
    response = _Handler()._handle_knowledge_connector_action(
        PROJECT,
        CONNECTOR,
        "acceptance",
        {"profile": "smoke"},
        tmp_path,
        {"name": "operator", "role": "knowledge_admin"},
    )

    assert observed == ["generic"]
    assert response["status"] == 202
    assert response["payload"]["data"]["status"] == "PENDING"
