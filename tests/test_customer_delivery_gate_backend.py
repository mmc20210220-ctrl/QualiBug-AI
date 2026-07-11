from __future__ import annotations

from ai_test_asset_center.customer_delivery_gate import (
    customer_delivery_rejection_explanations,
    customer_delivery_rejection_reasons,
    explain_rejection_reason,
    is_customer_deliverable_defect,
    split_customer_delivery_tracks,
)


def _ready_finding() -> dict:
    return {
        "id": "BUG-1",
        "finding_id": "finding-1",
        "evidence_id": "evidence-1",
        "execution_id": "execution-1",
        "experiment_id": "experiment-1",
        "obligation_id": "obligation-1",
        "slice_id": "slice-1",
        "candidate_id": "candidate-1",
        "title": "支付金额守恒失败",
        "bug_status": "reproduced",
        "gate_passed": True,
        "execution_status": "executed",
        "confirmation_status": "validated_candidate",
        "evidence_quality": {
            "level": "validated",
            "score": 95,
            "can_reproduce": True,
        },
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
            "sandbox_write": {
                "cleanup": {"status": "completed", "receipt_ref": "audit://cleanup/BUG-1"}
            },
        },
        "reproduction": {
            "method": "POST",
            "path": "/api/payments",
            "is_synthetic": False,
            "har_evidence": {"status_code": 200, "response_body": {"paid_amount": 1}},
        },
    }


def test_backend_gate_accepts_only_fully_validated_replayable_defect() -> None:
    assert is_customer_deliverable_defect(_ready_finding()) is True
    assert customer_delivery_rejection_reasons(_ready_finding()) == []
    assert customer_delivery_rejection_explanations(_ready_finding()) == []


def test_backend_gate_rejects_incomplete_mainline_identity_chain() -> None:
    finding = _ready_finding()
    finding.pop("experiment_id")

    assert is_customer_deliverable_defect(finding) is False
    assert "IDENTITY_CHAIN_INCOMPLETE" in customer_delivery_rejection_reasons(finding)


def test_backend_gate_accepts_traceable_legacy_runtime_identity() -> None:
    finding = _ready_finding()
    for field in (
        "candidate_id",
        "slice_id",
        "obligation_id",
        "experiment_id",
        "execution_id",
    ):
        finding.pop(field)
    finding["source"] = "v12_state_graph"
    finding["raw_evidence"]["execution_trace"] = {"evidence_id": finding["evidence_id"]}

    assert is_customer_deliverable_defect(finding) is True
    assert "IDENTITY_CHAIN_INCOMPLETE" not in customer_delivery_rejection_reasons(finding)


def test_backend_gate_rejects_missing_business_evidence_status() -> None:
    finding = _ready_finding()
    finding.pop("evidence_status")

    assert is_customer_deliverable_defect(finding) is False
    assert "BUSINESS_EVIDENCE_NOT_VALIDATED" in customer_delivery_rejection_reasons(finding)


def test_backend_gate_rejects_missing_requirements() -> None:
    finding = _ready_finding()
    finding["evidence_status"]["missing_requirements"] = ["AFTER_SNAPSHOT_MISSING"]

    assert is_customer_deliverable_defect(finding) is False
    assert "BUSINESS_EVIDENCE_NOT_VALIDATED" in customer_delivery_rejection_reasons(finding)


def test_backend_gate_rejects_low_quality_or_non_replayable_evidence() -> None:
    low_score = _ready_finding()
    low_score["evidence_quality"]["score"] = 89

    cannot_reproduce = _ready_finding()
    cannot_reproduce["evidence_quality"]["can_reproduce"] = False

    assert is_customer_deliverable_defect(low_score) is False
    assert is_customer_deliverable_defect(cannot_reproduce) is False
    assert "EVIDENCE_QUALITY_NOT_VALIDATED" in customer_delivery_rejection_reasons(low_score)
    assert "EVIDENCE_QUALITY_NOT_VALIDATED" in customer_delivery_rejection_reasons(cannot_reproduce)


def test_backend_gate_rejects_synthetic_or_blocked_findings() -> None:
    synthetic = _ready_finding()
    synthetic["reproduction"]["is_synthetic"] = True

    blocked = _ready_finding()
    blocked["block_reason"] = "environment_blocked"

    assert is_customer_deliverable_defect(synthetic) is False
    assert is_customer_deliverable_defect(blocked) is False
    assert "MISSING_REAL_REPLAY_ASSET" in customer_delivery_rejection_reasons(synthetic)
    assert "BLOCKED_ENVIRONMENT_BLOCKED" in customer_delivery_rejection_reasons(blocked)


def test_backend_gate_rejects_auth_route_and_coverage_blockers() -> None:
    for marker in ("auth_blocked", "route_blocked", "coverage_gap", "not_reproduced"):
        finding = _ready_finding()
        finding["block_reason"] = marker
        reasons = customer_delivery_rejection_reasons(finding)
        assert is_customer_deliverable_defect(finding) is False, marker
        assert f"BLOCKED_{marker.upper()}" in reasons


def test_backend_gate_rejects_non_executed_or_unconfirmed_results() -> None:
    not_executed = _ready_finding()
    not_executed["execution_status"] = "planned"

    not_confirmed = _ready_finding()
    not_confirmed["confirmation_status"] = "needs_review"

    assert is_customer_deliverable_defect(not_executed) is False
    assert is_customer_deliverable_defect(not_confirmed) is False
    assert "NOT_EXECUTED" in customer_delivery_rejection_reasons(not_executed)
    assert "NOT_CONFIRMED" in customer_delivery_rejection_reasons(not_confirmed)


def test_backend_gate_rejects_missing_real_request_response_or_failure_assertion() -> None:
    no_request = _ready_finding()
    no_request["raw_evidence"]["request_raw"] = {}
    no_request["reproduction"]["method"] = ""
    no_request["reproduction"]["path"] = ""

    no_response = _ready_finding()
    no_response["raw_evidence"]["response_raw"] = {}
    no_response["reproduction"]["har_evidence"] = {}

    no_assertion = _ready_finding()
    no_assertion["expected"] = ""
    no_assertion["actual"] = ""
    no_assertion["failed_assertions"] = []
    no_assertion["expected_actual_comparison"] = {"difference": ""}

    assert is_customer_deliverable_defect(no_request) is False
    assert is_customer_deliverable_defect(no_response) is False
    assert is_customer_deliverable_defect(no_assertion) is False
    assert "MISSING_REAL_REPLAY_ASSET" in customer_delivery_rejection_reasons(no_request)
    assert "MISSING_CUSTOMER_FACING_HARD_EVIDENCE" in customer_delivery_rejection_reasons(no_response)
    assert "MISSING_CUSTOMER_FACING_HARD_EVIDENCE" in customer_delivery_rejection_reasons(no_assertion)


def test_backend_gate_explains_rejection_reasons_for_internal_users() -> None:
    finding = _ready_finding()
    finding["execution_status"] = "planned"

    explanations = customer_delivery_rejection_explanations(finding)

    assert any(item["code"] == "NOT_EXECUTED" for item in explanations)
    assert all(item.get("label") for item in explanations)
    assert all(item.get("detail") for item in explanations)
    assert all(item.get("next_action") for item in explanations)
    assert explain_rejection_reason("NOT_EXECUTED")["label"] == "尚未真实执行"


def test_backend_gate_splits_non_ready_items_into_internal_clues() -> None:
    ready = _ready_finding()
    clue = _ready_finding()
    clue["id"] = "CLUE-1"
    clue["evidence_status"]["business_evidence_status"] = "PENDING"

    defects, clues = split_customer_delivery_tracks([ready, clue])

    assert [item["id"] for item in defects] == ["BUG-1"]
    assert [item["id"] for item in clues] == ["CLUE-1"]
    assert defects[0]["customer_visible"] is True
    assert clues[0]["customer_visible"] is False
    assert defects[0]["customer_delivery_status"] == "defect"
    assert clues[0]["customer_delivery_status"] == "clue"
    assert defects[0]["customer_delivery_gate_reasons"] == []
    assert defects[0]["customer_delivery_gate_explanations"] == []
    assert "BUSINESS_EVIDENCE_NOT_VALIDATED" in clues[0]["customer_delivery_gate_reasons"]
    assert clues[0]["customer_delivery_gate_explanations"][0]["label"]
    assert clues[0]["customer_delivery_gate_explanations"][0]["next_action"]


def test_backend_gate_accepts_db_snapshot_replay_asset_without_har() -> None:
    finding = _ready_finding()
    finding["raw_evidence"]["response_raw"] = {}
    finding["raw_evidence"]["db_snapshot"] = {
        "table": "orders",
        "assertion": "orders row count changed 1->2",
        "before": {"row_count": 1},
        "after": {"row_count": 2},
    }
    finding["reproduction"]["har_evidence"] = {}

    assert is_customer_deliverable_defect(finding) is True
    assert customer_delivery_rejection_reasons(finding) == []
