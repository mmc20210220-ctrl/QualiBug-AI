from __future__ import annotations

import pytest

from ai_test_asset_center import scan_stage_progress
from ai_test_asset_center.scan_stage_finalization_hook import (
    _finalize_scan_stage_progress,
)
from ai_test_asset_center.scan_stage_progress import (
    begin_scan_stage_progress,
    mark_scan_stage,
    read_scan_stage_progress,
)


def test_finalization_projects_only_authoritative_completed_outputs(tmp_path) -> None:
    project = "final_stage_project"
    begin_scan_stage_progress(tmp_path, project)
    mark_scan_stage(tmp_path, project, "evidence_collection", "active")

    result = {
        "success": True,
        "execution_status": "completed",
        "evidence_bundle": {"status": "persisted", "bundle_id": "EV_1"},
        "test_data_plan": {
            "status": "blocked_with_testability_gap",
            "strategy": "blocked_with_testability_gap",
        },
        "release_gate": {"status": "blocked", "verdict": "fail"},
        "report_path": "/tmp/intelligence_report.json",
    }

    projected = _finalize_scan_stage_progress(
        result,
        project=project,
        root=tmp_path,
    )
    assert projected is result

    state = read_scan_stage_progress(tmp_path, project)
    assert state["stages"]["evidence_collection"]["status"] == "completed"
    assert state["stages"]["test_data_assessment"]["status"] == "blocked"
    # A release verdict of FAIL/BLOCKED is a valid completed gate evaluation;
    # it must not be confused with telemetry/execution failure.
    assert state["stages"]["delivery_finalization"]["status"] == "completed"
    assert "verdict=fail" in state["stages"]["delivery_finalization"]["detail"]


def test_failed_scan_marks_only_the_current_active_stage_failed(tmp_path) -> None:
    project = "failed_stage_project"
    begin_scan_stage_progress(tmp_path, project)
    mark_scan_stage(tmp_path, project, "runtime_execution", "active")

    _finalize_scan_stage_progress(
        {"success": False, "error": "runner exploded"},
        project=project,
        root=tmp_path,
    )

    state = read_scan_stage_progress(tmp_path, project)
    assert state["stages"]["runtime_execution"]["status"] == "failed"
    assert state["stages"]["test_data_assessment"]["status"] == "pending"
    assert state["stages"]["delivery_finalization"]["status"] == "pending"


def test_stage_persistence_failure_is_observability_only(tmp_path, monkeypatch) -> None:
    project = "io_failure_project"

    def fail_write(*_args, **_kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(
        scan_stage_progress,
        "_write_json_object_atomic",
        fail_write,
    )

    # Valid telemetry writes fail soft and cannot become a scan failure.
    initial = begin_scan_stage_progress(tmp_path, project)
    assert initial["schema"] == "qualibug.scan-stage-progress.v1"
    updated = mark_scan_stage(
        tmp_path,
        project,
        "runtime_execution",
        "active",
        detail="real runner entered",
    )
    assert updated["stages"]["runtime_execution"]["status"] == "active"

    # Programming/contract errors still fail loudly; fail-soft applies only to
    # telemetry persistence, not to invented stages or percentages.
    with pytest.raises(ValueError, match="unknown scan stage"):
        mark_scan_stage(tmp_path, project, "invented_stage", "active")
    with pytest.raises(ValueError, match="invalid scan stage status"):
        mark_scan_stage(tmp_path, project, "runtime_execution", "50_percent")
