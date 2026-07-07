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


class TestDBClueVsEvidence:
    """SQL 提示和表线索不能冒充已验证 DB 证据。"""

    def test_sql_verify_hint_does_not_count_as_verified_db_evidence(self):
        finding = _make_minimal_finding(
            expected="库存应扣减",
            actual="接口返回 200，但需要进一步核验数据库",
            evidence={"status_code": 200, "body": {"ok": True}},
            investigation_guidance={
                "relevant_tables": ["inventory"],
                "sql_verify": "SELECT stock FROM inventory WHERE sku='SKU-1';",
            },
        )
        evidence_completeness = _compute_evidence_completeness(finding)
        dims = {d["key"]: d["present"] for d in evidence_completeness["dimensions"]}
        bug_status = _compute_bug_status(finding, _compute_evidence_quality(finding, "/api/test"), evidence_completeness)

        assert dims["db_evidence"] is False
        assert bug_status["status"] != "reproduced"


class TestValidatedEvidenceQualityCanonicalization:
    """Validated customer-ready findings must not keep stale "待补强证据" labels."""

    def test_validated_upstream_quality_overrides_stale_label_and_summary(self):
        finding = _make_minimal_finding(
            gate_passed=True,
            evidence_quality={
                "level": "validated",
                "score": 95,
                "label": "待补强证据",
                "summary": "已有部分定位信息，但缺少关键运行时证据，暂不应作为已验证缺陷交付。",
                "missing": [],
                "next_actions": ["补充更多证据"],
                "can_reproduce": True,
            },
            evidence_status={
                "semantic_verdict": "SEMANTIC_CONFIRMED",
                "business_evidence_status": "VALIDATED",
            },
        )

        quality = _compute_evidence_quality(finding, "/api/test")

        assert quality["level"] == "validated"
        assert quality["can_reproduce"] is True
        assert quality["label"] == "可交付证据"
        assert quality["summary"] == "证据完整，可直接提交研发修复。"
        assert quality["missing"] == []
        assert quality["next_actions"] == []
