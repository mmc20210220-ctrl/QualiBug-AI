from __future__ import annotations


def test_missing_dag_fixture_cannot_be_reconciled_into_resolved_evidence() -> None:
    from ai_test_asset_center.experiment_fixture_materializer_with_preconditions import (
        _reject_synthetic_activation_reconciliation,
    )

    exp = {
        "precondition_plan": [{"step_id": "pre-1"}],
        "control_plan": [{"step_id": "control-1"}],
        "treatment_plan": [{"step_id": "treatment-1"}],
    }
    state = {
        "status": "ready",
        "fixture_receipts": [
            {
                "node_id": "fixture-missing",
                "kind": "stale_requirement",
                "status": "resolved",
                "source": "activation_requirement_reconciliation",
            }
        ],
        "contract_evidence_receipts": [
            {
                "kind": "fixture",
                "subject_id": "fixture-missing",
                "status": "OBSERVED",
                "receipt_id": "fake-observed-fixture",
            }
        ],
        "steps_out": [{"phase": "fixture_setup", "status_code": 201}],
        "pending_fixture_cleanups": [{"target": "created-row"}],
    }

    governed = _reject_synthetic_activation_reconciliation(exp=exp, state=state)

    assert governed is state
    assert governed["status"] == "ready"
    row = governed["fixture_receipts"][0]
    assert row["status"] == "BLOCKED"
    assert row["reason_code"] == "BLOCKED_FIXTURE_DAG_DRIFT"
    assert row["reconciliation_is_evidence"] is False
    assert governed["contract_evidence_receipts"] == []
    assert exp["precondition_plan"] == []
    assert exp["control_plan"] == []
    assert exp["treatment_plan"] == []
    # Cleanup context survives the block.
    assert governed["steps_out"]
    assert governed["pending_fixture_cleanups"]


def test_unexecuted_constructible_fixture_is_also_dag_drift() -> None:
    from ai_test_asset_center.experiment_fixture_materializer_with_preconditions import (
        _reject_synthetic_activation_reconciliation,
    )

    exp = {"precondition_plan": [], "control_plan": [], "treatment_plan": []}
    state = {
        "status": "ready",
        "fixture_receipts": [
            {
                "node_id": "fixture-skipped",
                "kind": "runtime_binding",
                "status": "resolved",
                "source": "activation_requirement_reconciliation",
            }
        ],
        "contract_evidence_receipts": [],
    }

    governed = _reject_synthetic_activation_reconciliation(exp=exp, state=state)

    assert governed is state
    assert governed["fixture_receipts"][0]["detail"] == (
        "activation_required_fixture_not_executed"
    )
    assert governed["fixture_activation_reconciliation_reason_code"] == (
        "BLOCKED_FIXTURE_DAG_DRIFT"
    )
