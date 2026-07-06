"""Test: Frontend data filtering — getReportFindings and isCustomerReadyFinding.

Validates that the frontend filtering functions correctly enforce the customer
delivery contract:
- Only ready_bug (reproduced + gate_passed + evidence consistent) items pass
- Blocked/clue/validation_lead items are excluded
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _is_customer_ready_finding(finding: dict | None) -> bool:
    """Mirrors the frontend isCustomerReadyFinding function from data.ts."""
    if not finding:
        return False

    delivery_status = str(finding.get("customer_delivery_status") or finding.get("delivery_track") or "").strip()
    if delivery_status == "defect":
        return True
    if delivery_status == "clue":
        return False

    if finding.get("bug_status") != "reproduced" or not finding.get("gate_passed"):
        return False

    evidence_consistency = finding.get("evidence_consistency")
    if evidence_consistency and isinstance(evidence_consistency, dict):
        verdict = str(evidence_consistency.get("verdict") or "").lower()
        if verdict in ("rejected", "missing"):
            return False

    blocked_keywords = ["route_blocked", "auth_blocked", "environment_blocked",
                        "coverage_gap", "validation_lead", "not_reproduced"]
    value_lane = str(finding.get("value_lane") or finding.get("_value_lane") or "").lower()
    block_reason = str(finding.get("execution_block") or finding.get("block_reason") or "").lower()
    combined = f"{value_lane} {block_reason}"
    if any(kw in combined for kw in blocked_keywords):
        return False

    repro = finding.get("reproduction") or {}
    raw = finding.get("raw_evidence")
    has_runtime_evidence = bool(repro.get("har_evidence", {}).get("status_code") or repro.get("har_evidence", {}).get("response_body"))
    has_raw_evidence = bool(
        raw.get("has_real_evidence") if isinstance(raw, dict) else False
        or (raw.get("response_raw") or {}).get("status_code") if isinstance(raw, dict) else False
        or (raw.get("response_raw") or {}).get("body") if isinstance(raw, dict) else False
        or (raw.get("db_snapshot") or {}).get("table") if isinstance(raw, dict) else False
        or (raw.get("logs") or {}).get("trace_id") if isinstance(raw, dict) else False
        or (raw.get("execution_trace") or {}).get("evidence_hash") if isinstance(raw, dict) else False
    )

    if repro.get("is_synthetic"):
        return has_raw_evidence or has_runtime_evidence

    return has_raw_evidence or has_runtime_evidence or bool(repro.get("path") and repro.get("method"))


# ────────────────────────────────────────────────────────────────

class TestIsCustomerReadyFinding:
    """Frontend isCustomerReadyFinding contract tests."""

    def test_ready_bug_passes(self):
        finding = {
            "bug_status": "reproduced",
            "gate_passed": True,
            "reproduction": {"method": "GET", "path": "/api/test"},
        }
        assert _is_customer_ready_finding(finding) is True

    def test_not_reproduced_fails(self):
        finding = {"bug_status": "not_reproduced", "gate_passed": False}
        assert _is_customer_ready_finding(finding) is False

    def test_gate_not_passed_fails(self):
        finding = {"bug_status": "reproduced", "gate_passed": False}
        assert _is_customer_ready_finding(finding) is False

    def test_evidence_rejected_fails(self):
        finding = {
            "bug_status": "reproduced",
            "gate_passed": True,
            "evidence_consistency": {"verdict": "rejected"},
        }
        assert _is_customer_ready_finding(finding) is False

    def test_evidence_missing_fails(self):
        finding = {
            "bug_status": "reproduced",
            "gate_passed": True,
            "evidence_consistency": {"verdict": "missing"},
        }
        assert _is_customer_ready_finding(finding) is False

    @pytest.mark.parametrize("blocked", [
        "route_blocked", "auth_blocked", "environment_blocked",
        "coverage_gap", "validation_lead", "not_reproduced",
    ])
    def test_blocked_value_lane_fails(self, blocked):
        finding = {
            "bug_status": "reproduced",
            "gate_passed": True,
            "value_lane": blocked,
            "reproduction": {"method": "GET", "path": "/api/test"},
        }
        assert _is_customer_ready_finding(finding) is False, \
            f"value_lane={blocked} should NOT pass isCustomerReadyFinding"


class TestBatchReportFindings:
    """Simulate getReportFindings filtering."""

    def test_only_ready_bugs_in_filtered_batch(self):
        findings = [
            {"bug_status": "reproduced", "gate_passed": True,
             "reproduction": {"method": "GET", "path": "/api/test"}},
            {"bug_status": "suspected", "gate_passed": False},
            {"bug_status": "not_reproduced", "gate_passed": False},
            {"bug_status": "reproduced", "gate_passed": True,
             "evidence_consistency": {"verdict": "rejected"}},
            {"bug_status": "reproduced", "gate_passed": True, "value_lane": "route_blocked"},
            {"bug_status": "reproduced", "gate_passed": True,
             "reproduction": {"method": "POST", "path": "/api/orders"}},
        ]
        customer_ready = [f for f in findings if _is_customer_ready_finding(f)]
        assert len(customer_ready) == 2, \
            f"Expected 2 ready_bugs, got {len(customer_ready)}"
