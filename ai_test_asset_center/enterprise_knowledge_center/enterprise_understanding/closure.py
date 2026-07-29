"""Minimum closure checks for enterprise business understanding.

A parsed schema or a list of entities is not equivalent to understanding an
enterprise. This stage prevents empty, field-only, behaviorless, or structurally
incomplete source models from reporting PASS.
"""
from __future__ import annotations

from typing import Any

from .behavior_ir_logic_gate import build_business_behavior_ir_v1
from .gate import assess_understanding_model
from .implementation_binding_governance import (
    build_governed_behavior_implementation_bindings,
)
from .schema import as_dict, as_list, dedupe_evidence, new_unknown, text


def _project_implementation_binding_asset(
    asset: dict[str, Any],
    *,
    bindings: list[dict[str, Any]],
    unknowns: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    gate: dict[str, Any],
) -> None:
    """Expose the downstream implementation gate without changing semantic truth."""
    asset["behavior_implementation_bindings"] = bindings
    asset["implementation_binding_unknowns"] = unknowns
    asset["implementation_binding_conflicts"] = conflicts
    asset["implementation_binding_gate"] = gate

    metrics = as_dict(gate.get("metrics"))
    summary = as_dict(asset.get("summary"))
    summary.update(
        {
            "implementation_binding_status": gate.get("status"),
            "implementation_binding_ready": bool(gate.get("entry_allowed")),
            "scenario_planning_allowed": bool(gate.get("scenario_planning_allowed")),
            "implementation_execution_allowed": bool(gate.get("execution_allowed")),
            "behavior_implementation_binding_count": len(bindings),
            "scenario_ready_binding_count": int(
                metrics.get("scenario_ready_binding_count") or 0
            ),
            "implementation_binding_unknown_count": len(unknowns),
            "implementation_binding_conflict_count": len(conflicts),
        }
    )
    asset["summary"] = summary

    prior_gaps = [
        dict(row)
        for row in as_list(asset.get("coverage_gaps"))
        if isinstance(row, dict)
        and text(row.get("kind"))
        not in {
            "IMPLEMENTATION_BINDING_PARTIAL",
            "BLOCKED_IMPLEMENTATION_BINDING_CONFLICT",
        }
    ]
    status = text(gate.get("status"))
    if status != "PASS":
        blocked = status.startswith("BLOCKED")
        prior_gaps.append(
            {
                "kind": (
                    "BLOCKED_IMPLEMENTATION_BINDING_CONFLICT"
                    if blocked
                    else "IMPLEMENTATION_BINDING_PARTIAL"
                ),
                "gap_type": "business_behavior_not_bound_to_observable_system_surface",
                "source_id": "*",
                "implementation_binding_status": status,
                "scenario_planning_allowed": bool(
                    gate.get("scenario_planning_allowed")
                ),
                "execution_allowed": bool(gate.get("execution_allowed")),
                "unknown_count": len(unknowns),
                "conflict_count": len(conflicts),
                "operator_action": gate.get("required_operator_action"),
                "semantic_understanding_is_not_changed": True,
            }
        )
    asset["coverage_gaps"] = prior_gaps

    governance = as_dict(asset.get("governance"))
    governance.update(
        {
            "business_behavior_implementation_binding_enabled": True,
            "semantic_and_implementation_gates_are_separate": True,
            "token_overlap_cannot_authorize_endpoint_binding": True,
            "scenario_planning_requires_confirmed_behavior_and_observers": True,
            "implementation_binding_does_not_create_executable_tests": True,
            "implementation_execution_requires_later_assertion_compilation": True,
        }
    )
    asset["governance"] = governance


def apply_minimum_understanding_closure(
    model: dict[str, Any],
    asset: dict[str, Any],
) -> dict[str, Any]:
    ledger = as_dict(asset.get("business_fact_ledger"))
    facts = [row for row in as_list(ledger.get("items")) if isinstance(row, dict)]
    accepted_behavior_facts = [
        row
        for row in facts
        if text(row.get("status")) == "ACCEPTED"
        and text(row.get("kind")) in {"RULE", "STATE_TRANSITION"}
    ]
    pending_facts = [row for row in facts if text(row.get("status")) == "PENDING"]
    active_sources = [
        row
        for row in as_list(asset.get("source_inventory"))
        if isinstance(row, dict) and text(row.get("status") or "active") == "active"
    ]

    row_ledger, behaviors, behavior_conflicts, behavior_unknowns, behavior_gate = (
        build_business_behavior_ir_v1(
            asset,
            facts,
            [row for row in as_list(model.get("operations")) if isinstance(row, dict)],
        )
    )
    model["decision_matrix_row_ledger"] = row_ledger
    model["business_behaviors"] = behaviors
    model["behavior_conflicts"] = behavior_conflicts
    model["behavior_ir_gate"] = behavior_gate

    (
        implementation_bindings,
        implementation_unknowns,
        implementation_conflicts,
        implementation_gate,
    ) = build_governed_behavior_implementation_bindings(asset, behaviors)
    model["behavior_implementation_bindings"] = implementation_bindings
    model["implementation_binding_unknowns"] = implementation_unknowns
    model["implementation_binding_conflicts"] = implementation_conflicts
    model["implementation_binding_gate"] = implementation_gate
    model["implementation_evidence_index"] = dedupe_evidence(
        [
            evidence
            for binding in implementation_bindings
            for evidence in as_list(binding.get("evidence"))
            if isinstance(evidence, dict)
        ]
    )
    _project_implementation_binding_asset(
        asset,
        bindings=implementation_bindings,
        unknowns=implementation_unknowns,
        conflicts=implementation_conflicts,
        gate=implementation_gate,
    )

    # Semantic understanding and implementation binding are separate gates. Missing
    # endpoints or observers block scenario planning, not what the source materials say.
    unknowns = [
        row
        for row in [*as_list(model.get("unknowns")), *behavior_unknowns]
        if isinstance(row, dict)
    ]
    conflicts = [
        row
        for row in [*as_list(model.get("conflicts")), *behavior_conflicts]
        if isinstance(row, dict)
    ]

    if active_sources and not accepted_behavior_facts and not row_ledger:
        unknowns.append(
            new_unknown(
                "NO_BUSINESS_BEHAVIOR_UNDERSTOOD",
                "已接入企业资料，但尚未形成任何可追溯的业务规则、状态行为事实或决策矩阵行。字段、表名和对象清单不能替代业务理解。",
                severity="P0",
                blocks_formal_understanding=True,
                reason_code="NO_BUSINESS_BEHAVIOR_UNDERSTOOD",
                details={
                    "active_source_count": len(active_sources),
                    "fact_count": len(facts),
                    "pending_fact_count": len(pending_facts),
                    "decision_matrix_row_count": len(row_ledger),
                },
            )
        )

    facts_with_actions = [
        row
        for row in accepted_behavior_facts
        if text(
            as_dict(row.get("action")).get("canonical")
            or as_dict(row.get("action")).get("raw")
        )
    ]
    if facts_with_actions and not as_list(model.get("operations")):
        unknowns.append(
            new_unknown(
                "NO_BUSINESS_OPERATION_UNDERSTOOD",
                "资料中存在明确业务动作，但尚未形成任何对象绑定的正式业务操作。",
                severity="P0",
                blocks_formal_understanding=True,
                reason_code="NO_BUSINESS_OPERATION_UNDERSTOOD",
                details={"action_fact_ids": [row.get("fact_id") for row in facts_with_actions]},
            )
        )

    behavior_evidence = dedupe_evidence(
        [
            evidence
            for behavior in behaviors
            for evidence in as_list(behavior.get("evidence"))
            if isinstance(evidence, dict)
        ]
    )
    model["evidence_index"] = dedupe_evidence(
        [*as_list(model.get("evidence_index")), *behavior_evidence]
    )
    if active_sources and not as_list(model.get("evidence_index")):
        unknowns.append(
            new_unknown(
                "UNDERSTANDING_WITHOUT_SOURCE_EVIDENCE",
                "企业认知模型没有形成任何可追溯来源证据，不能视为理解完成。",
                severity="P0",
                blocks_formal_understanding=True,
                reason_code="UNDERSTANDING_WITHOUT_SOURCE_EVIDENCE",
            )
        )

    model["unknowns"] = list(
        {
            text(row.get("unknown_id")): row
            for row in unknowns
            if isinstance(row, dict) and text(row.get("unknown_id"))
        }.values()
    )
    model["conflicts"] = list(
        {
            text(row.get("conflict_id")): row
            for row in conflicts
            if isinstance(row, dict) and text(row.get("conflict_id"))
        }.values()
    )
    behavior_metrics = as_dict(behavior_gate.get("metrics"))
    implementation_metrics = as_dict(implementation_gate.get("metrics"))
    model["source_summary"] = {
        "active_source_count": len(active_sources),
        "business_fact_count": len(facts),
        "accepted_behavior_fact_count": len(accepted_behavior_facts),
        "pending_fact_count": len(pending_facts),
        "formal_business_object_count": len(as_list(model.get("business_objects"))),
        "formal_operation_count": len(as_list(model.get("operations"))),
        "formal_lifecycle_count": len(as_list(model.get("lifecycles"))),
        "decision_matrix_row_count": len(row_ledger),
        "business_behavior_count": len(behaviors),
        "confirmed_behavior_count": int(
            behavior_metrics.get("confirmed_behavior_count") or 0
        ),
        "candidate_behavior_count": int(
            behavior_metrics.get("candidate_behavior_count") or 0
        ),
        "incomplete_behavior_count": int(
            behavior_metrics.get("incomplete_behavior_count") or 0
        ),
        "conflicted_behavior_count": int(
            behavior_metrics.get("conflicted_behavior_count") or 0
        ),
        "unresolved_condition_combinator_count": int(
            behavior_metrics.get("unresolved_condition_combinator_count") or 0
        ),
        "behavior_implementation_binding_count": len(implementation_bindings),
        "scenario_ready_binding_count": int(
            implementation_metrics.get("scenario_ready_binding_count") or 0
        ),
        "implementation_binding_unknown_count": len(implementation_unknowns),
        "implementation_binding_conflict_count": len(implementation_conflicts),
        "implementation_binding_status": implementation_gate.get("status"),
    }
    gate = assess_understanding_model(
        model,
        upstream_gate=as_dict(asset.get("enterprise_comprehension_gate")),
    )
    model["gate"] = gate
    model["metrics"] = dict(gate.get("metrics") or {})
    model["metrics"]["implementation_binding_status"] = implementation_gate.get("status")
    model["metrics"]["scenario_ready_binding_count"] = int(
        implementation_metrics.get("scenario_ready_binding_count") or 0
    )

    # Document-structure completeness is part of the semantic closure.
    # Implementation binding remains a separate downstream gate.
    from .document_structure_gate import apply_document_structure_completeness

    return apply_document_structure_completeness(model, asset)


__all__ = ["apply_minimum_understanding_closure"]
