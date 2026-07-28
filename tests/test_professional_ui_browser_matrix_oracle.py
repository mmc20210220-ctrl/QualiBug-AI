from __future__ import annotations

import json

from ai_test_asset_center import discovery_runtime_semantic_binding as _runtime  # noqa: F401
from ai_test_asset_center import formal_ui_surface as formal
from ai_test_asset_center import observer_contracts_base as observers
from ai_test_asset_center.professional_ui_browser_matrix_verdict_guard import (
    _apply_matrix_verdict_to_receipt,
    _rebuild_receipt,
    _sanitized_matrix_for_result,
)
from ai_test_asset_center.enterprise_knowledge_center._formal_ui_browser_matrix_guard import (
    AGGREGATION_POLICY,
    SCHEMA_VERSION,
)


def _base_receipt(matrix_receipt: dict[str, object]) -> dict[str, object]:
    return observers._receipt(
        observer_id=formal.OBSERVER_ID,
        status="INDETERMINATE",
        reason_code="UI_EXPECTATION_RESULT_UNPROVEN",
        evidence={
            formal.EVIDENCE_KEY: {
                "expectation_satisfied": None,
                "violation_observed": False,
                "browser_matrix": matrix_receipt,
            }
        },
    )


def test_typed_profile_failure_is_rebound_to_formal_ui_violation() -> None:
    receipt = _base_receipt({
        "schema_version": SCHEMA_VERSION,
        "aggregation_policy": AGGREGATION_POLICY,
        "status": "VIOLATION_OBSERVED",
        "profiles": [
            {
                "profile_id": "firefox-desktop",
                "browser_engine": "firefox",
                "status": "failed",
                "typed_failure_action": "expect_visible",
                "typed_failure_code": "target_missing",
            }
        ],
    })

    applied = _apply_matrix_verdict_to_receipt(receipt)
    rebuilt = _rebuild_receipt(applied)
    evidence = rebuilt["evidence"][formal.EVIDENCE_KEY]

    assert rebuilt["status"] == "OBSERVED"
    assert rebuilt["reason_code"] == ""
    assert evidence["expectation_satisfied"] is False
    assert evidence["violation_observed"] is True
    assert evidence["failed_expectation"] == {
        "action": "expect_visible",
        "matrix_profile_id": "firefox-desktop",
        "browser_engine": "firefox",
    }
    assert evidence["failure_type"] == "target_missing"
    assert observers.validate_observer_receipt(rebuilt) == rebuilt


def test_profile_runtime_failure_remains_indeterminate() -> None:
    receipt = _base_receipt({
        "schema_version": SCHEMA_VERSION,
        "aggregation_policy": AGGREGATION_POLICY,
        "status": "PROFILE_EXECUTION_FAILED",
        "profiles": [
            {
                "profile_id": "webkit-mobile",
                "browser_engine": "webkit",
                "status": "failed",
                "typed_failure_action": "",
                "typed_failure_code": "",
            }
        ],
    })

    rebuilt = _rebuild_receipt(_apply_matrix_verdict_to_receipt(receipt))
    evidence = rebuilt["evidence"][formal.EVIDENCE_KEY]

    assert rebuilt["status"] == "INDETERMINATE"
    assert rebuilt["reason_code"] == "UI_BROWSER_MATRIX_PROFILE_RUNTIME_FAILED"
    assert evidence["expectation_satisfied"] is None
    assert evidence["violation_observed"] is False
    assert observers.validate_observer_receipt(rebuilt) == rebuilt


def test_all_profiles_executed_is_required_for_property_held() -> None:
    receipt = _base_receipt({
        "schema_version": SCHEMA_VERSION,
        "aggregation_policy": AGGREGATION_POLICY,
        "status": "ALL_PROFILES_EXECUTED",
        "all_profiles_executed": True,
        "profiles": [],
    })

    rebuilt = _rebuild_receipt(_apply_matrix_verdict_to_receipt(receipt))
    evidence = rebuilt["evidence"][formal.EVIDENCE_KEY]

    assert rebuilt["status"] == "OBSERVED"
    assert rebuilt["reason_code"] == ""
    assert evidence["expectation_satisfied"] is True
    assert evidence["violation_observed"] is False


def test_execution_projection_fingerprints_raw_user_agent() -> None:
    raw_user_agent = "QualiBug private customer browser identity/1.0"
    sanitized = _sanitized_matrix_for_result({
        "schema_version": SCHEMA_VERSION,
        "aggregation_policy": AGGREGATION_POLICY,
        "profiles": [
            {
                "profile_id": "webkit-mobile",
                "browser_engine": "webkit",
                "device_class": "mobile",
                "user_agent": raw_user_agent,
            }
        ],
    })

    serialized = json.dumps(sanitized, ensure_ascii=False)
    assert raw_user_agent not in serialized
    assert sanitized["profiles"][0]["user_agent_fingerprint"]
    assert sanitized["raw_user_agent_in_execution_result"] is False
