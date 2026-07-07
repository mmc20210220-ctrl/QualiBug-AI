from __future__ import annotations

from ai_test_asset_center.private_pilot_server import install_customer_delivery_gate_patch
from ai_test_asset_center.private_pilot_service import _normalize_command_center_envelope


def _legacy_ready_item() -> dict:
    return {
        "id": "BUG-1",
        "title": "支付金额守恒失败",
        "severity": "P1",
        "bug_status": "reproduced",
        "gate_passed": True,
        "execution_status": "executed",
        "confirmation_status": "validated_candidate",
        "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
        "evidence_status": {
            "semantic_verdict": "SEMANTIC_CONFIRMED",
            "business_evidence_status": "VALIDATED",
            "final_review_status": "VALIDATED_CANDIDATE",
            "missing_requirements": [],
        },
        "raw_evidence": {
            "has_real_evidence": True,
            "timestamp": "2026-07-06T12:00:00Z",
            "request_raw": {"method": "POST", "path": "/api/payments"},
            "response_raw": {"status_code": 200, "body": {"paid_amount": 1}},
        },
        "reproduction": {
            "method": "POST",
            "path": "/api/payments",
            "is_synthetic": False,
            "har_evidence": {"status_code": 200, "response_body": {"paid_amount": 1}},
        },
    }


def test_command_center_envelope_surfaces_execution_evidence_summary_and_metrics() -> None:
    install_customer_delivery_gate_patch()
    payload = _normalize_command_center_envelope(
        {
            "ok": True,
            "data": {
                "risks": [_legacy_ready_item()],
                "ui_execution_summary": {
                    "status": "partial",
                    "requested": 2,
                    "executed": 1,
                    "failed": 0,
                    "blocked": 1,
                    "artifact_count": 2,
                    "finding_count": 1,
                    "created_data_count": 1,
                    "evidence_captured_count": 1,
                    "provider_distribution": {"page_agent": 2},
                    "artifact_refs": ["platform_workspace/demo/page_agent_runs/scan/ui_req_1/final.png"],
                    "summary": "UI execution partial: requested 2, executed 1, blocked 1.",
                },
            },
        }
    )
    data = payload["data"]

    assert data["execution_evidence_summary"]["status"] == "partial"
    assert data["execution_evidence_summary"]["executed"] == 1
    assert data["execution_evidence_summary"]["created_data_count"] == 1
    assert data["scan_meta"]["ui_execution_requested"] == 2
    assert data["scan_meta"]["ui_execution_executed"] == 1
    assert data["value_metrics"]["ui_execution_artifact_count"] == 2
    assert data["executive_summary"]["ui_execution_summary"] == "UI execution partial: requested 2, executed 1, blocked 1."
