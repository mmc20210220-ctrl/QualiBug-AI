from __future__ import annotations

import pytest

from ai_test_asset_center.operation_causality_receipt_integrity import (
    validate_operation_causality_transport_receipt,
)
from ai_test_asset_center.operation_causality_runtime import (
    finalize_operation_causality_transport,
    prepare_operation_causality_preflight,
)
from tests.test_operation_causality_runtime import (
    _experiment,
    _plan_result,
)


def test_runtime_generated_transport_receipt_is_content_addressed() -> None:
    exp = _experiment()
    observations: dict = {}
    prepare_operation_causality_preflight(
        exp=exp,
        runtime_bindings={"request_id": "req-1"},
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )
    receipt = finalize_operation_causality_transport(
        exp=exp,
        result=_plan_result("req-1"),
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )[0]

    assert validate_operation_causality_transport_receipt(receipt) == receipt


def test_runtime_receipt_mutation_is_rejected() -> None:
    exp = _experiment()
    observations: dict = {}
    prepare_operation_causality_preflight(
        exp=exp,
        runtime_bindings={"request_id": "req-1"},
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )
    receipt = finalize_operation_causality_transport(
        exp=exp,
        result=_plan_result("req-1"),
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )[0]
    receipt["request_semantics_fingerprint"] = "tampered"

    with pytest.raises(
        ValueError,
        match="operation_causality_transport_receipt_fingerprint_invalid",
    ):
        validate_operation_causality_transport_receipt(receipt)
