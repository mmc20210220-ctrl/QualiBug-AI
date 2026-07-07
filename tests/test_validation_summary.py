from ai_test_asset_center.validation_summary import (
    build_validation_summary,
    build_validation_summary_report,
)


def _reports():
    return {
        "behavior_registry": {
            "total_behaviors": 3,
            "status_counts": {"violated": 1, "validated": 1, "observed": 1, "untested": 0},
        },
        "evidence_packages": {
            "total_packages": 2,
            "confirmed_packages": 1,
            "evidence_complete_packages": 1,
            "evidence_completeness_percent": 50.0,
        },
        "regression_assets": {
            "total_assets": 2,
            "confirmed_violation_assets": 1,
            "comparison_counts": {"ready": 0, "validated": 1, "failed": 1, "blocked": 0},
        },
        "behavior_traceability": {
            "total_traces": 3,
            "status_counts": {"complete": 1, "partial": 1, "unlinked": 1},
            "complete_traceability_percent": 33.33,
        },
        "behavior_coverage": {
            "total_behaviors": 3,
            "coverage_bucket_counts": {"covered": 1, "partially_covered": 1, "uncovered": 1},
            "covered_behavior_percent": 33.33,
            "observed_or_covered_behavior_percent": 66.67,
        },
    }


def test_build_validation_summary_aggregates_core_sections():
    summary = build_validation_summary(_reports())

    assert summary["summary_type"] == "validation_assurance"
    assert summary["product_boundary"] == "discover-prove-report-regression-validate"
    assert summary["assurance_level"] == "regression_attention"
    assert summary["north_star"] == {
        "metric": "confirmed_violation_rate",
        "confirmed_violations": 1,
        "detected_violations": 2,
    }
    assert summary["behavior_state"]["total_behaviors"] == 3
    assert summary["evidence_state"]["evidence_completeness_percent"] == 50.0
    assert summary["traceability_state"]["complete_traceability_percent"] == 33.33
    assert summary["regression_state"]["comparison_counts"]["failed"] == 1


def test_build_validation_summary_report_calculates_confirmed_violation_rate():
    summary = build_validation_summary_report(_reports())

    assert summary["north_star"]["confirmed_violation_rate"] == 50.0


def test_build_validation_summary_generates_attention_items_without_advisory_content():
    summary = build_validation_summary_report(_reports())

    assert summary["attention_items"] == [
        {"area": "coverage", "state": "uncovered_behaviors", "count": 1},
        {"area": "traceability", "state": "incomplete_chains", "count": 2},
        {"area": "evidence", "state": "incomplete_packages", "count": 1},
        {"area": "regression_validation", "state": "non_validated_results", "count": 1},
    ]

    rendered = str(summary).lower()
    forbidden = ("repair", "recommendation", "auto fix", "pull request", "patch")
    assert not any(term in rendered for term in forbidden)


def test_build_validation_summary_marks_strong_when_assurance_metrics_are_high():
    reports = _reports()
    reports["evidence_packages"]["evidence_completeness_percent"] = 100.0
    reports["regression_assets"]["comparison_counts"] = {"ready": 0, "validated": 2, "failed": 0, "blocked": 0}
    reports["behavior_traceability"]["complete_traceability_percent"] = 100.0
    reports["behavior_coverage"]["covered_behavior_percent"] = 100.0

    summary = build_validation_summary_report(reports)

    assert summary["assurance_level"] == "strong"
