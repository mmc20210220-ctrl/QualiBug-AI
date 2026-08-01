from __future__ import annotations

from pathlib import Path

import pytest

import ai_test_asset_center.feishu_tenant_acceptance_jobs as jobs
from ai_test_asset_center.feishu_tenant_acceptance_jobs import (
    get_current_feishu_tenant_acceptance_job,
    get_feishu_tenant_acceptance_job,
    start_feishu_tenant_acceptance_job,
)

PROJECT = "enterprise-project"
CONNECTOR = "feishu-prod"


def _registered(*args, **kwargs):
    return {
        "connector_instances": [
            {
                "connector_instance_id": CONNECTOR,
                "connector_type": "feishu",
                "status": "ACTIVE",
            }
        ]
    }


def _run_inline(target, name):
    target()
    return None


def test_missing_report_identity_marks_job_failed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(jobs, "list_connector_instances", _registered)

    result = start_feishu_tenant_acceptance_job(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        runner=lambda *args, **kwargs: {
            "verdict": "PASS",
            "acceptance_ready": True,
            "report_path": "",
        },
        thread_starter=_run_inline,
    )

    assert result["status"] == "FAILED"
    assert result["acceptance_ready"] is False
    assert result["report_id"] == ""
    assert result["error_type"] == "FeishuTenantAcceptanceJobError"


def test_verdict_and_readiness_must_be_consistent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(jobs, "list_connector_instances", _registered)

    result = start_feishu_tenant_acceptance_job(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        runner=lambda *args, **kwargs: {
            "verdict": "PASS",
            "acceptance_ready": False,
            "report_path": (
                "connector_acceptance_reports/feishu-prod/"
                "20260801T100100Z_aaaaaaaaaaaa.json"
            ),
        },
        thread_starter=_run_inline,
    )

    assert result["status"] == "FAILED"
    assert result["acceptance_ready"] is False
    assert result["report_id"] == ""
    assert result["error_type"] == "FeishuTenantAcceptanceJobError"


def test_recovering_historical_job_does_not_replace_current_pointer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(jobs, "list_connector_instances", _registered)
    historical_id = "ftaj_aaaaaaaaaaaaaaaaaaaaaaaa"
    current_id = "ftaj_bbbbbbbbbbbbbbbbbbbbbbbb"
    historical = {
        "schema": jobs.FEISHU_TENANT_ACCEPTANCE_JOB_SCHEMA,
        "job_id": historical_id,
        "project_id": PROJECT,
        "connector_instance_id": CONNECTOR,
        "profile": "pilot",
        "status": "RUNNING",
        "requested_at_utc": "2026-08-01T10:00:00Z",
        "started_at_utc": "2026-08-01T10:00:01Z",
        "completed_at_utc": "",
        "updated_at_utc": "2026-08-01T10:00:01Z",
        "updated_unix": 1.0,
        "report_id": "",
        "verdict": "",
        "acceptance_ready": False,
        "error_type": "",
        "owner_pid": 99999999,
        "owner_process_marker": "missing",
        "owner_native_thread_id": 99999999,
        "process_token": "other-process",
        "actor": {},
        "options": {},
        "governance": {},
    }
    current = {
        **historical,
        "job_id": current_id,
        "status": "COMPLETE",
        "completed_at_utc": "2026-08-01T10:05:00Z",
        "report_id": "20260801T100500Z_bbbbbbbbbbbb",
        "verdict": "PASS",
        "acceptance_ready": True,
        "owner_pid": 0,
        "owner_native_thread_id": 0,
    }
    jobs._write_json_object_atomic(
        jobs._history_path(tmp_path, PROJECT, CONNECTOR, historical_id),
        historical,
    )
    jobs._persist(tmp_path, PROJECT, CONNECTOR, current)

    recovered = get_feishu_tenant_acceptance_job(
        PROJECT,
        CONNECTOR,
        historical_id,
        root=tmp_path,
    )
    still_current = get_current_feishu_tenant_acceptance_job(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    )

    assert recovered["status"] == "INTERRUPTED"
    assert still_current["job_id"] == current_id
    assert still_current["status"] == "COMPLETE"
    assert still_current["acceptance_ready"] is True


def test_cross_process_owner_requires_live_worker_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "job_id": "ftaj_cccccccccccccccccccccccc",
        "owner_pid": 1234,
        "owner_process_marker": "marker",
        "owner_native_thread_id": 5678,
        "process_token": "other-process",
        "updated_unix": jobs.time.time(),
    }
    monkeypatch.setattr(jobs, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(jobs, "_process_marker", lambda pid: "marker")
    monkeypatch.setattr(jobs, "_native_thread_alive", lambda pid, tid: False)
    assert jobs._owner_alive(payload) is False

    monkeypatch.setattr(jobs, "_native_thread_alive", lambda pid, tid: True)
    assert jobs._owner_alive(payload) is True
