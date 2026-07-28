from __future__ import annotations

from ai_test_asset_center import discovery_runtime_semantic_binding as _runtime  # noqa: F401
from ai_test_asset_center.formal_ui_surface import (
    EVIDENCE_KEY,
    OBSERVER_ID,
    RISK_FAMILY,
)
from ai_test_asset_center.observer_contracts_base import _receipt
from ai_test_asset_center.professional_ui_coverage_projection import (
    build_professional_ui_coverage,
)
from ai_test_asset_center.enterprise_knowledge_center._formal_ui_browser_matrix_guard import (
    AGGREGATION_POLICY,
    SCHEMA_VERSION,
)


def _matrix() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "aggregation_policy": AGGREGATION_POLICY,
        "profiles": [
            {
                "profile_id": "chromium-desktop",
                "browser_engine": "chromium",
                "device_class": "desktop",
                "viewport_width": 1280,
                "viewport_height": 720,
                "device_scale_factor": 1,
                "is_mobile": False,
                "has_touch": False,
                "locale": "zh-CN",
                "timezone_id": "Asia/Shanghai",
                "color_scheme": "light",
                "reduced_motion": "no-preference",
                "user_agent": "",
            },
            {
                "profile_id": "webkit-mobile",
                "browser_engine": "webkit",
                "device_class": "mobile",
                "viewport_width": 390,
                "viewport_height": 844,
                "device_scale_factor": 3,
                "is_mobile": True,
                "has_touch": True,
                "locale": "zh-CN",
                "timezone_id": "Asia/Shanghai",
                "color_scheme": "dark",
                "reduced_motion": "reduce",
                "user_agent": "",
            },
        ],
    }


def _matrix_receipt() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "aggregation_policy": AGGREGATION_POLICY,
        "status": "ALL_PROFILES_EXECUTED",
        "profile_count": 2,
        "executed_profile_count": 2,
        "failed_profile_count": 0,
        "blocked_profile_count": 0,
        "all_profiles_executed": True,
        "typed_violation_profile_count": 0,
        "runtime_failure_profile_count": 0,
        "violation_observed": False,
        "profiles": [
            {
                "profile_id": "chromium-desktop",
                "browser_engine": "chromium",
                "device_class": "desktop",
                "status": "executed",
            },
            {
                "profile_id": "webkit-mobile",
                "browser_engine": "webkit",
                "device_class": "mobile",
                "status": "executed",
            },
        ],
    }


def test_coverage_projects_declared_and_observed_matrix_profiles() -> None:
    observer = _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        evidence={
            EVIDENCE_KEY: {
                "expectation_satisfied": True,
                "browser_matrix": _matrix_receipt(),
            }
        },
    )
    result = {
        "test_obligations": {
            "obligations": [
                {
                    "obligation_id": "obl-matrix",
                    "risk_family": RISK_FAMILY,
                    "property": {
                        "ui_request": {
                            "browser_matrix": _matrix(),
                            "browser_plan": {
                                "steps": [
                                    {"action": "goto", "url": "/orders"},
                                    {
                                        "action": "expect_visible",
                                        "selector": "#orders",
                                    },
                                ]
                            },
                        }
                    },
                }
            ]
        },
        "experiment_execution": {
            "results": {
                "obl-matrix": {
                    "obligation_id": "obl-matrix",
                    "observer_receipts": [observer],
                }
            }
        },
        "obligation_attempt_ledger": {"attempts": []},
    }

    coverage = build_professional_ui_coverage(result)
    matrix = coverage["browser_device_matrix"]

    assert matrix["declared_matrix_contract_count"] == 1
    assert matrix["declared_profile_count"] == 2
    assert matrix["declared_engine_profile_counts"] == {
        "chromium": 1,
        "webkit": 1,
    }
    assert matrix["declared_device_class_profile_counts"] == {
        "desktop": 1,
        "mobile": 1,
    }
    assert matrix["declared_mobile_profile_count"] == 1
    assert matrix["declared_touch_profile_count"] == 1
    assert matrix["observed_matrix_receipt_count"] == 1
    assert matrix["observed_profile_count"] == 2
    assert matrix["all_profiles_executed_matrix_count"] == 1
    assert matrix["declared_profiles_without_observation_count"] == 0


def test_capability_boundary_is_precise_not_overclaimed() -> None:
    coverage = build_professional_ui_coverage({})
    boundary = coverage["capability_boundary"]

    assert boundary["cross_browser_matrix_supported"] is True
    assert boundary["cross_browser_matrix_engines"] == [
        "chromium",
        "firefox",
        "webkit",
    ]
    assert boundary["device_profile_matrix_supported"] is True
    assert boundary["matrix_property_held_requires_all_profiles"] is True
    assert boundary["matrix_runtime_failure_is_violation"] is False
    assert boundary["matrix_bundled_browser_engines_required"] is True
    assert boundary["matrix_system_browser_fallback_supported"] is False
    assert boundary["cross_browser_interactive_matrix_supported"] is False
    assert boundary["cross_browser_visual_baseline_supported"] is False
