from ai_test_asset_center.regression_asset_library import (
    build_regression_asset,
    build_regression_asset_library,
    build_regression_asset_report,
    compare_regression_result,
)


def test_build_regression_asset_links_confirmed_violation_to_behavior():
    asset = build_regression_asset(
        {
            "regression_asset_id": "REG-ORDER-001",
            "violation_id": "VIO-ORDER-001",
            "confirmed": True,
            "behavior_id": "BEH-ORDER-CREATE",
            "behavior_name": "Create Order",
            "evidence_id": "EVID-1",
            "request": {"method": "POST", "path": "/orders"},
            "reproduction_steps": ["submit order request"],
        }
    )

    assert asset["asset_id"] == "REG-ORDER-001"
    assert asset["source_violation"] == {
        "violation_id": "VIO-ORDER-001",
        "confirmed": True,
    }
    assert asset["behavior"] == {
        "behavior_id": "BEH-ORDER-CREATE",
        "behavior_name": "Create Order",
    }
    assert asset["evidence_linkage"]["evidence_ids"] == ["EVID-1"]
    assert asset["replay_input"]["request"] == {"method": "POST", "path": "/orders"}
    assert asset["status"] == "ready"


def test_build_regression_asset_uses_stable_fallback_ids():
    asset = build_regression_asset({}, index=7)

    assert asset["asset_id"] == "REG-0007"
    assert asset["source_violation"]["violation_id"] == "VIO-0007"
    assert asset["behavior"]["behavior_id"] == "BEH-0007"


def test_build_regression_asset_library_reports_counts_and_behavior_links():
    library = build_regression_asset_library(
        [
            {"violation_id": "VIO-1", "confirmed": True, "behavior_id": "BEH-1"},
            {"violation_id": "VIO-2", "confirmed": False, "behavior_id": "BEH-2"},
            {"violation_id": "VIO-3", "confirmed_bug": True, "behavior_id": "BEH-1"},
        ]
    )

    assert library["total_assets"] == 3
    assert library["confirmed_violation_assets"] == 2
    assert library["linked_behaviors"] == 2
    assert library["behavior_ids"] == ["BEH-1", "BEH-2"]


def test_compare_regression_result_validates_absent_violation():
    asset = build_regression_asset(
        {"regression_asset_id": "REG-1", "violation_id": "VIO-1", "behavior_id": "BEH-1"}
    )

    comparison = compare_regression_result(
        asset,
        {"asset_id": "REG-1", "result_id": "RUN-1", "passed": True, "violation_present": False},
    )

    assert comparison["comparison_status"] == "validated"
    assert comparison["matches_asset"] is True
    assert comparison["source_violation_id"] == "VIO-1"


def test_compare_regression_result_marks_present_violation_failed():
    asset = build_regression_asset(
        {"regression_asset_id": "REG-1", "violation_id": "VIO-1", "behavior_id": "BEH-1"}
    )

    comparison = compare_regression_result(
        asset,
        {"asset_id": "REG-1", "result_id": "RUN-2", "violation_present": True},
    )

    assert comparison["comparison_status"] == "failed"
    assert comparison["violation_present"] is True


def test_build_regression_asset_report_counts_comparison_states():
    report = build_regression_asset_report(
        [
            {"regression_asset_id": "REG-1", "violation_id": "VIO-1", "confirmed": True, "behavior_id": "BEH-1"},
            {"regression_asset_id": "REG-2", "violation_id": "VIO-2", "confirmed": True, "behavior_id": "BEH-2"},
        ],
        [
            {"asset_id": "REG-1", "result_id": "RUN-1", "passed": True},
            {"asset_id": "REG-2", "result_id": "RUN-2", "violation_present": True},
        ],
    )

    assert report["comparison_counts"] == {
        "ready": 0,
        "validated": 1,
        "failed": 1,
        "blocked": 0,
    }


def test_regression_asset_library_does_not_emit_rejected_language():
    library = build_regression_asset_library(
        [{"violation_id": "VIO-PAYMENT-001", "confirmed": True, "behavior_id": "BEH-PAYMENT"}]
    )

    serialized = str(library).lower()
    assert "fix" not in serialized
    assert "repair" not in serialized
    assert "recommendation" not in serialized
    assert "remediation" not in serialized
