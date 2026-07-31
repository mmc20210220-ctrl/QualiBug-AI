from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center import operation_causality_runtime as runtime
from ai_test_asset_center.operation_causality_receipt_integrity import (
    validate_operation_causality_transport_receipt,
)
from ai_test_asset_center.operation_causality_transport_authority import (
    _tighten_receipt,
    install_operation_causality_transport_authority,
)
from tests.test_operation_causality_runtime import (
    _experiment,
    _plan_result,
)


def _prepare() -> tuple[dict, dict]:
    experiment = _experiment()
    observations: dict = {}
    runtime.prepare_operation_causality_preflight(
        exp=experiment,
        runtime_bindings={"request_id": "req-1"},
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )
    return experiment, observations


def test_genuine_governance_receipt_remains_attributed() -> None:
    install_operation_causality_transport_authority()
    experiment, observations = _prepare()

    receipt = runtime.finalize_operation_causality_transport(
        exp=experiment,
        result=_plan_result("req-1"),
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )[0]

    assert receipt["status"] == "ATTRIBUTED"
    assert receipt["reason_code"] == ""
    assert receipt["transport_receipt_id"] == "transport-receipt-1"
    assert receipt["transport_reached"] is True
    assert validate_operation_causality_transport_receipt(receipt) == receipt


def test_request_body_fingerprint_cannot_impersonate_transport_receipt() -> None:
    install_operation_causality_transport_authority()
    experiment, observations = _prepare()
    result = _plan_result("req-1")
    result["steps"][0].pop("governance_receipt")

    receipt = runtime.finalize_operation_causality_transport(
        exp=experiment,
        result=result,
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )[0]

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == (
        "OPERATION_CAUSAL_GOVERNANCE_TRANSPORT_RECEIPT_MISSING"
    )
    assert receipt["transport_receipt_id"] == ""
    assert receipt["transport_reached"] is False
    assert receipt["transport_receipt_id"] != "body-fingerprint"
    assert observations[runtime.TRANSPORT_KEY] == [receipt]
    assert validate_operation_causality_transport_receipt(receipt) == receipt


def test_missing_governance_receipt_overrides_drift_surrogate() -> None:
    install_operation_causality_transport_authority()
    experiment, observations = _prepare()
    result = _plan_result("req-2")
    result["steps"][0].pop("governance_receipt")

    receipt = runtime.finalize_operation_causality_transport(
        exp=experiment,
        result=result,
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )[0]

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == (
        "OPERATION_CAUSAL_GOVERNANCE_TRANSPORT_RECEIPT_MISSING"
    )
    assert receipt["source_value_fingerprint_match"] is False
    assert receipt["transport_receipt_id"] == ""
    assert validate_operation_causality_transport_receipt(receipt) == receipt


def test_claimed_transport_receipt_must_match_governance_receipt() -> None:
    install_operation_causality_transport_authority()
    experiment, observations = _prepare()
    valid = runtime.finalize_operation_causality_transport(
        exp=experiment,
        result=_plan_result("req-1"),
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )[0]
    tampered = deepcopy(valid)
    tampered["transport_receipt_id"] = "surrogate-or-stale-receipt"

    tightened = _tighten_receipt(
        tampered,
        candidates=["transport-receipt-1"],
    )

    assert tightened["status"] == "INDETERMINATE"
    assert tightened["reason_code"] == (
        "OPERATION_CAUSAL_GOVERNANCE_TRANSPORT_RECEIPT_MISMATCH"
    )
    assert tightened["transport_receipt_id"] == "transport-receipt-1"
    assert tightened["transport_reached"] is True
    assert validate_operation_causality_transport_receipt(tightened) == tightened


def test_earlier_ambiguous_step_reason_is_not_overwritten() -> None:
    install_operation_causality_transport_authority()
    experiment, observations = _prepare()
    result = _plan_result("req-1")
    duplicate = deepcopy(result["steps"][0])
    duplicate["step_id"] = "treatment-2"
    duplicate["governance_receipt"] = {"receipt_id": "transport-receipt-2"}
    result["steps"].append(duplicate)

    receipt = runtime.finalize_operation_causality_transport(
        exp=experiment,
        result=result,
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )[0]

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == (
        "OPERATION_CAUSAL_TRANSPORT_STEP_AMBIGUOUS"
    )
    assert receipt["transport_receipt_id"] == ""
    assert receipt["source_value_fingerprint_match"] is False
    assert validate_operation_causality_transport_receipt(receipt) == receipt
