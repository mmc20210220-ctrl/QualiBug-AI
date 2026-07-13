from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.customer_delivery_gate import (
    apply_governed_campaign_cleanup,
    build_customer_delivery_gate_receipt,
    customer_delivery_rejection_reasons,
    is_customer_deliverable_defect,
)
from ai_test_asset_center.discovery_quality_projection import attach_quality_projection_to_scan_result
from ai_test_asset_center.discovery_mainline_contract import build_mainline_run_contract


MAINLINE_RUN = build_mainline_run_contract(
    mainline_authority="experiment_candidate",
    run_id="RUN-CLEANUP-1",
    campaign_id="CMP-CLEANUP-1",
    target_id="TARGET-CLEANUP-1",
    environment_id="ENV-CLEANUP-1",
    policy_version="v2",
    evaluation_mode="operational",
)


def _valid_finding() -> dict:
    return {
        "candidate_id": "candidate-1",
        "slice_id": "slice-1",
        "obligation_id": "obligation-1",
        "experiment_id": "experiment-1",
        "execution_id": "execution-1",
        "evidence_id": "evidence-1",
        "finding_id": "finding-1",
        "mainline_run": {
            "contract_fingerprint": MAINLINE_RUN["contract_fingerprint"]
        },
        "title": "observed defect",
        "severity": "P1",
        "bug_status": "reproduced",
        "gate_passed": True,
        "execution_status": "executed",
        "confirmation_status": "confirmed",
        "customer_delivery_status": "defect",
        "expected": "rejected",
        "actual": "accepted",
        "timestamp": "2026-07-10T00:00:00Z",
        "evidence_consistency": {"verdict": "confirmed"},
        "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
        "evidence_status": {
            "semantic_verdict": "SEMANTIC_CONFIRMED",
            "business_evidence_status": "VALIDATED",
            "final_review_status": "CUSTOMER_READY",
            "missing_requirements": [],
        },
        "reproduction": {
            "method": "PATCH",
            "path": "/api/resources/{id}",
            "is_synthetic": False,
            "har_evidence": {"status_code": 200, "response_body": {"status": "accepted"}},
        },
        "raw_evidence": {
            "request_raw": {"method": "PATCH", "path": "/api/resources/{id}"},
            "response_raw": {"status_code": 200, "body": {"status": "accepted"}},
            "timestamp": "2026-07-10T00:00:00Z",
            "has_real_evidence": True,
        },
    }


def test_delivery_gate_emits_terminal_receipt_for_deliverable_finding() -> None:
    finding = _valid_finding()
    finding["evidence"] = {
        "cleanup": {"status": "completed", "receipt_ref": "cleanup-receipt-1"}
    }

    receipt = build_customer_delivery_gate_receipt(
        finding,
        obligation_id="obligation-1",
        execution_id="execution-1",
    )

    assert receipt["status"] == "DELIVERABLE"
    assert receipt["execution_id"] == "execution-1"
    assert receipt["finding_id"] == "finding-1"
    assert receipt["reason_code"] == ""
    assert receipt["gate_receipt_id"]


def test_delivery_gate_rejects_executed_obligation_without_oracle_violation() -> None:
    receipt = build_customer_delivery_gate_receipt(
        None,
        obligation_id="obligation-1",
        execution_id="execution-1",
    )

    assert receipt["status"] == "REJECTED"
    assert receipt["reason_code"] == "ORACLE_NOT_VIOLATED"
    assert receipt["finding_id"] == ""
    assert receipt["gate_receipt_id"]
    next_execution = build_customer_delivery_gate_receipt(
        None,
        obligation_id="obligation-1",
        execution_id="execution-2",
    )
    assert next_execution["gate_receipt_id"] != receipt["gate_receipt_id"]


def test_failed_governed_cleanup_blocks_customer_delivery() -> None:
    finding = _valid_finding()
    finding["evidence"] = {"cleanup": {"status": "failed", "receipt_ref": ""}}

    assert is_customer_deliverable_defect(finding) is False
    assert "CLEANUP_NOT_SUCCEEDED" in customer_delivery_rejection_reasons(finding)


def test_non_reversible_governed_cleanup_blocks_customer_delivery() -> None:
    finding = _valid_finding()
    finding["evidence"] = {
        "cleanup": {"status": "not_reversible", "receipt_ref": "/api/resources/{id}"}
    }

    assert is_customer_deliverable_defect(finding) is False
    assert "CLEANUP_NOT_SUCCEEDED" in customer_delivery_rejection_reasons(finding)


def test_validated_runtime_db_write_evidence_is_not_demoted_by_not_reversible_cleanup() -> None:
    finding = _valid_finding()
    finding["evidence"] = {"cleanup": {"status": "not_reversible", "receipt_ref": ""}}
    finding["raw_evidence"]["db_snapshot"] = {
        "status": "captured",
        "any_change": True,
        "changed_tables": [{"table": "orders", "added": 1}],
    }

    assert "CLEANUP_NOT_SUCCEEDED" not in customer_delivery_rejection_reasons(finding)
    assert is_customer_deliverable_defect(finding) is True


def test_successful_cleanup_requires_receipt() -> None:
    finding = _valid_finding()
    finding["evidence"] = {"cleanup": {"status": "completed", "receipt_ref": ""}}

    assert "CLEANUP_RECEIPT_MISSING" in customer_delivery_rejection_reasons(finding)
    finding["evidence"]["cleanup"]["receipt_ref"] = "cleanup-receipt-1"
    assert is_customer_deliverable_defect(finding) is True


def test_write_method_without_cleanup_fails_closed_and_read_only_is_explicit() -> None:
    finding = _valid_finding()
    assert is_customer_deliverable_defect(finding) is False
    assert "CLEANUP_EVIDENCE_MISSING" in customer_delivery_rejection_reasons(finding)
    declared_read_only = deepcopy(finding)
    declared_read_only["evidence"] = {"cleanup": {"status": "read_only", "receipt_ref": ""}}
    assert is_customer_deliverable_defect(declared_read_only) is True
    explicit_safe_read_only = deepcopy(finding)
    explicit_safe_read_only["execution_semantics"] = "safe_read_only"
    assert is_customer_deliverable_defect(explicit_safe_read_only) is True


def test_not_reversible_or_not_applicable_write_cleanup_stays_internal() -> None:
    finding = _valid_finding()
    finding["evidence"] = {
        "cleanup": {"status": "not_reversible", "receipt_ref": "/api/orders/{id}/cancel"},
    }
    assert "CLEANUP_NOT_SUCCEEDED" in customer_delivery_rejection_reasons(finding)
    assert is_customer_deliverable_defect(finding) is False

    na = deepcopy(finding)
    na["evidence"] = {"cleanup": {"status": "not_applicable", "receipt_ref": ""}}
    assert "CLEANUP_NOT_SUCCEEDED" in customer_delivery_rejection_reasons(na)
    assert is_customer_deliverable_defect(na) is False


def test_action_write_not_required_without_no_mutation_proof_stays_internal() -> None:
    finding = _valid_finding()
    finding["evidence"] = {
        "cleanup": {"status": "not_required", "receipt_ref": "/api/orders/{id}/cancel"},
    }
    assert "CLEANUP_NOT_SUCCEEDED" in customer_delivery_rejection_reasons(finding)
    assert is_customer_deliverable_defect(finding) is False


def test_action_style_strategy_allows_not_required_cleanup() -> None:
    finding = _valid_finding()
    finding["evidence"] = {
        "cleanup": {
            "status": "not_required",
            "strategy": "action_post_on_existing_resource",
            "receipt_ref": "/api/orders/{id}/cancel",
        },
    }
    assert "CLEANUP_NOT_SUCCEEDED" not in customer_delivery_rejection_reasons(finding)
    assert is_customer_deliverable_defect(finding) is True


def test_rejected_write_with_observer_unchanged_proof_allows_not_required() -> None:
    finding = _valid_finding()
    finding["evidence"] = {
        "cleanup": {
            "status": "not_required",
            "strategy": "rejected_write_observer_unchanged",
            "receipt_ref": "/api/orders/{id}/cancel",
        },
    }
    assert "CLEANUP_NOT_SUCCEEDED" not in customer_delivery_rejection_reasons(finding)
    assert is_customer_deliverable_defect(finding) is True


def test_governed_campaign_reset_does_not_readjudicate_cleanup_failure() -> None:
    finding = _valid_finding()
    finding["evidence"] = {"cleanup": {"status": "failed", "receipt_ref": ""}}
    defects, clues = apply_governed_campaign_cleanup(
        [finding],
        {
            "status": "SUCCEEDED",
            "dirty_environment": False,
            "audit_receipt_id": "campaign-cleanup-audit-1",
            "after_cleanup_observation_ref": "state:clean",
        },
    )

    assert defects == []
    assert len(clues) == 1
    assert clues[0]["evidence"]["cleanup"] == {
        "status": "failed",
        "receipt_ref": "",
    }


def test_governed_campaign_reset_preserves_upstream_cleanup_terminal() -> None:
    finding = _valid_finding()
    finding["evidence"] = {"cleanup": {"status": "not_reversible", "receipt_ref": "/api/resources/{id}"}}
    demoted = deepcopy(finding)
    demoted["upstream_gate_passed"] = True
    demoted["gate_passed"] = False
    demoted["customer_delivery_status"] = "candidate"
    demoted["customer_delivery_gate_reasons"] = ["CLEANUP_NOT_SUCCEEDED"]

    defects, clues = apply_governed_campaign_cleanup(
        [demoted],
        {
            "status": "SUCCEEDED",
            "dirty_environment": False,
            "audit_receipt_id": "campaign-cleanup-audit-1",
            "after_cleanup_observation_ref": "state:clean",
        },
    )

    assert defects == []
    assert len(clues) == 1
    assert clues[0]["gate_passed"] is False
    assert clues[0]["customer_delivery_status"] == "candidate"
    assert clues[0]["customer_delivery_gate_reasons"] == [
        "CLEANUP_NOT_SUCCEEDED"
    ]


def test_campaign_cleanup_cannot_update_formal_projection() -> None:
    finding = _valid_finding()
    finding["evidence"] = {"cleanup": {"status": "not_reversible", "receipt_ref": "/api/resources/{id}"}}
    demoted = deepcopy(finding)
    demoted["upstream_gate_passed"] = True
    demoted["gate_passed"] = False
    demoted["customer_delivery_status"] = "candidate"
    demoted["customer_delivery_gate_reasons"] = ["CLEANUP_NOT_SUCCEEDED"]
    scan_result = {
        "mainline_run": MAINLINE_RUN,
        "findings": [],
        "candidate_findings": [demoted],
        "discovery_funnel": {
            "validated_bug_count": 0,
            "formal_count_projection": {"formal_customer_deliverable_count": 0},
        },
    }

    defects, clues = apply_governed_campaign_cleanup(
        list(scan_result["findings"]) + list(scan_result["candidate_findings"]),
        {
            "status": "SUCCEEDED",
            "dirty_environment": False,
            "audit_receipt_id": "campaign-cleanup-audit-1",
            "after_cleanup_observation_ref": "state:clean",
        },
    )
    projected = attach_quality_projection_to_scan_result(
        {
            **scan_result,
            "findings": defects,
            "candidate_findings": clues,
        }
    )

    assert projected["formal_count_projection"]["formal_customer_deliverable_count"] == 0
    assert projected["discovery_funnel"]["validated_bug_count"] == 0
    assert projected["discovery_funnel"]["formal_count_projection"]["formal_customer_deliverable_count"] == 0
    assert projected["discovery_funnel"]["formal_count_projection"]["funnel_validated_bug_count"] == 0
    assert (
        projected["discovery_funnel"]["formal_count_projection"]["count_consistency"][
            "formal_equals_funnel_validated"
        ]
        is True
    )


def test_governed_campaign_reset_does_not_promote_other_evidence_gaps() -> None:
    finding = _valid_finding()
    finding["evidence"] = {"cleanup": {"status": "failed", "receipt_ref": ""}}
    finding["evidence_quality"]["score"] = 20
    defects, clues = apply_governed_campaign_cleanup(
        [finding],
        {
            "status": "SUCCEEDED",
            "dirty_environment": False,
            "audit_receipt_id": "campaign-cleanup-audit-1",
            "after_cleanup_observation_ref": "state:clean",
        },
    )

    assert defects == []
    assert len(clues) == 1
    assert clues[0]["evidence_quality"]["score"] == 20
    assert "EVIDENCE_QUALITY_NOT_VALIDATED" in customer_delivery_rejection_reasons(
        clues[0]
    )
