from ai_test_asset_center.bug_fix_recommendation import (
    build_fix_recommendation,
    build_fix_recommendation_report,
    classify_root_cause,
)


def test_classifies_access_control_root_cause():
    finding = {"title": "Low privilege role can access tenant privacy data"}

    result = classify_root_cause(finding)

    assert result["root_cause_category"] == "access_control"
    assert "authorization" in result["fix_recommendation"]


def test_classifies_payment_integrity_root_cause():
    finding = {"title": "Duplicate payment creates wrong order amount"}

    result = classify_root_cause(finding)

    assert result["root_cause_category"] == "payment_or_financial_integrity"
    assert "idempotency" in result["fix_recommendation"]


def test_build_fix_recommendation_includes_severity_and_fix():
    finding = {
        "title": "Order API crashes with 500 exception",
        "confirmed_bug": True,
        "response": {"status": 500},
    }

    result = build_fix_recommendation(finding)

    assert result["severity"] == "P1"
    assert result["root_cause_category"] == "runtime_exception"
    assert result["fix_recommendation"]


def test_build_fix_recommendation_report_counts_root_causes():
    report = build_fix_recommendation_report(
        [
            {"title": "Low privilege permission leak"},
            {"title": "Payment duplicate order amount"},
            {"title": "Validation missing for required field"},
        ]
    )

    assert report["total_findings"] == 3
    assert report["root_cause_counts"]["access_control"] == 1
    assert report["root_cause_counts"]["payment_or_financial_integrity"] == 1
    assert report["root_cause_counts"]["input_validation"] == 1
