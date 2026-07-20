"""Hypothesis prioritization and deduplication for discovery coverage optimization.

This module provides intelligent hypothesis ranking and deduplication to maximize
the probability of finding real bugs within execution budget constraints.

Key features:
- Semantic deduplication using title/description similarity
- Multi-factor priority scoring (risk, source, severity, history)
- Dynamic engine weight adjustment based on historical hit rates
- Budget-aware selection for optimal coverage
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

# ── Risk type priority weights ──
RISK_PRIORITY = {
    "permission_boundary": 1.00,
    "authorization": 0.95,
    "data_conservation": 0.92,
    "state_machine": 0.90,
    "isolation": 0.88,
    "idempotency": 0.85,
    "concurrency": 0.82,
    "data_reconciliation": 0.80,
    "async_event": 0.75,
    "sensitive_data": 0.72,
    "input_validation": 0.68,
    "error_handling": 0.65,
    "historical_regression": 0.60,
}

# ── Source reliability weights ──
SOURCE_RELIABILITY = {
    "explicit_requirement": 1.00,
    "api_contract": 0.92,
    "business_rule": 0.88,
    "schema_constraint": 0.85,
    "permission_matrix": 0.82,
    "state_diagram": 0.80,
    "inferred_pattern": 0.65,
    "heuristic": 0.50,
    "analogical": 0.40,
}

# ── Severity weights ──
SEVERITY_WEIGHT = {
    "P0": 1.00,
    "P1": 0.85,
    "P2": 0.65,
    "P3": 0.45,
    "P4": 0.30,
}


def prioritize_and_deduplicate(
    hypotheses: list[dict[str, Any]],
    *,
    max_count: int = 80,
    history: dict[str, Any] | None = None,
    engine_weights: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Prioritize and deduplicate hypotheses for optimal discovery coverage.

    Args:
        hypotheses: List of hypothesis dicts
        max_count: Maximum hypotheses to return
        history: Historical hit rate data per engine/risk_type
        engine_weights: Dynamic weights per engine (from learning)

    Returns:
        Sorted and deduplicated list of hypotheses
    """
    if not hypotheses:
        return []

    # Step 1: Deduplicate
    unique = _deduplicate_hypotheses(hypotheses)

    # Step 2: Score each hypothesis
    scored: list[tuple[float, dict[str, Any]]] = []
    for h in unique:
        score = _compute_priority_score(h, history, engine_weights)
        h["_priority_score"] = score
        scored.append((score, h))

    # Step 3: Sort by score descending
    scored.sort(key=lambda x: -x[0])

    # Step 4: Apply diversity constraint (avoid over-representing one risk type)
    selected = _apply_diversity_constraint(
        [h for _, h in scored],
        max_count=max_count,
    )

    return selected


def _deduplicate_hypotheses(hypotheses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate hypotheses based on semantic similarity."""
    seen_signatures: set[str] = set()
    unique: list[dict[str, Any]] = []

    for h in hypotheses:
        if not isinstance(h, dict):
            continue

        # Generate signature from key fields
        sig = _hypothesis_signature(h)
        if sig in seen_signatures:
            continue
        seen_signatures.add(sig)
        unique.append(h)

    return unique


def _hypothesis_signature(h: dict[str, Any]) -> str:
    """Generate a deduplication signature for a hypothesis."""
    # Normalize title
    title = str(h.get("title") or h.get("hypothesis") or "").lower()
    title = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", title).strip()

    # Normalize risk type
    risk = str(h.get("risk_type") or h.get("category") or "").lower()

    # Normalize target (method + path)
    vm = h.get("verification_method") or {}
    method = str(vm.get("method") or "").upper()
    path = str(vm.get("path") or "").lower()
    path = re.sub(r"/\d+", "/{id}", path)  # Normalize IDs
    path = re.sub(r"/[0-9a-f]{8,}", "/{uuid}", path)  # Normalize UUIDs

    # Combine into signature
    parts = [risk, method, path]
    if title:
        # Use first 50 chars of normalized title
        parts.append(title[:50])

    sig = "|".join(parts)
    return hashlib.sha256(sig.encode()).hexdigest()[:24]


def _compute_priority_score(
    h: dict[str, Any],
    history: dict[str, Any] | None,
    engine_weights: dict[str, float] | None,
) -> float:
    """Compute priority score for a hypothesis.

    Score = risk_weight * 0.30
          + source_reliability * 0.20
          + severity_weight * 0.20
          + engine_weight * 0.15
          + history_bonus * 0.15
    """
    # Risk weight
    risk_type = str(h.get("risk_type") or h.get("category") or "").lower()
    risk_weight = RISK_PRIORITY.get(risk_type, 0.50)

    # Source reliability
    source = str(h.get("source") or h.get("evidence_source") or "").lower()
    source_weight = SOURCE_RELIABILITY.get(source, 0.50)

    # Severity weight
    severity = str(h.get("severity") or "").upper()
    severity_weight = SEVERITY_WEIGHT.get(severity, 0.50)

    # Engine weight (from learning or default)
    engine = str(h.get("engine") or h.get("source_engine") or "").lower()
    if engine_weights and engine in engine_weights:
        engine_weight = engine_weights[engine]
    else:
        engine_weight = 0.60  # Default neutral weight

    # History bonus (based on historical hit rate for this risk type)
    history_bonus = 0.50  # Default neutral
    if history:
        risk_history = history.get("by_risk_type", {}).get(risk_type, {})
        hit_rate = float(risk_history.get("hit_rate", 0.0) or 0.0)
        history_bonus = min(1.0, hit_rate * 2.0)  # Scale hit rate to 0-1

        # Engine-specific history
        engine_history = history.get("by_engine", {}).get(engine, {})
        engine_hit_rate = float(engine_history.get("hit_rate", 0.0) or 0.0)
        if engine_hit_rate > 0:
            engine_weight = min(1.0, engine_hit_rate * 1.5)

    # Confidence from hypothesis itself
    confidence = float(h.get("confidence") or 0.5)

    # Compute weighted score
    score = (
        risk_weight * 0.28
        + source_weight * 0.18
        + severity_weight * 0.18
        + engine_weight * 0.12
        + history_bonus * 0.12
        + confidence * 0.12
    )

    return round(score, 4)


def _apply_diversity_constraint(
    sorted_hypotheses: list[dict[str, Any]],
    *,
    max_count: int,
    max_per_risk_type: int = 0,
) -> list[dict[str, Any]]:
    """Apply diversity constraint to avoid over-representing one risk type.

    If max_per_risk_type is 0, auto-calculate as max_count / num_risk_types * 1.5
    """
    if not sorted_hypotheses:
        return []

    # Auto-calculate max per risk type
    if max_per_risk_type <= 0:
        unique_risks = len({
            str(h.get("risk_type") or h.get("category") or "unknown").lower()
            for h in sorted_hypotheses
        })
        max_per_risk_type = max(5, int(max_count / max(unique_risks, 1) * 1.5))

    selected: list[dict[str, Any]] = []
    risk_counts: dict[str, int] = {}

    for h in sorted_hypotheses:
        if len(selected) >= max_count:
            break

        risk = str(h.get("risk_type") or h.get("category") or "unknown").lower()
        count = risk_counts.get(risk, 0)

        if count < max_per_risk_type:
            selected.append(h)
            risk_counts[risk] = count + 1

    return selected


def compute_engine_weights_from_history(
    history: dict[str, Any],
    *,
    default_weight: float = 0.60,
    min_weight: float = 0.20,
    max_weight: float = 1.00,
) -> dict[str, float]:
    """Compute dynamic engine weights from historical performance.

    Engines with higher hit rates get higher weights.
    """
    weights: dict[str, float] = {}
    by_engine = history.get("by_engine", {})

    for engine, stats in by_engine.items():
        if not isinstance(stats, dict):
            continue
        hit_rate = float(stats.get("hit_rate", 0.0) or 0.0)
        total = int(stats.get("total", 0) or 0)

        if total < 3:
            # Not enough data, use default
            weights[engine] = default_weight
        else:
            # Scale hit rate to weight (0.2 - 1.0)
            weight = min_weight + (max_weight - min_weight) * min(1.0, hit_rate * 2.0)
            weights[engine] = round(weight, 3)

    return weights


def merge_hypothesis_sources(
    *sources: list[dict[str, Any]],
    max_total: int = 100,
) -> list[dict[str, Any]]:
    """Merge hypotheses from multiple sources with deduplication.

    Sources are processed in order, with earlier sources having priority
    for duplicate resolution.
    """
    merged: list[dict[str, Any]] = []
    seen_signatures: set[str] = set()

    for source in sources:
        for h in source:
            if not isinstance(h, dict):
                continue
            sig = _hypothesis_signature(h)
            if sig not in seen_signatures:
                seen_signatures.add(sig)
                merged.append(h)

    # Prioritize merged list
    return prioritize_and_deduplicate(merged, max_count=max_total)
