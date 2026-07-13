from __future__ import annotations

from ai_test_asset_center.assertion_dsl import evaluate_assertion
from ai_test_asset_center.observer_contracts import (
    observe_authorization_comparison,
    observe_experiment_requirements,
)


def _write_step(
    *,
    phase: str,
    status: int,
    before: object,
    after: object,
    path: str = "/resources",
) -> dict:
    return {
        "phase": phase,
        "step_id": f"{phase}_1",
        "method": "POST",
        "path": path,
        "status_code": status,
        "body": {},
        "governance_receipt": {
            "before": {"status": 200, "body": before},
            "write": {"status": status, "body": {}},
            "after": {"status": 200, "body": after},
        },
    }


def _experiment() -> dict:
    return {
        "assertions": [{
            "assertion_id": "assert-authorization",
            "kind": "owner_tenant_visibility",
            "property": {"require_same_resource": True},
        }],
        "observers": [
            {"observer_id": "business_effect"},
            {"observer_id": "authorization_comparison"},
        ],
    }


def test_main_observer_pipeline_flags_accepted_restricted_write() -> None:
    control_step = _write_step(
        phase="control",
        status=201,
        before={"data": []},
        after={"data": [{"id": "resource-1"}]},
    )
    treatment_step = _write_step(
        phase="treatment",
        status=200,
        before={"data": [{"id": "resource-1"}]},
        after={"data": [{"id": "resource-1"}]},
    )
    observations = {
        "control_observation": control_step,
        "treatment_observation": treatment_step,
        "execution_steps": [control_step, treatment_step],
    }

    receipts = observe_experiment_requirements(
        _experiment(),
        observations=observations,
    )
    authorization = next(
        receipt
        for receipt in receipts
        if receipt["observer_id"] == "authorization_comparison"
    )

    assert authorization["status"] == "OBSERVED"
    evidence = authorization["evidence"]
    assert evidence["same_resource_proven"] is True
    assert evidence["viewer_request_accepted"] is True
    assert evidence["viewer_business_effect_observed"] is False
    assert evidence["viewer_can_access"] is True
    assert evidence["leak_detected"] is True
    assert (
        evidence["authorization_failure_mode"]
        == "restricted_write_request_accepted"
    )

    verdict = evaluate_assertion(
        {
            "assertion_id": "assert-authorization",
            "kind": "owner_tenant_visibility",
        },
        observations={
            "control_succeeded": True,
            **evidence,
        },
    )
    assert verdict["status"] == "VIOLATION"


def test_restricted_write_rejected_with_403_remains_a_pass() -> None:
    receipt = observe_authorization_comparison(
        control={
            "method": "POST",
            "path": "/resources",
            "status_code": 201,
        },
        treatment={
            "method": "POST",
            "path": "/resources",
            "status_code": 403,
        },
        require_same_resource=True,
        business_effect={
            "business_effect_observed": True,
            "control_effect_count": 1,
            "treatment_effect_count": 0,
        },
    )

    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"]["viewer_can_access"] is False
    assert receipt["evidence"]["leak_detected"] is False


def test_accepted_write_on_a_different_path_is_indeterminate() -> None:
    receipt = observe_authorization_comparison(
        control={
            "method": "POST",
            "path": "/resources",
            "status_code": 201,
        },
        treatment={
            "method": "POST",
            "path": "/other-resources",
            "status_code": 200,
        },
        require_same_resource=True,
        business_effect={
            "business_effect_observed": True,
            "control_effect_count": 1,
            "treatment_effect_count": 0,
        },
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "SAME_RESOURCE_NOT_PROVEN"


def test_read_authorization_semantics_are_unchanged() -> None:
    receipt = observe_authorization_comparison(
        control={
            "method": "GET",
            "path": "/resources/resource-1",
            "status_code": 200,
            "body": {"id": "resource-1", "name": "owner-view"},
        },
        treatment={
            "method": "GET",
            "path": "/resources/resource-1",
            "status_code": 200,
            "body": {"id": "resource-1", "name": "viewer-view"},
        },
        require_same_resource=True,
    )

    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"]["same_resource_proven"] is True
    assert receipt["evidence"]["viewer_can_access"] is True
    assert receipt["evidence"]["leak_detected"] is True
