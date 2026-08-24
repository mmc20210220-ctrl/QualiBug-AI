"""Tests for generic small-scale validation gate and receipt validation.

Covers SPEC §19: 20 test scenarios for the generic bootstrap materialization
and small-scale execution gate.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from ai_test_asset_center.small_scale_validation_gate import (
    HARD_BUDGET_CAP,
    FORMAL_BUDGET,
    SMALL_SCALE_BUDGET,
    apply_gate_invalidation,
    audit_anti_hardcoding,
    audit_gate_module_hardcoding,
    check_validation_gate,
    get_validation_budget,
    is_placeholder_value,
    mark_run_invalid,
    select_target_rules_by_structure,
    truncate_to_budget,
    validate_entity_materialization,
    validate_pre_request_checks,
)
from ai_test_asset_center.enterprise_test_data_receipts import (
    issue_test_data_receipt,
    validate_receipt_for_execution,
    verify_test_data_receipt,
)


# ── Fixtures ──

@pytest.fixture
def tmp_root(tmp_path: Path) -> Path:
    """Temporary project root."""
    return tmp_path


@pytest.fixture
def sample_obligations() -> list[dict[str, Any]]:
    """Generic obligations without project-specific content."""
    return [
        {
            "obligation_id": "obl_001",
            "rule_id": "conservation.entity_a.sum_check",
            "rule_type": "CONSERVATION",
            "confidence": 0.85,
            "structured_expression": {"kind": "SUM", "sum_field": "amount", "aggregate": True},
            "observer_requirements": [{"observer_type": "before_state"}, {"observer_type": "after_state"}],
            "fixture_dependencies": [{"resolved": True}],
            "related_entities": ["entity_b"],
        },
        {
            "obligation_id": "obl_002",
            "rule_id": "causal.entity_c.postcondition",
            "rule_type": "CAUSAL_POSTCONDITION",
            "confidence": 0.9,
            "structured_expression": {"kind": "IMPLIES", "before_field": "status"},
            "observer_requirements": [{"observer_type": "after_state"}],
            "fixture_dependencies": [{"resolved": True}],
            "related_entities": [],
        },
        {
            "obligation_id": "obl_003",
            "rule_id": "state.entity_d.transition_valid",
            "rule_type": "STATE_TRANSITION",
            "confidence": 0.75,
            "structured_expression": {"kind": "STATE", "from_state": "active"},
            "observer_requirements": [{"observer_type": "before_state"}],
            "fixture_dependencies": [],
            "related_entities": [],
        },
        {
            "obligation_id": "obl_004",
            "rule_id": "limit.entity_e.max_value",
            "rule_type": "LIMIT_CONSTRAINT",
            "confidence": 0.6,
            "structured_expression": {"kind": "LIMIT"},
            "observer_requirements": [],
            "fixture_dependencies": [],
            "related_entities": [],
        },
        {
            "obligation_id": "obl_005",
            "rule_id": "compensation.entity_f.undo",
            "rule_type": "COMPENSATION",
            "confidence": 0.8,
            "structured_expression": {"kind": "IMPLIES", "delta": True},
            "observer_requirements": [{"observer_type": "after_state"}],
            "fixture_dependencies": [{"resolved": True}],
            "related_entities": ["entity_g"],
        },
    ]


@pytest.fixture
def sample_experiments() -> dict[str, dict[str, Any]]:
    """Compiled experiments keyed by obligation_id."""
    return {
        "obl_001": {"experiment_id": "exp_001", "compile_receipt": {"status": "COMPILED"}},
        "obl_002": {"experiment_id": "exp_002", "compile_receipt": {"status": "COMPILED"}},
        "obl_003": {"experiment_id": "exp_003", "compile_receipt": {"status": "READY"}},
        "obl_004": {"experiment_id": "exp_004", "compile_receipt": {"status": "COMPILED"}},
        "obl_005": {"experiment_id": "exp_005", "compile_receipt": {"status": "COMPILED"}},
    }


# ── Test 1: Fixed Project A rules do NOT enter target set ──

def test_project_a_rules_not_in_generic_selection():
    """Fixed Project A rules must not appear in generic target selection."""
    # These are the old hardcoded rules that must be gone
    project_a_rules = [
        "conservation.inventory.reserve_sum",
        "conservation.order.amount_formula",
        "conservation.refund.amount_lte_paid",
        "causal.order.create_inventory_reserve",
        "causal.order.cancel_inventory_release",
        "causal.payment.pay_status_change",
        "state.order.pending_to_paid",
        "state.order.pending_to_cancelled",
        "state.order.paid_to_shipped",
    ]
    # Self-audit must pass
    audit = audit_gate_module_hardcoding()
    assert audit["generic_target_selection"] == "PASS", f"Violations: {audit['violations']}"
    # None of the project A rules should be in the module source
    import inspect
    from ai_test_asset_center import small_scale_validation_gate
    source = inspect.getsource(small_scale_validation_gate)
    for rule in project_a_rules:
        assert rule not in source, f"Found hardcoded rule: {rule}"


# ── Test 2: Rules selected by structural scoring ──

def test_structural_scoring_selection(sample_obligations, sample_experiments):
    """Rules must be selected by structural scoring, not by name."""
    result = select_target_rules_by_structure(sample_obligations, sample_experiments)
    assert result["selection_method"] == "structural_scoring"
    assert result["selected_count"] > 0
    # Higher confidence + compiled should score higher
    selected_ids = result["selected_obligation_ids"]
    assert "obl_002" in selected_ids  # confidence 0.9 + compiled
    assert "obl_001" in selected_ids  # confidence 0.85 + compiled + multi-entity


# ── Test 3: Create API response extracts real ID ──

def test_entity_materialization_from_receipt():
    """Entity ID proven by receipt is valid."""
    result = validate_entity_materialization(
        "real-entity-abc123",
        receipt_proven=True,
        observer_proven=False,
    )
    assert result["valid"] is True
    assert result["is_placeholder"] is False
    assert result["receipt_proven"] is True


# ── Test 4: Post-creation GET verification ──

def test_entity_materialization_from_observer():
    """Entity ID proven by observer GET is valid."""
    result = validate_entity_materialization(
        "real-entity-xyz789",
        receipt_proven=False,
        observer_proven=True,
    )
    assert result["valid"] is True
    assert result["observer_proven"] is True


# ── Test 5: Relation verification ──

def test_receipt_relation_not_verified(tmp_root):
    """Receipt with unverified relations must fail."""
    receipt = issue_test_data_receipt(
        "test_project",
        root=tmp_root,
        kind="creation",
        campaign_id="camp_001",
        scope_id="scope_001",
        environment_ref="env_local",
        actor={"name": "test"},
        data_scope_ref="disposable_001",
    )
    result = validate_receipt_for_execution(
        "test_project",
        root=tmp_root,
        receipt_id=receipt["receipt_id"],
        run_id="run_001",
        campaign_id="camp_001",
        environment_ref="env_local",
        required_relations=[
            {"parent_entity_id": "e1", "child_entity_id": "e2", "verified": False}
        ],
    )
    assert result["valid"] is False
    assert result["code"] == "RECEIPT_RELATION_NOT_VERIFIED"


# ── Test 6: State precondition verification ──

def test_receipt_precondition_not_verified(tmp_root):
    """Receipt with unverified preconditions must fail."""
    receipt = issue_test_data_receipt(
        "test_project",
        root=tmp_root,
        kind="creation",
        campaign_id="camp_001",
        scope_id="scope_001",
        environment_ref="env_local",
        actor={"name": "test"},
        data_scope_ref="disposable_001",
    )
    result = validate_receipt_for_execution(
        "test_project",
        root=tmp_root,
        receipt_id=receipt["receipt_id"],
        run_id="run_001",
        campaign_id="camp_001",
        environment_ref="env_local",
        required_preconditions=[
            {"rule_id": "rule_x", "passed": False}
        ],
    )
    assert result["valid"] is False
    assert result["code"] == "RECEIPT_PRECONDITION_NOT_VERIFIED"


# ── Test 7: Receipt generation ──

def test_receipt_generation(tmp_root):
    """Receipt must be generated with valid hash."""
    receipt = issue_test_data_receipt(
        "test_project",
        root=tmp_root,
        kind="creation",
        campaign_id="camp_gen",
        scope_id="scope_gen",
        environment_ref="env_test",
        actor={"name": "bootstrap"},
        data_scope_ref="disposable_gen",
    )
    assert receipt["receipt_id"].startswith("tdr_")
    assert receipt["receipt_hash"]
    assert receipt["campaign_id"] == "camp_gen"
    assert receipt["environment_ref"] == "env_test"


# ── Test 8: Receipt Run mismatch ──

def test_receipt_run_mismatch(tmp_root):
    """Receipt bound to different campaign must fail."""
    receipt = issue_test_data_receipt(
        "test_project",
        root=tmp_root,
        kind="creation",
        campaign_id="camp_A",
        scope_id="scope_A",
        environment_ref="env_A",
        actor={"name": "test"},
        data_scope_ref="disposable_A",
    )
    result = validate_receipt_for_execution(
        "test_project",
        root=tmp_root,
        receipt_id=receipt["receipt_id"],
        run_id="run_B",
        campaign_id="camp_B",  # Different campaign
        environment_ref="env_A",
    )
    assert result["valid"] is False
    assert result["code"] == "RECEIPT_RUN_MISMATCH"


# ── Test 9: Receipt Environment mismatch ──

def test_receipt_environment_mismatch(tmp_root):
    """Receipt bound to different environment must fail."""
    receipt = issue_test_data_receipt(
        "test_project",
        root=tmp_root,
        kind="creation",
        campaign_id="camp_C",
        scope_id="scope_C",
        environment_ref="env_prod",
        actor={"name": "test"},
        data_scope_ref="disposable_C",
    )
    result = validate_receipt_for_execution(
        "test_project",
        root=tmp_root,
        receipt_id=receipt["receipt_id"],
        run_id="run_C",
        campaign_id="camp_C",
        environment_ref="env_staging",  # Different environment
    )
    assert result["valid"] is False
    assert result["code"] == "RECEIPT_ENVIRONMENT_MISMATCH"


# ── Test 10: Receipt expired ──

def test_receipt_expired(tmp_root):
    """Receipt older than max_age must fail."""
    receipt = issue_test_data_receipt(
        "test_project",
        root=tmp_root,
        kind="creation",
        campaign_id="camp_D",
        scope_id="scope_D",
        environment_ref="env_D",
        actor={"name": "test"},
        data_scope_ref="disposable_D",
    )
    # Validate with max_age=0 seconds (immediately expired)
    # Need to set issued_at_utc to past
    from ai_test_asset_center.enterprise_test_data_receipts import _read_registry, _paths, _atomic_json
    registry = _read_registry(tmp_root, "test_project")
    rid = receipt["receipt_id"]
    registry["receipts"][rid]["issued_at_utc"] = "2020-01-01T00:00:00Z"
    # Recompute hash
    from ai_test_asset_center.enterprise_test_data_receipts import _hash
    r = registry["receipts"][rid]
    r["receipt_hash"] = _hash({k: v for k, v in r.items() if k != "receipt_hash"})
    _atomic_json(_paths(tmp_root, "test_project")["registry"], registry)

    result = validate_receipt_for_execution(
        "test_project",
        root=tmp_root,
        receipt_id=rid,
        run_id="run_D",
        campaign_id="camp_D",
        environment_ref="env_D",
        max_age_seconds=3600,
    )
    assert result["valid"] is False
    assert result["code"] == "RECEIPT_EXPIRED"


# ── Test 11: Receipt Hash invalid ──

def test_receipt_hash_invalid(tmp_root):
    """Receipt with tampered hash must fail."""
    receipt = issue_test_data_receipt(
        "test_project",
        root=tmp_root,
        kind="creation",
        campaign_id="camp_E",
        scope_id="scope_E",
        environment_ref="env_E",
        actor={"name": "test"},
        data_scope_ref="disposable_E",
    )
    # Tamper with the hash
    from ai_test_asset_center.enterprise_test_data_receipts import _read_registry, _paths, _atomic_json
    registry = _read_registry(tmp_root, "test_project")
    rid = receipt["receipt_id"]
    registry["receipts"][rid]["receipt_hash"] = "tampered_hash_value"
    _atomic_json(_paths(tmp_root, "test_project")["registry"], registry)

    result = validate_receipt_for_execution(
        "test_project",
        root=tmp_root,
        receipt_id=rid,
        run_id="run_E",
        campaign_id="camp_E",
        environment_ref="env_E",
    )
    assert result["valid"] is False
    assert result["code"] == "RECEIPT_HASH_INVALID"


# ── Test 12: Placeholder Path blocked ──

def test_placeholder_path_blocked():
    """Path with placeholder must be blocked."""
    result = validate_entity_materialization("{entity_id}")
    assert result["valid"] is False
    assert result["is_placeholder"] is True
    assert "PLACEHOLDER" in result["block_reason"]


# ── Test 13: Placeholder Body blocked ──

def test_placeholder_body_blocked():
    """Body with placeholder value must be blocked in pre-request check."""
    experiment = {
        "experiment_id": "exp_placeholder",
        "compile_receipt": {"status": "COMPILED"},
        "steps": [
            {"method": "POST", "path": "/api/v1/entities", "body": {"name": "placeholder_name"}}
        ],
        "actor": {"actor_id": "actor_1"},
        "observers": [{"observer_type": "after_state"}],
    }
    result = validate_pre_request_checks(experiment, receipt_valid=True)
    assert result["blocked"] is True
    assert "BLOCKED_UNRESOLVED_BODY_PLACEHOLDERS" in result["blockers"]


# ── Test 14: Unverified UUID blocked ──

def test_unverified_uuid_blocked():
    """Random UUID without proof must be blocked."""
    result = validate_entity_materialization(
        "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        receipt_proven=False,
        observer_proven=False,
    )
    assert result["valid"] is False
    assert result["block_reason"] == "BLOCKED_ENTITY_NOT_MATERIALIZED"


# ── Test 15: Real Receipt ID allows execution ──

def test_real_receipt_id_allows_execution(tmp_root):
    """Valid receipt with all checks passing allows execution."""
    receipt = issue_test_data_receipt(
        "test_project",
        root=tmp_root,
        kind="creation",
        campaign_id="camp_F",
        scope_id="scope_F",
        environment_ref="env_F",
        actor={"name": "test"},
        data_scope_ref="disposable_F",
    )
    result = validate_receipt_for_execution(
        "test_project",
        root=tmp_root,
        receipt_id=receipt["receipt_id"],
        run_id="run_F",
        campaign_id="camp_F",
        environment_ref="env_F",
        required_entities=[{"entity_id": "e1", "verified": True}],
        required_relations=[{"parent_entity_id": "e1", "child_entity_id": "e2", "verified": True}],
        required_preconditions=[{"rule_id": "r1", "passed": True}],
    )
    assert result["valid"] is True
    assert result["code"] == "RECEIPT_VALID"


# ── Test 16: Small Scale over 20 truncated ──

def test_small_scale_over_20_truncated():
    """More than 20 experiments must be truncated in small_scale phase."""
    experiments = [{"experiment_id": f"exp_{i}"} for i in range(30)]
    truncated, receipt = truncate_to_budget(experiments, phase="small_scale")
    assert len(truncated) == 20
    assert receipt["was_truncated"] is True
    assert receipt["original_count"] == 30
    assert receipt["truncated_count"] == 20


# ── Test 17: Formal over 100 truncated ──

def test_formal_over_100_truncated():
    """More than 100 experiments must be truncated in formal phase."""
    experiments = [{"experiment_id": f"exp_{i}"} for i in range(150)]
    truncated, receipt = truncate_to_budget(experiments, phase="formal")
    assert len(truncated) == 100
    assert receipt["was_truncated"] is True


# ── Test 18: Shared hard cap enforced ──

def test_hard_cap_enforced():
    """Budget can never exceed the hard cap regardless of contract override."""
    from ai_test_asset_center.small_scale_validation_gate import HARD_BUDGET_CAP

    budget = get_validation_budget({"experiment_budget": HARD_BUDGET_CAP + 300}, phase="formal")
    assert budget == HARD_BUDGET_CAP
    budget2 = get_validation_budget({"experiment_budget": HARD_BUDGET_CAP + 399}, phase="small_scale")
    assert budget2 == HARD_BUDGET_CAP


# ── Test 19: Gate failure auto-marks Run invalid ──

def test_gate_failure_auto_invalidates_run():
    """Gate failure must auto-invalidate the run."""
    gate_result = {
        "status": "FAILED",
        "auto_invalidation": {
            "action": "mark_run_invalid",
            "reason": "SMALL_SCALE_VALIDATION_FAILED",
            "failures": ["RECEIPT_NOT_BOUND_TO_RUN"],
        },
    }
    mainline_run = {"run_id": "run_test", "status": "ACTIVE"}
    updated = apply_gate_invalidation(gate_result, mainline_run)
    assert updated["status"] == "INVALID"
    assert updated["invalidation_reason"] == "SMALL_SCALE_VALIDATION_FAILED"
    assert updated["can_count_for_scoring"] is False


# ── Test 20: Gate pass allows Formal Run ──

def test_gate_pass_allows_formal():
    """Gate pass in small_scale phase must allow formal run."""
    batch_result = {
        "results": [
            {
                "campaign_id": "camp_ok",
                "execution_receipt": {"campaign_id": "camp_ok"},
                "status": "EXECUTED",
                "steps": [{"status_code": 200, "path": "/api/v1/entities/real-id-1"}],
                "contract_evidence_receipts": [{"kind": "fixture", "status": "OBSERVED"}],
                "oracle_verdict": {"status": "EVALUATED"},
            }
            for _ in range(9)
        ],
    }
    gate = check_validation_gate(
        batch_result,
        campaign_id="camp_ok",
        run_id="run_ok",
        phase="small_scale",
    )
    assert gate["status"] == "PASSED"
    assert gate["can_proceed_to_formal"] is True


# ── Additional: Anti-hardcoding audit ──

def test_anti_hardcoding_detects_project_a():
    """Anti-hardcoding audit must detect Project A patterns."""
    bad_source = "rule_id = 'conservation.inventory.reserve_sum'"
    result = audit_anti_hardcoding(bad_source, filename="gate.py")
    assert result["passed"] is False
    assert any(v["code"] == "RULE_ID_HARDCODE" for v in result["violations"])


def test_anti_hardcoding_clean_source():
    """Clean generic source must pass audit."""
    good_source = "rule_id = obligation.get('rule_id')"
    result = audit_anti_hardcoding(good_source, filename="gate.py")
    assert result["passed"] is True


# ── Additional: Placeholder detection ──

@pytest.mark.parametrize("value,expected", [
    ("qb_test_123", True),
    ("placeholder_abc", True),
    ("example_entity", True),
    ("test-id-001", True),
    ("00000000-0000-0000-0000-000000000000", True),
    ("{entity_id}", True),
    ("<contract_id>", True),
    ("${resource_id}", True),
    ("{{template}}", True),
    ("", True),
    ("real-entity-abc123", False),
    ("550e8400-e29b-41d4-a716-446655440000", False),
    ("/api/v1/contracts/cf-123", False),
])
def test_placeholder_detection(value: str, expected: bool):
    """Placeholder detection must catch all known patterns."""
    assert is_placeholder_value(value) is expected


# ── Additional: Category balance ──

def test_category_balance_enforced():
    """No more than 3 rules per category."""
    obligations = [
        {
            "obligation_id": f"obl_cons_{i}",
            "rule_id": f"conservation.entity_{i}.check",
            "rule_type": "CONSERVATION",
            "confidence": 0.9,
            "structured_expression": {"kind": "SUM"},
            "observer_requirements": [{"observer_type": "before_state"}],
            "fixture_dependencies": [],
        }
        for i in range(5)
    ]
    experiments = {
        f"obl_cons_{i}": {"experiment_id": f"exp_{i}", "compile_receipt": {"status": "COMPILED"}}
        for i in range(5)
    }
    result = select_target_rules_by_structure(obligations, experiments)
    # Max 3 from conservation category
    conservation_selected = [
        s for s in result["selected_obligations"] if s["category"] == "conservation"
    ]
    assert len(conservation_selected) <= 3
