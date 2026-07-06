from ai_test_asset_center.bug_risk_scoring import build_bug_risk_report, enrich_bug_with_risk, score_bug_risk


def test_score_bug_risk_classifies_p0_security_payment_failure():
    finding = {
        "title": "Payment authorization privacy leak causes financial data loss",
        "confirmed_bug": True,
        "runtime_evidence": {
            "request": {"url": "/api/pay"},
            "response": {"status": 500},
        },
    }

    scored = score_bug_risk(finding)

    assert scored["severity"] == "P0"
    assert scored["risk_score"] == 90
    assert "critical business/security impact" in scored["risk_reasons"]
    assert "server-side failure status 500" in scored["risk_reasons"]


def test_score_bug_risk_classifies_p1_runtime_failure():
    finding = {
        "title": "Order API crashes with 500 exception",
        "confirmed": True,
        "response": {"status_code": 500},
    }

    scored = score_bug_risk(finding)

    assert scored["severity"] == "P1"
    assert scored["risk_score"] == 70


def test_score_bug_risk_classifies_p2_functional_issue():
    finding = {
        "title": "Validation missing for coupon field",
        "confirmed_bug": True,
    }

    scored = score_bug_risk(finding)

    assert scored["severity"] == "P2"
    assert scored["risk_score"] == 40


def test_score_bug_risk_classifies_p3_low_impact_issue():
    scored = score_bug_risk({"title": "Button label typo"})

    assert scored["severity"] == "P3"
    assert scored["risk_score"] == 0
    assert scored["risk_reasons"] == ["low explicit impact in available finding data"]


def test_enrich_bug_with_risk_preserves_original_fields():
    enriched = enrich_bug_with_risk({"id": "BUG-1", "title": "timeout on export"})

    assert enriched["id"] == "BUG-1"
    assert enriched["severity"] == "P2"
    assert enriched["risk_score"] == 30


def test_build_bug_risk_report_counts_severities_and_highest_risk():
    report = build_bug_risk_report(
        [
            {"title": "Payment security data loss", "confirmed_bug": True, "response": {"status": 500}},
            {"title": "Validation missing", "confirmed_bug": True},
            {"title": "Typo"},
        ]
    )

    assert report["total_findings"] == 3
    assert report["severity_counts"] == {"P0": 1, "P1": 0, "P2": 1, "P3": 1}
    assert report["highest_risk_finding"]["severity"] == "P0"
