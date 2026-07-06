from ai_test_asset_center.confirmed_bug_gate import build_confirmed_bug_evidence_report


def test_end_to_end_confirmed_bug_promotion_chain():
    findings = [
        {
            "title": "real bug",
            "confirmed_bug": True,
            "runtime_evidence": {
                "request": {"url": "/api/demo"},
                "response": {"status": 500},
            },
        },
        {
            "title": "unsupported confirmation",
            "confirmed_bug": True,
        },
    ]

    report = build_confirmed_bug_evidence_report(findings)

    assert report["confirmed_bug_candidates"] == 2
    assert report["evidence_backed_confirmed_bugs"] == 1
    assert report["non_evidence_backed_confirmed_bugs"] == 1
    assert report["confirmed_bug_promotion_blocked"] == 1
