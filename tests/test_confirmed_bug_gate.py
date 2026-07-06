from ai_test_asset_center.confirmed_bug_gate import (
    build_confirmed_bug_evidence_report,
    can_promote_confirmed_bug,
    filter_promotable_confirmed_bugs,
    is_confirmed_bug_candidate,
)


def test_confirmed_bug_candidate_detection_from_flags_and_status():
    assert is_confirmed_bug_candidate({"confirmed_bug": True})
    assert is_confirmed_bug_candidate({"status": "confirmed"})
    assert is_confirmed_bug_candidate({"bug_status": "verified-bug"})
    assert is_confirmed_bug_candidate({"finding_type": "confirmed_bug"})
    assert not is_confirmed_bug_candidate({"status": "suspected"})
    assert not is_confirmed_bug_candidate({"summary": "Possible issue"})


def test_confirmed_bug_requires_runtime_evidence_for_promotion():
    assert can_promote_confirmed_bug(
        {
            "status": "confirmed_bug",
            "request": "POST /api/order/create",
            "response": {"status_code": 500},
        }
    )
    assert can_promote_confirmed_bug(
        {
            "confirmed": True,
            "runtime_evidence": {"probe": {"observed": {"status_code": 409}}},
        }
    )
    assert not can_promote_confirmed_bug(
        {
            "status": "confirmed_bug",
            "reason": "The model thinks this is likely a bug.",
        }
    )
    assert not can_promote_confirmed_bug(
        {
            "status": "suspected",
            "request": "GET /api/users/1",
            "status_code": 500,
        }
    )


def test_filter_promotable_confirmed_bugs_from_nested_payload():
    payload = {
        "findings": [
            {"id": "bug-1", "status": "confirmed", "request": "GET /api/a", "status_code": 500},
            {"id": "bug-2", "status": "confirmed", "summary": "No evidence"},
            {"id": "bug-3", "status": "suspected", "request": "GET /api/b", "status_code": 400},
        ]
    }

    promotable = filter_promotable_confirmed_bugs(payload)
    assert [item["id"] for item in promotable] == ["bug-1"]


def test_confirmed_bug_evidence_report_counts_blocked_promotions():
    report = build_confirmed_bug_evidence_report(
        {
            "confirmed_bugs": [
                {"id": "bug-1", "confirmed_bug": True, "request": "POST /api/pay", "status_code": 500},
                {"id": "bug-2", "confirmed_bug": True, "summary": "No runtime artifact"},
                {"id": "bug-3", "status": "suspected", "summary": "Not confirmed"},
                {"id": "bug-4", "finding_status": "reproduced", "probe": {"observed": {"status": 409}}},
            ]
        }
    )

    assert report == {
        "confirmed_bug_candidates": 3,
        "evidence_backed_confirmed_bugs": 2,
        "non_evidence_backed_confirmed_bugs": 1,
        "confirmed_bug_evidence_ratio": 2 / 3,
        "confirmed_bug_promotion_blocked": 1,
    }
