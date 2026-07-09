from ai_test_asset_center import display_ready_formatter as formatter
from ai_test_asset_center.display_ready_no_fix_advice_patch import (
    install_display_ready_no_fix_advice_patch,
    restore_display_ready_no_fix_advice_patch,
)


def _sample_finding() -> dict:
    return {
        "risk_id": "BUG-1",
        "title": "[V12 SecurityOracle] 越权访问已复现",
        "severity": "P1",
        "risk_type": "permission_bypass",
        "status": "confirmed",
        "_api_method": "GET",
        "_api_path": "/api/orders/1",
        "expected_behavior": "普通用户只能查看自己的订单",
        "actual_behavior": "普通用户可以查看他人订单",
        "evidence": {
            "calls": [
                {
                    "call": "GET /api/orders/1",
                    "path": "/api/orders/1",
                    "results": {
                        "user_a": {"status": 200, "body": "{\"order_id\":1}", "duration_ms": 12}
                    },
                }
            ]
        },
        "failed_assertions": [
            {"type": "authorization", "expected": "403", "actual": "200", "detail": "越权响应成功"}
        ],
        "reproducibility": {"reproducible": True, "reproduction_confidence": 1.0},
        "semantic_verdict": "SEMANTIC_CONFIRMED",
        "business_evidence_status": "VALIDATED",
    }


def test_display_ready_patch_strips_fix_advice_from_technical_details() -> None:
    restore_display_ready_no_fix_advice_patch()
    install_display_ready_no_fix_advice_patch()
    try:
        details = formatter._build_technical_details(
            _sample_finding(),
            {"relevant_tables": [], "trace_id": ""},
            {"method": "GET", "path": "/api/orders/1"},
        )

        assert "recommended_fix" not in details
        assert "possible_root_cause" not in details
        assert details["api_endpoint"]["path"] == "/api/orders/1"
        assert details["product_responsibility_boundary"]["no_fix_advice"] is True
        assert "regression_verification_obligations" in details
    finally:
        restore_display_ready_no_fix_advice_patch()


def test_display_ready_patch_strips_fix_advice_from_formatted_finding() -> None:
    restore_display_ready_no_fix_advice_patch()
    install_display_ready_no_fix_advice_patch()
    try:
        formatted = formatter._format_single_finding(_sample_finding())

        assert "recommended_fix" not in formatted
        assert "regression_suggestions" not in formatted
        assert "recommended_fix" not in formatted["technical_details"]
        assert "possible_root_cause" not in formatted["technical_details"]
        assert formatted["expected_actual_comparison"]["difference"]
        assert formatted["raw_evidence"]["has_real_evidence"] is True
        assert formatted["product_responsibility_boundary"]["no_fix_advice"] is True
        assert formatted["technical_details"]["product_responsibility_boundary"]["no_fix_advice"] is True
        assert "regression_verification_obligations" in formatted["technical_details"]
    finally:
        restore_display_ready_no_fix_advice_patch()
