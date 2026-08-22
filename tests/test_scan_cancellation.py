import sys
from pathlib import Path
from unittest import mock

import pytest

import ai_test_asset_center.scan_cancellation as sc

PROJECT = "test-proj-scan-cancel-001"


def _lease_dir(root: Path, project: str) -> Path:
    return sc._cancel_path(root, project).parent


def test_no_active_scan_returns_no_active_scan_and_never_creates_lease_dir(tmp_path):
    # platform_workspace/<safe> exists, but the live lease dir must NOT be created
    # by request_scan_cancel (fail-closed: a cancel request can never make a
    # future scan acquisition block on a stale marker).
    safe = sc.safe_project_id(PROJECT)
    ws = tmp_path / "platform_workspace" / safe
    ws.mkdir(parents=True)
    res = sc.request_scan_cancel(
        Path(tmp_path), PROJECT, requester={"name": "op", "role": "admin"}
    )
    assert res["requested"] is False
    assert res["reason_code"] == "NO_ACTIVE_SCAN"
    assert not (ws / ".runtime_locks" / "scan.lock").exists()
    assert not sc._cancel_path(Path(tmp_path), PROJECT).exists()


def test_request_write_read_consume_lifecycle(tmp_path):
    owner = {
        "schema": "x",
        "token": "TKN-1",
        "project_id": PROJECT,
        "mode": "manual_scan",
        "started_at_utc": "2020-01-01T00:00:00Z",
    }
    _lease_dir(Path(tmp_path), PROJECT).mkdir(parents=True)
    with mock.patch.object(sc, "active_scan_owner", return_value=owner):
        res = sc.request_scan_cancel(
            Path(tmp_path), PROJECT, requester={"name": "op", "role": "admin"}
        )
        assert res["requested"] is True
        assert res["reason_code"] == "SCAN_CANCEL_REQUESTED"

        payload = sc.read_scan_cancel_request(Path(tmp_path), PROJECT)
        assert payload.get("target_token") == "TKN-1"
        assert payload.get("schema") == sc.CANCEL_REQUEST_SCHEMA

        consumed = sc.consume_scan_cancel_request(Path(tmp_path), PROJECT)
        assert consumed == payload
        # after consume, nothing pending and marker file removed
        assert sc.read_scan_cancel_request(Path(tmp_path), PROJECT) == {}
        assert not sc._cancel_path(Path(tmp_path), PROJECT).exists()


def test_consume_nothing_pending_returns_empty(tmp_path):
    assert sc.consume_scan_cancel_request(Path(tmp_path), PROJECT) == {}


def test_read_obsolete_when_lease_replaced(tmp_path):
    owner = {
        "schema": "x",
        "token": "TKN-1",
        "project_id": PROJECT,
        "mode": "m",
        "started_at_utc": "t",
    }
    _lease_dir(Path(tmp_path), PROJECT).mkdir(parents=True)
    with mock.patch.object(sc, "active_scan_owner", return_value=owner):
        sc.request_scan_cancel(Path(tmp_path), PROJECT)
    # lease replaced -> token mismatch -> obsolete request is invisible
    owner2 = dict(owner)
    owner2["token"] = "TKN-2"
    with mock.patch.object(sc, "active_scan_owner", return_value=owner2):
        assert sc.read_scan_cancel_request(Path(tmp_path), PROJECT) == {}


def test_persist_failure_returns_error_not_silent(tmp_path):
    owner = {
        "schema": "x",
        "token": "TKN-1",
        "project_id": PROJECT,
        "mode": "m",
        "started_at_utc": "t",
    }
    _lease_dir(Path(tmp_path), PROJECT).mkdir(parents=True)
    with mock.patch.object(sc, "active_scan_owner", return_value=owner):
        with mock.patch.object(
            sc, "_write_json_object_atomic", side_effect=RuntimeError("disk")
        ):
            res = sc.request_scan_cancel(Path(tmp_path), PROJECT)
    assert res["requested"] is False
    assert res["reason_code"] == "CANCEL_REQUEST_PERSIST_FAILED"
