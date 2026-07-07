from ai_test_asset_center.behavior_coverage import (
    build_behavior_coverage,
    build_behavior_coverage_report,
)


def test_build_behavior_coverage_classifies_statuses():
    report = build_behavior_coverage(
        [
            {"behavior_id": "BEH-reg", "regression_asset_id": "REG-1"},
            {"behavior_id": "BEH-vio", "violation_id": "VIO-1"},
            {"behavior_id": "BEH-val", "package_id": "EP-1"},
            {"behavior_id": "BEH-obs", "validation_run_id": "VAL-1"},
            {"behavior_id": "BEH-none", "behavior_name": "Registered only"},
        ]
    )

    assert report["total_behaviors"] == 5
    assert report["status_counts"] == {
        "validated": 1,
        "violated": 1,
        "regression_tracked": 1,
        "observed": 1,
        "untested": 1,
    }
    assert report["coverage_bucket_counts"] == {
        "covered": 3,
        "partially_covered": 1,
        "uncovered": 1,
    }


def test_build_behavior_coverage_merges_mixed_records_by_behavior():
    report = build_behavior_coverage(
        [
            {
                "behavior_id": "BEH-checkout",
                "behavior_name": "Checkout total remains stable",
                "validation_run_id": "VAL-1",
            },
            {
                "package_id": "EP-VIO-1",
                "violation": {
                    "behavior_id": "BEH-checkout",
                    "violation_id": "VIO-1",
                },
                "traceability": {
                    "validation_run_ids": ["VAL-1"],
                    "evidence_package_ids": ["EP-VIO-1"],
                },
            },
            {
                "asset_id": "REG-1",
                "behavior": {"behavior_id": "BEH-checkout"},
            },
        ]
    )

    assert report["total_behaviors"] == 1
    behavior = report["behaviors"][0]
    assert behavior["behavior_id"] == "BEH-checkout"
    assert behavior["validation_run_refs"] == ["VAL-1"]
    assert behavior["evidence_refs"] == ["EP-VIO-1"]
    assert behavior["violation_refs"] == ["VIO-1"]
    assert behavior["regression_refs"] == ["REG-1"]
    assert behavior["status"] == "regression_tracked"
    assert behavior["coverage_bucket"] == "covered"


def test_build_behavior_coverage_report_calculates_percentages():
    report = build_behavior_coverage_report(
        [
            {"behavior_id": "BEH-covered", "package_id": "EP-1"},
            {"behavior_id": "BEH-observed", "validation_run_id": "VAL-1"},
            {"behavior_id": "BEH-uncovered", "behavior_name": "Registered only"},
        ]
    )

    assert report["covered_behavior_percent"] == 33.33
    assert report["observed_or_covered_behavior_percent"] == 66.67


def test_behavior_coverage_does_not_emit_out_of_boundary_language():
    report = build_behavior_coverage_report(
        [
            {"behavior_id": "BEH-safe", "package_id": "EP-1"},
        ]
    )

    rendered = str(report).lower()
    forbidden = ("repair", "recommendation", "auto fix", "pull request", "patch")
    assert not any(term in rendered for term in forbidden)
