from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.database_relation_delta_causality_integrity import (
    evaluate_database_relation_causal_delta_with_integrity,
    observe_operation_causality_with_integrity,
)
from tests.database_relation_delta_causality_fixtures import (
    build_observations,
    build_spec,
)


def test_content_addressed_causality_evidence_reaches_delta_violation() -> None:
    spec = build_spec()
    result = evaluate_database_relation_causal_delta_with_integrity(
        {"spec": spec, "observations": build_observations(spec)}
    )

    assert result["passed"] is False
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_CONSERVATION_VIOLATED"
    )
    assert result["actual"]["causal_scope_semantic_match"] is True
    assert result["actual"]["transport_receipt_integrity_valid"] is True


def test_causal_semantic_change_cannot_reuse_old_scope() -> None:
    spec = build_spec()
    spec["causal_attribution_contract"]["operation_ref"] = (
        "api:POST:/other-ledger"
    )
    result = evaluate_database_relation_causal_delta_with_integrity(
        {"spec": spec, "observations": build_observations(build_spec())}
    )

    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_CAUSAL_SCOPE_SEMANTIC_MISMATCH"
    )
    assert result["actual"]["causal_scope_semantic_match"] is False


def test_transport_receipt_mutation_breaks_content_integrity() -> None:
    spec = build_spec()
    observations = build_observations(spec)
    observations["operation_causality_transport_receipts"][0][
        "status_code"
    ] = 202
    result = evaluate_database_relation_causal_delta_with_integrity(
        {"spec": spec, "observations": observations}
    )

    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_CAUSAL_TRANSPORT_RECEIPT_INTEGRITY_INVALID"
    )
    assert result["actual"]["transport_receipt_integrity_valid"] is False


def test_causality_observer_rejects_receipt_with_raw_extra_field() -> None:
    spec = build_spec()
    observations = build_observations(spec)
    observations["operation_causality_transport_receipts"][0][
        "raw_value"
    ] = "req-1"
    receipt = observe_operation_causality_with_integrity(
        {
            "observations": observations,
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
        }
    )

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == (
        "OPERATION_CAUSALITY_TRANSPORT_RECEIPT_INVALID"
    )
    assert receipt["evidence"]["validated_receipt_count"] == 0
    assert receipt["evidence"]["integrity_failure_count"] == 1
    assert receipt["evidence"]["oracle_verdict_emitted"] is False


def test_duplicate_valid_transport_receipts_do_not_select_winner() -> None:
    spec = build_spec()
    observations = build_observations(spec)
    duplicate = deepcopy(observations["operation_causality_transport_receipts"][0])
    observations["operation_causality_transport_receipts"].append(duplicate)
    result = evaluate_database_relation_causal_delta_with_integrity(
        {"spec": spec, "observations": observations}
    )

    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_CAUSAL_TRANSPORT_RECEIPT_AMBIGUOUS"
    )
