from __future__ import annotations


def test_unstructured_fixture_response_cannot_prove_declared_field_precondition() -> None:
    from ai_test_asset_center.experiment_fixture_materializer_with_preconditions import (
        _strict_validate_fixture_preconditions,
    )

    failures = _strict_validate_fixture_preconditions(
        {
            "assertions": [
                {"kind": "state_transition", "state_field": "status"}
            ],
            "treatment_plan": [],
        },
        "created-id-only",
        "order_id",
    )

    assert failures == [
        {
            "field": "status",
            "reason": "fixture_precondition_response_unstructured",
            "target": "order_id",
        }
    ]


def test_fixture_precondition_failure_withdraws_later_resolved_receipt() -> None:
    from ai_test_asset_center.experiment_fixture_materializer_with_preconditions import (
        _reject_failed_fixture_preconditions,
    )

    exp = {
        "precondition_plan": [],
        "control_plan": [{"step_id": "control-1"}],
        "treatment_plan": [{"step_id": "treatment-1"}],
    }
    state = {
        "status": "ready",
        "fixture_receipts": [
            {
                "node_id": "fixture-order",
                "kind": "fixture_precondition_validation",
                "status": "FAILED",
                "failures": [{"field": "status"}],
            },
            {
                "node_id": "fixture-order",
                "kind": "runtime_binding",
                "status": "resolved",
            },
        ],
        "binding_materialization_receipts": [
            {
                "target": "order_id",
                "status": "PRECONDITION_FAILED",
                "precondition_failures": [{"field": "status"}],
            }
        ],
        "contract_evidence_receipts": [
            {
                "kind": "fixture",
                "subject_id": "fixture-order",
                "status": "OBSERVED",
            }
        ],
        "steps_out": [{"phase": "fixture_setup", "status_code": 201}],
        "pending_fixture_cleanups": [{"target": "order_id"}],
    }

    governed = _reject_failed_fixture_preconditions(exp=exp, state=state)

    assert governed is state
    assert governed["fixture_precondition_reason_code"] == (
        "BLOCKED_FIXTURE_CONTRACT_FAILED"
    )
    assert governed["contract_evidence_receipts"] == []
    assert all(
        row.get("status") != "resolved"
        for row in governed["fixture_receipts"]
        if row.get("node_id") == "fixture-order"
    )
    assert exp["control_plan"] == []
    assert exp["treatment_plan"] == []
    assert governed["steps_out"]
    assert governed["pending_fixture_cleanups"]
