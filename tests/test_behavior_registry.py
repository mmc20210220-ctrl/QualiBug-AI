from ai_test_asset_center.behavior_registry import build_behavior_registry, build_behavior_registry_report


def test_build_behavior_registry_groups_violations_and_evidence_by_behavior():
    registry = build_behavior_registry(
        [
            {
                "behavior_id": "BEH-ORDER-CREATE",
                "behavior_name": "Create Order",
                "category": "orders",
                "violation_id": "VIO-001",
                "evidence_id": "EVID-001",
            },
            {
                "behavior_id": "BEH-ORDER-CREATE",
                "bug_id": "BUG-008",
                "runtime_evidence": {"status_code": 500},
            },
            {
                "behavior_id": "BEH-EXPORT-DATA",
                "behavior_name": "Export Data",
                "category": "data-export",
                "evidence_ids": ["EVID-010", "EVID-011"],
            },
        ]
    )

    assert registry["total_behaviors"] == 2
    assert registry["status_counts"] == {
        "violated": 1,
        "validated": 1,
        "observed": 0,
        "untested": 0,
    }

    order_behavior = registry["behaviors"][0]
    assert order_behavior["behavior_id"] == "BEH-EXPORT-DATA"
    assert order_behavior["status"] == "validated"

    create_order = registry["behaviors"][1]
    assert create_order["behavior_id"] == "BEH-ORDER-CREATE"
    assert create_order["behavior_name"] == "Create Order"
    assert create_order["category"] == "orders"
    assert create_order["violations"] == ["VIO-001", "BUG-008"]
    assert create_order["evidence"] == ["EVID-001", {"status_code": 500}]
    assert create_order["status"] == "violated"


def test_build_behavior_registry_marks_observed_behavior_from_validation_run():
    registry = build_behavior_registry(
        [
            {
                "behavior_id": "BEH-LOGIN",
                "behavior_name": "Login",
                "validation_run_id": "RUN-001",
            }
        ]
    )

    assert registry["status_counts"]["observed"] == 1
    assert registry["behaviors"][0]["status"] == "observed"
    assert registry["behaviors"][0]["validation_runs"] == ["RUN-001"]


def test_build_behavior_registry_creates_stable_fallback_behavior_ids():
    registry = build_behavior_registry(
        [
            {"title": "Anonymous validation artifact"},
            {"title": "Second validation artifact"},
        ]
    )

    assert [item["behavior_id"] for item in registry["behaviors"]] == ["BEH-0001", "BEH-0002"]
    assert registry["status_counts"]["untested"] == 2


def test_build_behavior_registry_report_calculates_behavior_coverage():
    report = build_behavior_registry_report(
        [
            {"behavior_id": "BEH-1", "violation_id": "VIO-1"},
            {"behavior_id": "BEH-2", "evidence_id": "EVID-2"},
            {"behavior_id": "BEH-3", "validation_run_id": "RUN-3"},
            {"behavior_id": "BEH-4"},
        ]
    )

    assert report["total_behaviors"] == 4
    assert report["behavior_coverage_percent"] == 50.0
    assert report["highest_attention_behavior"]["behavior_id"] == "BEH-1"
    assert report["highest_attention_behavior"]["status"] == "violated"


def test_behavior_registry_report_does_not_emit_fix_recommendations():
    report = build_behavior_registry_report(
        [
            {
                "behavior_id": "BEH-PAYMENT",
                "behavior_name": "Capture Payment",
                "violation_id": "VIO-PAYMENT-001",
                "evidence_id": "EVID-PAYMENT-001",
            }
        ]
    )

    serialized = str(report).lower()
    assert "fix" not in serialized
    assert "recommendation" not in serialized
    assert "repair" not in serialized
