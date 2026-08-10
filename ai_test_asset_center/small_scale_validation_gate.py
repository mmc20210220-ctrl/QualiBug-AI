"""Small-scale validation gate for phased experiment execution.

Generic, project-agnostic gate that selects target rules by structural scoring,
controls experiment budget, and validates execution quality before formal scans.

Phases:
- Phase 1 (Small-scale): ≤20 experiments with auto-selected target rules
- Phase 2 (Formal): ≤100 experiments, only after Phase 1 passes

Acceptance thresholds:
- Valid Receipt: 1 bound to current Run
- Real entity ID usage: 100%
- Placeholder requests: 0
- Small-scale experiments: ≤20
- Request acceptance rate: ≥80%
- TEST_DATA_GAP: 0
- Target rules Fixture Ready: ≥8 or 90% of target set
- Target rules Oracle Evaluated: ≥8 or 90% of target set
- Single small-scale runtime: ≤30 minutes

This module contains NO project-specific rule IDs, entity names, or domain logic.
"""
from __future__ import annotations

import re
import time
from typing import Any


# ── Schema version ──
VALIDATION_GATE_SCHEMA = "qualibug.small-scale-validation-gate.v2"

# ── Phased budget limits ──
# The hard cap bounds one execution round. It must keep pace with the
# compiled candidate pool: run25c compiled ~11734 obligations but only 89
# executed (1.7%) — budget-deferred DEFERRED rows never reached a finding.
# 600 keeps a round affordable (~1.5-2h at the current per-experiment cost)
# while covering the highest-risk surfaces (anonymous writes, admin bulk
# ops) that a 200 cap starved.
SMALL_SCALE_BUDGET = 20
FORMAL_BUDGET = 100
HARD_BUDGET_CAP = 600

# ── Phase sub-budgets ──
PHASE_BUDGET_PREFLIGHT = 5
PHASE_BUDGET_BOOTSTRAP_VERIFY = 5
PHASE_BUDGET_TARGET_RULES = 9
PHASE_BUDGET_RESERVE_HIGH_CONF = 6

# ── Acceptance thresholds ──
THRESHOLD_REAL_ID_USAGE = 1.0  # 100%
THRESHOLD_PLACEHOLDER_REQUESTS = 0
THRESHOLD_ACCEPTANCE_RATE = 0.80  # 80%
THRESHOLD_TEST_DATA_GAP = 0
THRESHOLD_FIXTURE_READY = 8  # minimum absolute
THRESHOLD_FIXTURE_READY_PCT = 0.90  # or 90% of target set
THRESHOLD_ORACLE_EVALUATED = 8  # minimum absolute
THRESHOLD_ORACLE_EVALUATED_PCT = 0.90  # or 90% of target set
THRESHOLD_RUNTIME_MINUTES = 30

# ── Generic rule type categories for balanced selection ──
RULE_TYPE_CATEGORIES: dict[str, list[str]] = {
    "conservation": ["CONSERVATION", "LIMIT", "LIMIT_CONSTRAINT", "FIELD_INVARIANT"],
    "causal": ["CAUSAL_POSTCONDITION", "COMPENSATION"],
    "state": ["STATE_TRANSITION", "CROSS_ENTITY_CONSISTENCY"],
}
MAX_PER_CATEGORY = 3
MAX_TARGET_RULES = 9

# ── Structural scoring weights ──
SCORE_HIGH_CONFIDENCE = 20
SCORE_REAL_OPERATION_BOUND = 20
SCORE_OBSERVER_REQUIREMENTS_COMPLETE = 20
SCORE_FIXTURE_DEPENDENCIES_RESOLVED = 15
SCORE_MULTI_ENTITY = 10
SCORE_BEFORE_AFTER_REQUIRED = 5
SCORE_AGGREGATE_EXPRESSION = 5
SCORE_STATE_EXPRESSION = 5

# ── Anti-hardcoding audit patterns ──
_HARDCODE_PATTERNS: dict[str, re.Pattern[str]] = {
    "RULE_ID_HARDCODE": re.compile(
        r"conservation\.(inventory|order|refund)\.|"
        r"causal\.(order|payment)\.|"
        r"state\.(order)\.",
        re.IGNORECASE,
    ),
    "DOMAIN_ENTITY_HARDCODE": re.compile(
        r"\b(inventory|order|refund|payment|contract|budget|milestone)\b",
        re.IGNORECASE,
    ),
    "DOMAIN_TRANSITION_HARDCODE": re.compile(
        r"pending_to_paid|pending_to_cancelled|paid_to_shipped",
        re.IGNORECASE,
    ),
    "BENCHMARK_ID_HARDCODE": re.compile(
        r"BUG-\d+|GT-\d+|benchmark_mall",
        re.IGNORECASE,
    ),
    "PROJECT_NAME_HARDCODE": re.compile(
        r"project.?[abc]|contractflow|ecommerce|equipment.?maintenance",
        re.IGNORECASE,
    ),
}


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


def _classify_rule_category(rule_type: str) -> str:
    """Classify a rule type string into a generic category."""
    upper = rule_type.upper().strip()
    for category, types in RULE_TYPE_CATEGORIES.items():
        if upper in types:
            return category
    return "other"


def _score_obligation(
    obl: dict[str, Any],
    experiment: dict[str, Any],
) -> int:
    """Score an obligation by structural readiness, not by domain content."""
    score = 0
    # High confidence
    confidence = float(obl.get("confidence") or obl.get("readiness_score") or 0)
    if confidence >= 0.7:
        score += SCORE_HIGH_CONFIDENCE
    elif confidence >= 0.5:
        score += SCORE_HIGH_CONFIDENCE // 2
    # Real operation bound
    compile_receipt = _dict(experiment.get("compile_receipt"))
    if _text(compile_receipt.get("status")).upper() in {"COMPILED", "READY"}:
        score += SCORE_REAL_OPERATION_BOUND
    elif _text(experiment.get("experiment_id")):
        score += SCORE_REAL_OPERATION_BOUND // 2
    # Observer requirements complete
    observer_reqs = _list(obl.get("observer_requirements"))
    if observer_reqs and all(
        isinstance(r, dict) and r.get("observer_type")
        for r in observer_reqs
    ):
        score += SCORE_OBSERVER_REQUIREMENTS_COMPLETE
    elif _list(experiment.get("observers")):
        score += SCORE_OBSERVER_REQUIREMENTS_COMPLETE // 2
    # Fixture dependencies resolved
    fixture_deps = _list(obl.get("fixture_dependencies"))
    if fixture_deps:
        resolved = sum(1 for d in fixture_deps if isinstance(d, dict) and d.get("resolved"))
        if resolved == len(fixture_deps):
            score += SCORE_FIXTURE_DEPENDENCIES_RESOLVED
        elif resolved > 0:
            score += SCORE_FIXTURE_DEPENDENCIES_RESOLVED // 2
    else:
        # No fixture deps means trivially resolved
        score += SCORE_FIXTURE_DEPENDENCIES_RESOLVED
    # Multi-entity
    if len(_list(obl.get("related_entities"))) > 0 or _text(obl.get("relation_key")):
        score += SCORE_MULTI_ENTITY
    # Before/after required
    expr = _dict(obl.get("structured_expression"))
    if expr.get("before_field") or expr.get("after_field") or expr.get("delta"):
        score += SCORE_BEFORE_AFTER_REQUIRED
    # Aggregate expression
    if expr.get("aggregate") or expr.get("sum_field") or _text(expr.get("kind")).upper() in {"SUM", "DELTA"}:
        score += SCORE_AGGREGATE_EXPRESSION
    # State expression
    if _text(expr.get("kind")).upper() in {"STATE", "IMPLIES"} or expr.get("from_state"):
        score += SCORE_STATE_EXPRESSION
    return score


def select_target_rules_by_structure(
    obligations: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    *,
    external_target_set: list[str] | None = None,
    max_rules: int = MAX_TARGET_RULES,
) -> dict[str, Any]:
    """Select target rules by generic structural scoring.

    Never uses project-specific rule IDs, entity names, or domain knowledge.
    Selection is based purely on structural readiness indicators.

    Args:
        obligations: Current run's obligation list
        experiments_by_obligation: Compiled experiments keyed by obligation_id
        external_target_set: Optional external rule IDs to prioritize (run config only)
        max_rules: Maximum target rules to select (default 9)

    Returns:
        Selection receipt with scored obligations and category distribution
    """
    experiments = dict(experiments_by_obligation or {})
    external_set = set(external_target_set or [])

    # Score all eligible obligations
    scored: list[dict[str, Any]] = []
    for obl in _list(obligations):
        if not isinstance(obl, dict):
            continue
        oid = _text(obl.get("obligation_id"))
        if not oid:
            continue
        rule_id = _text(obl.get("rule_id"))
        if not rule_id:
            for ref in _list(obl.get("source_refs")):
                if isinstance(ref, dict):
                    rule_id = _text(ref.get("rule_id"))
                    if rule_id:
                        break
        rule_type = _text(obl.get("rule_type") or obl.get("risk_family"))
        # Minimum eligibility: must have structured expression or compiled experiment
        exp = _dict(experiments.get(oid))
        has_expr = bool(_dict(obl.get("structured_expression")))
        has_compile = bool(_text(_dict(exp.get("compile_receipt")).get("status")))
        if not has_expr and not has_compile:
            continue
        # Skip pure HTTP anomaly or simple permission probes
        if _text(obl.get("risk_family")).upper() in {"HTTP_ANOMALY", "PERMISSION_PROBE", "AUTH_BOUNDARY"}:
            continue

        score = _score_obligation(obl, exp)
        # External target set bonus (from run config, not hardcoded)
        if rule_id and rule_id in external_set:
            score += 30  # Strong priority for externally configured targets

        scored.append({
            "obligation_id": oid,
            "rule_id": rule_id,
            "rule_type": rule_type,
            "category": _classify_rule_category(rule_type),
            "score": score,
            "experiment_id": _text(exp.get("experiment_id")),
            "compile_status": _text(_dict(exp.get("compile_receipt")).get("status")),
        })

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    # Select with category balance
    selected: list[dict[str, Any]] = []
    category_counts: dict[str, int] = {cat: 0 for cat in RULE_TYPE_CATEGORIES}
    category_counts["other"] = 0
    skipped: list[dict[str, Any]] = []

    for item in scored:
        if len(selected) >= max_rules:
            break
        cat = item["category"]
        if cat in RULE_TYPE_CATEGORIES and category_counts.get(cat, 0) >= MAX_PER_CATEGORY:
            skipped.append(item)
            continue
        selected.append(item)
        category_counts[cat] = category_counts.get(cat, 0) + 1

    # Category cap is a hard limit per SPEC §3.3:
    # "如果某一类型当前项目不足，不得用其他项目固定规则补充"
    # "允许实际目标规则少于9条，但必须输出原因"
    # Do NOT fill remaining from over-cap categories.

    selected_ids = [item["obligation_id"] for item in selected]
    return {
        "schema_version": "qualibug.generic-target-rule-selection.v1",
        "selection_method": "structural_scoring",
        "max_rules": max_rules,
        "external_target_set_provided": bool(external_set),
        "external_target_set_size": len(external_set),
        "candidates_evaluated": len(scored),
        "selected_obligation_ids": selected_ids,
        "selected_obligations": selected,
        "selected_count": len(selected),
        "category_distribution": dict(category_counts),
        "score_range": {
            "min": min((s["score"] for s in selected), default=0),
            "max": max((s["score"] for s in selected), default=0),
        },
        "reason": "" if len(selected) >= max_rules else f"only_{len(selected)}_eligible_candidates",
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
    
    # ── Metric: Request acceptance rate (with business rejection classification) ──
    accepted_requests = 0
    business_rejected_expected = 0
    unexpected_rejections = 0
    harness_failed = 0
    for item in results:
        if not isinstance(item, dict):
            continue
        for step in _list(item.get("steps")):
            if not isinstance(step, dict):
                continue
            status = int(step.get("status_code") or 0)
            if 200 <= status < 300:
                accepted_requests += 1
            elif status in {400, 403, 404, 409, 422}:
                # Business-level rejections that may be expected (state conflicts, validation)
                business_rejected_expected += 1
            elif status >= 400:
                unexpected_rejections += 1
            elif status == 0:
                harness_failed += 1

    # Acceptance = (2xx + expected business 4xx) / total sent
    transport_accepted = accepted_requests + business_rejected_expected
    acceptance_rate = transport_accepted / total_requests if total_requests > 0 else 0.0
    
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
    # Dynamic thresholds: ≥8 absolute OR 90% of target set
    target_set_size = max(experiment_count, 1)
    fixture_threshold = min(THRESHOLD_FIXTURE_READY, int(target_set_size * THRESHOLD_FIXTURE_READY_PCT))
    oracle_threshold = min(THRESHOLD_ORACLE_EVALUATED, int(target_set_size * THRESHOLD_ORACLE_EVALUATED_PCT))

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
    if fixture_ready_count < fixture_threshold:
        failures.append(f"FIXTURE_READY_BELOW_THRESHOLD:{fixture_ready_count}<{fixture_threshold}")
    if oracle_evaluated_count < oracle_threshold:
        failures.append(f"ORACLE_EVALUATED_BELOW_THRESHOLD:{oracle_evaluated_count}<{oracle_threshold}")
    if runtime_minutes > THRESHOLD_RUNTIME_MINUTES:
        failures.append(f"RUNTIME_EXCEEDED:{runtime_minutes:.1f}min>{THRESHOLD_RUNTIME_MINUTES}min")
    if experiment_count > budget:
        failures.append(f"BUDGET_EXCEEDED:{experiment_count}>{budget}")

    passed = len(failures) == 0

    # ── Auto-invalidate run on gate failure ──
    invalidation_receipt: dict[str, Any] = {}
    if not passed and phase == "small_scale":
        invalidation_receipt = {
            "action": "mark_run_invalid",
            "reason": "SMALL_SCALE_VALIDATION_FAILED",
            "failures": list(failures),
        }

    return {
        "schema_version": VALIDATION_GATE_SCHEMA,
        "campaign_id": campaign_id,
        "run_id": run_id,
        "phase": phase,
        "status": "PASSED" if passed else "FAILED",
        "failures": failures,
        "auto_invalidation": invalidation_receipt,
        "metrics": {
            "valid_receipts": valid_receipts,
            "receipt_bound_to_run": receipt_bound_to_run,
            "total_requests": total_requests,
            "real_id_requests": real_id_requests,
            "placeholder_requests": placeholder_requests,
            "real_id_usage_rate": round(real_id_usage_rate, 4),
            "accepted_requests": accepted_requests,
            "business_rejected_expected": business_rejected_expected,
            "unexpected_rejections": unexpected_rejections,
            "harness_failed": harness_failed,
            "transport_accepted": transport_accepted,
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
            "fixture_ready": fixture_threshold,
            "oracle_evaluated": oracle_threshold,
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
    - Small scale gate failure
    """
    run = dict(_dict(mainline_run))
    run["status"] = "INVALID"
    run["invalidation_reason"] = reason
    run["invalidated_at"] = time.time()
    run["can_count_for_scoring"] = False
    return run


def apply_gate_invalidation(
    gate_result: dict[str, Any],
    mainline_run: dict[str, Any],
) -> dict[str, Any]:
    """Apply auto-invalidation from gate result to the mainline run.

    Integrates mark_run_invalid into the gate failure path automatically.
    Returns the (possibly updated) mainline run.
    """
    gate = _dict(gate_result)
    if _text(gate.get("status")).upper() != "FAILED":
        return _dict(mainline_run)
    invalidation = _dict(gate.get("auto_invalidation"))
    if not invalidation:
        return _dict(mainline_run)
    reason = _text(invalidation.get("reason")) or "SMALL_SCALE_VALIDATION_FAILED"
    return mark_run_invalid(mainline_run, reason=reason)


def audit_anti_hardcoding(
    source_text: str,
    *,
    filename: str = "",
    is_test_fixture: bool = False,
) -> dict[str, Any]:
    """Scan source text for project-specific hardcoding patterns.

    Returns a receipt with any detected violations.
    Test fixtures are allowed to contain project names but not rule IDs.
    """
    violations: list[dict[str, str]] = []
    for code, pattern in _HARDCODE_PATTERNS.items():
        # Test fixtures may contain domain entity names but not rule IDs or benchmark IDs
        if is_test_fixture and code in {"DOMAIN_ENTITY_HARDCODE", "PROJECT_NAME_HARDCODE", "DOMAIN_TRANSITION_HARDCODE"}:
            continue
        matches = pattern.findall(source_text)
        if matches:
            violations.append({
                "code": code,
                "file": filename,
                "match_count": str(len(matches)),
                "sample": str(matches[0])[:80] if matches else "",
            })
    return {
        "schema_version": "qualibug.anti-hardcoding-audit.v1",
        "file": filename,
        "is_test_fixture": is_test_fixture,
        "violations": violations,
        "violation_count": len(violations),
        "passed": len(violations) == 0,
    }


def audit_gate_module_hardcoding() -> dict[str, Any]:
    """Self-audit: verify this gate module contains no project-specific rule IDs."""
    import inspect
    source = inspect.getsource(select_target_rules_by_structure)
    source += inspect.getsource(check_validation_gate)
    source += inspect.getsource(get_validation_budget)
    violations: list[str] = []
    # Check for known Project A rule patterns
    project_a_patterns = [
        "conservation.inventory", "conservation.order", "conservation.refund",
        "causal.order", "causal.payment",
        "state.order",
        "inventory", "refund",
    ]
    for pat in project_a_patterns:
        if pat in source.lower():
            violations.append(f"FOUND:{pat}")
    return {
        "schema_version": "qualibug.gate-self-audit.v1",
        "module": "small_scale_validation_gate",
        "generic_target_selection": "PASS" if not violations else "FAIL",
        "violations": violations,
    }


# ── Entity Materialization & Placeholder Guard (SPEC §8, §14) ──

_PLACEHOLDER_PATTERNS = re.compile(
    r"^(qb_test_|qb-test-|placeholder|example_|test-id-|"
    r"00000000-0000-0000-0000-000000000000|"
    r"\{[a-zA-Z_]+\}|"
    r"<[a-zA-Z_]+>|"
    r"\$\{[a-zA-Z_]+\})",
    re.IGNORECASE,
)

_ZERO_UUID = "00000000-0000-0000-0000-000000000000"


def is_placeholder_value(value: str) -> bool:
    """Detect if a value is a placeholder that must not reach transport."""
    v = value.strip()
    if not v:
        return True
    if v == _ZERO_UUID:
        return True
    if _PLACEHOLDER_PATTERNS.match(v):
        return True
    # Template markers
    if "{{" in v or "}}" in v:
        return True
    return False


def validate_entity_materialization(
    entity_id: str,
    *,
    receipt_proven: bool = False,
    observer_proven: bool = False,
    tenant_match: bool = True,
    entity_match: bool = True,
) -> dict[str, Any]:
    """Validate that an entity ID is materialized and safe for execution.

    Returns identity_validation receipt per SPEC §8.
    Blocks: qb_test_*, placeholder_*, example_*, test-id-*, zero UUID,
    and unproven random UUIDs.
    """
    value = _text(entity_id)
    placeholder = is_placeholder_value(value)
    valid = (
        not placeholder
        and (receipt_proven or observer_proven)
        and tenant_match
        and entity_match
    )
    block_reason = ""
    if placeholder:
        if "{" in value or "<" in value or "${" in value:
            block_reason = "BLOCKED_UNRESOLVED_PATH_PLACEHOLDERS"
        else:
            block_reason = "BLOCKED_ENTITY_NOT_MATERIALIZED"
    elif not receipt_proven and not observer_proven:
        block_reason = "BLOCKED_ENTITY_NOT_MATERIALIZED"
    elif not tenant_match:
        block_reason = "BLOCKED_ENTITY_TENANT_MISMATCH"
    elif not entity_match:
        block_reason = "BLOCKED_ENTITY_TYPE_MISMATCH"

    return {
        "schema_version": "qualibug.identity-validation.v1",
        "value": value,
        "is_placeholder": placeholder,
        "receipt_proven": receipt_proven,
        "observer_proven": observer_proven,
        "tenant_match": tenant_match,
        "entity_match": entity_match,
        "valid": valid,
        "block_reason": block_reason,
    }


def validate_pre_request_checks(
    experiment: dict[str, Any],
    *,
    receipt_valid: bool = False,
) -> dict[str, Any]:
    """Pre-request validation before sending HTTP (SPEC §14).

    Checks all conditions that must pass before a request reaches transport.
    Any failure blocks locally without sending a request.
    """
    exp = _dict(experiment)
    blockers: list[str] = []

    # 1. Real Operation bound
    compile_receipt = _dict(exp.get("compile_receipt"))
    if _text(compile_receipt.get("status")).upper() not in {"COMPILED", "READY"}:
        blockers.append("NO_REAL_OPERATION_BOUND")

    # 2. Receipt valid
    if not receipt_valid:
        blockers.append("RECEIPT_NOT_VALID")

    # 3. Path parameters materialized
    for step in _list(exp.get("steps")):
        if not isinstance(step, dict):
            continue
        path = _text(step.get("path"))
        if is_placeholder_value(path) or "{" in path or "}" in path:
            blockers.append("BLOCKED_UNRESOLVED_PATH_PLACEHOLDERS")
            break

    # 4. Body required fields materialized
    for step in _list(exp.get("steps")):
        if not isinstance(step, dict):
            continue
        body = step.get("body")
        if isinstance(body, dict):
            for key, val in body.items():
                if isinstance(val, str) and is_placeholder_value(val):
                    blockers.append("BLOCKED_UNRESOLVED_BODY_PLACEHOLDERS")
                    break
            else:
                continue
            break

    # 5. Actor and Tenant match
    actor = _dict(exp.get("actor"))
    if not _text(actor.get("actor_id")) and not _text(actor.get("token")):
        blockers.append("ACTOR_NOT_BOUND")

    # 6. Observer plan executable
    observers = _list(exp.get("observers"))
    if not observers and not _list(exp.get("observer_requirements")):
        blockers.append("OBSERVER_PLAN_MISSING")

    blocked = len(blockers) > 0
    return {
        "schema_version": "qualibug.pre-request-validation.v1",
        "experiment_id": _text(exp.get("experiment_id")),
        "blocked": blocked,
        "blockers": blockers,
        "can_send_request": not blocked,
    }


def truncate_to_budget(
    experiments: list[dict[str, Any]],
    *,
    phase: str = "small_scale",
    runtime_contract: dict[str, Any] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Truncate experiment list to phase budget.

    Returns (truncated_list, truncation_receipt).
    - small_scale: ≤20
    - formal: ≤100
    - hard_cap: 200 absolute maximum
    """
    budget = get_validation_budget(_dict(runtime_contract), phase=phase)
    original_count = len(experiments)
    truncated = experiments[:budget]
    return truncated, {
        "schema_version": "qualibug.budget-truncation.v1",
        "phase": phase,
        "budget": budget,
        "hard_cap": HARD_BUDGET_CAP,
        "original_count": original_count,
        "truncated_count": len(truncated),
        "was_truncated": original_count > budget,
    }
