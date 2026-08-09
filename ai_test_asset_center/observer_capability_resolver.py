"""Observer Capability Resolver — find real observer operations from Behavior IR.

SPEC v1.2 §7: Observer Resolution Capability

This module resolves observer requirements to real, independent, bindable
GET/HEAD operations declared in Behavior IR. It never invents paths or
guesses operations.

Output: qualibug.observer-resolution-plan.v1
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


# ─── Observer Kinds ───────────────────────────────────────────────────────────

OBSERVER_KINDS = frozenset({
    "identity_read",       # GET /entity/{id}
    "collection_read",     # GET /entities
    "state_read",          # GET with state field
    "effect_read",         # GET related entity affected by write
    "existence_check",     # HEAD /entity/{id}
})


# ─── Candidate Scoring ────────────────────────────────────────────────────────


def _score_candidate(
    *,
    operation: dict[str, Any],
    primary_operation: dict[str, Any],
    behavior_ir: dict[str, Any],
    observer_requirement: str,
) -> dict[str, Any]:
    """Score an observer candidate operation.

    Only considers SPEC-allowed factors:
    - source_relation_strength
    - entity_match
    - identity_field_match
    - path_binding_completeness
    - independence
    - read_only_safety
    - source_confidence
    """
    op = _dict(operation)
    primary = _dict(primary_operation)
    ir = _dict(behavior_ir)

    score = 0.0
    reasons: list[str] = []

    # Read-only safety (mandatory)
    method = _text(op.get("method")).upper()
    if method not in ("GET", "HEAD"):
        return {"score": -1.0, "reasons": ["not_read_only"], "eligible": False}
    score += 0.2
    reasons.append("read_only_safe")

    # Entity match: same collection or related entity
    op_path = _text(op.get("path") or op.get("raw_path"))
    primary_path = _text(primary.get("path") or primary.get("raw_path"))
    op_entity = _text(op.get("entity_ref") or op.get("entity"))
    primary_entity = _text(primary.get("entity_ref") or primary.get("entity"))

    if op_entity and primary_entity and op_entity == primary_entity:
        score += 0.3
        reasons.append("entity_match_exact")
    elif op_path and primary_path:
        # Collection prefix match
        op_coll = op_path.split("{")[0].rstrip("/")
        primary_coll = primary_path.split("{")[0].rstrip("/")
        if op_coll and primary_coll and op_coll == primary_coll:
            score += 0.2
            reasons.append("collection_match")

    # Identity field match
    op_id_fields = _list(op.get("identity_fields") or op.get("path_params"))
    primary_id_fields = _list(primary.get("identity_fields") or primary.get("path_params"))
    if op_id_fields and primary_id_fields:
        overlap = set(str(f) for f in op_id_fields) & set(str(f) for f in primary_id_fields)
        if overlap:
            score += 0.2
            reasons.append("identity_field_overlap")

    # Source relation strength
    op_id = _text(op.get("id"))
    primary_id = _text(primary.get("id"))
    from .compile_batch_context import get_batch_indexes

    _indexes = get_batch_indexes()
    if _indexes is not None and op_id:
        # O(1) set lookups instead of an O(relations) scan per candidate
        # (SPEC-11 4.2); identical membership semantics. An empty op_id falls
        # back to the scan, which would match empty relation operation_refs.
        has_relation = (
            (op_id, primary_id) in _indexes.relation_pairs
            or (primary_id, op_id) in _indexes.relation_pairs
            or op_id in _indexes.relation_operation_refs
        )
    else:
        relations = _list(ir.get("relations"))
        has_relation = any(
            isinstance(r, dict)
            and (
                (_text(r.get("from_ref")) == op_id and _text(r.get("to_ref")) == primary_id)
                or (_text(r.get("from_ref")) == primary_id and _text(r.get("to_ref")) == op_id)
                or (_text(r.get("operation_ref")) == op_id)
            )
            for r in relations
        )
    if has_relation:
        score += 0.2
        reasons.append("source_relation_exists")

    # Independence: not the same operation as primary
    if op_id and primary_id and op_id != primary_id:
        score += 0.1
        reasons.append("independent_from_primary")

    # Source confidence
    source_refs = _list(op.get("source_refs"))
    if source_refs:
        score += 0.1
        reasons.append("has_source_refs")

    return {"score": round(score, 4), "reasons": reasons, "eligible": score >= 0.4}


# ─── Main Resolver ────────────────────────────────────────────────────────────


def resolve_observer_capability(
    *,
    observer_requirement: str,
    primary_operation: dict[str, Any],
    behavior_ir: dict[str, Any],
    required_entity_ref: str = "",
    required_bindings: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve an observer requirement to a real Behavior IR operation.

    Args:
        observer_requirement: e.g. "before_state", "after_state", "entity_state"
        primary_operation: The primary write/read operation dict.
        behavior_ir: The full Behavior IR graph.
        required_entity_ref: Entity the observer must target.
        required_bindings: Bindings needed for the observer path.

    Returns:
        qualibug.observer-resolution-plan.v1
    """
    ir = _dict(behavior_ir)
    primary = _dict(primary_operation)
    ops = _list(ir.get("operations"))
    primary_id = _text(primary.get("id"))
    primary_method = _text(primary.get("method")).upper()

    # Determine observer kind from requirement
    observer_kind = "identity_read"
    if observer_requirement in ("before_state", "after_state", "final_state"):
        observer_kind = "identity_read"
    elif observer_requirement in ("entity_state", "business_effect"):
        observer_kind = "effect_read"
    elif observer_requirement in ("collection_state",):
        observer_kind = "collection_read"

    # Find candidates: only real GET/HEAD operations from Behavior IR
    candidates: list[dict[str, Any]] = []
    for op in ops:
        if not isinstance(op, dict):
            continue
        op_id = _text(op.get("id"))
        if not op_id or op_id == primary_id:
            continue
        method = _text(op.get("method")).upper()
        if method not in ("GET", "HEAD"):
            continue

        scoring = _score_candidate(
            operation=op,
            primary_operation=primary,
            behavior_ir=ir,
            observer_requirement=observer_requirement,
        )
        if scoring["eligible"]:
            candidates.append({
                "operation": op,
                "score": scoring["score"],
                "reasons": scoring["reasons"],
            })

    # Sort by score descending
    candidates.sort(key=lambda c: c["score"], reverse=True)

    # Select best candidate
    if not candidates:
        return {
            "schema_version": "qualibug.observer-resolution-plan.v1",
            "observer_requirement": observer_requirement,
            "observer_kind": observer_kind,
            "operation_ref": "",
            "method": "",
            "path": "",
            "entity_ref": "",
            "identity_strategy": "",
            "required_bindings": list(required_bindings or []),
            "available_bindings": [],
            "independent_from_primary_response": True,
            "source_refs": [],
            "confidence": 0.0,
            "resolution_status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_OBSERVER",
            "detail": "no_eligible_read_operation_in_behavior_ir",
            "binding_dependency_status": "",
            "ambiguous_candidates": [],
            "fingerprint": "",
        }

    # ── SPEC v1.2.1 §6.3: Ambiguity detection ──
    # When top two candidates have score difference < 0.05 and no stronger
    # source relation distinguishes them, return AMBIGUOUS.
    ambiguous_candidates: list[dict[str, Any]] = []
    if len(candidates) >= 2:
        score_gap = candidates[0]["score"] - candidates[1]["score"]
        if score_gap < 0.05:
            # Check if source relations distinguish them
            op0 = _dict(candidates[0]["operation"])
            op1 = _dict(candidates[1]["operation"])
            src0 = _list(op0.get("source_refs"))
            src1 = _list(op1.get("source_refs"))
            # More source refs = stronger evidence
            if len(src0) == len(src1):
                ambiguous_candidates = [
                    {"operation_ref": _text(op0.get("id")), "score": candidates[0]["score"]},
                    {"operation_ref": _text(op1.get("id")), "score": candidates[1]["score"]},
                ]
                return {
                    "schema_version": "qualibug.observer-resolution-plan.v1",
                    "observer_requirement": observer_requirement,
                    "observer_kind": observer_kind,
                    "operation_ref": "",
                    "method": "",
                    "path": "",
                    "entity_ref": "",
                    "identity_strategy": "",
                    "required_bindings": list(required_bindings or []),
                    "available_bindings": [],
                    "independent_from_primary_response": True,
                    "source_refs": [],
                    "confidence": 0.0,
                    "resolution_status": "AMBIGUOUS",
                    "reason_code": "BLOCKED_MISSING_OBSERVER",
                    "detail": "observer_candidate_ambiguous",
                    "binding_dependency_status": "",
                    "ambiguous_candidates": ambiguous_candidates,
                    "candidates_evaluated": len(candidates),
                    "fingerprint": "",
                }

    best = candidates[0]
    best_op = _dict(best["operation"])
    best_op_id = _text(best_op.get("id"))
    best_path = _text(best_op.get("path") or best_op.get("raw_path"))
    best_method = _text(best_op.get("method")).upper()
    best_entity = _text(best_op.get("entity_ref") or best_op.get("entity"))

    # Determine identity strategy
    identity_strategy = "path_identity" if "{" in best_path else "collection_filter"

    # Check binding completeness
    path_params: list[str] = []
    if "{" in best_path:
        import re
        path_params = re.findall(r"\{(\w+)\}", best_path)
    available_bindings = [p for p in path_params if p in (required_bindings or [])]
    missing_bindings = [p for p in path_params if p not in (required_bindings or [])]

    # ── SPEC v1.2.1 §6.2: Binding-dependent resolution status ──
    # missing_bindings non-empty → PENDING_BINDING (not RESOLVED)
    # Binding Graph may later produce these bindings from fixture/primary response.
    if missing_bindings:
        resolution_status = "PENDING_BINDING"
        binding_dependency_status = "awaiting_binding_graph"
        confidence = best["score"] * 0.7
    else:
        resolution_status = "RESOLVED"
        binding_dependency_status = "complete"
        confidence = best["score"]

    # Fingerprint
    fp_content = {
        "observer_requirement": observer_requirement,
        "operation_ref": best_op_id,
        "method": best_method,
        "path": best_path,
    }
    fingerprint = hashlib.sha256(
        json.dumps(fp_content, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()[:32]

    return {
        "schema_version": "qualibug.observer-resolution-plan.v1",
        "observer_requirement": observer_requirement,
        "observer_kind": observer_kind,
        "operation_ref": best_op_id,
        "method": best_method,
        "path": best_path,
        "entity_ref": best_entity,
        "identity_strategy": identity_strategy,
        "required_bindings": list(required_bindings or []),
        "available_bindings": available_bindings,
        "missing_bindings": missing_bindings,
        "independent_from_primary_response": best_op_id != primary_id,
        "source_refs": list(best_op.get("source_refs") or [])[:3],
        "confidence": round(confidence, 4),
        "resolution_status": resolution_status,
        "binding_dependency_status": binding_dependency_status,
        "ambiguous_candidates": [],
        "reason_code": "" if resolution_status == "RESOLVED" else "PENDING_BINDING",
        "detail": "" if resolution_status == "RESOLVED" else f"missing_bindings:{';'.join(missing_bindings[:5])}",
        "candidates_evaluated": len(candidates),
        "scoring_reasons": best["reasons"],
        "fingerprint": fingerprint,
    }
