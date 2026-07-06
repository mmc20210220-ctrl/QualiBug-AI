"""Test: Customer value lane contract — data.risks only contains ready_bug items.

This is a CONTRACT test: any deviation from the contract means the customer-facing
frontend will display fake bugs. The contract defines the exact filtering rules
that must be enforced at both the backend (display_ready_formatter) and frontend
(data.ts isCustomerReadyFinding) layers.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ────────────────────────────────────────────────────────────────
# Contract: The ONLY items that may appear in data.risks
# ────────────────────────────────────────────────────────────────


def _passes_customer_delivery_contract(finding: dict) -> bool:
    """Contract check: does this finding qualify for customer-facing data.risks?

    Rules (all must be true):
    1. bug_status == "reproduced"
    2. gate_passed == True
    3. is_reproducible == True
    4. evidence_consistency.verdict NOT in ("rejected", "missing")
    5. value_lane / block_reason NOT in blocked categories
    """
    bug_status = str(finding.get("bug_status") or "")
    if bug_status != "reproduced":
        return False

    if not bool(finding.get("gate_passed")):
        return False

    if not bool(finding.get("is_reproducible")):
        return False

    evidence_consistency = finding.get("evidence_consistency") or {}
    if isinstance(evidence_consistency, dict):
        ec_verdict = str(evidence_consistency.get("verdict") or "").lower()
        if ec_verdict in ("rejected", "missing"):
            return False

    block_keywords = (
        "route_blocked", "auth_blocked", "environment_blocked",
        "coverage_gap", "validation_lead", "not_reproduced",
    )
    value_lane = str(finding.get("value_lane") or finding.get("_value_lane") or "")
    block_reason = str(finding.get("execution_block") or finding.get("block_reason") or "")
    combined = f"{value_lane} {block_reason}".lower()
    if any(kw in combined for kw in block_keywords):
        return False

    return True


# ────────────────────────────────────────────────────────────────

class TestCustomerDeliveryContract:
    """Verify the customer delivery contract filtering rules."""

    def test_ready_bug_passes_contract(self):
        """A well-formed ready_bug passes the contract."""
        finding = {
            "bug_status": "reproduced",
            "gate_passed": True,
            "is_reproducible": True,
            "evidence_consistency": {"verdict": "consistent"},
            "value_lane": "ready_bug",
            "title": "Real bug with evidence",
        }
        assert _passes_customer_delivery_contract(finding) is True

    def test_suspected_status_fails_contract(self):
        """suspected bug_status should NOT appear in customer delivery."""
        finding = {
            "bug_status": "suspected",
            "gate_passed": False,
            "is_reproducible": False,
        }
        assert _passes_customer_delivery_contract(finding) is False

    def test_risk_clue_status_fails_contract(self):
        """risk_clue should NOT appear in customer delivery."""
        finding = {
            "bug_status": "risk_clue",
            "gate_passed": False,
        }
        assert _passes_customer_delivery_contract(finding) is False

    def test_not_reproduced_status_fails_contract(self):
        """not_reproduced should NEVER be in data.risks."""
        finding = {
            "bug_status": "not_reproduced",
            "gate_passed": False,
            "is_reproducible": False,
        }
        assert _passes_customer_delivery_contract(finding) is False

    def test_gate_passed_false_fails_contract(self):
        """gate_passed=False blocks even if status is 'reproduced'."""
        finding = {
            "bug_status": "reproduced",
            "gate_passed": False,
            "is_reproducible": True,
        }
        assert _passes_customer_delivery_contract(finding) is False

    def test_evidence_rejected_fails_contract(self):
        """evidence_consistency.verdict=rejected blocks delivery."""
        finding = {
            "bug_status": "reproduced",
            "gate_passed": True,
            "is_reproducible": True,
            "evidence_consistency": {"verdict": "rejected"},
        }
        assert _passes_customer_delivery_contract(finding) is False

    def test_evidence_missing_fails_contract(self):
        """evidence_consistency.verdict=missing blocks delivery."""
        finding = {
            "bug_status": "reproduced",
            "gate_passed": True,
            "is_reproducible": True,
            "evidence_consistency": {"verdict": "missing"},
        }
        assert _passes_customer_delivery_contract(finding) is False

    @pytest.mark.parametrize("blocked_value_lane", [
        "route_blocked", "auth_blocked", "environment_blocked",
        "coverage_gap", "validation_lead", "not_reproduced",
    ])
    def test_blocked_lanes_fail_contract(self, blocked_value_lane):
        """Any blocked/cannot-reproduce value_lane fails the contract."""
        finding = {
            "bug_status": "reproduced",
            "gate_passed": True,
            "is_reproducible": True,
            "value_lane": blocked_value_lane,
        }
        assert _passes_customer_delivery_contract(finding) is False, \
            f"value_lane={blocked_value_lane} should NOT pass the contract"


# ────────────────────────────────────────────────────────────────

class TestBatchFiltering:
    """Simulate the frontend getReportFindings filtering logic."""

    def test_filtered_risks_only_contains_ready_bugs(self):
        """A batch of mixed findings should only allow ready_bug through."""
        findings = [
            {"bug_status": "reproduced", "gate_passed": True, "is_reproducible": True,
             "evidence_consistency": {"verdict": "consistent"}, "value_lane": "ready_bug"},
            {"bug_status": "suspected", "gate_passed": False},
            {"bug_status": "reproduced", "gate_passed": True, "is_reproducible": True,
             "evidence_consistency": {"verdict": "rejected"}},
            {"bug_status": "not_reproduced", "gate_passed": False},
            {"bug_status": "risk_clue", "gate_passed": False, "value_lane": "validation_lead"},
        ]
        filtered = [f for f in findings if _passes_customer_delivery_contract(f)]
        assert len(filtered) == 1, \
            f"Expected 1 ready_bug in batch, got {len(filtered)}: {filtered}"
        assert filtered[0]["value_lane"] == "ready_bug"
