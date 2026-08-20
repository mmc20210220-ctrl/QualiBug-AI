from __future__ import annotations

from ai_test_asset_center.runtime_implementation_candidate_projection import (
    candidate_read_operations,
    merge_candidate_read_operations,
)


def _candidate(
    *,
    candidate_id: str = "rfc_1",
    method: str = "GET",
    path: str = "/api/runtime-readback",
    status: str = "CANDIDATE",
    kind: str = "runtime_observation_path",
    evidence: bool = True,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "status": status,
        "kind": kind,
        "method": method,
        "path": path,
        "evidence_refs": ["observer_receipt_1"] if evidence else [],
        "source_refs": [
            {
                "source_id": "runtime_observer",
                "locator": path,
                "kind": "runtime_observation",
            }
        ],
    }


def test_receipt_backed_candidate_get_becomes_low_authority_operation() -> None:
    rows = candidate_read_operations({"candidates": [_candidate()]})
    assert len(rows) == 1
    row = rows[0]
    assert row["method"] == "GET"
    assert row["path"] == "/api/runtime-readback"
    assert row["derivation"] == "runtime-fact-candidate"
    assert row["authority_grade"] == "RUNTIME_OBSERVED"
    assert row["read_write"] == "read"


def test_write_candidates_are_never_promoted_to_implementation_surface() -> None:
    ledger = {
        "candidates": [
            _candidate(candidate_id="post", method="POST"),
            _candidate(candidate_id="delete", method="DELETE"),
        ]
    }
    assert candidate_read_operations(ledger) == []


def test_unreceipted_and_non_candidate_rows_are_rejected() -> None:
    ledger = {
        "candidates": [
            _candidate(candidate_id="no-proof", evidence=False),
            _candidate(candidate_id="needs-authority", status="NEEDS_AUTHORITY"),
            _candidate(candidate_id="wrong-kind", kind="runtime_cleanup_capability"),
        ]
    }
    assert candidate_read_operations(ledger) == []


def test_documented_operation_identity_wins_over_runtime_candidate() -> None:
    documented = [
        {
            "id": "documented_op",
            "operation_id": "documented_op",
            "method": "GET",
            "path": "/api/runtime-readback",
            "derivation": "api-spec",
        }
    ]
    merged, receipt = merge_candidate_read_operations(
        documented,
        {"candidates": [_candidate()]},
    )
    assert len(merged) == 1
    assert merged[0]["id"] == "documented_op"
    assert merged[0]["derivation"] == "api-spec"
    assert receipt["added_operation_count"] == 0


def test_new_candidate_read_surface_is_added_without_business_fact_promotion() -> None:
    documented = [
        {"id": "known", "method": "GET", "path": "/api/known"}
    ]
    merged, receipt = merge_candidate_read_operations(
        documented,
        {"candidates": [_candidate()]},
    )
    keys = {(row["method"], row["path"]) for row in merged}
    assert keys == {("GET", "/api/known"), ("GET", "/api/runtime-readback")}
    assert receipt["added_operation_count"] == 1
    assert receipt["write_surface_promoted"] is False
    assert receipt["business_fact_promoted"] is False


def test_duplicate_runtime_candidates_do_not_duplicate_operation_identity() -> None:
    ledger = {
        "candidates": [
            _candidate(candidate_id="rfc_1"),
            _candidate(candidate_id="rfc_2"),
        ]
    }
    rows = candidate_read_operations(ledger)
    assert len(rows) == 1
