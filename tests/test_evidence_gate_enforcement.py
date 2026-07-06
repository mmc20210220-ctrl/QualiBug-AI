"""Test: Evidence gate enforcement — evidence_consistency rejected blocks ready_bug.

Validates:
- evidence_consistency.verdict = "rejected" → gate_passed = False
- evidence_consistency.verdict = "missing" → gate_passed = False
- route_blocked / auth_blocked / environment_blocked → gate_passed = False
- coverage_gap / validation_lead → NOT in data.risks
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from ai_test_asset_center.display_ready_formatter import (  # noqa: E402
    _compute_bug_status,
    _compute_evidence_completeness,
    _compute_evidence_quality,
    _enforce_evidence_gate,
)


def _make_minimal_finding(**overrides) -> dict:
    """Minimal finding with enough structure for gate enforcement."""
    base: dict = {
        "id": "bug-001",
        "title": "Test finding",
        "description": "Test description",
        "severity": "P1",
        "reproduction": {
            "method": "GET",
            "path": "/api/test",
            "steps": ["Step 1", "Step 2"],
        },
        "expected": "Should return 200",
        "actual": "Returned 500",
        "har_evidence": {"status_code": 500, "response_body": '{"error": "server error"}'},
    }
    base.update(overrides)
    return base


class TestEvidenceConsistencyRejected:
    """evidence_consistency.verdict rejected → gate_passed must be False."""

    def test_rejected_verdict_blocks_gate(self):
        finding = _make_minimal_finding(
            evidence_consistency={"verdict": "rejected"}
        )
        evidence_quality = _compute_evidence_quality(finding, "/api/test")
        evidence_completeness = _compute_evidence_completeness(finding)
        bug_status = _compute_bug_status(finding, evidence_quality, evidence_completeness)
        result = _enforce_evidence_gate(finding, bug_status, evidence_completeness)
        assert result["gate_passed"] is False, \
            f"Expected gate_passed=False for rejected evidence, got {result}"
        assert result["status"] in ("not_reproduced", "suspected"), \
            f"Expected not_reproduced/suspected, got {result['status']}"

    def test_missing_verdict_blocks_gate(self):
        finding = _make_minimal_finding(
            evidence_consistency={"verdict": "missing"}
        )
        evidence_quality = _compute_evidence_quality(finding, "/api/test")
        evidence_completeness = _compute_evidence_completeness(finding)
        bug_status = _compute_bug_status(finding, evidence_quality, evidence_completeness)
        result = _enforce_evidence_gate(finding, bug_status, evidence_completeness)
        assert result["gate_passed"] is False, \
            f"Expected gate_passed=False for missing evidence, got {result}"

    def test_inconsistent_verdict_blocks_gate(self):
        finding = _make_minimal_finding(
            evidence_consistency={"verdict": "inconsistent"}
        )
        evidence_quality = _compute_evidence_quality(finding, "/api/test")
        evidence_completeness = _compute_evidence_completeness(finding)
        bug_status = _compute_bug_status(finding, evidence_quality, evidence_completeness)
        result = _enforce_evidence_gate(finding, bug_status, evidence_completeness)
        assert result["gate_passed"] is False


class TestBlockedRoutesNotReadyBug:
    """route_blocked / auth_blocked / environment_blocked → NOT in data.risks."""

    def test_route_blocked_blocks_gate(self):
        finding = _make_minimal_finding(value_lane="route_blocked")
        evidence_quality = _compute_evidence_quality(finding, "/api/test")
        evidence_completeness = _compute_evidence_completeness(finding)
        bug_status = _compute_bug_status(finding, evidence_quality, evidence_completeness)
        result = _enforce_evidence_gate(finding, bug_status, evidence_completeness)
        assert result["gate_passed"] is False, \
            f"route_blocked should block gate, got {result}"

    @pytest.mark.parametrize("blocked_type", [
        "auth_blocked", "environment_blocked", "coverage_gap",
        "validation_lead", "not_reproduced",
    ])
    def test_blocked_types_block_gate(self, blocked_type):
        finding = _make_minimal_finding(value_lane=blocked_type)
        evidence_quality = _compute_evidence_quality(finding, "/api/test")
        evidence_completeness = _compute_evidence_completeness(finding)
        bug_status = _compute_bug_status(finding, evidence_quality, evidence_completeness)
        result = _enforce_evidence_gate(finding, bug_status, evidence_completeness)
        assert result["gate_passed"] is False, \
            f"{blocked_type} should block gate_passed, got {result}"

    def test_route_blocked_in_execution_block(self):
        finding = _make_minimal_finding(execution_block="route_blocked")
        evidence_quality = _compute_evidence_quality(finding, "/api/test")
        evidence_completeness = _compute_evidence_completeness(finding)
        bug_status = _compute_bug_status(finding, evidence_quality, evidence_completeness)
        result = _enforce_evidence_gate(finding, bug_status, evidence_completeness)
        assert result["gate_passed"] is False
