"""Blocker Attribution — classify why each obligation is blocked.

SPEC v1.2 §6: Unified Blocker Attribution

This module attributes each blocked obligation to exactly one primary cause
category, enabling targeted capability recovery without blind fixes.

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


# ─── Main Attribution Function ────────────────────────────────────────────────


def attribute_blocker(
    *,
    obligation: dict[str, Any],
    experiment: dict[str, Any] | None,
    execution_result: dict[str, Any] | None,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Attribute the primary blocker for a single obligation.

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
            "recoverability": "",
            "confidence": 1.0,
            "missing_capabilities": [],
            "available_evidence": [],
            "source_refs": list(obl.get("source_refs") or [])[:5],
            "recommended_fix_class": "",
            "must_remain_blocked": False,
            "fingerprint": "",
        }

    # Look up attribution
    attribution = "UNKNOWN"
    recoverability = "UNKNOWN"
    must_remain_blocked = False
    confidence = 0.5

    if reason_code in _REASON_ATTRIBUTION:
        attribution, recoverability, must_remain_blocked = _REASON_ATTRIBUTION[reason_code]
        confidence = 0.9
    else:
        # Heuristic attribution from reason detail
        detail_lower = reason_detail.lower()
        if "observer" in detail_lower or "observation" in detail_lower:
            attribution = "OBSERVER_CAPABILITY_GAP"
            recoverability = "RECOVERABLE"
            confidence = 0.7
        elif "binding" in detail_lower or "placeholder" in detail_lower:
            attribution = "BINDING_GRAPH_GAP"
            recoverability = "RECOVERABLE"
            confidence = 0.7
        elif "fixture" in detail_lower:
            attribution = "FIXTURE_CAPABILITY_GAP"
            recoverability = "RECOVERABLE"
            confidence = 0.7
        elif "actor" in detail_lower or "secret" in detail_lower:
            attribution = "SOURCE_GAP"
            recoverability = "SOURCE_DEPENDENT"
            confidence = 0.7
        elif "cleanup" in detail_lower or "reversib" in detail_lower:
            attribution = "CLEANUP_CAPABILITY_GAP"
            recoverability = "SOURCE_DEPENDENT"
            confidence = 0.7
        elif "environment" in detail_lower or "production" in detail_lower:
            attribution = "ENVIRONMENT_GAP"
            recoverability = "ENVIRONMENT_DEPENDENT"
            must_remain_blocked = True
            confidence = 0.8
        elif "oracle" in detail_lower:
            attribution = "ORACLE_INPUT_GAP"
            recoverability = "RECOVERABLE"
            confidence = 0.7

    # Determine missing capabilities
    missing_capabilities: list[str] = []
    if attribution == "OBSERVER_CAPABILITY_GAP":
        missing_capabilities.append("read_operation_for_observer")
    elif attribution == "BINDING_GRAPH_GAP":
        missing_capabilities.append("binding_source_resolution")
    elif attribution == "FIXTURE_CAPABILITY_GAP":
        missing_capabilities.append("fixture_materialization")
    elif attribution == "CLEANUP_CAPABILITY_GAP":
        missing_capabilities.append("compensation_relation")
    elif attribution == "ORACLE_INPUT_GAP":
        missing_capabilities.append("oracle_input_coverage")

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
    recommended_fix_class = fix_class_map.get(attribution, "unknown")

    # Fingerprint
    fp_content = {
        "obligation_id": oid,
        "reason_code": reason_code,
        "attribution": attribution,
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
        "reason_code": reason_code,
        "reason_detail": reason_detail,
        "attribution": attribution,
        "recoverability": recoverability,
        "confidence": confidence,
        "missing_capabilities": missing_capabilities,
        "available_evidence": [],
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
