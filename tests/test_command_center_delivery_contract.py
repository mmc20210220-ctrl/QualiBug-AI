from __future__ import annotations

from ai_test_asset_center.command_center_delivery_contract import normalize_command_center_delivery


def _ready_finding() -> dict:
    return {
        "id": "BUG-1",
        "title": "支付金额守恒失败",
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


def test_command_center_delivery_contract_keeps_only_gate_accepted_defects() -> None:
    ready = _ready_finding()
    clue = _ready_finding()
    clue["id"] = "CLUE-1"
    clue["evidence_status"]["business_evidence_status"] = "PENDING"

    normalized = normalize_command_center_delivery({"data": {"risks": [ready, clue], "clues": []}})
    data = normalized["data"]

    assert [item["id"] for item in data["defects"]] == ["BUG-1"]
    assert [item["id"] for item in data["risks"]] == ["BUG-1"]
    assert [item["id"] for item in data["clues"]] == ["CLUE-1"]
    assert data["delivery_contract"]["ready_bug_count"] == 1
    assert data["delivery_contract"]["clue_count"] == 1
    assert data["defects"][0]["customer_visible"] is True
    assert data["clues"][0]["customer_visible"] is False
    assert data["clues"][0]["customer_delivery_gate_reasons"]


def test_command_center_delivery_contract_rechecks_legacy_defects() -> None:
    fake_defect = _ready_finding()
    fake_defect["id"] = "FAKE-DEFECT"
    fake_defect["gate_passed"] = False

    normalized = normalize_command_center_delivery({"data": {"defects": [fake_defect], "risks": []}})
    data = normalized["data"]

    assert data["defects"] == []
    assert data["risks"] == []
    assert [item["id"] for item in data["clues"]] == ["FAKE-DEFECT"]
    assert "GATE_NOT_PASSED" in data["clues"][0]["customer_delivery_gate_reasons"]


def test_command_center_delivery_contract_preserves_existing_clues() -> None:
    ready = _ready_finding()
    existing_clue = {"id": "OLD-CLUE", "title": "历史线索", "customer_delivery_status": "clue"}

    normalized = normalize_command_center_delivery({"data": {"findings": [ready], "clues": [existing_clue]}})
    data = normalized["data"]

    assert [item["id"] for item in data["defects"]] == ["BUG-1"]
    assert any(item["id"] == "OLD-CLUE" for item in data["clues"])
    assert normalized["delivery_contract"]["source"] == "backend_customer_delivery_gate"


def test_command_center_delivery_contract_syncs_summary_counters_after_gate() -> None:
    ready = _ready_finding()
    fake_defect = _ready_finding()
    fake_defect["id"] = "FAKE-DEFECT"
    fake_defect["gate_passed"] = False

    normalized = normalize_command_center_delivery({
        "data": {
            "defects": [ready, fake_defect],
            "scan_meta": {"ready_bug_count": 99, "customer_ready_defects": 99, "internal_clue_count": 0},
            "value_metrics": {"ready_bug_count": 99, "defect_count": 99, "clue_count": 0},
            "executive_summary": {"total_bugs_found": 99, "ready_bugs": 99, "customer_ready_defects": 99, "internal_clues": 0},
        }
    })
    data = normalized["data"]

    assert data["ready_bug_count"] == 1
    assert data["internal_clue_count"] == 1
    assert data["scan_meta"]["ready_bug_count"] == 1
    assert data["scan_meta"]["customer_ready_defects"] == 1
    assert data["scan_meta"]["internal_clue_count"] == 1
    assert data["value_metrics"]["ready_bug_count"] == 1
    assert data["value_metrics"]["defect_count"] == 1
    assert data["value_metrics"]["clue_count"] == 1
    assert data["executive_summary"]["total_bugs_found"] == 1
    assert data["executive_summary"]["ready_bugs"] == 1
    assert data["executive_summary"]["customer_ready_defects"] == 1
    assert data["executive_summary"]["internal_clues"] == 1
