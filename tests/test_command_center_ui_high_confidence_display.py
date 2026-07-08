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
        "expected": "订单金额应等于支付金额",
        "actual": "订单金额 100，支付金额 1",
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


def test_command_center_envelope_keeps_cross_verified_high_confidence_ui_grade() -> None:
    install_customer_delivery_gate_patch()
    high_conf = _legacy_ready_item()
    high_conf["id"] = "UI-HC-1"
    high_conf["title"] = "订单详情页状态异常"
    high_conf["severity"] = "P1"
    high_conf["risk_type"] = "ui_execution"
    high_conf["confidence_score"] = 0.85
    high_conf["ui_candidate_gate"] = {"passed": True}
    high_conf["ui_verification"] = {"status": "verified", "reason": "sqlite_row_match"}
    high_conf["high_confidence_candidate"] = True
    high_conf["candidate_tier"] = "high_confidence_ui_candidate"
    high_conf["evidence_quality"] = {"level": "cross_verified", "score": 85, "can_reproduce": True}

    candidate = _legacy_ready_item()
    candidate["id"] = "UI-CAND-1"
    candidate["title"] = "订单列表页状态标签异常"
    candidate["severity"] = "P3"
    candidate["risk_type"] = "ui_execution"
    candidate["confidence_score"] = 0.71
    candidate["ui_candidate_gate"] = {"passed": True}
    candidate["ui_verification"] = {"status": "mismatch", "reason": "page_agent_object_binding_incomplete"}
    candidate["candidate_tier"] = "ui_candidate"
    candidate["evidence_quality"] = {"level": "runtime_consistent", "score": 80, "can_reproduce": True}

    payload = _normalize_command_center_envelope({"ok": True, "data": {"defects": [high_conf, candidate], "clues": []}})
    data = payload["data"]

    assert [item["id"] for item in data["defects"]] == ["UI-HC-1", "UI-CAND-1"]
    assert data["clues"] == []

    defect = data["defects"][0]
    assert defect["verification_badge"] == "ui_verified"
    assert defect["verification_label"] == "已二次验真"
    assert defect["candidate_tier"] == "high_confidence_ui_candidate"
    assert defect["high_confidence_candidate"] is True
    assert defect["priority_label"] == "P1"
    assert defect["evidence_quality"]["level"] == "cross_verified"

    assert data["value_metrics"]["ui_total"] == 2
    assert data["value_metrics"]["ui_candidate_total"] == 2
    assert data["value_metrics"]["ui_verified_candidate_total"] == 1
    assert data["value_metrics"]["ui_high_confidence_candidate_total"] == 1
    assert data["executive_summary"]["ui_verified_candidates"] == 1
    assert data["executive_summary"]["ui_high_confidence_candidates"] == 1
    assert data["scan_meta"]["ui_verified_candidates"] == 1
    assert data["scan_meta"]["ui_high_confidence_candidates"] == 1
