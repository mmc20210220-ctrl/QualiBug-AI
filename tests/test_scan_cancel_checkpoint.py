from pathlib import Path
from unittest import mock

import pytest

import ai_test_asset_center.scan_cancellation as sc
import ai_test_asset_center._experiment_batch_executor_single_finding_mechanics_base as batch
import ai_test_asset_center.experiment_executor as ex

PROJECT = "test-proj-cancel-checkpoint-001"


@pytest.fixture
def isolated_batch(monkeypatch):
    # Isolate the executor from any real target / auth / transport so the
    # checkpoint preemption is the only thing under test.
    monkeypatch.setattr(batch, "load_actor_tokens", lambda *a, **k: {})
    monkeypatch.setattr(batch, "_load_service_base_urls", lambda *a, **k: {})
    monkeypatch.setattr(
        ex, "execute_one_experiment", lambda *a, **k: {"status": "BLOCKED", "reason_code": "TEST_STUB"}
    )


def _run_batch(root, project, selected):
    return batch.execute_selected_experiments(
        selected,
        experiments_by_obligation={},
        behavior_ir={},
        root=root,
        project=project,
        base_url="",
        runtime_contract={},
        mainline_run={"campaign_id": "C1"},
        campaign_id="C1",
    )


def _write_marker(root, project, token="TKN-X"):
    owner = {
        "schema": "x",
        "token": token,
        "project_id": project,
        "mode": "manual_scan",
        "started_at_utc": "2020-01-01T00:00:00Z",
    }
    sc._cancel_path(root, project).parent.mkdir(parents=True)
    sc.request_scan_cancel(root, project, requester={"name": "op", "role": "admin"})
    return owner


def test_cancel_marker_defers_all_selected_and_runs_nothing(tmp_path, isolated_batch):
    selected = [{"obligation_id": f"O{i}", "experiment_id": f"E{i}"} for i in range(3)]
    owner = {
        "schema": "x",
        "token": "TKN-X",
        "project_id": PROJECT,
        "mode": "manual_scan",
        "started_at_utc": "2020-01-01T00:00:00Z",
    }
    sc._cancel_path(Path(tmp_path), PROJECT).parent.mkdir(parents=True)
    with mock.patch.object(sc, "active_scan_owner", return_value=owner):
        # marker is produced by the module under test itself, with the live
        # lease owner in effect, exactly like the HTTP handler path.
        sc.request_scan_cancel(Path(tmp_path), PROJECT, requester={"name": "op", "role": "admin"})
        out = _run_batch(Path(tmp_path), PROJECT, selected)
    assert out["operator_cancelled_count"] == 3
    assert len(out["results"]) == 3
    assert all(r["reason_code"] == "OPERATOR_CANCELLED" for r in out["results"])
    assert all(r["status"] == "DEFERRED" for r in out["results"])
    # Honest boundary: nothing actually executed — an in-flight experiment is
    # never killed, and nothing unstarted was started.
    assert not any(r.get("status") == "EXECUTED" for r in out["results"])


def test_no_marker_does_not_fake_cancel(tmp_path, isolated_batch):
    selected = [{"obligation_id": f"O{i}", "experiment_id": f"E{i}"} for i in range(2)]
    out = _run_batch(Path(tmp_path), PROJECT, selected)
    assert out["operator_cancelled_count"] == 0
    assert not any(r.get("reason_code") == "OPERATOR_CANCELLED" for r in out["results"])


def test_checkpoint_failure_is_tolerated_no_fake_cancel(tmp_path, isolated_batch):
    selected = [{"obligation_id": f"O{i}", "experiment_id": f"E{i}"} for i in range(2)]
    # _pending_scan_cancel swallows read errors and returns {}; the scan must
    # continue, never synthesize a cancellation.
    with mock.patch.object(sc, "read_scan_cancel_request", side_effect=RuntimeError("boom")):
        out = _run_batch(Path(tmp_path), PROJECT, selected)
    assert out["operator_cancelled_count"] == 0
    assert not any(r.get("reason_code") == "OPERATOR_CANCELLED" for r in out["results"])
