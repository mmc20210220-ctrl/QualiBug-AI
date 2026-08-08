"""Safe Experiment Prioritizer — budget-aware execution ordering.

SPEC v1.2 §12: Experiment Prioritization and Budget Allocation

This module orders experiments by safety, observability, depth, and
novelty potential. It never changes blocking decisions or gate standards.

Ordering only affects execution sequence within a fixed budget.

Family-fair execution budget (distribution balance): on top of the
operation-fair tier, each risk family present in the pool keeps a minimum
execution quota (top ``family_quota`` scored rows per family, default 1)
ABOVE every second-tier row. A large, high-scoring authorization base can
therefore never push state/idempotency/conservation/validation/privacy
obligations out of the per-batch budget — with a budget of at least
``family_quota * <distinct families>`` every family executes, on any
enterprise system. Families are taken from the obligation rows themselves
(``risk_family``, the product's open family registry), never hardcoded.
"""
from __future__ import annotations

from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


# ─── Priority Factors ─────────────────────────────────────────────────────────

# Allowed factors (SPEC §12.1)
ALLOWED_FACTORS = frozenset({
    "risk_depth",
    "operation_reachability",
    "observer_readiness",
    "binding_readiness",
    "proof_readiness",
    "source_confidence",
    "historical_findings_by_mechanism",
    "root_cause_novelty",
    "entity_chain_depth",
    "state_transition_depth",
    "cross_entity_depth",
})


# ─── Scoring ──────────────────────────────────────────────────────────────────


def score_experiment_priority(
    *,
    experiment: dict[str, Any],
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
    historical_findings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score an experiment for execution priority.

    Higher score = higher priority. Only uses SPEC-allowed factors.
    Never uses benchmark bug IDs, known bug locations, or hardcoded hints.

    Returns:
        {"score": float, "factors": dict, "experiment_id": str}
    """
    exp = _dict(experiment)
    obl = _dict(obligation)
    ir = _dict(behavior_ir)

    factors: dict[str, float] = {}
    score = 0.0

    # Risk depth: deeper risk families get higher priority
    family = _text(obl.get("risk_family"))
    deep_families = {"conservation", "concurrency", "isolation", "state_integrity", "consistency"}
    medium_families = {"authorization", "visibility", "lifecycle", "invariant"}
    if family in deep_families:
        factors["risk_depth"] = 1.0
        score += 3.0
    elif family in medium_families:
        factors["risk_depth"] = 0.6
        score += 2.0
    else:
        factors["risk_depth"] = 0.3
        score += 1.0

    # Observer readiness: more observers = more observable
    observers = _list(exp.get("observers"))
    observer_count = len(observers)
    factors["observer_readiness"] = min(observer_count / 3.0, 1.0)
    score += factors["observer_readiness"] * 2.0

    # Binding readiness: fewer unresolved = more ready
    binding_plan = _list(exp.get("binding_plan"))
    unresolved = sum(
        1 for b in binding_plan
        if isinstance(b, dict) and _text(b.get("status")) == "unresolved"
    )
    total_bindings = max(len(binding_plan), 1)
    factors["binding_readiness"] = 1.0 - (unresolved / total_bindings)
    score += factors["binding_readiness"] * 1.5

    # Proof readiness: has write proof = more ready
    proof = _dict(exp.get("write_reversibility_proof"))
    safety = _dict(exp.get("safety_contract"))
    if not safety.get("governed_write") or _text(proof.get("proof_status")) == "PROVEN":
        factors["proof_readiness"] = 1.0
        score += 1.5
    else:
        factors["proof_readiness"] = 0.0

    # Source confidence: more source refs = higher confidence
    source_refs = _list(obl.get("source_refs"))
    factors["source_confidence"] = min(len(source_refs) / 3.0, 1.0)
    score += factors["source_confidence"] * 1.0

    # Root cause novelty: prefer mechanisms not yet found
    history = _list(historical_findings)
    found_mechanisms = {
        _text(f.get("mechanism") or f.get("risk_family"))
        for f in history if isinstance(f, dict)
    }
    mechanism = _text(obl.get("mechanism") or obl.get("risk_family"))
    if mechanism and mechanism not in found_mechanisms:
        factors["root_cause_novelty"] = 1.0
        score += 2.0
    else:
        factors["root_cause_novelty"] = 0.2
        score += 0.5

    # Entity chain depth: cross-entity = deeper
    required_ops = _list(obl.get("required_operations"))
    if len(required_ops) > 1:
        factors["entity_chain_depth"] = min(len(required_ops) / 3.0, 1.0)
        score += factors["entity_chain_depth"] * 1.5
    else:
        factors["entity_chain_depth"] = 0.0

    # State transition depth
    prop = _dict(obl.get("property"))
    if _text(prop.get("from_state")) and _text(prop.get("to_state")):
        factors["state_transition_depth"] = 0.8
        score += 1.0
    else:
        factors["state_transition_depth"] = 0.0

    return {
        "score": round(score, 4),
        "factors": factors,
        "experiment_id": _text(exp.get("experiment_id")),
        "obligation_id": _text(exp.get("obligation_id")),
        "risk_family": family,
    }


# ─── Batch Prioritization ─────────────────────────────────────────────────────


def prioritize_experiments(
    *,
    experiments: list[dict[str, Any]],
    obligations: list[dict[str, Any]],
    behavior_ir: dict[str, Any],
    historical_findings: list[dict[str, Any]] | None = None,
    budget: int = 100,
    family_quota: int = 1,
) -> dict[str, Any]:
    """Prioritize experiments for execution within a budget.

    Ordering tiers (family-fair above operation-fair, then score):
      1. Family-fair: the top ``family_quota`` scored rows of every risk
         family present in the pool — a minimum execution quota per family
         so authorization can never crowd out state/idempotency/conservation/
         validation/privacy obligations.
      2. Operation-fair: the top scored row of every operation not already
         promoted by the family tier — one experiment per operation minimum.
      3. Remaining rows by score.

    Guarantees (for any pool, any target): with budget >= family_quota ×
    <distinct families> every family has at least its quota inside the
    budget (the family tier occupies the leading positions); when several
    families' top rows land on the same operation the two tiers together can
    need up to <distinct operations> + <distinct families> rows, so the
    batch executor floors the budget at that union bound and every operation
    keeps its minimum too. Does NOT change blocking status. Low-priority
    obligations remain in the funnel.
    """
    obl_by_id: dict[str, dict[str, Any]] = {}
    for obl in _list(obligations):
        if isinstance(obl, dict):
            oid = _text(obl.get("obligation_id"))
            if oid:
                obl_by_id[oid] = obl

    scored: list[dict[str, Any]] = []
    for exp in _list(experiments):
        if not isinstance(exp, dict):
            continue
        oid = _text(exp.get("obligation_id"))
        obl = obl_by_id.get(oid, {})
        priority = score_experiment_priority(
            experiment=exp,
            obligation=obl,
            behavior_ir=behavior_ir,
            historical_findings=historical_findings,
        )
        # Operation identity comes from the planner row (selected obligation
        # rows carry operation_key / path_prefix). Kept on the scored item so
        # the operation-fair ordering below can group without re-resolving.
        priority["operation_key"] = _text(obl.get("operation_key"))
        priority["path_prefix"] = _text(obl.get("path_prefix"))
        scored.append(priority)

    # Sort by score descending
    scored.sort(key=lambda x: x["score"], reverse=True)

    quota = max(1, int(family_quota or 1))

    # ── Family-fair first tier ──
    # Each risk family present in the pool keeps its minimum execution quota
    # (top `quota` scored rows per family) above every second-tier row, so a
    # large authorization base can never push state/idempotency/conservation/
    # validation/privacy obligations out of the per-batch budget.
    family_taken: dict[str, int] = {}
    family_tier: list[dict[str, Any]] = []
    for item in scored:
        family = _text(item.get("risk_family"))
        if not family:
            continue
        if family_taken.get(family, 0) >= quota:
            continue
        family_taken[family] = family_taken.get(family, 0) + 1
        family_tier.append(item)
    family_tier.sort(
        key=lambda x: (-float(x["score"]), _text(x["obligation_id"]))
    )
    family_tier_ids = {_text(item["obligation_id"]) for item in family_tier}

    # ── Operation-fair second tier ──
    # Promote the top-scoring experiment of each distinct operation above all
    # remaining rows, so any budget that fits one experiment per operation
    # always covers every operation. An operation whose top row was already
    # promoted by the family tier is covered there — its second row must not
    # consume an operation-tier slot.
    promoted_ops: set[str] = set()
    operation_tier: list[dict[str, Any]] = []
    for item in scored:
        op = item["operation_key"]
        if not op or op in promoted_ops:
            continue
        promoted_ops.add(op)
        if _text(item["obligation_id"]) in family_tier_ids:
            continue
        operation_tier.append(item)
    operation_tier.sort(
        key=lambda x: (-float(x["score"]), _text(x["obligation_id"]))
    )
    operation_tier_ids = {_text(item["obligation_id"]) for item in operation_tier}

    # ── Remainder by score ──
    promoted_ids = family_tier_ids | operation_tier_ids
    rest = [
        item for item in scored if _text(item["obligation_id"]) not in promoted_ids
    ]
    ordered = family_tier + operation_tier + rest

    # Mark budget boundary
    for i, item in enumerate(ordered):
        item["within_budget"] = i < budget
        item["execution_rank"] = i + 1

    # Family coverage within the budget (operator-visible, generic).
    family_coverage: dict[str, int] = {}
    for item in ordered[:budget]:
        family = _text(item.get("risk_family"))
        if family:
            family_coverage[family] = family_coverage.get(family, 0) + 1

    return {
        "schema_version": "qualibug.experiment-priority.v1",
        "total_scored": len(ordered),
        "budget": budget,
        "family_quota": quota,
        "within_budget_count": min(len(ordered), budget),
        "family_coverage": family_coverage,
        "families_present": sorted(family_taken),
        "prioritized": ordered,
    }
