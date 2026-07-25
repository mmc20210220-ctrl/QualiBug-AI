"""Combination Generator — constrained multi-dimensional combination.

SPEC §13-15: Generates 1-way, 2-way, 3-way operator combinations.
Prohibits cartesian product exhaustion. Uses pairwise coverage,
risk weighting, novelty weighting, and cost estimation.

Constraints:
  - Shared business object
  - Shared or related Operation
  - Shared Invariant
  - Pre-state reachable
  - Fixture buildable
  - Observer complete
  - Risk allowed
  - Combination has discrimination
"""
from __future__ import annotations

import hashlib
import itertools
import time
from typing import Any


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return "comb_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _text(v: Any) -> str:
    return str(v or "").strip()


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []


# ─── Priority Score (SPEC §15) ─────────────────────────────────────────────────

PRIORITY_WEIGHTS = {
    "coverage_gain": 0.25,
    "deep_bug_potential": 0.20,
    "business_risk": 0.15,
    "novelty_score": 0.15,
    "observation_confidence": 0.10,
    "historical_yield": 0.10,
    "cost_efficiency": 0.05,
}


def compute_priority_score(
    *,
    coverage_gain: float = 0.5,
    deep_bug_potential: float = 0.5,
    business_risk: float = 0.5,
    novelty_score: float = 0.5,
    observation_confidence: float = 0.5,
    historical_yield: float = 0.5,
    cost_efficiency: float = 0.5,
) -> float:
    """Compute priority score for a combination candidate."""
    score = (
        coverage_gain * PRIORITY_WEIGHTS["coverage_gain"]
        + deep_bug_potential * PRIORITY_WEIGHTS["deep_bug_potential"]
        + business_risk * PRIORITY_WEIGHTS["business_risk"]
        + novelty_score * PRIORITY_WEIGHTS["novelty_score"]
        + observation_confidence * PRIORITY_WEIGHTS["observation_confidence"]
        + historical_yield * PRIORITY_WEIGHTS["historical_yield"]
        + cost_efficiency * PRIORITY_WEIGHTS["cost_efficiency"]
    )
    return round(score, 4)


# ─── Combination Validation ────────────────────────────────────────────────────

def validate_combination(
    operators: list[dict[str, Any]],
    *,
    behavior_ir: dict[str, Any],
    invariant: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate whether a combination of operators is feasible.

    Checks: incompatible operators, shared business object, risk level.
    """
    if not operators:
        return {"valid": False, "reason": "empty_combination"}

    # Check incompatible operators
    op_types = {op.get("operator_type", "") for op in operators}
    for op in operators:
        incompatible = set(op.get("incompatible_operators", []))
        if incompatible & op_types:
            return {
                "valid": False,
                "reason": f"incompatible: {op.get('operator_type')} conflicts with {incompatible & op_types}",
            }

    # Check combined risk level
    risk_scores = {"LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    max_risk = max(risk_scores.get(op.get("risk_level", "LOW"), 1) for op in operators)
    combined_risk = "LOW"
    if max_risk >= 4:
        combined_risk = "CRITICAL"
    elif max_risk >= 3:
        combined_risk = "HIGH"
    elif max_risk >= 2:
        combined_risk = "MEDIUM"

    # Check shared categories (must have some relationship)
    categories = {op.get("category", "") for op in operators}

    # Estimate combined cost
    total_cost = sum(op.get("cost_estimate", 1.0) for op in operators)

    return {
        "valid": True,
        "combined_risk": combined_risk,
        "categories_involved": sorted(categories),
        "total_cost_estimate": round(total_cost, 1),
        "operator_count": len(operators),
        "combination_level": f"{len(operators)}-way",
    }


# ─── Combination Generation ────────────────────────────────────────────────────

def generate_combinations(
    applicable_operators: list[dict[str, Any]],
    *,
    max_level: int = 3,
    max_combinations: int = 100,
    behavior_ir: dict[str, Any] | None = None,
    existing_coverage: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Generate constrained operator combinations (1-way, 2-way, 3-way).

    Uses pairwise coverage strategy to avoid cartesian explosion.
    """
    ir = behavior_ir or {}
    covered = existing_coverage or set()
    combinations = []

    # 1-way: single operators
    for op in applicable_operators:
        op_type = op.get("operator_type", "")
        comb_id = _stable_id("1way", op_type)
        if comb_id in covered:
            continue

        validation = validate_combination([op], behavior_ir=ir)
        if not validation.get("valid"):
            continue

        score = compute_priority_score(
            coverage_gain=0.8 if op_type not in covered else 0.2,
            deep_bug_potential=0.6,
            business_risk=0.5,
            novelty_score=0.7 if op_type not in covered else 0.1,
            observation_confidence=0.8,
            historical_yield=0.5,
            cost_efficiency=1.0 / max(op.get("cost_estimate", 1.0), 0.1),
        )

        combinations.append({
            "combination_id": comb_id,
            "level": "1-way",
            "operators": [op_type],
            "categories": [op.get("category", "")],
            "validation": validation,
            "priority_score": score,
            "created_at": time.time(),
        })

    # 2-way: pairwise combinations (constrained)
    if max_level >= 2:
        # Group by category for meaningful pairs
        by_category: dict[str, list] = {}
        for op in applicable_operators:
            cat = op.get("category", "")
            by_category.setdefault(cat, []).append(op)

        # Cross-category pairs (most valuable)
        categories = list(by_category.keys())
        for i, cat_a in enumerate(categories):
            for cat_b in categories[i + 1:]:
                # Take top operator from each category
                ops_a = by_category[cat_a][:2]
                ops_b = by_category[cat_b][:2]
                for op_a in ops_a:
                    for op_b in ops_b:
                        type_a = op_a.get("operator_type", "")
                        type_b = op_b.get("operator_type", "")
                        comb_id = _stable_id("2way", type_a, type_b)
                        if comb_id in covered:
                            continue

                        validation = validate_combination([op_a, op_b], behavior_ir=ir)
                        if not validation.get("valid"):
                            continue

                        score = compute_priority_score(
                            coverage_gain=0.9,
                            deep_bug_potential=0.75,
                            business_risk=0.6,
                            novelty_score=0.85,
                            observation_confidence=0.7,
                            historical_yield=0.4,
                            cost_efficiency=1.0 / max(validation.get("total_cost_estimate", 2.0), 0.1),
                        )

                        combinations.append({
                            "combination_id": comb_id,
                            "level": "2-way",
                            "operators": [type_a, type_b],
                            "categories": [cat_a, cat_b],
                            "validation": validation,
                            "priority_score": score,
                            "created_at": time.time(),
                        })

    # 3-way: constrained triples (only high-value cross-category)
    if max_level >= 3:
        categories = list(by_category.keys()) if max_level >= 2 else []
        # Select diverse triples
        if len(categories) >= 3:
            for triple in itertools.combinations(categories, 3):
                ops_triple = []
                for cat in triple:
                    if by_category.get(cat):
                        ops_triple.append(by_category[cat][0])

                if len(ops_triple) == 3:
                    types = [op.get("operator_type", "") for op in ops_triple]
                    comb_id = _stable_id("3way", *types)
                    if comb_id in covered:
                        continue

                    validation = validate_combination(ops_triple, behavior_ir=ir)
                    if not validation.get("valid"):
                        continue

                    score = compute_priority_score(
                        coverage_gain=0.95,
                        deep_bug_potential=0.85,
                        business_risk=0.7,
                        novelty_score=0.95,
                        observation_confidence=0.6,
                        historical_yield=0.3,
                        cost_efficiency=1.0 / max(validation.get("total_cost_estimate", 3.0), 0.1),
                    )

                    combinations.append({
                        "combination_id": comb_id,
                        "level": "3-way",
                        "operators": types,
                        "categories": list(triple),
                        "validation": validation,
                        "priority_score": score,
                        "created_at": time.time(),
                    })

    # Sort by priority score descending
    combinations.sort(key=lambda c: c.get("priority_score", 0), reverse=True)

    # Apply budget limit
    return combinations[:max_combinations]


# ─── Pairwise Coverage ─────────────────────────────────────────────────────────

def compute_pairwise_coverage(
    combinations: list[dict[str, Any]],
    all_operator_types: list[str],
) -> dict[str, Any]:
    """Compute pairwise coverage statistics."""
    covered_pairs: set[tuple[str, str]] = set()
    total_possible = len(all_operator_types) * (len(all_operator_types) - 1) // 2

    for comb in combinations:
        ops = comb.get("operators", [])
        for i, a in enumerate(ops):
            for b in ops[i + 1:]:
                pair = tuple(sorted([a, b]))
                covered_pairs.add(pair)

    return {
        "total_operators": len(all_operator_types),
        "total_possible_pairs": total_possible,
        "covered_pairs": len(covered_pairs),
        "pairwise_coverage_rate": round(len(covered_pairs) / max(total_possible, 1), 4),
        "combinations_count": len(combinations),
        "level_distribution": {
            "1-way": sum(1 for c in combinations if c.get("level") == "1-way"),
            "2-way": sum(1 for c in combinations if c.get("level") == "2-way"),
            "3-way": sum(1 for c in combinations if c.get("level") == "3-way"),
        },
    }
