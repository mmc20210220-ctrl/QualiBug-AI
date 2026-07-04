from __future__ import annotations

from ai_test_asset_center.discovery_accounting import classify_issue_accounting


def _base_issue() -> dict:
    return {
        "verification": {"verdict": "validated_bug"},
        "reproduction_steps": ["1. call api", "2. observe defect"],
        "evidence_refs": ["packet:1"],
        "evidence": {
            "request": {"method": "POST", "url": "/api/orders"},
            "response": {"status_code": 500},
        },
    }


def test_classify_issue_accounting_marks_strict_validated_bug_when_all_requirements_present() -> None:
    accounting = classify_issue_accounting(_base_issue())

    assert accounting["accounting_state"] == "validated"
    assert accounting["strict_validated_bug"] is True
    assert accounting["verifier_passed"] is True
    assert accounting["has_reproduction"] is True
    assert accounting["has_evidence_refs"] is True
    assert accounting["blocker_reason_codes"] == []


def test_classify_issue_accounting_downgrades_missing_verifier_to_pending() -> None:
    issue = _base_issue()
    issue.pop("verification")

    accounting = classify_issue_accounting(issue)

    assert accounting["accounting_state"] == "pending"
    assert accounting["strict_validated_bug"] is False
    assert accounting["verifier_passed"] is False
    assert "missing_strict_verifier" in accounting["blocker_reason_codes"]
    assert accounting["primary_blocker_reason_code"] == "missing_strict_verifier"


def test_classify_issue_accounting_downgrades_missing_reproduction_to_pending() -> None:
    issue = _base_issue()
    issue.pop("reproduction_steps")
    issue["evidence"] = {"response": {"status_code": 500}}

    accounting = classify_issue_accounting(issue)

    assert accounting["accounting_state"] == "pending"
    assert accounting["strict_validated_bug"] is False
    assert accounting["has_reproduction"] is False
    assert "missing_reproduction" in accounting["blocker_reason_codes"]


def test_classify_issue_accounting_downgrades_missing_evidence_refs_to_pending() -> None:
    issue = _base_issue()
    issue.pop("evidence_refs")
    issue.pop("reproduction_steps")
    issue["evidence"] = {}
    issue["reproduction_pack"] = {
        "request": {"method": "POST", "url": "/api/orders"},
        "response": {"status_code": 500},
    }

    accounting = classify_issue_accounting(issue)

    assert accounting["accounting_state"] == "pending"
    assert accounting["strict_validated_bug"] is False
    assert accounting["has_evidence_refs"] is False
    assert "missing_evidence_refs" in accounting["blocker_reason_codes"]


def test_classify_issue_accounting_keeps_candidate_only_runtime_errors_out_of_pending_bucket() -> None:
    issue = {
        "evidence": {"response": {"error": "candidate_only"}},
    }

    accounting = classify_issue_accounting(issue)

    assert accounting["accounting_state"] == "candidate"
    assert accounting["strict_validated_bug"] is False
    assert "candidate_only" in accounting["blocker_reason_codes"]
    assert "missing_strict_verifier" in accounting["blocker_reason_codes"]
