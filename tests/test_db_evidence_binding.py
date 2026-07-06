"""Test: DB before/after evidence binding — must be tied to a specific business operation.

Validates:
- DB before/after snapshots must be bound to a business_operation (login, order, pay, etc.)
- Without DB config/permission, db_evidence_unavailable_reason must be set
- DB assertions must have expected vs actual comparison
- No fabricated DB evidence allowed
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _validate_db_evidence_binding(finding: dict) -> dict:
    """Check if a finding's DB evidence is properly bound to business operations.

    Returns: {valid: bool, reasons: list[str]}
    """
    reasons: list[str] = []
    db_evidence = finding.get("db_evidence") or {}
    db_snapshots = finding.get("db_snapshots") or []

    # Case 1: No DB evidence at all
    if not db_evidence and not db_snapshots:
        return {"valid": True, "reasons": []}  # No DB evidence = not a violation

    # Case 2: Has DB evidence but no business operation
    if isinstance(db_evidence, dict):
        before = db_evidence.get("before_db_snapshot") or db_evidence.get("before")
        after = db_evidence.get("after_db_snapshot") or db_evidence.get("after")
        assertion = db_evidence.get("db_assertion") or db_evidence.get("assertion")
        business_op = db_evidence.get("business_operation") or db_evidence.get("operation")

        if before or after:
            if not business_op:
                reasons.append("DB证据缺少business_operation绑定，无法确定是哪个接口/操作触发的变更")
            if not assertion:
                reasons.append("缺少db_assertion，无法验证期望值vs实际值的对比")
            if not before:
                reasons.append("缺少before_db_snapshot")
            if not after:
                reasons.append("缺少after_db_snapshot")

    # Case 3: Has db_evidence_unavailable_reason — acceptable
    if finding.get("db_evidence_unavailable_reason"):
        return {"valid": True, "reasons": []}

    return {
        "valid": len(reasons) == 0,
        "reasons": reasons,
    }


# ────────────────────────────────────────────────────────────────

class TestDBEvidenceBinding:
    """DB evidence must be tied to a specific business operation."""

    def test_db_evidence_with_business_operation_valid(self):
        """DB evidence with before/after + operation + assertion = valid."""
        finding = {
            "db_evidence": {
                "before_db_snapshot": {"table": "inventory", "value": 100},
                "after_db_snapshot": {"table": "inventory", "value": 99},
                "db_assertion": "库存应扣减1，实际扣减1",
                "business_operation": "POST /api/orders/create",
            }
        }
        result = _validate_db_evidence_binding(finding)
        assert result["valid"] is True, f"Failed: {result['reasons']}"

    def test_db_evidence_without_operation_invalid(self):
        """DB evidence without business_operation = invalid."""
        finding = {
            "db_evidence": {
                "before_db_snapshot": {"table": "inventory", "value": 100},
                "after_db_snapshot": {"table": "inventory", "value": 99},
            }
        }
        result = _validate_db_evidence_binding(finding)
        assert result["valid"] is False
        assert any("business_operation" in r for r in result["reasons"])

    def test_db_evidence_without_assertion_invalid(self):
        """DB evidence without assertion = invalid."""
        finding = {
            "db_evidence": {
                "before_db_snapshot": {"table": "coupon", "value": "unused"},
                "after_db_snapshot": {"table": "coupon", "value": "used"},
                "business_operation": "POST /api/coupons/redeem",
            }
        }
        result = _validate_db_evidence_binding(finding)
        assert result["valid"] is False
        assert any("db_assertion" in r for r in result["reasons"])

    def test_no_db_evidence_is_fine(self):
        """No DB evidence at all = not a violation."""
        finding = {}
        result = _validate_db_evidence_binding(finding)
        assert result["valid"] is True

    def test_db_unavailable_reason_present_is_valid(self):
        """db_evidence_unavailable_reason present = acceptable (no fabrication)."""
        finding = {
            "db_evidence_unavailable_reason": "No database credentials configured",
            "db_evidence": {"before": {"value": "?"}},
        }
        result = _validate_db_evidence_binding(finding)
        assert result["valid"] is True


# ────────────────────────────────────────────────────────────────

class TestBusinessScenarioCoverage:
    """Verify that business scenarios are properly classified for DB binding."""

    @pytest.mark.parametrize("scenario,operation,expected", [
        ("inventory_deduction", "POST /api/orders", "库存扣减"),
        ("coupon_usage", "POST /api/coupons/redeem", "优惠券使用"),
        ("order_status_flow", "PUT /api/orders/123/status", "订单状态流转"),
        ("payment_idempotency", "POST /api/payments", "支付幂等"),
        ("refund_status", "POST /api/refunds", "退款状态"),
        ("user_data_access", "GET /api/users/456", "用户越权访问"),
    ])
    def test_business_scenario_mapped_to_operation(self, scenario, operation, expected):
        """Each business scenario should be mapped to a specific operation."""
        finding = {
            "db_evidence": {
                "before_db_snapshot": {"value": "before_state"},
                "after_db_snapshot": {"value": "after_state"},
                "db_assertion": f"{expected}验证通过",
                "business_operation": operation,
            },
            "db_evidence_unavailable_reason": "",
        }
        result = _validate_db_evidence_binding(finding)
        assert result["valid"] is True, \
            f"Scenario {scenario} ({expected}) failed: {result['reasons']}"
