from __future__ import annotations

from pathlib import Path

from ai_test_asset_center.flow_data_materialization import (
    BLOCKED_FLOW_DATA_MATERIALIZATION_INCOMPLETE,
    BLOCKED_FLOW_DATA_REQUIREMENT_DRIFT,
    STATUS_BLOCKED,
    STATUS_VALID,
    validate_flow_data_materialization,
)
from ai_test_asset_center import experiment_fixture_materializer_with_preconditions as facade


def _requirement() -> dict:
    return {
        "schema_version": "qualibug.flow-data-requirement.v1",
        "status": "FROZEN",
        "requirement_id": "flow_data_1",
        "requirement_fingerprint": "fp_1",
        "materialized_before_measurement_targets": ["id", "tenant_id"],
        "materialization_authority": {
            "binding_plan": "binding_plan",
            "dependency_plan": "fixture_dependency_dag",
            "executor": "experiment_fixture_materializer_core",
        },
    }


def _experiment() -> dict:
    return {
        "compile_receipt": {
            "status": "COMPILED",
            "compile_freeze_status": "FROZEN",
            "flow_data_requirement_id": "flow_data_1",
            "flow_data_requirement_fingerprint": "fp_1",
        },
        "flow_data_requirement": _requirement(),
        "precondition_plan": [{"step_id": "pre_1"}],
        "control_plan": [{"step_id": "control_1"}],
        "treatment_plan": [{"step_id": "treatment_1"}],
    }


def _ready_state() -> dict:
    return {
        "status": "ready",
        "runtime_bindings": {"id": "42", "tenant_id": "tenant_a"},
        "binding_materialization_receipts": [
            {"target": "id", "status": "BOUND"},
            {"target": "tenant_id", "status": "BOUND"},
        ],
        "fixture_receipts": [],
        "steps_out": [],
        "pending_fixture_cleanups": [
            {"target": "id", "cleanup": {"method": "DELETE"}}
        ],
        "contract_evidence_receipts": [],
        "cleanup_failures": 0,
    }


def test_materialization_validates_exact_requirement_and_receipts() -> None:
    receipt = validate_flow_data_materialization(_experiment(), _ready_state())

    assert receipt["status"] == STATUS_VALID
    assert receipt["requirement_id"] == "flow_data_1"
    assert receipt["required_target_count"] == 2
    assert receipt["materialized_target_count"] == 2
    assert receipt["missing_targets"] == []
    assert receipt["unreceipted_targets"] == []


def test_materialized_value_without_receipt_is_not_authoritative() -> None:
    state = _ready_state()
    state["binding_materialization_receipts"] = [
        {"target": "id", "status": "BOUND"}
    ]

    receipt = validate_flow_data_materialization(_experiment(), state)

    assert receipt["status"] == STATUS_BLOCKED
    assert receipt["reason_code"] == (
        BLOCKED_FLOW_DATA_MATERIALIZATION_INCOMPLETE
    )
    assert receipt["unreceipted_targets"] == ["tenant_id"]


def test_missing_runtime_value_blocks_measurement() -> None:
    state = _ready_state()
    state["runtime_bindings"].pop("tenant_id")

    receipt = validate_flow_data_materialization(_experiment(), state)

    assert receipt["status"] == STATUS_BLOCKED
    assert receipt["missing_targets"] == ["tenant_id"]


def test_requirement_fingerprint_drift_blocks_runtime() -> None:
    experiment = _experiment()
    experiment["flow_data_requirement"]["requirement_fingerprint"] = "drifted"

    receipt = validate_flow_data_materialization(experiment, _ready_state())

    assert receipt["status"] == STATUS_BLOCKED
    assert receipt["reason_code"] == BLOCKED_FLOW_DATA_REQUIREMENT_DRIFT
    assert "requirement_fingerprint_mismatch" in receipt["detail"]


def test_legacy_experiment_without_declared_requirement_remains_compatible() -> None:
    receipt = validate_flow_data_materialization(
        {"compile_receipt": {"status": "COMPILED"}},
        {"runtime_bindings": {}},
    )

    assert receipt["status"] == STATUS_VALID
    assert receipt["legacy_experiment"] is True


def test_flow_data_block_preserves_cleanup_and_skips_precondition(monkeypatch) -> None:
    state = _ready_state()
    state["runtime_bindings"].pop("tenant_id")
    exp = _experiment()

    monkeypatch.setattr(
        facade,
        "_materialize_experiment_fixtures",
        lambda **kwargs: state,
    )

    def forbidden_precondition(**kwargs):
        raise AssertionError("precondition must not execute after data block")

    monkeypatch.setattr(facade, "execute_precondition_plan", forbidden_precondition)

    result = facade.materialize_experiment_fixtures(
        exp=exp,
        actors={},
        ops={},
        tokens={},
        root=Path("."),
        project="project",
        base_url="http://example.test",
        runtime_contract={},
        campaign_id="campaign",
    )

    assert result["status"] == "ready"
    assert result["flow_data_materialization_blocked"] is True
    assert result["pending_fixture_cleanups"] == state[
        "pending_fixture_cleanups"
    ]
    assert exp["precondition_plan"] == []
    assert exp["control_plan"] == []
    assert exp["treatment_plan"] == []
    assert exp["blocked_measured_plans"]["phase"] == (
        "flow_data_materialization"
    )
    flow_receipts = [
        row
        for row in result["fixture_receipts"]
        if row.get("kind") == "flow_data_materialization"
    ]
    assert len(flow_receipts) == 1
    assert flow_receipts[0]["status"] == STATUS_BLOCKED


def test_valid_flow_data_proceeds_to_precondition(monkeypatch) -> None:
    state = _ready_state()
    exp = _experiment()
    called = {"count": 0}

    monkeypatch.setattr(
        facade,
        "_materialize_experiment_fixtures",
        lambda **kwargs: state,
    )

    def successful_precondition(**kwargs):
        called["count"] += 1
        return {
            "status": "ESTABLISHED",
            "established": True,
            "governed_write_steps": [],
            "receipts": [],
        }

    monkeypatch.setattr(
        facade,
        "execute_precondition_plan",
        successful_precondition,
    )

    result = facade.materialize_experiment_fixtures(
        exp=exp,
        actors={},
        ops={},
        tokens={},
        root=Path("."),
        project="project",
        base_url="http://example.test",
        runtime_contract={},
        campaign_id="campaign",
    )

    assert called["count"] == 1
    assert result["status"] == "ready"
    assert result["state_precondition_established"] is True
    assert result["flow_data_materialization_receipt"]["status"] == STATUS_VALID
