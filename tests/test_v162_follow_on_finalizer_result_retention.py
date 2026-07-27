"""Follow-on Finalizer outcomes must be retained in experiment_execution.results."""
from __future__ import annotations

from ai_test_asset_center.discovery_runtime_execution_support import (
    _merge_experiment_execution_results,
    _sum_batch_int,
)


def test_merge_experiment_execution_results_keeps_follow_on_finalizer_rows():
    primary = {
        "results": [
            {
                "obligation_id": "obl_primary",
                "execution_id": "exec_primary",
                "lifecycle_state": "TRUE_COMPLETED",
                "cleanup_equivalence_receipt": {"equivalence_status": "EQUIVALENT"},
                "execution_finalization_receipt": {"derived_terminal_status": "TRUE_COMPLETED"},
            }
        ]
    }
    follow_on = {
        "results": [
            {
                "obligation_id": "obl_follow_on",
                "execution_id": "exec_follow_on",
                "lifecycle_state": "TRUE_COMPLETED",
                "cleanup_equivalence_receipt": {"equivalence_status": "EQUIVALENT"},
                "execution_finalization_receipt": {"derived_terminal_status": "TRUE_COMPLETED"},
                "finding_filter_reason": "oracle_property_held",
            }
        ]
    }
    surface = {
        "results": [
            {
                "obligation_id": "obl_surface",
                "execution_id": "exec_surface",
                "status": "EXECUTED",
            }
        ]
    }
    merged = _merge_experiment_execution_results(primary, follow_on, surface)
    assert [row["obligation_id"] for row in merged] == [
        "obl_primary",
        "obl_follow_on",
        "obl_surface",
    ]
    follow = next(row for row in merged if row["obligation_id"] == "obl_follow_on")
    assert follow["lifecycle_state"] == "TRUE_COMPLETED"
    assert follow["cleanup_equivalence_receipt"]["equivalence_status"] == "EQUIVALENT"
    assert follow["execution_finalization_receipt"]["derived_terminal_status"] == (
        "TRUE_COMPLETED"
    )
    assert follow["finding_filter_reason"] == "oracle_property_held"


def test_merge_experiment_execution_results_dedupes_by_execution_id():
    first = {
        "results": [
            {"obligation_id": "obl_a", "execution_id": "exec_a", "lifecycle_state": "TRUE_COMPLETED"}
        ]
    }
    duplicate = {
        "results": [
            {"obligation_id": "obl_a", "execution_id": "exec_a", "lifecycle_state": "RECEIPT_INCOMPLETE"}
        ]
    }
    merged = _merge_experiment_execution_results(first, duplicate)
    assert len(merged) == 1
    assert merged[0]["lifecycle_state"] == "TRUE_COMPLETED"


def test_sum_batch_int_includes_follow_on_counters():
    batches = [
        {"blocked_count": 2, "cleanup_failures": 1},
        {"blocked_count": 3, "cleanup_failures": 0},
        {"blocked_count": 1, "cleanup_failures": 2},
    ]
    assert _sum_batch_int(batches, "blocked_count") == 6
    assert _sum_batch_int(batches, "cleanup_failures") == 3
