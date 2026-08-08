# -*- coding: utf-8 -*-
"""Unit tests: finding severity/confidence differentiation grading (Task 10).

Covers ``ai_test_asset_center/finding_risk_grading.py``:
- severity grading per violation shape (critical/high/medium/low),
- dynamic confidence from evidence-chain completeness,
- backward compatibility (severity / evidence_quality.level+score preserved),
- idempotency and non-gradeable (internal clue) pass-through.
"""
from __future__ import annotations

from ai_test_asset_center.finding_risk_grading import (
    apply_finding_grading,
    compute_confidence,
    confidence_grade_for,
    grade_severity,
    is_gradeable,
)


def _finding(**overrides):
    finding = {
        "severity": "P1",
        "confidence_score": 0.85,
        "category": "owner_tenant_visibility",
        "risk_family": "authorization",
        "actual": {"leak_detected": True, "owner_can_access": True, "viewer_can_access": True},
        "expected": {"leak_detected": False, "owner_can_access": True, "viewer_can_access": False},
        "description": "control=admin succeeded; treatment=auditor violated the typed assertion",
        "reproduction_steps": ["POST /api/x -> HTTP 200"],
        "reproduction": {"method": "POST", "path": "/api/x", "reproduction_steps": ["POST /api/x -> HTTP 200"]},
        "raw_evidence": {
            "has_real_evidence": True,
            "request_raw": {"method": "POST", "path": "/api/x"},
            "response_raw": {"status_code": 200, "body": "{}"},
            "control_actor": "admin",
            "observations": {"control_succeeded": True},
        },
        "evidence": {"effect_count": 0, "control_succeeded": True},
        "cleanup_failures": 0,
        "delivery_occurrence_count": 3,
        "gate_passed": True,
        "semantic_verdict": "SEMANTIC_CONFIRMED",
        "business_evidence_status": "VALIDATED",
        "failed_assertions": [{"assertion_id": "assert_authorization", "kind": "owner_tenant_visibility", "observer_receipt_ids": ["o1", "o2", "o3"]}],
        "evidence_quality": {"level": "validated", "score": 90, "can_reproduce": True},
    }
    finding.update(overrides)
    return finding


# ── severity: critical ────────────────────────────────────────────────────
def test_conservation_negative_is_critical():
    f = _finding(
        risk_family="conservation",
        category="non_negative",
        actual={"after": {"available_qty": -1, "locked_qty": -3}},
        expected={"operator": "non_negative"},
    )
    assert grade_severity(f) == "critical"


def test_money_negative_in_nested_payload_is_critical():
    f = _finding(
        risk_family="money",
        category="balance_invariant",
        actual={"before": {"balance": 100}, "after": {"balance": -50}},
    )
    assert grade_severity(f) == "critical"


def test_auth_bypass_backdoor_endpoint_is_critical():
    f = _finding(
        risk_family="authorization",
        reproduction={"method": "POST", "path": "/api/auth/debug/token"},
        reproduction_steps=["POST /api/auth/debug/token -> HTTP 200"],
    )
    assert grade_severity(f) == "critical"


def test_destruction_of_others_data_is_critical():
    f = _finding(
        risk_family="isolation",
        category="owner_tenant_visibility",
        actual={"deleted": True, "viewer_can_access": True},
        expected={"deleted": False},
        reproduction={"method": "DELETE", "path": "/api/orders/other/cancel"},
        evidence={"effect_count": 1},
    )
    assert grade_severity(f) == "critical"


# ── severity: high ────────────────────────────────────────────────────────
def test_cross_role_exposure_is_high():
    f = _finding(risk_family="authorization", category="owner_tenant_visibility")
    assert grade_severity(f) == "high"


def test_cross_owner_exposure_isolation_is_high():
    f = _finding(risk_family="isolation", category="owner_tenant_visibility")
    assert grade_severity(f) == "high"


def test_delete_without_observed_effect_is_high_not_critical():
    # DELETE access violation with no evidenced effect: escalation, not destruction.
    f = _finding(
        reproduction={"method": "DELETE", "path": "/api/cart/items/abc"},
        evidence={"effect_count": 0},
    )
    assert grade_severity(f) == "high"


# ── severity: medium ──────────────────────────────────────────────────────
def test_validation_rejection_is_medium():
    f = _finding(
        risk_family="validation",
        category="validation_rejection",
        actual={"status_code": 200, "treatment_effect_count": 1},
        expected={"status_class": 4, "treatment_effect_count": 0},
    )
    assert grade_severity(f) == "medium"


def test_http_status_class_mismatch_is_medium():
    f = _finding(category="http_status_class", actual=404, expected=2)
    assert grade_severity(f) == "medium"


def test_parameter_boundary_negative_without_money_is_medium():
    f = _finding(
        risk_family="validation",
        category="parameter_boundary",
        actual={"after": {"quantity": -5}},
        expected={"operator": "non_negative"},
    )
    assert grade_severity(f) == "medium"


# ── severity: low ─────────────────────────────────────────────────────────
def test_display_copy_family_is_low():
    f = _finding(risk_family="visibility", category="owner_tenant_visibility")
    assert grade_severity(f) == "low"


def test_display_keyword_description_is_low():
    f = _finding(
        risk_family="unknown_family",
        category="some_assertion",
        description="Source rule violated: 成功、失败、取消、无权限等结果必须有文字状态.",
    )
    assert grade_severity(f) == "low"


# ── severity: default / edges ─────────────────────────────────────────────
def test_unknown_family_defaults_to_medium():
    f = _finding(risk_family="uncategorized", category="generic_assertion")
    assert grade_severity(f) == "medium"


def test_internal_clue_is_not_gradeable():
    clue = {"severity": "P2", "source": "experiment_contract_oracle", "evidence": {"demotion_reason": "x"}}
    assert is_gradeable(clue) is False
    assert grade_severity(clue) == ""
    out = apply_finding_grading(clue)
    assert out == clue
    assert "severity_grade" not in out


def test_non_dict_passthrough():
    assert apply_finding_grading(None) is None
    assert apply_finding_grading("x") == "x"


# ── confidence: evidence-chain differentiation ────────────────────────────
def test_full_chain_multi_occurrence_is_high_confidence():
    score, basis = compute_confidence(_finding())
    assert score == 0.95
    assert confidence_grade_for(score) == "high"
    assert "reproduced" in basis and "dual_arm_control" in basis and "cleanup_complete" in basis
    assert "occurrences>=3" in basis


def test_single_occurrence_is_medium_confidence():
    f = _finding(delivery_occurrence_count=1)
    score, basis = compute_confidence(f)
    assert score == 0.75
    assert confidence_grade_for(score) == "medium"
    assert "occurrences=1" in basis


def test_double_occurrence_is_high_confidence():
    f = _finding(delivery_occurrence_count=2)
    score, _basis = compute_confidence(f)
    assert score == 0.85
    assert confidence_grade_for(score) == "high"


def test_missing_reproduction_is_low_confidence():
    f = _finding(reproduction_steps=[], reproduction={}, raw_evidence={})
    score, basis = compute_confidence(f)
    assert score < 0.60
    assert confidence_grade_for(score) == "low"
    assert "no_reproduction" in basis


def test_cleanup_failure_reduces_confidence():
    full = _finding()
    dirty = _finding(cleanup_failures=1)
    score_full, _ = compute_confidence(full)
    score_dirty, basis = compute_confidence(dirty)
    assert score_dirty == round(score_full - 0.10, 2)
    assert "cleanup_incomplete" in basis


def test_no_control_evidence_reduces_confidence():
    f = _finding(raw_evidence={"has_real_evidence": True}, evidence={})
    score, basis = compute_confidence(f)
    assert "no_control_evidence" in basis
    assert score <= 0.75


def test_confidence_capped_at_097():
    f = _finding(delivery_occurrence_count=25)
    score, _ = compute_confidence(f)
    assert score <= 0.97


# ── backward compatibility + integration ──────────────────────────────────
def test_severity_and_evidence_quality_preserved():
    f = _finding()
    out = apply_finding_grading(f)
    assert out["severity"] == "P1"
    assert out["evidence_quality"]["level"] == "validated"
    assert out["evidence_quality"]["score"] == 90
    assert out["severity_grade"] == "high"
    assert out["evidence_quality"]["confidence"] == 0.95
    assert out["evidence_quality"]["confidence_grade"] == "high"
    assert out["grading"]["schema_version"] == "qualibug.finding-grading.v1"


def test_grading_is_idempotent():
    f = _finding()
    once = apply_finding_grading(f)
    twice = apply_finding_grading(once)
    assert once == twice
    assert f["severity"] == "P1"  # input never mutated


def test_input_finding_not_mutated():
    f = _finding()
    snapshot = dict(f)
    apply_finding_grading(f)
    assert f == snapshot


def test_grading_metadata_records_rule_and_basis():
    out = apply_finding_grading(_finding(risk_family="conservation", category="non_negative",
                                        actual={"after": {"qty": -2}}))
    assert out["severity_grade"] == "critical"
    assert "money_conservation_negative" in out["grading"]["rules_applied"]


# ── integration: delivery-path enricher wiring ────────────────────────────
def test_enrich_finding_applies_grading():
    from ai_test_asset_center.evidence_enricher_v3 import enrich_finding

    f = _finding(risk_family="isolation", category="owner_tenant_visibility",
                 evidence_quality={"level": "executed_candidate", "score": 0, "can_reproduce": False})
    out = enrich_finding(f)
    assert out["severity_grade"] == "high"
    assert out["severity"] == "P1"
    assert out["evidence_quality"]["confidence"] > 0
