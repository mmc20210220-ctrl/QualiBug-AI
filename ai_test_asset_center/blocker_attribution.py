"""Blocker Attribution — classify why each obligation is blocked.

SPEC v1.2 §6 + SPEC v1.2.1 §11: Two-Phase Blocker Attribution

Phase A (Reason Candidate): Map reason_code to attribution category.
Phase B (Evidence Refinement): Verify attribution against actual IR evidence,
    checking operations, source refs, binding satisfiability, adapters,
    actor config, conflicting sources, environment policy, irreversible writes.

Each obligation gets a unique primary_attribution + primary_reason,
with optional secondary_contributors.

Output: qualibug.blocker-attribution.v1
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


# ─── Attribution Categories ───────────────────────────────────────────────────

ATTRIBUTION_CATEGORIES = frozenset({
    "SOURCE_GAP",
    "BEHAVIOR_MODEL_GAP",
    "COMPILER_GAP",
    "OBSERVER_CAPABILITY_GAP",
    "BINDING_GRAPH_GAP",
    "FIXTURE_CAPABILITY_GAP",
    "ADAPTER_CAPABILITY_GAP",
    "ENVIRONMENT_GAP",
    "POLICY_SAFETY_BLOCK",
    "TARGET_SYSTEM_RESPONSE",
    "ORACLE_INPUT_GAP",
    "CLEANUP_CAPABILITY_GAP",
    "UNKNOWN",
})

RECOVERABILITY_VALUES = frozenset({
    "RECOVERABLE",
    "SOURCE_DEPENDENT",
    "ENVIRONMENT_DEPENDENT",
    "PERMANENTLY_BLOCKED",
    "UNKNOWN",
})

# ─── Reason Code → Attribution Mapping ────────────────────────────────────────

_REASON_ATTRIBUTION: dict[str, tuple[str, str, bool]] = {
    # reason_code: (attribution, recoverability, must_remain_blocked)
    "BLOCKED_MISSING_OBSERVER": ("OBSERVER_CAPABILITY_GAP", "RECOVERABLE", False),
    "BLOCKED_MISSING_BINDING": ("BINDING_GRAPH_GAP", "RECOVERABLE", False),
    "BLOCKED_MISSING_FIXTURE": ("FIXTURE_CAPABILITY_GAP", "RECOVERABLE", False),
    "BLOCKED_MISSING_ACTOR": ("SOURCE_GAP", "SOURCE_DEPENDENT", False),
    "BLOCKED_MISSING_OPERATION": ("BEHAVIOR_MODEL_GAP", "RECOVERABLE", False),
    "BLOCKED_NON_REVERSIBLE_WRITE": ("CLEANUP_CAPABILITY_GAP", "SOURCE_DEPENDENT", False),
    "BLOCKED_INVALID_CLEANUP_PLAN": ("CLEANUP_CAPABILITY_GAP", "RECOVERABLE", False),
    "BLOCKED_CLEANUP_CONTRACT_DRIFT": ("COMPILER_GAP", "RECOVERABLE", False),
    "BLOCKED_UNSUPPORTED_ADAPTER": ("ADAPTER_CAPABILITY_GAP", "ENVIRONMENT_DEPENDENT", False),
    "BLOCKED_CONFLICTING_SOURCE": ("SOURCE_GAP", "SOURCE_DEPENDENT", True),
    "BLOCKED_MISSING_ACTOR_SECRET": ("SOURCE_GAP", "SOURCE_DEPENDENT", False),
    "BLOCKED_ORACLE_INPUT_INCOMPLETE": ("ORACLE_INPUT_GAP", "RECOVERABLE", False),
    "MISSING_PRIMARY_OPERATION": ("BEHAVIOR_MODEL_GAP", "RECOVERABLE", False),
    "non_production_environment_required": ("ENVIRONMENT_GAP", "ENVIRONMENT_DEPENDENT", True),
    "HARNESS_FAILURE": ("TARGET_SYSTEM_RESPONSE", "UNKNOWN", False),
    "HARNESS_CLEANUP_EQUIVALENCE_FAILED": ("CLEANUP_CAPABILITY_GAP", "RECOVERABLE", False),
    "BLOCKED_CLEANUP_EQUIVALENCE_INDETERMINATE": ("CLEANUP_CAPABILITY_GAP", "RECOVERABLE", False),
}


# The funnel and ledger must classify a terminal reason from the reason code
# itself.  Keep this registry beside the existing blocker-attribution authority
# so there is one mapping, rather than teaching each projection to infer a
# family from free-form detail text.  ``reason_family`` deliberately contains
# a few explicit non-blocking/deferred families which are not attribution
# categories (for example, a normal oracle rejection).
REASON_CODE_REGISTRY_SCHEMA = "qualibug.discovery-reason-code-registry.v1"


def _reason_definition(
    reason_family: str,
    *,
    recoverability: str = "UNKNOWN",
    is_blocking: bool = True,
    must_remain_blocked: bool = False,
) -> dict[str, Any]:
    return {
        "reason_family": reason_family,
        "recoverability": recoverability,
        "is_blocking": is_blocking,
        "must_remain_blocked": must_remain_blocked,
    }


REASON_CODE_REGISTRY: dict[str, dict[str, Any]] = {
    code: _reason_definition(
        attribution,
        recoverability=recoverability,
        must_remain_blocked=must_remain_blocked,
    )
    for code, (attribution, recoverability, must_remain_blocked)
    in _REASON_ATTRIBUTION.items()
}
REASON_CODE_REGISTRY.update({
    "ORACLE_NOT_VIOLATED": _reason_definition(
        "NORMAL_OUTCOME", recoverability="NOT_APPLICABLE", is_blocking=False,
    ),
    "ORACLE_NO_VIOLATION": _reason_definition(
        "NORMAL_OUTCOME", recoverability="NOT_APPLICABLE", is_blocking=False,
    ),
    "ASSERTION_NOT_VIOLATED": _reason_definition(
        "NORMAL_OUTCOME", recoverability="NOT_APPLICABLE", is_blocking=False,
    ),
    "SURFACE_DISCOVERY_OBSERVATION_ONLY": _reason_definition(
        "DISCOVERY_DIAGNOSTIC", recoverability="NOT_APPLICABLE", is_blocking=False,
    ),
    "FIELD_LEVEL_RULE_NOT_EXECUTABLE": _reason_definition("COMPILER_GAP", recoverability="RECOVERABLE"),
    "STATE_RULE_PRECONDITION_NOT_ESTABLISHED": _reason_definition("COMPILER_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE": _reason_definition("ORACLE_INPUT_GAP", recoverability="RECOVERABLE"),
    "CONTRACT_ORACLE_HARNESS_FAILED": _reason_definition("TARGET_SYSTEM_RESPONSE"),
    "HARNESS_CONNECTION_FAILED": _reason_definition("TARGET_SYSTEM_RESPONSE"),
    "EXECUTION_OBSERVABILITY_GAP": _reason_definition("OBSERVER_CAPABILITY_GAP", recoverability="RECOVERABLE"),
    "BLOCKED_POLICY": _reason_definition("POLICY_SAFETY_BLOCK", must_remain_blocked=True),
    "BLOCKED_TARGET_POLICY": _reason_definition("POLICY_SAFETY_BLOCK", must_remain_blocked=True),
    "BLOCKED_RUNTIME_TARGET": _reason_definition("POLICY_SAFETY_BLOCK", must_remain_blocked=True),
    "SLICE_BUDGET_REACHED": _reason_definition("EXECUTION_BUDGET", recoverability="RECOVERABLE"),
    "OBLIGATION_BUDGET_REACHED": _reason_definition("EXECUTION_BUDGET", recoverability="RECOVERABLE"),
    "OBLIGATION_NOT_IN_PLAN": _reason_definition("PLANNING_DEFERRED", recoverability="RECOVERABLE"),
    "DEFERRED": _reason_definition("PLANNING_DEFERRED", recoverability="RECOVERABLE"),
    "CLEANUP_COMPENSATION_FAILED": _reason_definition("CLEANUP_CAPABILITY_GAP", recoverability="RECOVERABLE"),
    "LEGACY_EXECUTION_ERROR": _reason_definition("TARGET_SYSTEM_RESPONSE"),
    "ORACLE_EXCEPTION": _reason_definition("ORACLE_INPUT_GAP", recoverability="RECOVERABLE"),
    "POST_REQUEST_PRECONDITION_FAILED": _reason_definition("TARGET_SYSTEM_RESPONSE"),
})


def profile_reason_code(reason_code: str) -> dict[str, Any]:
    """Return the explicit registry row for a terminal reason code.

    Unknown codes are not guessed from their detail.  They are returned as an
    unregistered reason so the funnel can fail safe and operators can extend
    this single registry with the real emitter's contract.
    """

    normalized = _text(reason_code)
    definition = REASON_CODE_REGISTRY.get(normalized)
    if definition is None:
        return {
            "registry_status": "UNREGISTERED",
            "reason_code": normalized,
            **_reason_definition("UNREGISTERED", is_blocking=True),
        }
    return {
        "registry_status": "REGISTERED",
        "reason_code": normalized,
        **dict(definition),
    }


# ─── SPEC v1.2.1 §11.2: Phase B Evidence Refinement ─────────────────────────


def _phase_b_evidence_refinement(
    *,
    attribution: str,
    reason_code: str,
    obligation: dict[str, Any],
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Phase B: Verify Phase A attribution against real evidence in IR/experiment.

    Returns refinement result with confirmed/adjusted attribution and evidence.
    """
    ir = _dict(behavior_ir)
    exp = _dict(experiment)
    obl = _dict(obligation)
    ops = _list(ir.get("operations"))
    relations = _list(ir.get("relations"))
    actors = _list(ir.get("actors"))

    evidence: list[dict[str, Any]] = []
    secondary_contributors: list[str] = []
    adjusted_attribution = attribution
    adjusted_reason = reason_code
    confidence_boost = 0.0

    # Check 1: Does IR contain candidate operations for the obligation?
    entity_ref = _text(obl.get("entity_ref") or obl.get("target_entity"))
    has_candidate_op = any(
        _text(op.get("entity_ref") or op.get("entity")) == entity_ref
        for op in ops if isinstance(op, dict)
    ) if entity_ref else bool(ops)
    evidence.append({
        "check": "ir_candidate_operation_exists",
        "passed": has_candidate_op,
        "detail": f"entity_ref={entity_ref}, ops_count={len(ops)}",
    })

    # Check 2: Source refs availability
    source_refs = _list(obl.get("source_refs"))
    has_source_refs = len(source_refs) > 0
    evidence.append({
        "check": "source_refs_available",
        "passed": has_source_refs,
        "detail": f"source_refs_count={len(source_refs)}",
    })

    # Check 3: Binding satisfiability
    binding_graph = _dict(exp.get("binding_coverage_graph"))
    binding_status = _text(binding_graph.get("graph_status"))
    bindings_satisfiable = binding_status != "BLOCKED"
    evidence.append({
        "check": "binding_satisfiability",
        "passed": bindings_satisfiable,
        "detail": f"binding_graph_status={binding_status or 'not_available'}",
    })
    if not bindings_satisfiable and attribution != "BINDING_GRAPH_GAP":
        secondary_contributors.append("BINDING_GRAPH_GAP")

    # Check 4: Adapter availability
    adapter_ref = _text(exp.get("adapter_ref") or exp.get("transport_adapter"))
    has_adapter = bool(adapter_ref)
    evidence.append({
        "check": "adapter_available",
        "passed": has_adapter,
        "detail": f"adapter_ref={adapter_ref or 'none'}",
    })
    if not has_adapter and attribution != "ADAPTER_CAPABILITY_GAP":
        secondary_contributors.append("ADAPTER_CAPABILITY_GAP")

    # Check 5: Actor configuration
    actor_contract = _dict(exp.get("actor_selection_contract"))
    has_actors = bool(actor_contract.get("treatment_actor_ref") or actor_contract.get("control_actor_ref"))
    ir_has_actors = len(actors) > 0
    evidence.append({
        "check": "actor_configured",
        "passed": has_actors or ir_has_actors,
        "detail": f"exp_actors={has_actors}, ir_actors={len(actors)}",
    })
    if not (has_actors or ir_has_actors) and attribution != "SOURCE_GAP":
        secondary_contributors.append("SOURCE_GAP")

    # Check 6: Conflicting source materials
    has_conflict = any(
        _text(r.get("relation_type")) == "conflicts_with"
        for r in relations if isinstance(r, dict)
    )
    evidence.append({
        "check": "no_conflicting_sources",
        "passed": not has_conflict,
        "detail": f"conflict_relations={has_conflict}",
    })
    if has_conflict and attribution != "SOURCE_GAP":
        secondary_contributors.append("SOURCE_GAP")

    # Check 7: Environment policy
    env_policy = _text(exp.get("environment_policy") or obl.get("environment_policy"))
    requires_non_prod = "non_production" in env_policy.lower() or "sandbox" in env_policy.lower()
    evidence.append({
        "check": "environment_policy_compatible",
        "passed": not requires_non_prod,
        "detail": f"environment_policy={env_policy or 'default'}",
    })
    if requires_non_prod and attribution != "ENVIRONMENT_GAP":
        secondary_contributors.append("ENVIRONMENT_GAP")

    # Check 8: Permanent irreversible write
    cleanup_plan = _list(exp.get("cleanup_plan"))
    has_cleanup = len(cleanup_plan) > 0
    compensation = _dict(exp.get("compensation_relation_plan"))
    has_compensation = _text(compensation.get("status")) in ("RESOLVED", "COMPLETE")
    irreversible = not has_cleanup and not has_compensation
    evidence.append({
        "check": "reversible_write",
        "passed": not irreversible,
        "detail": f"cleanup_steps={len(cleanup_plan)}, compensation={has_compensation}",
    })
    if irreversible and attribution != "CLEANUP_CAPABILITY_GAP":
        secondary_contributors.append("CLEANUP_CAPABILITY_GAP")

    # ── Attribution adjustment based on evidence ──
    passed_count = sum(1 for e in evidence if e["passed"])
    total_checks = len(evidence)

    # If original attribution evidence check fails, adjust
    if attribution == "OBSERVER_CAPABILITY_GAP" and has_candidate_op:
        # IR has operations but observer still blocked → refine to binding
        if not bindings_satisfiable:
            adjusted_attribution = "BINDING_GRAPH_GAP"
            adjusted_reason = "BLOCKED_MISSING_BINDING"
            confidence_boost = 0.05
    elif attribution == "BEHAVIOR_MODEL_GAP" and has_candidate_op:
        # IR has candidate ops → not a behavior model gap
        if not has_source_refs:
            adjusted_attribution = "SOURCE_GAP"
            adjusted_reason = "BLOCKED_MISSING_ACTOR"
            confidence_boost = 0.05

    # Confidence based on evidence ratio
    evidence_ratio = passed_count / total_checks if total_checks > 0 else 0.5
    refined_confidence = min(0.95, 0.6 + evidence_ratio * 0.3 + confidence_boost)

    return {
        "primary_attribution": adjusted_attribution,
        "primary_reason": adjusted_reason,
        "secondary_contributors": list(dict.fromkeys(secondary_contributors)),  # dedupe preserve order
        "evidence_checks": evidence,
        "evidence_passed": passed_count,
        "evidence_total": total_checks,
        "refined_confidence": refined_confidence,
        "adjusted": adjusted_attribution != attribution,
    }


# ─── Main Attribution Function ────────────────────────────────────────────────


def attribute_blocker(
    *,
    obligation: dict[str, Any],
    experiment: dict[str, Any] | None,
    execution_result: dict[str, Any] | None,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Attribute the primary blocker for a single obligation.

    SPEC v1.2.1 §11: Two-phase attribution.
    Phase A: Reason Candidate from reason_code mapping.
    Phase B: Evidence Refinement against IR/experiment evidence.

    Args:
        obligation: The test obligation.
        experiment: The compiled experiment (may be BLOCKED).
        execution_result: The execution result (may be None if not executed).
        behavior_ir: The Behavior IR graph.

    Returns:
        qualibug.blocker-attribution.v1 receipt.
    """
    obl = _dict(obligation)
    exp = _dict(experiment)
    result = _dict(execution_result)
    ir = _dict(behavior_ir)

    oid = _text(obl.get("obligation_id"))
    eid = _text(exp.get("experiment_id"))

    # Determine terminal stage and reason
    terminal_stage = ""
    reason_code = ""
    reason_detail = ""

    compile_receipt = _dict(exp.get("compile_receipt"))
    compile_status = _text(compile_receipt.get("status")).upper()

    if compile_status == "BLOCKED":
        terminal_stage = "COMPILER_ENTERED"
        reason_code = _text(compile_receipt.get("reason_code"))
        reason_detail = _text(compile_receipt.get("detail"))
    elif compile_status == "DEFERRED":
        terminal_stage = "COMPILER_ENTERED"
        reason_code = _text(compile_receipt.get("reason_code")) or "DEFERRED"
        reason_detail = _text(compile_receipt.get("detail"))
    elif result:
        exec_status = _text(result.get("status")).upper()
        if exec_status == "BLOCKED":
            terminal_stage = "RUNTIME_PROOF_VALID"
            reason_code = _text(result.get("reason_code"))
            reason_detail = _text(result.get("detail"))
        elif exec_status == "HARNESS_FAILURE":
            terminal_stage = "TARGET_TRANSPORT_REACHED"
            reason_code = "HARNESS_FAILURE"
            reason_detail = _text(result.get("reason_code"))

    # If no blocker found, obligation is not blocked
    if not reason_code:
        return {
            "schema_version": "qualibug.blocker-attribution.v1",
            "obligation_id": oid,
            "experiment_id": eid,
            "terminal_stage": "",
            "reason_code": "",
            "reason_detail": "",
            "attribution": "",
            "primary_attribution": "",
            "primary_reason": "",
            "secondary_contributors": [],
            "recoverability": "",
            "confidence": 1.0,
            "missing_capabilities": [],
            "available_evidence": [],
            "evidence_refinement": None,
            "source_refs": list(obl.get("source_refs") or [])[:5],
            "recommended_fix_class": "",
            "must_remain_blocked": False,
            "fingerprint": "",
        }

    # ── Phase A: Reason Candidate ──
    attribution = "UNKNOWN"
    recoverability = "UNKNOWN"
    must_remain_blocked = False
    confidence = 0.5

    registry_profile = profile_reason_code(reason_code)
    if registry_profile["registry_status"] == "REGISTERED":
        attribution = str(registry_profile["reason_family"])
        recoverability = str(registry_profile["recoverability"])
        must_remain_blocked = bool(registry_profile["must_remain_blocked"])
        confidence = 0.9
    else:
        # An unregistered code is a visible contract defect.  Never infer its
        # family from free-form detail because that can turn an unknown failure
        # into a misleading customer capability claim.
        attribution = "UNKNOWN"
        recoverability = "UNKNOWN"
        confidence = 0.0

    # ── Phase B: Evidence Refinement (SPEC v1.2.1 §11.2) ──
    refinement = _phase_b_evidence_refinement(
        attribution=attribution,
        reason_code=reason_code,
        obligation=obl,
        experiment=exp,
        behavior_ir=ir,
    )
    primary_attribution = refinement["primary_attribution"]
    primary_reason = refinement["primary_reason"]
    secondary_contributors = refinement["secondary_contributors"]
    confidence = refinement["refined_confidence"]

    # Determine missing capabilities
    missing_capabilities: list[str] = []
    if primary_attribution == "OBSERVER_CAPABILITY_GAP":
        missing_capabilities.append("read_operation_for_observer")
    elif primary_attribution == "BINDING_GRAPH_GAP":
        missing_capabilities.append("binding_source_resolution")
    elif primary_attribution == "FIXTURE_CAPABILITY_GAP":
        missing_capabilities.append("fixture_materialization")
    elif primary_attribution == "CLEANUP_CAPABILITY_GAP":
        missing_capabilities.append("compensation_relation")
    elif primary_attribution == "ORACLE_INPUT_GAP":
        missing_capabilities.append("oracle_input_coverage")
    elif primary_attribution == "ADAPTER_CAPABILITY_GAP":
        missing_capabilities.append("adapter_registration")
    elif primary_attribution == "SOURCE_GAP":
        missing_capabilities.append("source_material_acquisition")

    # Recommended fix class
    fix_class_map = {
        "OBSERVER_CAPABILITY_GAP": "observer_resolution_enhancement",
        "BINDING_GRAPH_GAP": "binding_propagation_fix",
        "FIXTURE_CAPABILITY_GAP": "fixture_dag_enhancement",
        "COMPILER_GAP": "compiler_logic_fix",
        "CLEANUP_CAPABILITY_GAP": "compensation_relation_recovery",
        "ORACLE_INPUT_GAP": "oracle_input_contract_fix",
        "ADAPTER_CAPABILITY_GAP": "adapter_registration",
        "SOURCE_GAP": "source_material_acquisition",
        "BEHAVIOR_MODEL_GAP": "behavior_ir_enhancement",
        "ENVIRONMENT_GAP": "environment_configuration",
        "POLICY_SAFETY_BLOCK": "none_permanent",
        "TARGET_SYSTEM_RESPONSE": "target_investigation",
    }
    recommended_fix_class = fix_class_map.get(primary_attribution, "unknown")

    # Fingerprint
    fp_content = {
        "obligation_id": oid,
        "reason_code": primary_reason,
        "reason_registry_status": registry_profile["registry_status"],
        "attribution": primary_attribution,
        "recoverability": recoverability,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fp_content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32]

    return {
        "schema_version": "qualibug.blocker-attribution.v1",
        "obligation_id": oid,
        "experiment_id": eid,
        "terminal_stage": terminal_stage,
        "reason_code": primary_reason,
        "reason_detail": reason_detail,
        "attribution": primary_attribution,
        "primary_attribution": primary_attribution,
        "primary_reason": primary_reason,
        "secondary_contributors": secondary_contributors,
        "recoverability": recoverability,
        "confidence": confidence,
        "missing_capabilities": missing_capabilities,
        "available_evidence": refinement["evidence_checks"],
        "evidence_refinement": {
            "evidence_passed": refinement["evidence_passed"],
            "evidence_total": refinement["evidence_total"],
            "adjusted": refinement["adjusted"],
        },
        "source_refs": list(obl.get("source_refs") or [])[:5],
        "recommended_fix_class": recommended_fix_class,
        "must_remain_blocked": must_remain_blocked,
        "fingerprint": fingerprint,
    }


# ─── Batch Attribution ────────────────────────────────────────────────────────


def attribute_all_blockers(
    *,
    obligations: list[dict[str, Any]],
    experiments: list[dict[str, Any]],
    execution_results: list[dict[str, Any]],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Attribute blockers for all obligations in a campaign.

    Returns summary with per-attribution counts and recoverability breakdown.
    """
    exp_by_oid: dict[str, dict[str, Any]] = {}
    for exp in _list(experiments):
        if isinstance(exp, dict):
            oid = _text(exp.get("obligation_id"))
            if oid:
                exp_by_oid[oid] = exp

    result_by_oid: dict[str, dict[str, Any]] = {}
    for res in _list(execution_results):
        if isinstance(res, dict):
            oid = _text(res.get("obligation_id"))
            if oid:
                result_by_oid[oid] = res

    attributions: list[dict[str, Any]] = []
    attribution_counts: dict[str, int] = {}
    recoverability_counts: dict[str, int] = {}
    recoverable_count = 0
    permanent_count = 0

    seen_oids: set[str] = set()
    for obl in _list(obligations):
        if not isinstance(obl, dict):
            continue
        oid = _text(obl.get("obligation_id"))
        if not oid or oid in seen_oids:
            continue
        seen_oids.add(oid)

        attr = attribute_blocker(
            obligation=obl,
            experiment=exp_by_oid.get(oid),
            execution_result=result_by_oid.get(oid),
            behavior_ir=behavior_ir,
        )
        # Only include blocked obligations
        if _text(attr.get("reason_code")):
            attributions.append(attr)
            cat = _text(attr.get("attribution"))
            attribution_counts[cat] = attribution_counts.get(cat, 0) + 1
            rec = _text(attr.get("recoverability"))
            recoverability_counts[rec] = recoverability_counts.get(rec, 0) + 1
            if rec == "RECOVERABLE":
                recoverable_count += 1
            elif rec in ("PERMANENTLY_BLOCKED",) or attr.get("must_remain_blocked"):
                permanent_count += 1

    return {
        "schema_version": "qualibug.blocker-attribution-batch.v1",
        "total_blocked": len(attributions),
        "recoverable_count": recoverable_count,
        "permanent_count": permanent_count,
        "attribution_counts": attribution_counts,
        "recoverability_counts": recoverability_counts,
        "attributions": attributions,
    }
