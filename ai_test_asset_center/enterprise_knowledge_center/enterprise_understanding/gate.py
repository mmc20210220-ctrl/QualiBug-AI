"""Fail-closed completeness gate for the enterprise understanding model."""
from __future__ import annotations

from typing import Any

from .schema import BEHAVIOR_GATE_SCHEMA, GATE_SCHEMA, as_dict, as_list, text, validate_model_shape


def _formal_entries(model: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in (
        "business_objects",
        "actors",
        "operations",
        "object_relations",
        "lifecycles",
        "processes",
        "business_behaviors",
    ):
        rows.extend(row for row in as_list(model.get(key)) if isinstance(row, dict))
    return rows


def _unresolved_conflicts(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in as_list(model.get("conflicts"))
        if isinstance(row, dict)
        and text(row.get("status") or "UNRESOLVED").upper()
        not in {"RESOLVED", "SUPERSEDED", "DISMISSED"}
    ]


def _validation_view(model: dict[str, Any]) -> dict[str, Any]:
    """Supply additive v1 behavior fields for older hand-built model fixtures."""
    result = dict(model or {})
    result.setdefault("decision_matrix_row_ledger", [])
    result.setdefault("business_behaviors", [])
    result.setdefault("behavior_conflicts", [])
    result.setdefault(
        "behavior_ir_gate",
        {
            "schema": BEHAVIOR_GATE_SCHEMA,
            "status": "NOT_BUILT",
            "entry_allowed": False,
            "metrics": {},
        },
    )
    return result


def assess_understanding_model(
    model: dict[str, Any],
    *,
    upstream_gate: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assess structural and semantic completeness without claiming recall."""
    upstream = as_dict(upstream_gate)
    structural_violations = validate_model_shape(_validation_view(model))
    unknowns = [row for row in as_list(model.get("unknowns")) if isinstance(row, dict)]
    critical_unknowns = [row for row in unknowns if bool(row.get("blocks_formal_understanding"))]
    conflicts = _unresolved_conflicts(model)
    formal_entries = _formal_entries(model)
    traceable_entries = [row for row in formal_entries if as_list(row.get("evidence"))]
    operations = [row for row in as_list(model.get("operations")) if isinstance(row, dict)]
    bound_operations = [row for row in operations if as_list(row.get("object_refs"))]
    actors = [row for row in as_list(model.get("actors")) if isinstance(row, dict)]
    authorization_contracts = [
        contract
        for actor in actors
        for contract in as_list(actor.get("authorization_contracts"))
        if isinstance(contract, dict)
    ]
    authorization_unknowns = [
        row
        for row in as_list(model.get("authorization_unknowns"))
        if isinstance(row, dict)
    ]
    authorization_allow = [
        row for row in authorization_contracts if text(row.get("decision")) == "ALLOW"
    ]
    authorization_deny = [
        row for row in authorization_contracts if text(row.get("decision")) == "DENY"
    ]
    authorization_unknown_contracts = [
        row for row in authorization_contracts if text(row.get("decision")) == "UNKNOWN"
    ]
    declared_authorization_actors = [
        row
        for row in actors
        if text(row.get("authorization_status")) in {"RESOLVED", "UNRESOLVED"}
    ]
    resolved_authorization_actors = [
        row for row in actors if text(row.get("authorization_status")) == "RESOLVED"
    ]
    responsibility_only_actors = [
        row
        for row in actors
        if as_list(row.get("responsibility_operation_refs"))
        and text(row.get("authorization_status")) == "NOT_DECLARED"
    ]
    unspecified_scope_contracts = [
        row
        for row in authorization_contracts
        if text(row.get("scope")).lower() in {"", "unspecified"}
        or (isinstance(row.get("scope"), dict) and not row.get("scope"))
    ]
    lifecycles = [row for row in as_list(model.get("lifecycles")) if isinstance(row, dict)]
    lifecycle_transitions = [
        transition
        for lifecycle in lifecycles
        for transition in as_list(lifecycle.get("transitions"))
        if isinstance(transition, dict)
    ]
    complete_transitions = [
        row for row in lifecycle_transitions if text(row.get("completeness")) == "COMPLETE"
    ]
    behaviors = [row for row in as_list(model.get("business_behaviors")) if isinstance(row, dict)]
    confirmed_behaviors = [row for row in behaviors if text(row.get("status")) == "CONFIRMED"]
    candidate_behaviors = [row for row in behaviors if text(row.get("status")) == "CANDIDATE"]
    incomplete_behaviors = [row for row in behaviors if text(row.get("status")) == "INCOMPLETE"]
    conflicted_behaviors = [row for row in behaviors if text(row.get("status")) == "CONFLICTED"]

    def ratio(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 1.0

    metrics = {
        "business_object_count": len(as_list(model.get("business_objects"))),
        "actor_count": len(actors),
        "operation_count": len(operations),
        "object_relation_count": len(as_list(model.get("object_relations"))),
        "lifecycle_count": len(lifecycles),
        "process_count": len(as_list(model.get("processes"))),
        "decision_matrix_row_count": len(as_list(model.get("decision_matrix_row_ledger"))),
        "business_behavior_count": len(behaviors),
        "confirmed_behavior_count": len(confirmed_behaviors),
        "candidate_behavior_count": len(candidate_behaviors),
        "incomplete_behavior_count": len(incomplete_behaviors),
        "conflicted_behavior_count": len(conflicted_behaviors),
        "behavior_conflict_count": len(as_list(model.get("behavior_conflicts"))),
        "authorization_contract_count": len(authorization_contracts),
        "authorization_allow_count": len(authorization_allow),
        "authorization_deny_count": len(authorization_deny),
        "authorization_unknown_contract_count": len(authorization_unknown_contracts),
        "authorization_unknown_count": len(authorization_unknowns),
        "authorization_declared_actor_count": len(declared_authorization_actors),
        "authorization_resolved_actor_count": len(resolved_authorization_actors),
        "responsibility_only_actor_count": len(responsibility_only_actors),
        "authorization_unspecified_scope_count": len(unspecified_scope_contracts),
        "authorization_resolution_rate": ratio(
            len(authorization_allow) + len(authorization_deny),
            len(authorization_contracts),
        ),
        "actor_authorization_resolution_rate": ratio(
            len(resolved_authorization_actors),
            len(declared_authorization_actors),
        ),
        "unknown_count": len(unknowns),
        "critical_unknown_count": len(critical_unknowns),
        "unresolved_conflict_count": len(conflicts),
        "structural_violation_count": len(structural_violations),
        "source_traceability_rate": ratio(len(traceable_entries), len(formal_entries)),
        "operation_object_binding_rate": ratio(len(bound_operations), len(operations)),
        "lifecycle_transition_completeness": ratio(
            len(complete_transitions), len(lifecycle_transitions)
        ),
        "confirmed_behavior_rate": ratio(len(confirmed_behaviors), len(behaviors)),
        "behavior_source_traceability_rate": ratio(
            len([row for row in behaviors if as_list(row.get("evidence"))]), len(behaviors)
        ),
    }
    projection_inputs = (
        metrics["source_traceability_rate"],
        metrics["operation_object_binding_rate"],
        metrics["lifecycle_transition_completeness"],
        metrics["behavior_source_traceability_rate"],
        metrics["authorization_resolution_rate"],
        1.0 if not critical_unknowns else 0.0,
        1.0 if not conflicts else 0.0,
        1.0 if not structural_violations else 0.0,
    )
    metrics["model_completeness_projection"] = round(
        sum(projection_inputs) / len(projection_inputs), 4
    )
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
        blocking_reasons = ["UNRESOLVED_BUSINESS_FACT_OR_BEHAVIOR_CONFLICTS"]
    elif critical_unknowns:
        status = "BLOCKED_ENTERPRISE_UNDERSTANDING_CRITICAL_UNKNOWN"
        blocking_reasons = sorted(
            {text(row.get("reason_code") or row.get("kind")) for row in critical_unknowns}
        )
    elif unknowns:
        status = "PARTIAL_ENTERPRISE_UNDERSTANDING"
        blocking_reasons = sorted(
            {text(row.get("reason_code") or row.get("kind")) for row in unknowns}
        )
    else:
        status = "PASS"
        blocking_reasons = []

    entry_allowed = status == "PASS"
    if not authorization_contracts:
        authorization_status = "NOT_DECLARED"
    elif authorization_unknown_contracts or authorization_unknowns:
        authorization_status = "PARTIAL_AUTHORIZATION_UNRESOLVED"
    else:
        authorization_status = "PASS"
    authorization_entry_allowed = authorization_status in {"PASS", "NOT_DECLARED"}

    return {
        "schema": GATE_SCHEMA,
        "status": status,
        "entry_allowed": entry_allowed,
        "quality_claim": "MODEL_COMPLETENESS_PROJECTION_NOT_RECALL",
        "language_contract": "CHINESE_SOURCE_TEXT_IS_FACT_AUTHORITY",
        "metrics": metrics,
        "authorization_gate": {
            "schema": "qualibug.enterprise-authorization-gate.v1",
            "status": authorization_status,
            "entry_allowed": authorization_entry_allowed,
            "unknown_never_authorizes": True,
            "unknown_never_denies": True,
            "responsibility_is_permission": False,
            "unresolved": authorization_unknowns,
            "required_operator_action": (
                "resolve source-backed role/resource/action/decision coordinates before compiling authorization obligations"
                if not authorization_entry_allowed
                else ""
            ),
        },
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
