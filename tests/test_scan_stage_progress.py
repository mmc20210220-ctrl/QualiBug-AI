from __future__ import annotations

from types import SimpleNamespace

import pytest

from ai_test_asset_center import discovery_runtime
from ai_test_asset_center.private_pilot_continuous import _get_continuous_state
from ai_test_asset_center.private_pilot_scan_coordinator import project_scan_lease
from ai_test_asset_center.scan_stage_progress import (
    begin_scan_stage_progress,
    mark_scan_stage,
    read_scan_stage_progress,
)


def _inputs(tmp_path, project: str = "stage_project") -> SimpleNamespace:
    return SimpleNamespace(root=tmp_path, project=project)


def test_stage_authority_requires_explicit_real_transitions(tmp_path) -> None:
    project = "stage_project"
    state = begin_scan_stage_progress(tmp_path, project)
    assert all(item["status"] == "pending" for item in state["stages"].values())

    mark_scan_stage(
        tmp_path,
        project,
        "runtime_execution",
        "active",
        detail="real runner entered",
    )
    active = read_scan_stage_progress(tmp_path, project)
    assert active["stages"]["runtime_execution"]["status"] == "active"
    assert active["stages"]["runtime_execution"]["started_at_utc"]
    assert active["stages"]["scenario_planning"]["status"] == "pending"

    mark_scan_stage(tmp_path, project, "runtime_execution", "completed")
    completed = read_scan_stage_progress(tmp_path, project)
    assert completed["stages"]["runtime_execution"]["status"] == "completed"
    assert completed["stages"]["runtime_execution"]["finished_at_utc"]
    # Time passing or another stage completing must never auto-advance pending stages.
    assert completed["stages"]["test_data_assessment"]["status"] == "pending"
    assert completed["stages"]["delivery_finalization"]["status"] == "pending"

    with pytest.raises(ValueError, match="unknown scan stage"):
        mark_scan_stage(tmp_path, project, "invented_progress", "active")
    with pytest.raises(ValueError, match="invalid scan stage status"):
        mark_scan_stage(tmp_path, project, "runtime_execution", "75_percent")


def test_real_planner_controls_understanding_and_scenario_stages(tmp_path, monkeypatch) -> None:
    inputs = _inputs(tmp_path)
    marker = object()

    def fake_planner(received_inputs, campaign_handle):
        assert received_inputs is inputs
        assert campaign_handle == "campaign"
        in_flight = read_scan_stage_progress(tmp_path, inputs.project)
        assert in_flight["stages"]["enterprise_understanding"]["status"] == "active"
        assert in_flight["stages"]["scenario_planning"]["status"] == "active"
        return marker

    monkeypatch.setattr(discovery_runtime, "_build_discovery_plan", fake_planner)
    result = discovery_runtime.build_discovery_plan(inputs, "campaign")
    assert result is marker

    state = read_scan_stage_progress(tmp_path, inputs.project)
    assert state["stages"]["enterprise_understanding"]["status"] == "completed"
    assert state["stages"]["scenario_planning"]["status"] == "completed"
    assert state["stages"]["runtime_execution"]["status"] == "pending"


def test_planner_failure_marks_only_owned_planning_stages_failed(tmp_path, monkeypatch) -> None:
    inputs = _inputs(tmp_path)

    def fail_planner(*_args, **_kwargs):
        raise RuntimeError("planning failed")

    monkeypatch.setattr(discovery_runtime, "_build_discovery_plan", fail_planner)
    with pytest.raises(RuntimeError, match="planning failed"):
        discovery_runtime.build_discovery_plan(inputs, "campaign")

    state = read_scan_stage_progress(tmp_path, inputs.project)
    assert state["stages"]["enterprise_understanding"]["status"] == "failed"
    assert state["stages"]["scenario_planning"]["status"] == "failed"
    assert state["stages"]["runtime_execution"]["status"] == "pending"


def test_real_runner_controls_execution_and_evidence_stages(tmp_path, monkeypatch) -> None:
    inputs = _inputs(tmp_path)
    begin_scan_stage_progress(tmp_path, inputs.project)

    def fake_runner(received_inputs, campaign_handle, plan):
        assert received_inputs is inputs
        assert campaign_handle == "campaign"
        assert plan == "plan"
        in_flight = read_scan_stage_progress(tmp_path, inputs.project)
        assert in_flight["stages"]["runtime_execution"]["status"] == "active"
        assert in_flight["stages"]["evidence_collection"]["status"] == "active"
        return {"ok": True, "experiment": "receipt"}

    monkeypatch.setattr(discovery_runtime, "_run_experiment_candidate", fake_runner)
    result = discovery_runtime.run_experiment_candidate(inputs, "campaign", "plan")
    assert result == {"ok": True, "experiment": "receipt"}

    state = read_scan_stage_progress(tmp_path, inputs.project)
    assert state["stages"]["runtime_execution"]["status"] == "completed"
    # Evidence remains active because caller-owned UI/evidence normalization continues later.
    assert state["stages"]["evidence_collection"]["status"] == "active"
    assert state["stages"]["test_data_assessment"]["status"] == "pending"
    assert state["stages"]["delivery_finalization"]["status"] == "pending"


def test_runner_failure_marks_execution_and_evidence_failed(tmp_path, monkeypatch) -> None:
    inputs = _inputs(tmp_path)
    begin_scan_stage_progress(tmp_path, inputs.project)

    def fail_runner(*_args, **_kwargs):
        raise RuntimeError("runner failed")

    monkeypatch.setattr(discovery_runtime, "_run_experiment_candidate", fail_runner)
    with pytest.raises(RuntimeError, match="runner failed"):
        discovery_runtime.run_experiment_candidate(inputs, "campaign", "plan")

    state = read_scan_stage_progress(tmp_path, inputs.project)
    assert state["stages"]["runtime_execution"]["status"] == "failed"
    assert state["stages"]["evidence_collection"]["status"] == "failed"


def test_http_status_projects_stage_snapshot_only_while_lease_is_live(tmp_path) -> None:
    project = "stage_project"
    begin_scan_stage_progress(tmp_path, project)
    mark_scan_stage(tmp_path, project, "enterprise_understanding", "active")

    idle = _get_continuous_state(tmp_path, project)
    assert idle["active_scan_live"] is False
    assert idle["scan_stage_progress"] == {}

    with project_scan_lease(
        tmp_path,
        project,
        mode="manual_scan",
        tenant_id="tenant_stage",
    ):
        running = _get_continuous_state(tmp_path, project)
        assert running["active_scan_live"] is True
        assert running["scan_stage_progress"]["schema"] == "qualibug.scan-stage-progress.v1"
        assert running["scan_stage_progress"]["stages"]["enterprise_understanding"]["status"] == "active"

    finished = _get_continuous_state(tmp_path, project)
    assert finished["active_scan_live"] is False
    assert finished["scan_stage_progress"] == {}
