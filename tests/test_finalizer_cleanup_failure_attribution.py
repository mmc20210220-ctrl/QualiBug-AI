from __future__ import annotations

from ai_test_asset_center import experiment_outcome_finalizer_core as finalizer


def test_cleanup_transport_failure_requires_transport_evidence() -> None:
    observations = {
        "cleanup_execution_receipt": {
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "status": "FAILED",
            "attempted": True,
            "transport_reached": False,
            "status_code": 0,
            "reason_code": "CLEANUP_CONNECTION_FAILED",
        }
    }
    assert finalizer._classify_harness_failure(
        [], observations, [], cleanup_failures=1
    ) == "HARNESS_CLEANUP_TRANSPORT_FAILED"


def test_cleanup_response_rejection_is_not_transport_failure() -> None:
    observations = {
        "cleanup_execution_receipt": {
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "status": "FAILED",
            "attempted": True,
            "transport_reached": True,
            "status_code": 409,
            "reason_code": "CLEANUP_RESPONSE_REJECTED",
        }
    }
    assert finalizer._classify_harness_failure(
        [], observations, [], cleanup_failures=1
    ) == "HARNESS_CLEANUP_RESPONSE_REJECTED"


def test_cleanup_equivalence_failure_has_its_own_attribution() -> None:
    observations = {
        "cleanup_equivalence_receipt": {
            "equivalence_status": "NOT_EQUIVALENT",
            "reason_code": "ENTITY_STILL_PRESENT_AFTER_CLEANUP",
        },
        "cleanup_execution_receipt": {
            "schema_version": "qualibug.cleanup-execution-receipt.v1",
            "status": "ACCEPTED",
            "attempted": True,
            "transport_reached": True,
            "status_code": 200,
        },
    }
    assert finalizer._classify_harness_failure(
        [], observations, [], cleanup_failures=1
    ) == "HARNESS_CLEANUP_EQUIVALENCE_FAILED"


def test_unattributed_cleanup_failure_is_never_invented_as_transport() -> None:
    assert finalizer._classify_harness_failure(
        [], {}, [], cleanup_failures=1
    ) == finalizer.HARNESS_CLEANUP_FAILURE_UNATTRIBUTED
    assert finalizer.HARNESS_CLEANUP_FAILURE_UNATTRIBUTED in (
        finalizer.HARNESS_FAILURE_SUBTYPES
    )


def test_non_cleanup_harness_classification_delegates_existing_behavior() -> None:
    assert finalizer._classify_harness_failure(
        [], {"harness_error": True}, [], cleanup_failures=0
    ) == "HARNESS_REQUEST_BUILD_FAILED"
