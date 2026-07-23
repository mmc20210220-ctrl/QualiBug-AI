"""Small-scale validation gate for phased experiment execution.

Implements the acceptance threshold checking required before formal scans:
- Phase 1 (Small-scale): ≤20 experiments with fixed 9 target rules
- Phase 2 (Formal): ≤100 experiments, only after Phase 1 passes

Acceptance thresholds:
- Valid Receipt: 1 bound to current Run
- Real entity ID usage: 100%
- Placeholder requests: 0
- Small-scale experiments: ≤20
- Request acceptance rate: ≥80%
- TEST_DATA_GAP: 0
- Fixed 9 rules Fixture Ready: ≥8
- Fixed 9 rules Oracle Evaluated: ≥8
- Single small-scale runtime: ≤30 minutes
"""
from __future__ import annotations

import time
from typing import Any


# ── Schema version ──
VALIDATION_GATE_SCHEMA = "qualibug.small-scale-validation-gate.v1"

# ── Phased budget limits ──
SMALL_SCALE_BUDGET = 20
FORMAL_BUDGET = 100
HARD_BUDGET_CAP = 200

# ── Acceptance thresholds ──
THRESHOLD_REAL_ID_USAGE = 1.0  # 100%
THRESHOLD_PLACEHOLDER_REQUESTS = 0
THRESHOLD_ACCEPTANCE_RATE = 0.80  # 80%
THRESHOLD_TEST_DATA_GAP = 0
THRESHOLD_FIXTURE_READY = 8  # out of 9 rules
THRESHOLD_ORACLE_EVALUATED = 8  # out of 9 rules
THRESHOLD_RUNTIME_MINUTES = 30

# ── Fixed 9 target rules for small-scale validation ──
# Selected from golden_rule_set.json: 3 conservation + 3 causal + 3 state
FIXED_9_RULE_IDS: list[str] = [
    # Conservation rules (3)
    "conservation.inventory.reserve_sum",
    "conservation.order.amount_formula",
    "conservation.refund.amount_lte_paid",
    # Causal postcondition rules (3)
    "causal.order.create_inventory_reserve",
    "causal.order.cancel_inventory_release",
    "causal.payment.pay_status_change",
    # State transition rules (3)
    "state.order.pending_to_paid",
    "state.order.pending_to_cancelled",
    "state.order.paid_to_shipped",
]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def get_validation_budget(
    runtime_contract: dict[str, Any],
    *,
    phase: str = "small_scale",
) -> int:
    """Get experiment budget for the specified phase.
    
    Args:
        runtime_contract: Runtime contract dict that may override budget
        phase: "small_scale" (≤20) or "formal" (≤100)
    
    Returns:
        Budget limit for the phase
    """
    contract_budget = int(_dict(runtime_contract).get("experiment_budget") or 0)
    
    if phase == "small_scale":
        default_budget = SMALL_SCALE_BUDGET
    elif phase == "formal":
        default_budget = FORMAL_BUDGET
    else:
        default_budget = SMALL_SCALE_BUDGET
    
    # Contract can override but cannot exceed hard cap
    if contract_budget > 0:
        return min(contract_budget, HARD_BUDGET_CAP)
    return default_budget


def select_fixed_9_rules(
    obligations: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Select the fixed 9 target rules for small-scale validation.
    
    Returns a receipt with selected obligation IDs matching the fixed rule set.
    """
    experiments = dict(experiments_by_obligation or {})
    
    # Build mapping from rule_id to obligation
    rule_to_obligation: dict[str, dict[str, Any]] = {}
    for obl in obligations:
        if not isinstance(obl, dict):
            continue
        oid = _text(obl.get("obligation_id"))
        if not oid:
            continue
        # Try to match by rule_id in source_refs or obligation metadata
        rule_id = _text(obl.get("rule_id"))
        if not rule_id:
            # Try to extract from source_refs
            for ref in _list(obl.get("source_refs")):
                if isinstance(ref, dict):
                    rule_id = _text(ref.get("rule_id"))
                    if rule_id:
                        break
        if rule_id:
            rule_to_obligation[rule_id] = obl
    
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    matched_rules: list[str] = []
    missing_rules: list[str] = []
    
    for rule_id in FIXED_9_RULE_IDS:
        obl = rule_to_obligation.get(rule_id)
        if obl:
            oid = _text(obl.get("obligation_id"))
            exp = _dict(experiments.get(oid))
            selected.append({
                "obligation_id": oid,
                "rule_id": rule_id,
                "risk_family": _text(obl.get("risk_family")),
                "experiment_id": _text(exp.get("experiment_id")),
                "compile_status": _text(_dict(exp.get("compile_receipt")).get("status")),
            })
            selected_ids.add(oid)
            matched_rules.append(rule_id)
        else:
            missing_rules.append(rule_id)
    
    return {
        "schema_version": "qualibug.fixed-9-rule-selection.v1",
        "fixed_rule_ids": FIXED_9_RULE_IDS,
        "matched_rules": matched_rules,
        "missing_rules": missing_rules,
        "selected_obligation_ids": sorted(selected_ids),
        "selected_obligations": selected,
        "selected_count": len(selected),
        "match_rate": len(matched_rules) / len(FIXED_9_RULE_IDS) if FIXED_9_RULE_IDS else 0.0,
    }


def check_validation_gate(
    batch_result: dict[str, Any],
    *,
    campaign_id: str,
    run_id: str,
    phase: str = "small_scale",
    start_time: float | None = None,
) -> dict[str, Any]:
    """Check if batch execution passes the validation gate.
    
    Args:
        batch_result: Result from execute_selected_experiments
        campaign_id: Current campaign ID
        run_id: Current run ID
        phase: "small_scale" or "formal"
        start_time: Execution start timestamp for runtime check
    
    Returns:
        Validation gate receipt with pass/fail status and metrics
    """
    result = _dict(batch_result)
    results = _list(result.get("results"))
    
    # ── Metric: Valid Receipt bound to current Run ──
    valid_receipts = 0
    receipt_bound_to_run = False
    for item in results:
        if not isinstance(item, dict):
            continue
        receipt = _dict(item.get("execution_receipt"))
        if receipt and _text(receipt.get("campaign_id")) == campaign_id:
            valid_receipts += 1
            if _text(item.get("campaign_id")) == campaign_id:
                receipt_bound_to_run = True
    
    # ── Metric: Real entity ID usage rate ──
    total_requests = 0
    real_id_requests = 0
    placeholder_requests = 0
    
    for item in results:
        if not isinstance(item, dict):
            continue
        for step in _list(item.get("steps")):
            if not isinstance(step, dict):
                continue
            total_requests += 1
            path = _text(step.get("path"))
            skipped = _text(step.get("skipped_reason"))
            
            # Check for placeholder indicators
            if (
                "qb_test_" in path.lower()
                or "qb-test-" in path.lower()
                or "placeholder" in skipped.lower()
                or "unresolved" in skipped.lower()
                or "/1" in path  # Force-stripped placeholder
                or path.endswith("/1")
            ):
                placeholder_requests += 1
            elif step.get("status_code", 0) > 0:
                real_id_requests += 1
    
    real_id_usage_rate = real_id_requests / total_requests if total_requests > 0 else 0.0
    
    # ── Metric: Request acceptance rate ──
    accepted_requests = 0
    for item in results:
        if not isinstance(item, dict):
            continue
        for step in _list(item.get("steps")):
            if not isinstance(step, dict):
                continue
            status = int(step.get("status_code") or 0)
            if 200 <= status < 300:
                accepted_requests += 1
    
    acceptance_rate = accepted_requests / total_requests if total_requests > 0 else 0.0
    
    # ── Metric: TEST_DATA_GAP count ──
    test_data_gaps = 0
    for item in results:
        if not isinstance(item, dict):
            continue
        reason = _text(item.get("reason_code"))
        if "TEST_DATA_GAP" in reason or "MISSING_FIXTURE" in reason:
            test_data_gaps += 1
    
    # ── Metric: Fixed 9 rules Fixture Ready ──
    fixture_ready_count = 0
    oracle_evaluated_count = 0
    
    for item in results:
        if not isinstance(item, dict):
            continue
        status = _text(item.get("status")).upper()
        
        # Check fixture readiness from contract_evidence_receipts
        for receipt in _list(item.get("contract_evidence_receipts")):
            if not isinstance(receipt, dict):
                continue
            if _text(receipt.get("kind")) == "fixture":
                if _text(receipt.get("status")).upper() in {"OBSERVED", "RESOLVED", "BOUND"}:
                    fixture_ready_count += 1
                    break
        
        # Check oracle evaluation
        oracle = _dict(item.get("oracle_verdict"))
        if oracle and _text(oracle.get("status")).upper() in {"ACTIVE", "EVALUATED", "VIOLATION", "PROPERTY_HELD"}:
            oracle_evaluated_count += 1
    
    # ── Metric: Runtime ──
    runtime_minutes = 0.0
    if start_time:
        runtime_minutes = (time.time() - start_time) / 60.0
    
    # ── Metric: Experiment count ──
    experiment_count = len(results)
    budget = get_validation_budget({}, phase=phase)
    
    # ── Gate decision ──
    failures: list[str] = []
    
    if not receipt_bound_to_run:
        failures.append("RECEIPT_NOT_BOUND_TO_RUN")
    if real_id_usage_rate < THRESHOLD_REAL_ID_USAGE:
        failures.append(f"REAL_ID_USAGE_BELOW_THRESHOLD:{real_id_usage_rate:.2%}<{THRESHOLD_REAL_ID_USAGE:.0%}")
    if placeholder_requests > THRESHOLD_PLACEHOLDER_REQUESTS:
        failures.append(f"PLACEHOLDER_REQUESTS_EXCEEDED:{placeholder_requests}>{THRESHOLD_PLACEHOLDER_REQUESTS}")
    if acceptance_rate < THRESHOLD_ACCEPTANCE_RATE:
        failures.append(f"ACCEPTANCE_RATE_BELOW_THRESHOLD:{acceptance_rate:.2%}<{THRESHOLD_ACCEPTANCE_RATE:.0%}")
    if test_data_gaps > THRESHOLD_TEST_DATA_GAP:
        failures.append(f"TEST_DATA_GAP_EXCEEDED:{test_data_gaps}>{THRESHOLD_TEST_DATA_GAP}")
    if fixture_ready_count < THRESHOLD_FIXTURE_READY:
        failures.append(f"FIXTURE_READY_BELOW_THRESHOLD:{fixture_ready_count}<{THRESHOLD_FIXTURE_READY}")
    if oracle_evaluated_count < THRESHOLD_ORACLE_EVALUATED:
        failures.append(f"ORACLE_EVALUATED_BELOW_THRESHOLD:{oracle_evaluated_count}<{THRESHOLD_ORACLE_EVALUATED}")
    if runtime_minutes > THRESHOLD_RUNTIME_MINUTES:
        failures.append(f"RUNTIME_EXCEEDED:{runtime_minutes:.1f}min>{THRESHOLD_RUNTIME_MINUTES}min")
    if experiment_count > budget:
        failures.append(f"BUDGET_EXCEEDED:{experiment_count}>{budget}")
    
    passed = len(failures) == 0
    
    return {
        "schema_version": VALIDATION_GATE_SCHEMA,
        "campaign_id": campaign_id,
        "run_id": run_id,
        "phase": phase,
        "status": "PASSED" if passed else "FAILED",
        "failures": failures,
        "metrics": {
            "valid_receipts": valid_receipts,
            "receipt_bound_to_run": receipt_bound_to_run,
            "total_requests": total_requests,
            "real_id_requests": real_id_requests,
            "placeholder_requests": placeholder_requests,
            "real_id_usage_rate": round(real_id_usage_rate, 4),
            "accepted_requests": accepted_requests,
            "acceptance_rate": round(acceptance_rate, 4),
            "test_data_gaps": test_data_gaps,
            "fixture_ready_count": fixture_ready_count,
            "oracle_evaluated_count": oracle_evaluated_count,
            "runtime_minutes": round(runtime_minutes, 2),
            "experiment_count": experiment_count,
            "budget": budget,
        },
        "thresholds": {
            "real_id_usage": THRESHOLD_REAL_ID_USAGE,
            "placeholder_requests": THRESHOLD_PLACEHOLDER_REQUESTS,
            "acceptance_rate": THRESHOLD_ACCEPTANCE_RATE,
            "test_data_gap": THRESHOLD_TEST_DATA_GAP,
            "fixture_ready": THRESHOLD_FIXTURE_READY,
            "oracle_evaluated": THRESHOLD_ORACLE_EVALUATED,
            "runtime_minutes": THRESHOLD_RUNTIME_MINUTES,
        },
        "can_proceed_to_formal": passed and phase == "small_scale",
    }


def validate_run_not_invalidated(
    mainline_run: dict[str, Any],
) -> dict[str, Any]:
    """Check if the current run has been marked as invalidated.
    
    Returns a receipt indicating whether the run is valid for scoring.
    """
    run = _dict(mainline_run)
    run_id = _text(run.get("run_id"))
    status = _text(run.get("status")).upper()
    invalidation_reason = _text(run.get("invalidation_reason"))
    
    is_invalid = (
        status == "INVALID"
        or "INVALID_EXECUTION" in status
        or "INVALID_EXECUTION" in invalidation_reason
        or "NO_MATERIALIZED_FIXTURE" in invalidation_reason
    )
    
    return {
        "schema_version": "qualibug.run-validation.v1",
        "run_id": run_id,
        "status": status,
        "is_invalid": is_invalid,
        "invalidation_reason": invalidation_reason,
        "can_count_for_scoring": not is_invalid,
    }


def mark_run_invalid(
    mainline_run: dict[str, Any],
    *,
    reason: str,
) -> dict[str, Any]:
    """Mark a run as invalid for scoring purposes.
    
    This should be called when a run has critical issues like:
    - No materialized fixtures
    - All placeholder requests
    - Bootstrap failure
    """
    run = dict(_dict(mainline_run))
    run["status"] = "INVALID"
    run["invalidation_reason"] = reason
    run["invalidated_at"] = time.time()
    run["can_count_for_scoring"] = False
    return run
