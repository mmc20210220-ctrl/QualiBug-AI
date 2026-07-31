from __future__ import annotations

from ai_test_asset_center.database_relation_delta_causality_authority import (
    evaluate_database_relation_causal_delta_with_authority,
)
from tests.database_relation_delta_causality_fixtures import (
    build_observations,
    build_spec,
)


def test_approved_relation_decision_authorizes_causal_field() -> None:
    spec = build_spec()
    result = evaluate_database_relation_causal_delta_with_authority(
        {"spec": spec, "observations": build_observations(spec)}
    )

    assert result["passed"] is False
    assert result["reason_code"] == (
        "DATABASE_RELATION_DELTA_CONSERVATION_VIOLATED"
    )
    assert result["actual"]["relation_authority_match"] is True
    assert result["actual"]["automatic_authority_selection_used"] is False
    assert result["actual"]["causal_scope_semantic_match"] is True
    assert result["actual"]["transport_receipt_integrity_valid"] is True


def test_causal_mapping_decision_cannot_drift_from_relation_approval() -> None:
    spec = build_spec()
    spec["database_relation_delta_binding"][
        "causal_mapping_decision_id"
    ] = "decision:unrelated"
    result = evaluate_database_relation_causal_delta_with_authority(
        {"spec": spec, "observations": build_observations(build_spec())}
    )

    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_CAUSAL_RELATION_AUTHORITY_MISMATCH"
    )
    assert result["actual"]["relation_authority_match"] is False
    assert result["actual"]["automatic_authority_selection_used"] is False


def test_relation_mapping_decision_cannot_change_after_compilation() -> None:
    spec = build_spec()
    spec["database_relation_delta_binding"][
        "relation_mapping_decision_id"
    ] = "decision:replacement"
    result = evaluate_database_relation_causal_delta_with_authority(
        {"spec": spec, "observations": build_observations(build_spec())}
    )

    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_CAUSAL_RELATION_AUTHORITY_MISMATCH"
    )
    assert result["expected"]["relation_mapping_decision_id"] == (
        "decision:replacement"
    )
    assert result["actual"]["relation_authority_match"] is False


def test_nonempty_unrelated_decisions_do_not_create_authority() -> None:
    spec = build_spec()
    spec["causal_attribution_contract"][
        "mapping_decision_id"
    ] = "decision:causal-only"
    spec["database_relation_delta_binding"][
        "causal_mapping_decision_id"
    ] = "decision:causal-only"
    result = evaluate_database_relation_causal_delta_with_authority(
        {"spec": spec, "observations": build_observations(build_spec())}
    )

    assert result["passed"] is None
    assert result["reason_code"] == (
        "DATABASE_RELATION_CAUSAL_RELATION_AUTHORITY_MISMATCH"
    )
    assert result["expected"]["causal_mapping_decision_id"] == (
        "decision:causal-only"
    )
    assert result["expected"]["relation_mapping_decision_id"] == (
        "decision:relation"
    )
