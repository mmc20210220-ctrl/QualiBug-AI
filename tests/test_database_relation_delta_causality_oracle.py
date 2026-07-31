from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.database_relation_delta_causality_integrity import (
    evaluate_database_relation_causal_delta_with_integrity,
)
from ai_test_asset_center.operation_causality_receipt_integrity import (
    seal_operation_causality_transport_receipt,
)
from tests.database_relation_delta_causality_fixtures import (
    build_observations,
    build_spec,
    transport_receipt,
)


def test_causal_scope_allows_existing_delta_violation() -> None:
    spec = build_spec()
    result = evaluate_database_relation_causal_delta_with_integrity(
        {"spec": spec, "observations": build_observations(spec)}
    )

    assert result["passed"] is False
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_CONSERVATION_VIOLATED"
    )
    actual = result["actual"]
    assert actual["root_delta"] == "-15"
    assert actual["relation_delta"] == "10"
    assert actual["transport_scope_match"] is True
    assert actual["causal_value_fingerprint_match"] is True
    assert actual["causal_lineage_match"] is True
    assert actual["causal_scope_semantic_match"] is True
    assert actual["transport_receipt_integrity_valid"] is True
    assert actual["timestamp_window_attribution_used"] is False


def test_transport_and_relation_correlation_mismatch_is_indeterminate() -> None:
    spec = build_spec()
    observations = build_observations(spec)
    observations["operation_causality_transport_receipts"] = [
        transport_receipt(spec, "different-request")
    ]
    result = evaluate_database_relation_causal_delta_with_integrity(
        {"spec": spec, "observations": observations}
    )

    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_CAUSAL_VALUE_FINGERPRINT_MISMATCH"
    )


def test_cross_execution_causality_receipt_is_indeterminate() -> None:
    spec = build_spec()
    observations = build_observations(spec)
    receipt = deepcopy(
        observations["operation_causality_transport_receipts"][0]
    )
    receipt.pop("receipt_id")
    receipt["execution_id"] = "execution-2"
    observations["operation_causality_transport_receipts"] = [
        seal_operation_causality_transport_receipt(receipt)
    ]
    result = evaluate_database_relation_causal_delta_with_integrity(
        {"spec": spec, "observations": observations}
    )

    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_CAUSAL_RECEIPT_LINEAGE_MISMATCH"
    )


def test_timestamp_window_cannot_impersonate_exact_correlation() -> None:
    spec = build_spec()
    observations = build_observations(spec)
    scope = observations["approved_database_relation_phase_receipts"][1][
        "evidence"
    ]["approved_database_relation_aggregate_snapshot"][
        "causal_attribution_scope"
    ]
    scope["timestamp_window_attribution_used"] = True
    result = evaluate_database_relation_causal_delta_with_integrity(
        {"spec": spec, "observations": observations}
    )

    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_CAUSAL_RELATION_SCOPE_MISMATCH"
    )


def test_duplicate_transport_receipts_do_not_select_winner() -> None:
    spec = build_spec()
    observations = build_observations(spec)
    observations["operation_causality_transport_receipts"].append(
        deepcopy(observations["operation_causality_transport_receipts"][0])
    )
    result = evaluate_database_relation_causal_delta_with_integrity(
        {"spec": spec, "observations": observations}
    )

    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_CAUSAL_TRANSPORT_RECEIPT_AMBIGUOUS"
    )
