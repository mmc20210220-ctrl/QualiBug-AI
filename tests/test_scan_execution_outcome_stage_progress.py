from __future__ import annotations

import pytest

from ai_test_asset_center import artifact_store, evidence_artifact_store, release_gate
from ai_test_asset_center.scan_execution_outcome import (
    _evaluate_release_gate,
    _persist_execution_evidence,
    _test_data_receipt_verifier,
)
from ai_test_asset_center.scan_stage_progress import (
    begin_scan_stage_progress,
    read_scan_stage_progress,
)


def test_test_data_verifier_opens_real_assessment_stage(tmp_path) -> None:
    project = "native_test_data_stage"
    begin_scan_stage_progress(tmp_path, project)

    verifier = _test_data_receipt_verifier(tmp_path, project)
    assert callable(verifier)

    state = read_scan_stage_progress(tmp_path, project)
    assert state["stages"]["test_data_assessment"]["status"] == "active"
    assert "不可变收据" in state["stages"]["test_data_assessment"]["detail"]


def test_evidence_persistence_owns_active_and_completed_stage(
    tmp_path,
    monkeypatch,
) -> None:
    project = "native_evidence_stage"
    begin_scan_stage_progress(tmp_path, project)
    monkeypatch.setattr(artifact_store, "artifact_store_enabled", lambda: False)

    def fake_persist(project_id, **_kwargs):
        assert project_id == project
        in_flight = read_scan_stage_progress(tmp_path, project)
        assert in_flight["stages"]["evidence_collection"]["status"] == "active"
        return {"status": "persisted", "bundle_id": "EV_NATIVE"}

    monkeypatch.setattr(
        evidence_artifact_store,
        "persist_evidence_bundle",
        fake_persist,
    )

    bundle = _persist_execution_evidence(
        project,
        tmp_path,
        "scan_native",
        {},
        {},
        "completed",
        {},
    )
    assert bundle["status"] == "persisted"

    state = read_scan_stage_progress(tmp_path, project)
    assert state["stages"]["evidence_collection"]["status"] == "completed"
    assert "evidence_bundle=persisted" in state["stages"]["evidence_collection"]["detail"]


def test_evidence_persistence_failure_marks_stage_failed(
    tmp_path,
    monkeypatch,
) -> None:
    project = "native_evidence_failure"
    begin_scan_stage_progress(tmp_path, project)
    monkeypatch.setattr(artifact_store, "artifact_store_enabled", lambda: False)

    def fail_persist(*_args, **_kwargs):
        raise RuntimeError("evidence write failed")

    monkeypatch.setattr(
        evidence_artifact_store,
        "persist_evidence_bundle",
        fail_persist,
    )

    with pytest.raises(RuntimeError, match="evidence write failed"):
        _persist_execution_evidence(
            project,
            tmp_path,
            "scan_native",
            {},
            {},
            "completed",
            {},
        )

    state = read_scan_stage_progress(tmp_path, project)
    assert state["stages"]["evidence_collection"]["status"] == "failed"


def test_release_gate_owns_test_data_completion_and_delivery_active(
    tmp_path,
    monkeypatch,
) -> None:
    project = "native_delivery_stage"
    begin_scan_stage_progress(tmp_path, project)
    _test_data_receipt_verifier(tmp_path, project)

    def fake_gate(**_kwargs):
        in_flight = read_scan_stage_progress(tmp_path, project)
        assert in_flight["stages"]["test_data_assessment"]["status"] == "completed"
        assert in_flight["stages"]["delivery_finalization"]["status"] == "active"
        return {"status": "blocked", "verdict": "fail"}

    monkeypatch.setattr(release_gate, "evaluate_release_gate", fake_gate)

    gate = _evaluate_release_gate(
        project=project,
        root=tmp_path,
        campaign={},
        execution_status="completed",
        runtime_contract={},
        evidence_bundle={"status": "not_created"},
        test_data_plan={"status": "ready", "strategy": "reuse_verified_existing"},
        findings=[],
        coverage_gaps=[],
    )
    assert gate == {"status": "blocked", "verdict": "fail"}

    state = read_scan_stage_progress(tmp_path, project)
    assert state["stages"]["test_data_assessment"]["status"] == "completed"
    # A business release verdict of FAIL is still a successfully executed gate.
    assert state["stages"]["delivery_finalization"]["status"] == "completed"
    assert "verdict=fail" in state["stages"]["delivery_finalization"]["detail"]


def test_release_gate_failure_marks_delivery_execution_failed(
    tmp_path,
    monkeypatch,
) -> None:
    project = "native_delivery_failure"
    begin_scan_stage_progress(tmp_path, project)

    def fail_gate(**_kwargs):
        raise RuntimeError("gate engine failed")

    monkeypatch.setattr(release_gate, "evaluate_release_gate", fail_gate)

    with pytest.raises(RuntimeError, match="gate engine failed"):
        _evaluate_release_gate(
            project=project,
            root=tmp_path,
            campaign={},
            execution_status="completed",
            runtime_contract={},
            evidence_bundle={"status": "not_created"},
            test_data_plan={"status": "ready", "strategy": "reuse_verified_existing"},
            findings=[],
            coverage_gaps=[],
        )

    state = read_scan_stage_progress(tmp_path, project)
    assert state["stages"]["delivery_finalization"]["status"] == "failed"
