from ai_test_asset_center.behavior_traceability import (
    build_behavior_traceability,
    build_behavior_traceability_report,
)


def test_build_behavior_traceability_links_full_chain_by_behavior():
    report = build_behavior_traceability(
        [
            {
                "behavior_id": "BEH-login",
                "behavior_name": "Login rejects invalid passwords",
                "validation_run_id": "VAL-1",
                "package_id": "EP-VIO-1",
                "violation_id": "VIO-1",
                "regression_asset_id": "REG-1",
                "result_id": "RES-1",
            }
        ]
    )

    assert report["total_traces"] == 1
    assert report["status_counts"] == {"complete": 1, "partial": 0, "unlinked": 0}

    trace = report["traces"][0]
    assert trace["behavior_id"] == "BEH-login"
    assert trace["validation_run_ids"] == ["VAL-1"]
    assert trace["evidence_package_ids"] == ["EP-VIO-1"]
    assert trace["violation_ids"] == ["VIO-1"]
    assert trace["regression_asset_ids"] == ["REG-1"]
    assert trace["regression_result_ids"] == ["RES-1"]
    assert trace["status"] == "complete"
    assert trace["status_lifecycle"] == [
        "registered",
        "observed",
        "evidence-packaged",
        "violated",
        "regression-tracked",
        "regression-validated",
    ]


def test_build_behavior_traceability_merges_mixed_artifacts():
    report = build_behavior_traceability(
        [
            {
                "behavior_id": "BEH-checkout",
                "behavior_name": "Checkout total remains stable",
                "validation_run_id": "VAL-1",
            },
            {
                "package_id": "EP-VIO-2",
                "violation": {
                    "violation_id": "VIO-2",
                    "behavior_id": "BEH-checkout",
                    "behavior_name": "Checkout total remains stable",
                },
                "traceability": {
                    "validation_run_ids": ["VAL-1"],
                    "regression_asset_ids": ["REG-2"],
                },
            },
            {
                "asset_id": "REG-2",
                "behavior": {"behavior_id": "BEH-checkout"},
                "source_violation": {"violation_id": "VIO-2", "confirmed": True},
                "evidence_linkage": {"validation_run_ids": ["VAL-1"]},
            },
            {
                "asset_id": "REG-2",
                "behavior_id": "BEH-checkout",
                "result_id": "RES-2",
                "comparison_status": "validated",
            },
        ]
    )

    trace = report["traces"][0]
    assert report["status_counts"]["complete"] == 1
    assert trace["validation_run_ids"] == ["VAL-1"]
    assert trace["evidence_package_ids"] == ["EP-VIO-2"]
    assert trace["violation_ids"] == ["VIO-2"]
    assert trace["regression_asset_ids"] == ["REG-2"]
    assert trace["regression_result_ids"] == ["RES-2"]


def test_build_behavior_traceability_marks_partial_and_unlinked_chains():
    report = build_behavior_traceability(
        [
            {"behavior_id": "BEH-partial", "validation_run_id": "VAL-1"},
            {"behavior_id": "BEH-empty", "behavior_name": "Registered only"},
        ]
    )

    assert report["status_counts"] == {"complete": 0, "partial": 1, "unlinked": 1}
    traces_by_id = {trace["behavior_id"]: trace for trace in report["traces"]}
    assert traces_by_id["BEH-partial"]["status"] == "partial"
    assert traces_by_id["BEH-empty"]["status"] == "unlinked"


def test_build_behavior_traceability_report_calculates_completion_rate():
    report = build_behavior_traceability_report(
        [
            {
                "behavior_id": "BEH-complete",
                "validation_run_id": "VAL-1",
                "package_id": "EP-1",
                "violation_id": "VIO-1",
                "regression_asset_id": "REG-1",
                "result_id": "RES-1",
            },
            {"behavior_id": "BEH-partial", "validation_run_id": "VAL-2"},
        ]
    )

    assert report["total_traces"] == 2
    assert report["complete_traceability_percent"] == 50.0


def test_behavior_traceability_does_not_emit_out_of_boundary_language():
    report = build_behavior_traceability_report(
        [
            {
                "behavior_id": "BEH-safe",
                "validation_run_id": "VAL-1",
                "package_id": "EP-1",
                "violation_id": "VIO-1",
                "regression_asset_id": "REG-1",
                "result_id": "RES-1",
            }
        ]
    )

    rendered = str(report).lower()
    forbidden = ("repair", "recommendation", "auto fix", "pull request", "patch")
    assert not any(term in rendered for term in forbidden)
