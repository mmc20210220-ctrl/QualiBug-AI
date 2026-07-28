"""Fail-closed completeness gate for the enterprise understanding model."""
from __future__ import annotations

from typing import Any

from .schema import GATE_SCHEMA, as_dict, as_list, text, validate_model_shape


def _formal_entries(model: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in (
        "business_objects",
        "actors",
        "operations",
        "object_relations",
        "lifecycles",
        "processes",
    ):
        rows.extend(row for row in as_list(model.get(key)) if isinstance(row, dict))
    return rows


def _unresolved_conflicts(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in as_list(model.get("conflicts"))
        if isinstance(row, dict)
        and text(row.get("status") or "UNRESOLVED").upper() not in {"RESOLVED", "SUPERSEDED", "DISMISSED"}
    ]


def assess_understanding_model(
    model: dict[str, Any],
    *,
    upstream_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess structural and semantic completeness without claiming recall."""
    upstream = as_dict(upstream_gate)
    structural_violations = validate_model_shape(model)
    unknowns = [row for row in as_list(model.get("unknowns")) if isinstance(row, dict)]
    critical_unknowns = [row for row in unknowns if bool(row.get("blocks_formal_understanding"))]
    conflicts = _unresolved_conflicts(model)
    formal_entries = _formal_entries(model)
    traceable_entries = [row for row in formal_entries if as_list(row.get("evidence"))]
    operations = [row for row in as_list(model.get("operations")) if isinstance(row, dict)]
    bound_operations = [row for row in operations if as_list(row.get("object_refs"))]
    lifecycles = [row for row in as_list(model.get("lifecycles")) if isinstance(row, dict)]
    lifecycle_transitions = [
        transition
        for lifecycle in lifecycles
        for transition in as_list(lifecycle.get("transitions"))
        if isinstance(transition, dict)
    ]
    complete_transitions = [
        row
        for row in lifecycle_transitions
        if text(row.get("completeness")) == "COMPLETE"
    ]

    def ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 1.0

    metrics = {
        "business_object_count": len(as_list(model.get("business_objects"))),
        "actor_count": len(as_list(model.get("actors"))),
        "operation_count": len(operations),
        "object_relation_count": len(as_list(model.get("object_relations"))),
        "lifecycle_count": len(lifecycles),
        "process_count": len(as_list(model.get("processes"))),
        "unknown_count": len(unknowns),
        "critical_unknown_count": len(critical_unknowns),
        "unresolved_conflict_count": len(conflicts),
        "structural_violation_count": len(structural_violations),
        "source_traceability_rate": ratio(len(traceable_entries), len(formal_entries)),
        "operation_object_binding_rate": ratio(len(bound_operations), len(operations)),
        "lifecycle_transition_completeness": ratio(len(complete_transitions), len(lifecycle_transitions)),
    }
    projection_inputs = (
        metrics["source_traceability_rate"],
        metrics["operation_object_binding_rate"],
        metrics["lifecycle_transition_completeness"],
        1.0 if not critical_unknowns else 0.0,
        1.0 if not conflicts else 0.0,
        1.0 if not structural_violations else 0.0,
    )
    metrics["model_completeness_projection"] = round(sum(projection_inputs) / len(projection_inputs), 4)
    metrics["projection_contract"] = "INTERNAL_MODEL_CLOSURE_NOT_RECALL_OR_ACCURACY"

    upstream_ready = bool(upstream.get("entry_allowed", True))
    if not upstream_ready:
        status = "BLOCKED_UPSTREAM_BUSINESS_COMPREHENSION_GATE"
        blocking_reasons = [text(upstream.get("status")) or "UPSTREAM_NOT_READY"]
    elif structural_violations:
        status = "BLOCKED_ENTERPRISE_UNDERSTANDING_MODEL_INVALID"
        blocking_reasons = ["MODEL_SCHEMA_OR_EVIDENCE_INVALID"]
    elif conflicts:
        status = "BLOCKED_ENTERPRISE_UNDERSTANDING_CONFLICTING_FACTS"
        blocking_reasons = ["UNRESOLVED_BUSINESS_FACT_CONFLICTS"]
    elif critical_unknowns:
        status = "BLOCKED_ENTERPRISE_UNDERSTANDING_CRITICAL_UNKNOWN"
        blocking_reasons = sorted({text(row.get("reason_code") or row.get("kind")) for row in critical_unknowns})
    elif unknowns:
        status = "PARTIAL_ENTERPRISE_UNDERSTANDING"
        blocking_reasons = sorted({text(row.get("reason_code") or row.get("kind")) for row in unknowns})
    else:
        status = "PASS"
        blocking_reasons = []

    entry_allowed = status == "PASS"
    return {
        "schema": GATE_SCHEMA,
        "status": status,
        "entry_allowed": entry_allowed,
        "quality_claim": "MODEL_COMPLETENESS_PROJECTION_NOT_RECALL",
        "language_contract": "CHINESE_SOURCE_TEXT_IS_FACT_AUTHORITY",
        "metrics": metrics,
        "blocking_reasons": blocking_reasons,
        "critical_unknowns": critical_unknowns,
        "unresolved_conflicts": conflicts,
        "structural_violations": structural_violations,
        "required_operator_action": (
            "resolve every listed unknown/conflict or supply source evidence before treating enterprise understanding as complete"
            if not entry_allowed
            else ""
        ),
    }


__all__ = ["assess_understanding_model"]
