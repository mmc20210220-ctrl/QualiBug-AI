from __future__ import annotations

from ai_test_asset_center import process_graph_cleanup_equivalence as cleanup_eq


def _execution_set(*, restored: bool, final_status: str) -> dict:
    return {
        "environment_restoration_receipt": {
            "schema_version": "qualibug.environment-restoration-receipt.v1",
            "receipt_id": "env-1",
            "created_rows_remaining": 17,
            "modified_rows_not_restored": 4,
            "deleted_rows_not_restored": 2,
            "cleanup_failures": [
                {
                    "reason": "process_graph_cleanup_not_equivalent",
                }
            ],
            "environment_restored": restored,
            "final_status": final_status,
        }
    }


def test_pending_graph_environment_never_claims_measured_residue() -> None:
    execution = _execution_set(
        restored=False,
        final_status="PENDING_EQUIVALENCE",
    )

    cleanup_eq._seal_environment_truth(execution)

    receipt = execution["environment_restoration_receipt"]
    assert receipt["created_rows_remaining"] == 0
    assert receipt["modified_rows_not_restored"] == 0
    assert receipt["deleted_rows_not_restored"] == 0
    assert receipt["residual_counts_measured"] is False
    assert receipt["restoration_basis"] == (
        "pending_per_source_step_equivalence"
    )
    assert receipt["environment_restored"] is False
    assert receipt["final_status"] == "PENDING_EQUIVALENCE"


def test_dirty_graph_preserves_verdict_without_inventing_cardinality() -> None:
    execution = _execution_set(
        restored=False,
        final_status="ENVIRONMENT_DIRTY",
    )

    cleanup_eq._seal_environment_truth(
        execution,
        equivalence_status="NOT_EQUIVALENT",
    )

    receipt = execution["environment_restoration_receipt"]
    assert receipt["residual_counts_measured"] is False
    assert receipt["created_rows_remaining"] == 0
    assert receipt["environment_restored"] is False
    assert receipt["final_status"] == "ENVIRONMENT_DIRTY"
    assert receipt["restoration_basis"] == (
        "per_source_step_cleanup_equivalence"
    )
    assert receipt["cleanup_failures"][0][
        "residual_counts_measured"
    ] is False
