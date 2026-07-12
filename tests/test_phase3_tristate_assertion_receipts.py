from __future__ import annotations

import copy

import pytest

from ai_test_asset_center.assertion_dsl import (
    ASSERTION_RECEIPT_SCHEMA,
    evaluate_assertion,
    evaluate_assertions,
    validate_assertion_receipt,
)
from ai_test_asset_center.observer_contracts import build_observer_receipt


SOURCE_REFS = [
    {
        "kind": "api_contract",
        "source_id": "orders-api",
        "locator": "GET /orders/{orderId}",
    }
]


def _http_receipt(status: str = "OBSERVED") -> dict:
    return build_observer_receipt(
        observer_id="http_response",
        status=status,
        reason_code=("" if status == "OBSERVED" else "RESPONSE_UNAVAILABLE"),
        evidence={"status_code": 403},
    )


def test_assertion_receipt_distinguishes_pass_violation_and_indeterminate() -> None:
    observer = _http_receipt()
    passed = evaluate_assertion(
        {"assertion_id": "a-status", "kind": "http_status", "expected": 403},
        observations={"status_code": 403, "observer_receipts": [observer]},
        source_refs=SOURCE_REFS,
    )
    violation = evaluate_assertion(
        {"assertion_id": "a-status", "kind": "http_status", "expected": 403},
        observations={"status_code": 200, "observer_receipts": [observer]},
        source_refs=SOURCE_REFS,
    )
    indeterminate = evaluate_assertion(
        {
            "assertion_id": "a-conservation",
            "kind": "conservation",
            "equation": {"operator": "unchanged_sum", "terms": ["total"]},
        },
        observations={
            "before_values": {"total": 10},
            "observer_receipts": [observer],
        },
        source_refs=SOURCE_REFS,
    )

    assert passed["schema_version"] == ASSERTION_RECEIPT_SCHEMA
    assert passed["status"] == "PASS"
    assert passed["passed"] is True
    assert violation["status"] == "VIOLATION"
    assert violation["passed"] is False
    assert indeterminate["status"] == "INDETERMINATE"
    assert indeterminate["passed"] is None
    assert indeterminate["reason_code"] == "CONSERVATION_VALUES_MISSING"
    assert passed["observer_receipt_ids"] == [observer["receipt_id"]]
    assert passed["source_refs"] == SOURCE_REFS
    assert passed["expected"] == 403
    assert passed["actual"] == 403


def test_non_observed_typed_observer_cannot_become_violation() -> None:
    observer = _http_receipt("INDETERMINATE")
    result = evaluate_assertion(
        {"assertion_id": "a-status", "kind": "http_status", "expected": 403},
        observations={"status_code": 200, "observer_receipts": [observer]},
        source_refs=SOURCE_REFS,
    )

    assert result["status"] == "INDETERMINATE"
    assert result["passed"] is None
    assert result["reason_code"] == "OBSERVER_EVIDENCE_INDETERMINATE"
    assert result["harness_error"] is False


def test_assertion_rejects_observer_receipt_from_another_execution() -> None:
    observer = build_observer_receipt(
        observer_id="http_response",
        status="OBSERVED",
        evidence={"status_code": 200},
        campaign_id="campaign-1",
        execution_id="execution-other",
    )

    result = evaluate_assertion(
        {"assertion_id": "status", "kind": "http_status", "expected": 403},
        observations={"status_code": 200, "observer_receipts": [observer]},
        campaign_id="campaign-1",
        execution_id="execution-1",
    )

    assert result["status"] == "INDETERMINATE"
    assert result["reason_code"] == "OBSERVER_RECEIPT_LINEAGE_MISMATCH"
    assert result["harness_error"] is True


def test_invalid_assertion_spec_is_visible_harness_indeterminate() -> None:
    result = evaluate_assertion(
        {
            "assertion_id": "a-operator",
            "kind": "json_path_compare",
            "path": "$.amount",
            "operator": "exec",
            "expected": 10,
        },
        observations={
            "body": {"amount": 11},
            "observer_receipts": [_http_receipt()],
        },
        source_refs=SOURCE_REFS,
    )

    assert result["status"] == "INDETERMINATE"
    assert result["passed"] is None
    assert result["harness_error"] is True
    assert result["reason_code"] == "ASSERTION_EVALUATION_ERROR"
    assert "unsupported_operator" in result["error"]


@pytest.mark.parametrize(
    ("spec", "observations"),
    [
        (
            {
                "assertion_id": "bad-json-op",
                "kind": "json_path_compare",
                "path": "$.missing",
                "operator": "exec",
                "expected": 10,
            },
            {"body": {}},
        ),
        (
            {
                "assertion_id": "bad-conservation-op",
                "kind": "conservation",
                "equation": {"operator": "multiply"},
            },
            {},
        ),
    ],
)
def test_invalid_spec_never_hides_behind_missing_evidence(
    spec: dict,
    observations: dict,
) -> None:
    result = evaluate_assertion(
        spec,
        observations={
            **observations,
            "observer_receipts": [_http_receipt()],
        },
        source_refs=SOURCE_REFS,
    )

    assert result["status"] == "INDETERMINATE"
    assert result["harness_error"] is True
    assert result["reason_code"] == "ASSERTION_EVALUATION_ERROR"


def test_assertion_receipt_is_deterministic_and_tamper_evident() -> None:
    spec = {"assertion_id": "a-status", "kind": "http_status", "expected": 403}
    observations = {
        "status_code": 403,
        "observer_receipts": [_http_receipt()],
    }
    first = evaluate_assertion(spec, observations=observations, source_refs=SOURCE_REFS)
    second = evaluate_assertion(spec, observations=observations, source_refs=SOURCE_REFS)
    assert first == second
    assert validate_assertion_receipt(first) == first

    tampered = copy.deepcopy(first)
    tampered["actual"] = 200
    with pytest.raises(ValueError, match="assertion_receipt_fingerprint_invalid"):
        validate_assertion_receipt(tampered)


def test_assertion_batch_counts_keep_violations_and_indeterminate_separate() -> None:
    observed = _http_receipt()
    summary = evaluate_assertions(
        [
            {
                "assertion_id": "pass",
                "observer_id": "pass",
                "kind": "http_status",
                "expected": 200,
                "source_refs": SOURCE_REFS,
            },
            {
                "assertion_id": "violate",
                "observer_id": "violate",
                "kind": "http_status",
                "expected": 403,
                "source_refs": SOURCE_REFS,
            },
            {
                "assertion_id": "unknown",
                "observer_id": "unknown",
                "kind": "idempotency_effect",
                "source_refs": SOURCE_REFS,
            },
        ],
        observations_by_id={
            "pass": {"status_code": 200, "observer_receipts": [observed]},
            "violate": {"status_code": 200, "observer_receipts": [observed]},
            "unknown": {"http_statuses": [201, 201], "observer_receipts": [observed]},
        },
    )

    assert summary["total"] == 3
    assert summary["passed"] == 1
    assert summary["violations"] == 1
    assert summary["indeterminate"] == 1
    assert summary["failed"] == 1
