from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.database_relation_delta_causality_projection import (
    ASSERTION_KIND,
)
from ai_test_asset_center.operation_causality_runtime import (
    PREFLIGHT_KEY,
    TRANSPORT_KEY,
    finalize_operation_causality_transport,
    prepare_operation_causality_preflight,
)


def _experiment() -> dict:
    return {
        "assertions": [
            {
                "assertion_id": "assert:causal-ledger",
                "kind": ASSERTION_KIND,
                "causal_attribution_contract": {
                    "status": "BOUND",
                    "causal_scope_fingerprint": "causal-scope-1",
                    "operation_ref": "api:POST:/ledger",
                    "treatment_step_id": "treatment-1",
                    "value_source": "request.body.request_id",
                },
            }
        ],
        "treatment_plan": [
            {
                "step_id": "treatment-1",
                "operation_ref": "api:POST:/ledger",
                "body": {
                    "request_id": "{request_id}",
                    "amount": 10,
                },
            }
        ],
    }


def _plan_result(request_id: str) -> dict:
    return {
        "request_bodies_for_cleanup": {
            "treatment-1": {"request_id": request_id, "amount": 10}
        },
        "steps": [
            {
                "phase": "treatment",
                "step_id": "treatment-1",
                "operation_ref": "api:POST:/ledger",
                "status_code": 201,
                "request_body_fingerprint": "body-fingerprint",
                "request_semantics_fingerprint": "semantics-fingerprint",
                "governance_receipt": {"receipt_id": "transport-receipt-1"},
            }
        ],
    }


def test_actual_transport_value_matches_preflight() -> None:
    observations: dict = {}
    exp = _experiment()
    prepare_operation_causality_preflight(
        exp=exp,
        runtime_bindings={"request_id": "req-1"},
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )
    receipts = finalize_operation_causality_transport(
        exp=exp,
        result=_plan_result("req-1"),
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )

    assert observations[PREFLIGHT_KEY][0]["status"] == "BOUND"
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["status"] == "ATTRIBUTED"
    assert receipt["reason_code"] == ""
    assert receipt["source_value_fingerprint_match"] is True
    assert receipt["transport_receipt_id"] == "transport-receipt-1"
    assert receipt["request_semantics_fingerprint"] == "semantics-fingerprint"
    assert receipt["raw_causal_value_retained"] is False
    assert observations[TRANSPORT_KEY] == receipts


def test_materialized_request_drift_is_indeterminate() -> None:
    observations: dict = {}
    exp = _experiment()
    prepare_operation_causality_preflight(
        exp=exp,
        runtime_bindings={"request_id": "req-1"},
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )
    receipts = finalize_operation_causality_transport(
        exp=exp,
        result=_plan_result("req-2"),
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )

    receipt = receipts[0]
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "OPERATION_CAUSAL_SOURCE_VALUE_DRIFT"
    assert receipt["source_value_fingerprint_match"] is False
    assert receipt["preflight_value_fingerprint"] != (
        receipt["transport_value_fingerprint"]
    )


def test_duplicate_transport_steps_do_not_select_winner() -> None:
    observations: dict = {}
    exp = _experiment()
    prepare_operation_causality_preflight(
        exp=exp,
        runtime_bindings={"request_id": "req-1"},
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )
    result = _plan_result("req-1")
    duplicate = deepcopy(result["steps"][0])
    duplicate["step_id"] = "treatment-2"
    duplicate["governance_receipt"] = {"receipt_id": "transport-receipt-2"}
    result["steps"].append(duplicate)

    receipt = finalize_operation_causality_transport(
        exp=exp,
        result=result,
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )[0]

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == (
        "OPERATION_CAUSAL_TRANSPORT_STEP_AMBIGUOUS"
    )
    assert receipt["source_value_fingerprint_match"] is False
