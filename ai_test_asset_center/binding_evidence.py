"""Binding Evidence System — multi-source evidence collection and confidence scoring.

Each binding edge accumulates evidence from 8 dimensions. The composite
confidence score determines whether a binding can proceed to execution
or requires runtime probing.

Schema: qualibug.binding-evidence.v1

Evidence dimensions:
1. semantic_name      — Name similarity between IR node and runtime target
2. entity_context     — Entity membership and collection path alignment
3. data_type          — Type compatibility (string/int/enum match)
4. operation_context  — Operation method/path/params alignment
5. schema_relation    — Declared relations in Behavior IR support this binding
6. source_consistency — Multiple source documents agree
7. runtime_behavior   — Runtime probe confirmed the binding
8. correlation_consistency — Cross-entity correlation keys match
"""
from __future__ import annotations

import hashlib
import time
from typing import Any


SCHEMA_VERSION = "qualibug.binding-evidence.v1"

# ─── Evidence Dimensions ──────────────────────────────────────────────────────

EVIDENCE_DIMENSIONS = frozenset({
    "semantic_name",
    "entity_context",
    "data_type",
    "operation_context",
    "schema_relation",
    "source_consistency",
    "runtime_behavior",
    "correlation_consistency",
})

# Weight per dimension (sums to 1.0)
DIMENSION_WEIGHTS: dict[str, float] = {
    "semantic_name": 0.12,
    "entity_context": 0.15,
    "data_type": 0.10,
    "operation_context": 0.15,
    "schema_relation": 0.15,
    "source_consistency": 0.13,
    "runtime_behavior": 0.12,
    "correlation_consistency": 0.08,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def create_evidence(
    *,
    dimension: str,
    score: float,
    detail: str = "",
    source_ref: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a single evidence entry.

    Args:
        dimension: One of EVIDENCE_DIMENSIONS
        score: 0.0 to 1.0 confidence for this dimension
        detail: Human-readable explanation
        source_ref: Reference to the source that produced this evidence
        metadata: Additional structured data

    Returns:
        Evidence entry dict.
    """
    if dimension not in EVIDENCE_DIMENSIONS:
        raise ValueError(f"invalid_evidence_dimension:{dimension}")
    score = max(0.0, min(1.0, float(score)))

    return {
        "dimension": dimension,
        "score": score,
        "detail": detail,
        "source_ref": source_ref,
        "metadata": dict(metadata or {}),
        "timestamp": time.time(),
        "evidence_id": hashlib.sha256(
            f"{dimension}|{score}|{detail}|{time.time()}".encode()
        ).hexdigest()[:12],
    }


def compute_composite_confidence(evidence_list: list[dict[str, Any]]) -> float:
    """Compute weighted composite confidence from evidence entries.

    Uses the BEST score per dimension (latest evidence wins if multiple
    entries exist for the same dimension). Weighted average produces the
    final score.

    Returns:
        Float 0.0 to 1.0.
    """
    if not evidence_list:
        return 0.0

    # Best score per dimension
    best_per_dim: dict[str, float] = {}
    for entry in evidence_list:
        if not isinstance(entry, dict):
            continue
        dim = _text(entry.get("dimension"))
        if dim not in EVIDENCE_DIMENSIONS:
            continue
        score = float(entry.get("score", 0.0))
        if dim not in best_per_dim or score > best_per_dim[dim]:
            best_per_dim[dim] = score

    if not best_per_dim:
        return 0.0

    # Weighted average (only count dimensions that have evidence)
    total_weight = 0.0
    weighted_sum = 0.0
    for dim, score in best_per_dim.items():
        weight = DIMENSION_WEIGHTS.get(dim, 0.0)
        weighted_sum += score * weight
        total_weight += weight

    if total_weight == 0.0:
        return 0.0

    # Normalize by covered weight (partial evidence doesn't penalize)
    raw = weighted_sum / total_weight

    # Coverage penalty: if fewer than 3 dimensions have evidence, apply penalty
    coverage = len(best_per_dim) / len(EVIDENCE_DIMENSIONS)
    if coverage < 0.375:  # Less than 3/8 dimensions
        raw *= (0.5 + coverage)  # Scale down

    return round(max(0.0, min(1.0, raw)), 4)


def evaluate_binding_evidence(
    evidence_list: list[dict[str, Any]],
) -> dict[str, Any]:
    """Full evidence evaluation with gate classification.

    Returns:
        {
            "composite_confidence": float,
            "gate": "high_confidence" | "needs_probe" | "unusable",
            "dimensions_covered": int,
            "dimensions_missing": list[str],
            "strongest_dimension": str,
            "weakest_dimension": str,
            "recommendation": str,
        }
    """
    from .binding_ledger import confidence_gate

    composite = compute_composite_confidence(evidence_list)
    gate = confidence_gate(composite)

    # Dimension analysis
    covered_dims: set[str] = set()
    best_per_dim: dict[str, float] = {}
    for entry in evidence_list:
        if not isinstance(entry, dict):
            continue
        dim = _text(entry.get("dimension"))
        if dim in EVIDENCE_DIMENSIONS:
            covered_dims.add(dim)
            score = float(entry.get("score", 0.0))
            if dim not in best_per_dim or score > best_per_dim[dim]:
                best_per_dim[dim] = score

    missing = sorted(EVIDENCE_DIMENSIONS - covered_dims)
    strongest = max(best_per_dim, key=best_per_dim.get) if best_per_dim else ""
    weakest = min(best_per_dim, key=best_per_dim.get) if best_per_dim else ""

    # Recommendation
    if gate == "high_confidence":
        recommendation = "Promote to HIGH_CONFIDENCE or EXECUTABLE"
    elif gate == "needs_probe":
        recommendation = f"Schedule runtime probe to confirm. Weakest: {weakest}"
    else:
        recommendation = f"Insufficient evidence. Gather more from: {', '.join(missing[:3])}"

    return {
        "composite_confidence": composite,
        "gate": gate,
        "dimensions_covered": len(covered_dims),
        "dimensions_missing": missing,
        "strongest_dimension": strongest,
        "weakest_dimension": weakest,
        "recommendation": recommendation,
    }


# ─── Evidence Collectors (per dimension) ──────────────────────────────────────

def collect_semantic_name_evidence(
    *,
    ir_node_name: str,
    runtime_target_name: str,
) -> dict[str, Any]:
    """Score name similarity between IR node and runtime target."""
    ir_norm = _normalize_name(ir_node_name)
    rt_norm = _normalize_name(runtime_target_name)

    if not ir_norm or not rt_norm:
        score = 0.0
        detail = "empty_name"
    elif ir_norm == rt_norm:
        score = 1.0
        detail = "exact_match"
    elif ir_norm in rt_norm or rt_norm in ir_norm:
        score = 0.8
        detail = "substring_match"
    else:
        # Token overlap
        ir_tokens = set(ir_norm.split("_"))
        rt_tokens = set(rt_norm.split("_"))
        overlap = len(ir_tokens & rt_tokens)
        total = max(len(ir_tokens), len(rt_tokens), 1)
        score = overlap / total
        detail = f"token_overlap:{overlap}/{total}"

    return create_evidence(
        dimension="semantic_name",
        score=score,
        detail=detail,
        metadata={"ir_name": ir_node_name, "runtime_name": runtime_target_name},
    )


def collect_entity_context_evidence(
    *,
    entity_collection_path: str,
    operation_path: str,
) -> dict[str, Any]:
    """Score entity context alignment."""
    entity_norm = _normalize_path(entity_collection_path)
    op_norm = _normalize_path(operation_path)

    if not entity_norm or not op_norm:
        score = 0.0
        detail = "empty_path"
    elif entity_norm == op_norm:
        score = 1.0
        detail = "exact_collection_match"
    elif op_norm.startswith(entity_norm):
        score = 0.85
        detail = "operation_under_entity_collection"
    elif entity_norm in op_norm:
        score = 0.6
        detail = "entity_path_substring"
    else:
        score = 0.2
        detail = "no_path_alignment"

    return create_evidence(
        dimension="entity_context",
        score=score,
        detail=detail,
        metadata={"entity_path": entity_collection_path, "operation_path": operation_path},
    )


def collect_data_type_evidence(
    *,
    expected_type: str,
    actual_type: str,
) -> dict[str, Any]:
    """Score data type compatibility."""
    exp = _text(expected_type).lower()
    act = _text(actual_type).lower()

    if not exp or not act:
        score = 0.3
        detail = "type_unknown"
    elif exp == act:
        score = 1.0
        detail = "exact_type_match"
    elif exp in ("string", "integer", "number") and act in ("string", "integer", "number"):
        score = 0.7
        detail = "compatible_numeric_string"
    else:
        score = 0.2
        detail = f"type_mismatch:{exp}_vs_{act}"

    return create_evidence(
        dimension="data_type",
        score=score,
        detail=detail,
        metadata={"expected": expected_type, "actual": actual_type},
    )


def collect_operation_context_evidence(
    *,
    operation_method: str,
    operation_path: str,
    binding_target_path: str,
) -> dict[str, Any]:
    """Score operation context alignment for binding."""
    op_path = _normalize_path(operation_path)
    target_path = _normalize_path(binding_target_path)

    if not op_path or not target_path:
        score = 0.0
        detail = "empty_operation_path"
    elif op_path == target_path:
        score = 1.0
        detail = "exact_path_match"
    elif target_path.startswith(op_path.rsplit("/", 1)[0]):
        score = 0.75
        detail = "same_resource_collection"
    else:
        score = 0.3
        detail = "different_resource"

    return create_evidence(
        dimension="operation_context",
        score=score,
        detail=detail,
        metadata={
            "method": operation_method,
            "operation_path": operation_path,
            "target_path": binding_target_path,
        },
    )


def collect_schema_relation_evidence(
    *,
    relation_type: str,
    from_ref: str,
    to_ref: str,
    binding_source_id: str,
    binding_target_key: str,
) -> dict[str, Any]:
    """Score how well a Behavior IR relation supports this binding."""
    if not relation_type or not from_ref or not to_ref:
        score = 0.0
        detail = "incomplete_relation"
    elif binding_source_id in (from_ref, to_ref) and binding_target_key in (from_ref, to_ref):
        score = 1.0
        detail = f"direct_relation:{relation_type}"
    elif binding_source_id in (from_ref, to_ref):
        score = 0.7
        detail = f"source_in_relation:{relation_type}"
    else:
        score = 0.2
        detail = "indirect_relation"

    return create_evidence(
        dimension="schema_relation",
        score=score,
        detail=detail,
        metadata={
            "relation_type": relation_type,
            "from_ref": from_ref,
            "to_ref": to_ref,
        },
    )


def collect_runtime_behavior_evidence(
    *,
    probe_type: str,
    probe_result: str,
    probe_detail: str = "",
) -> dict[str, Any]:
    """Score runtime probe result."""
    result_scores = {
        "CONFIRMED": 1.0,
        "REJECTED": 0.0,
        "INCONCLUSIVE": 0.4,
        "BLOCKED_BY_ENVIRONMENT": 0.3,
    }
    score = result_scores.get(probe_result, 0.2)

    return create_evidence(
        dimension="runtime_behavior",
        score=score,
        detail=f"{probe_type}:{probe_result}:{probe_detail}",
        metadata={"probe_type": probe_type, "probe_result": probe_result},
    )


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _normalize_name(name: str) -> str:
    """Normalize a name for comparison: lowercase, strip non-alphanumeric."""
    import re
    return re.sub(r"[^a-z0-9]+", "_", _text(name).lower()).strip("_")


def _normalize_path(path: str) -> str:
    """Normalize a URL path for comparison."""
    import re
    normalized = re.sub(r"\{[^}]+\}", "", _text(path))
    return normalized.rstrip("/").lower()
