"""Test: Strict verifier for live issue → ready_bug promotion pipeline.

Validates that the `_strict_verifier_for_issue` function correctly gates issues
through all 9 mandatory checks before allowing them into the ready_bug lane.
Issues that fail any gate must be classified as validation_lead / coverage_gap /
rejected_evidence, NOT as ready_bug.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ai_test_asset_center.real_project_defect_discovery import \
    _strict_verifier_for_issue


# ────────────────────────────────────────────────────────────────
# Test helpers
# ────────────────────────────────────────────────────────────────

def _make_issue(**overrides) -> dict:
    """Create a minimal candidate issue with common test defaults."""
    base: dict = {
        "title": "Admin endpoint accessible without auth",
        "description": "POST /api/admin/users returns 200, should return 401",
        "severity": "P0",
        "category": "security",
        "reproduction": {
            "method": "POST",
            "path": "/api/admin/users",
            "steps": [
                "1. Start without authentication token",
                "2. Send POST /api/admin/users with body {}",
                "3. Observe HTTP 200 response with user list",
            ],
            "is_synthetic": False,
        },
        "har_evidence": {
            "method": "POST",
            "path": "/api/admin/users",
            "status_code": 200,
            "response_body": '{"users": [{"id": 1, "name": "admin"}]}',
        },
        "expected": "Return 401 Unauthorized when no token provided",
        "actual": "Returned 200 with full user list",
        "failed_assertions": ["预期 401 实际 200"],
        "evidence_refs": [{"type": "har", "ref": "har-001"}],
        "verification": {"verdict": "validated_bug"},
        "is_reproducible": True,
        "gate_passed": True,
        "bug_status": "reproduced",
    }
    base.update(overrides)
    return base


# ────────────────────────────────────────────────────────────────

class TestStrictVerifierPassesValidBug:
    """Issues with complete evidence should pass all gates."""

    def test_complete_issue_passes_all_gates(self):
        """A well-formed issue with all required fields passes all 9 gates."""
        issue = _make_issue()
        result = _strict_verifier_for_issue(issue)
        assert result["passes_strict_verifier"] is True, \
            f"Expected strict verifier to pass, failed gates: {result['failed_gates']}"
        assert result["verdict"] == "validated_bug"
        assert result["value_lane"] == "ready_bug"
        assert len(result["failed_gates"]) == 0

    def test_complete_issue_has_value_lane_ready_bug(self):
        """A passing issue must have value_lane == ready_bug."""
        issue = _make_issue()
        result = _strict_verifier_for_issue(issue)
        assert result.get("value_lane") == "ready_bug", \
            f"Expected value_lane='ready_bug', got '{result.get('value_lane')}'"


# ────────────────────────────────────────────────────────────────

class TestIncompleteEvidenceCantBeReadyBug:
    """Issues missing ANY required gate must NOT enter data.risks."""

    def test_missing_api_reference_blocks(self):
        """Missing method/path in both reproduction and har_evidence = no_api_reference gate failure."""
        issue = _make_issue(
            reproduction={"steps": ["step 1"], "is_synthetic": False},
            har_evidence={},  # also need to clear har_evidence
        )
        result = _strict_verifier_for_issue(issue)
        assert not result["passes_strict_verifier"]
        assert "no_api_reference" in result["failed_gates"]

    def test_missing_reproduction_steps_blocks(self):
        """Empty or synthetic reproduction steps = gate failure."""
        issue = _make_issue(reproduction={"method": "GET", "path": "/api/test",
                                           "steps": [], "is_synthetic": True})
        result = _strict_verifier_for_issue(issue)
        assert not result["passes_strict_verifier"]
        assert "no_real_reproduction_steps" in result["failed_gates"]

    def test_missing_evidence_refs_blocks(self):
        """No evidence_refs = gate failure."""
        issue = _make_issue(evidence_refs=[], evidence_chain=[])
        result = _strict_verifier_for_issue(issue)
        assert not result["passes_strict_verifier"]
        assert "no_evidence_refs" in result["failed_gates"]

    def test_missing_failed_assertions_blocks(self):
        """No failed_assertions = gate failure."""
        issue = _make_issue(failed_assertions=[])
        result = _strict_verifier_for_issue(issue)
        assert not result["passes_strict_verifier"]
        assert "no_failed_assertions" in result["failed_gates"]

    def test_missing_expected_actual_blocks(self):
        """No expected/actual comparison = gate failure."""
        issue = _make_issue(expected="", actual="")
        result = _strict_verifier_for_issue(issue)
        assert not result["passes_strict_verifier"]
        assert "no_expected_actual" in result["failed_gates"]

    def test_verdict_not_validated_blocks(self):
        """verification.verdict != validated_bug = gate failure."""
        issue = _make_issue(verification={"verdict": "pending"})
        result = _strict_verifier_for_issue(issue)
        assert not result["passes_strict_verifier"]
        assert "verdict_not_validated" in result["failed_gates"]

    def test_not_reproducible_blocks(self):
        """is_reproducible = False = gate failure."""
        issue = _make_issue(is_reproducible=False)
        result = _strict_verifier_for_issue(issue)
        assert not result["passes_strict_verifier"]
        assert "not_reproducible" in result["failed_gates"]

    def test_gate_not_passed_blocks(self):
        """gate_passed = False = gate failure."""
        issue = _make_issue(gate_passed=False)
        result = _strict_verifier_for_issue(issue)
        assert not result["passes_strict_verifier"]
        assert "gate_not_passed" in result["failed_gates"]


# ────────────────────────────────────────────────────────────────

class TestStatusContradictionDetected:
    """HAR status contradicts claim → rejected_evidence."""

    def test_claimed_500_actual_200_detected(self):
        """Claim says 500 but HAR shows 200 → status contradiction."""
        issue = _make_issue(
            title="Server returns 500 on admin access",
            description="GET /api/admin 返回500",
            har_evidence={"method": "GET", "path": "/api/admin",
                          "status_code": 200,
                          "response_body": '{"ok": true}'},
        )
        result = _strict_verifier_for_issue(issue)
        assert "status_contradiction" in result["failed_gates"]
        assert result.get("value_lane") == "rejected_evidence"


# ────────────────────────────────────────────────────────────────

class TestValueLaneClassification:
    """Verify correct value_lane assignment for each failure mode."""

    def test_incomplete_evidence_is_validation_lead(self):
        """Missing API ref + reproduction → internal_validation_lead."""
        issue = _make_issue(reproduction={"steps": [], "is_synthetic": True})
        result = _strict_verifier_for_issue(issue)
        assert result["value_lane"] in ("validation_lead", "rejected_evidence"), \
            f"Expected validation_lead or rejected_evidence, got {result['value_lane']}"
        assert result["value_lane"] != "ready_bug"

    def test_coverage_gap_is_not_ready_bug(self):
        """Issue classified as coverage_gap must not be ready_bug."""
        issue = _make_issue(failed_assertions=[], evidence_refs=[], is_reproducible=False)
        result = _strict_verifier_for_issue(issue)
        assert result.get("value_lane") != "ready_bug", \
            f"Coverage gap issue should NOT be classified as ready_bug"


# ────────────────────────────────────────────────────────────────

class TestIntegrationPromotion:
    """Verify live issues can be promoted to ready_bug when evidence is complete."""

    def test_live_issue_promoted_with_complete_evidence(self):
        """A 'needs_human_review' issue with all evidence filled in passes."""
        issue = _make_issue(status="needs_human_review")
        result = _strict_verifier_for_issue(issue)
        assert result["passes_strict_verifier"], \
            f"Failed gates: {result['failed_gates']} - reasons: {result['reasons']}"
        assert result["value_lane"] == "ready_bug"
