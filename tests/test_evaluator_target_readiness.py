from __future__ import annotations

import pytest

from ai_test_asset_center.evaluator_target_readiness import (
    EvaluatorTargetReadinessError,
    assess_serial_target_admission,
    build_target_readiness_receipt,
    validate_target_transition,
)


def _receipt(target_id: str, state: str) -> dict:
    return {"target_id": target_id, "state": state}


def test_serial_admission_uses_latest_receipt_for_each_target() -> None:
    receipts = [
        _receipt("benchmark-mall-131", "RUNTIME_READY"),
        _receipt("benchmark-mall-131", "STOPPED_CLEAN"),
    ]
    decision = assess_serial_target_admission(receipts, "openproject-17.6.0")
    assert decision["allowed"] is True
    assert decision["blocking_codes"] == []


def test_serial_admission_blocks_another_non_stopped_target() -> None:
    decision = assess_serial_target_admission(
        [_receipt("benchmark-mall-131", "RUNTIME_READY")],
        "openproject-17.6.0",
    )
    assert decision["allowed"] is False
    assert decision["blocking_codes"] == ["BLOCKED_ANOTHER_TARGET_ACTIVE"]
    assert decision["active_target_ids"] == ["benchmark-mall-131"]


def test_failed_target_still_requires_stopped_clean_receipt() -> None:
    decision = assess_serial_target_admission(
        [_receipt("benchmark-mall-131", "FAILED_SAFE")],
        "openproject-17.6.0",
    )
    assert decision["allowed"] is False


def test_transition_rejects_skipping_runtime_ready() -> None:
    with pytest.raises(EvaluatorTargetReadinessError, match="invalid target transition"):
        validate_target_transition("DEPLOYABLE", "EVALUATOR_READY")


def test_first_receipt_may_enter_asset_valid() -> None:
    validate_target_transition("NOT_STARTED", "ASSET_VALID")


def test_runtime_ready_requires_all_runtime_checks() -> None:
    with pytest.raises(EvaluatorTargetReadinessError, match="missing required checks"):
        build_target_readiness_receipt(
            target_id="openproject-17.6.0",
            target_role="held_out_candidate",
            state="RUNTIME_READY",
            previous_state="DEPLOYABLE",
            environment_type="sandbox",
            environment_ref="openproject-local-sandbox",
            requested_base_url="http://127.0.0.1:18080",
            approved_base_url="http://127.0.0.1:18080",
            checks={"health": "passed"},
            fingerprints={"source_sha256": "a" * 64},
        )


def test_receipt_is_not_measurement_and_has_immutable_fingerprint() -> None:
    checks = {
        "health": "passed",
        "login": "passed",
        "api": "passed",
        "database_observation": "passed",
        "fixture_prepare": "passed",
        "fixture_cleanup": "passed",
    }
    receipt = build_target_readiness_receipt(
        target_id="openproject-17.6.0",
        target_role="held_out_candidate",
        state="RUNTIME_READY",
        previous_state="DEPLOYABLE",
        environment_type="sandbox",
        environment_ref="openproject-local-sandbox",
        requested_base_url="http://127.0.0.1:18080",
        approved_base_url="http://127.0.0.1:18080",
        checks=checks,
        fingerprints={"source_sha256": "a" * 64},
    )
    assert receipt["schema_version"] == "qualibug.evaluator-target-readiness.v1"
    assert receipt["measurement_status"] == "NOT_MEASURED"
    assert receipt["commercial_promotion_evidence"] is False
    assert receipt["target_policy_decision"]["write_allowed"] is True
    assert receipt["receipt_fingerprint"].startswith("sha256:")
