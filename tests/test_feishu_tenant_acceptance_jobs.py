from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import ai_test_asset_center.feishu_tenant_acceptance_jobs as jobs
from ai_test_asset_center.feishu_tenant_acceptance_jobs import (
    FeishuTenantAcceptanceJobError,
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


def _report(root: Path, ready: bool = True):
    return {
        "verdict": "PASS" if ready else "FAIL",
        "acceptance_ready": ready,
        "report_path": (
            "platform_workspace/enterprise-project/enterprise_knowledge_center/"
            "connector_acceptance_reports/feishu-prod/"
            "20260801T100100Z_aaaaaaaaaaaa.json"
        ),
        "content": "CUSTOMER-CONTENT-MUST-NOT-BE-IN-JOB",
        "next_cursor": "RAW-CURSOR-MUST-NOT-BE-IN-JOB",
        "tenant_access_token": "TOKEN-MUST-NOT-BE-IN-JOB",
    }


def _run_inline(target, name):
    target()
    return None


def test_inline_job_completes_and_public_status_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(jobs, "list_connector_instances", _registered)

    status = start_feishu_tenant_acceptance_job(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        profile="pilot",
        actor={"name": "operator", "role": "knowledge_admin"},
        options={
            "runs": 2,
            "tenant_access_token": "MUST-NOT-PERSIST",
            "arbitrary": "MUST-NOT-PERSIST",
        },
        runner=lambda *args, **kwargs: _report(tmp_path),
        thread_starter=_run_inline,
    )

    assert status["status"] == "COMPLETE"
    assert status["acceptance_ready"] is True
    assert status["report_id"] == "20260801T100100Z_aaaaaaaaaaaa"
    assert status["governance"]["background_execution"] is True
    encoded = json.dumps(status, ensure_ascii=False)
    assert "CUSTOMER-CONTENT" not in encoded
    assert "RAW-CURSOR" not in encoded
    assert "TOKEN-MUST" not in encoded
    assert "report_path" not in encoded
    assert "owner_pid" not in encoded
    assert "process_token" not in encoded

    persisted = "\n".join(
        path.read_text(encoding="utf-8")
        for path in tmp_path.rglob("*.json")
        if "connector_acceptance_jobs" in str(path)
    )
    assert "CUSTOMER-CONTENT" not in persisted
    assert "RAW-CURSOR" not in persisted
    assert "MUST-NOT-PERSIST" not in persisted


def test_duplicate_job_is_blocked_while_worker_is_running(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(jobs, "list_connector_instances", _registered)
    entered = threading.Event()
    release = threading.Event()

    def runner(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return _report(tmp_path)

    first = start_feishu_tenant_acceptance_job(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
        runner=runner,
    )
    assert first["status"] in {"PENDING", "RUNNING"}
    assert entered.wait(5)

    with pytest.raises(
        FeishuTenantAcceptanceJobError,
        match="acceptance_job_already_running",
    ):
        start_feishu_tenant_acceptance_job(
            PROJECT,
            CONNECTOR,
            root=tmp_path,
            runner=runner,
        )

    release.set()
    thread = jobs._THREADS.get(first["job_id"])
    if thread is not None:
        thread.join(timeout=5)
    completed = get_feishu_tenant_acceptance_job(
        PROJECT,
        CONNECTOR,
        first["job_id"],
        root=tmp_path,
    )
    assert completed["status"] == "COMPLETE"


def test_current_job_recovers_disappeared_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(jobs, "list_connector_instances", _registered)
    job_id = "ftaj_aaaaaaaaaaaaaaaaaaaaaaaa"
    payload = {
        "schema": jobs.FEISHU_TENANT_ACCEPTANCE_JOB_SCHEMA,
        "job_id": job_id,
        "project_id": PROJECT,
        "connector_instance_id": CONNECTOR,
        "profile": "pilot",
        "status": "RUNNING",
        "requested_at_utc": "2026-08-01T10:00:00Z",
        "started_at_utc": "2026-08-01T10:00:01Z",
        "completed_at_utc": "",
        "updated_at_utc": "2026-08-01T10:00:01Z",
        "report_id": "",
        "verdict": "",
        "acceptance_ready": False,
        "error_type": "",
        "owner_pid": 99999999,
        "owner_process_marker": "missing",
        "process_token": "other-process",
        "actor": {"name": "operator", "role": "knowledge_admin"},
        "options": {},
        "governance": {},
    }
    jobs._persist(tmp_path, PROJECT, CONNECTOR, payload)
    lock = jobs._lock_path(tmp_path, PROJECT, CONNECTOR)
    jobs._write_lock(lock, {"job_id": job_id, "owner_pid": 99999999})

    recovered = get_current_feishu_tenant_acceptance_job(
        PROJECT,
        CONNECTOR,
        root=tmp_path,
    )

    assert recovered["status"] == "INTERRUPTED"
    assert recovered["terminal"] is True
    assert recovered["acceptance_ready"] is False
    assert recovered["error_type"] == "ACCEPTANCE_OWNER_DISAPPEARED"
    assert not lock.exists()


def test_unknown_or_inactive_connector_cannot_start_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        jobs,
        "list_connector_instances",
        lambda *args, **kwargs: {"connector_instances": []},
    )
    with pytest.raises(
        FeishuTenantAcceptanceJobError,
        match="connector_not_registered",
    ):
        start_feishu_tenant_acceptance_job(
            PROJECT,
            CONNECTOR,
            root=tmp_path,
            runner=lambda *args, **kwargs: _report(tmp_path),
            thread_starter=_run_inline,
        )
