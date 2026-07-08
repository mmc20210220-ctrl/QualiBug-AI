"""主链 4 regression: every planned test task carries an explicit lifecycle status.

Two real gaps were closed:
  A) BehaviorSlice (one generated test task) had no status field; tasks entered the
     campaign as opaque ids with no pending/running/passed/failed/blocked state that
     the frontend could surface. Now defaults to "pending" and serializes in to_dict().
  B) _persist_slice_ledger stored only attempted/confirmed id sets. The campaign
     progress now also derives an explicit per-task status map (attempted->running,
     confirmed->passed, blocked campaign->blocked) so the API/frontend can render a
     real task list instead of opaque id sets.
"""
from __future__ import annotations

import json

from ai_test_asset_center.business_state_graph import BehaviorSlice
from ai_test_asset_center.v12_pipeline import _derive_slice_status, _persist_slice_ledger


def _read_ledger(root, project: str) -> dict:
    path = root / "platform_workspace" / project / "defect_discovery" / "v12_behavior_slice_ledger.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_behavior_slice_defaults_pending_and_serializes():
    """Fix A: a freshly planned task is 'pending' and that status is emitted."""
    slice_ = BehaviorSlice(
        slice_id="BHV_test",
        entity="order",
        kind="transition",
        states=["created", "paid"],
        endpoints=["POST /api/order/pay"],
    )
    assert slice_.status == "pending"
    assert slice_.to_dict()["status"] == "pending"


def test_behavior_slice_status_override_serializes():
    """Fix A: an explicitly set status is preserved through to_dict()."""
    slice_ = BehaviorSlice(
        slice_id="BHV_test",
        entity="order",
        kind="transition",
        status="failed",
    )
    assert slice_.status == "failed"
    assert slice_.to_dict()["status"] == "failed"


def test_persist_slice_ledger_derives_running_and_passed(tmp_path):
    """Fix B: attempted-but-unconfirmed -> running; attempted-and-confirmed -> passed."""
    project = "mc4_a"
    ledger = {
        "campaign_id": "c1",
        "attempted_slice_ids": ["BHV_1", "BHV_2"],
        "confirmed_slice_ids": ["BHV_2"],
    }
    _persist_slice_ledger(tmp_path, project, ledger)
    persisted = _read_ledger(tmp_path, project)
    status = persisted["slice_status"]
    assert status["BHV_1"] == "running"
    assert status["BHV_2"] == "passed"


def test_persist_slice_ledger_blocked_campaign_overrides_to_blocked(tmp_path):
    """Fix B: a blocked campaign reclassifies unconfirmed attempted tasks as blocked."""
    project = "mc4_b"
    ledger = {
        "campaign_id": "c1",
        "campaign_status": "blocked",
        "attempted_slice_ids": ["BHV_1", "BHV_2"],
        "confirmed_slice_ids": ["BHV_2"],
    }
    _persist_slice_ledger(tmp_path, project, ledger)
    persisted = _read_ledger(tmp_path, project)
    status = persisted["slice_status"]
    assert status["BHV_1"] == "blocked"
    assert status["BHV_2"] == "passed"  # confirmed stays passed even when blocked


def test_persist_slice_ledger_unattempted_absent(tmp_path):
    """Fix B: planned-but-not-yet-attempted tasks are NOT in slice_status; they are
    implicitly 'pending' (only attempted/confirmed/blocked states are persisted)."""
    project = "mc4_c"
    ledger = {
        "campaign_id": "c1",
        "attempted_slice_ids": ["BHV_1"],
        "confirmed_slice_ids": [],
    }
    _persist_slice_ledger(tmp_path, project, ledger)
    persisted = _read_ledger(tmp_path, project)
    status = persisted["slice_status"]
    assert "BHV_1" in status
    assert "BHV_PLANNED_NOT_RUN" not in status


def test_derive_slice_status_helper_contract():
    """The shared helper must produce the exact running/passed/blocked mapping the
    in-memory result AND the persisted file both rely on (single source of truth)."""
    status = _derive_slice_status(
        attempted_ids=["BHV_1", "BHV_2", "BHV_3"],
        confirmed_ids=["BHV_2"],
        campaign_status="blocked",
    )
    assert status == {"BHV_1": "blocked", "BHV_2": "passed", "BHV_3": "blocked"}


def test_derive_slice_status_helper_non_blocked():
    status = _derive_slice_status(
        attempted_ids=["BHV_1", "BHV_2"],
        confirmed_ids=["BHV_2"],
        campaign_status="running",
    )
    assert status == {"BHV_1": "running", "BHV_2": "passed"}


def test_derive_slice_status_helper_ignores_empty_and_non_str():
    status = _derive_slice_status(
        attempted_ids=["BHV_1", "", None, "BHV_2"],
        confirmed_ids=[None, ""],
        campaign_status="",
    )
    assert status == {"BHV_1": "running", "BHV_2": "running"}
