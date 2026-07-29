"""Project implementation binding closure to the enterprise asset surface.

Semantic understanding and system implementation binding remain separate authorities.  This
module exposes a combined scenario-planning gate without changing the semantic understanding
gate and without enabling execution.
"""
from __future__ import annotations

from typing import Any

from .schema import as_dict, as_list, text

SCENARIO_PLANNING_GATE_SCHEMA = "qualibug.business-behavior-scenario-planning-gate.v1"

_BINDING_GAP_KINDS = {
    "BUSINESS_BEHAVIOR_IMPLEMENTATION_BINDING_PARTIAL",
    "BLOCKED_BUSINESS_BEHAVIOR_IMPLEMENTATION_BINDING",
    "BLOCKED_SCENARIO_PLANNING_SEMANTIC_GATE",
}


def _copy_dict_rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _scenario_gate(model: dict[str, Any]) -> dict[str, Any]:
    semantic_gate = as_dict(model.get("gate"))
    implementation_gate = as_dict(model.get("implementation_binding_gate"))
    semantic_ready = bool(semantic_gate.get("entry_allowed"))
    implementation_ready = bool(
        implementation_gate.get("scenario_planning_allowed")
        or implementation_gate.get("entry_allowed")
    )
    implementation_status = text(implementation_gate.get("status")) or "NOT_BUILT"

    if not semantic_ready:
        status = "BLOCKED_SCENARIO_PLANNING_SEMANTIC_GATE"
    elif implementation_status.startswith("BLOCKED"):
        status = implementation_status
    elif not implementation_ready:
        status = "PARTIAL_SCENARIO_PLANNING_IMPLEMENTATION_BINDING"
    else:
        status = "PASS"

    ready = status == "PASS"
    return {
        "schema": SCENARIO_PLANNING_GATE_SCHEMA,
        "status": status,
        "entry_allowed": ready,
        "scenario_planning_allowed": ready,
        "execution_allowed": False,
        "semantic_understanding_status": text(semantic_gate.get("status")) or "UNKNOWN",
        "semantic_understanding_ready": semantic_ready,
        "implementation_binding_status": implementation_status,
        "implementation_binding_ready": implementation_ready,
        "implementation_binding_metrics": dict(
            as_dict(implementation_gate.get("metrics"))
        ),
        "blocking_reasons": [
            reason
            for reason in (
                "SEMANTIC_UNDERSTANDING_NOT_CLOSED" if not semantic_ready else "",
                (
                    "IMPLEMENTATION_BINDING_NOT_CLOSED"
                    if semantic_ready and not implementation_ready
                    else ""
                ),
                (
                    "IMPLEMENTATION_BINDING_CONFLICT"
                    if implementation_status.startswith("BLOCKED")
                    else ""
                ),
            )
            if reason
        ],
        "required_contract": {
            "business_behavior_confirmed": True,
            "authoritative_action_entry_required": True,
            "all_preconditions_observable": True,
            "effect_or_outcome_channel_required": True,
            "ambiguous_binding_allowed": False,
            "token_overlap_endpoint_selection_allowed": False,
        },
        "request_payload_compiled": False,
        "expected_assertion_compiled": False,
        "runtime_environment_validated": False,
        "quality_claim": "SCENARIO_PLANNING_ENTRY_CLOSURE_NOT_RUNTIME_EXECUTABILITY",
    }


def _project_gap(asset: dict[str, Any], scenario_gate: dict[str, Any], model: dict[str, Any]) -> None:
    gaps = [
        dict(row)
        for row in as_list(asset.get("coverage_gaps"))
        if isinstance(row, dict) and text(row.get("kind")) not in _BINDING_GAP_KINDS
    ]
    if bool(scenario_gate.get("entry_allowed")):
        asset["coverage_gaps"] = gaps
        return

    semantic_ready = bool(scenario_gate.get("semantic_understanding_ready"))
    implementation_status = text(scenario_gate.get("implementation_binding_status"))
    if not semantic_ready:
        kind = "BLOCKED_SCENARIO_PLANNING_SEMANTIC_GATE"
        gap_type = "semantic_understanding_not_closed_for_scenario_planning"
    elif implementation_status.startswith("BLOCKED"):
        kind = "BLOCKED_BUSINESS_BEHAVIOR_IMPLEMENTATION_BINDING"
        gap_type = "implementation_binding_conflict"
    else:
        kind = "BUSINESS_BEHAVIOR_IMPLEMENTATION_BINDING_PARTIAL"
        gap_type = "implementation_binding_not_closed"

    implementation_gate = as_dict(model.get("implementation_binding_gate"))
    gaps.append(
        {
            "kind": kind,
            "gap_type": gap_type,
            "source_id": "*",
            "scenario_planning_status": scenario_gate.get("status"),
            "semantic_understanding_status": scenario_gate.get(
                "semantic_understanding_status"
            ),
            "implementation_binding_status": implementation_status,
            "implementation_binding_metrics": dict(
                as_dict(implementation_gate.get("metrics"))
            ),
            "implementation_binding_unknown_count": len(
                as_list(model.get("implementation_binding_unknowns"))
            ),
            "implementation_binding_conflict_count": len(
                as_list(model.get("implementation_binding_conflicts"))
            ),
            "operator_action": (
                "resolve source-backed behavior-to-interface and observer bindings; "
                "do not select an endpoint from token overlap or list order"
            ),
            "automatic_endpoint_fallback_allowed": False,
        }
    )
    asset["coverage_gaps"] = gaps


def project_implementation_binding_to_asset(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    """Expose binding assets and a combined scenario-planning gate at asset level."""
    bindings = _copy_dict_rows(model.get("behavior_implementation_bindings"))
    unknowns = _copy_dict_rows(model.get("implementation_binding_unknowns"))
    conflicts = _copy_dict_rows(model.get("implementation_binding_conflicts"))
    evidence = _copy_dict_rows(model.get("implementation_evidence_index"))
    implementation_gate = dict(as_dict(model.get("implementation_binding_gate")))
    scenario_gate = _scenario_gate(model)

    asset["behavior_implementation_bindings"] = bindings
    asset["implementation_binding_unknowns"] = unknowns
    asset["implementation_binding_conflicts"] = conflicts
    asset["implementation_evidence_index"] = evidence
    asset["implementation_binding_gate"] = implementation_gate
    asset["scenario_planning_gate"] = scenario_gate
    _project_gap(asset, scenario_gate, model)

    metrics = as_dict(implementation_gate.get("metrics"))
    summary = as_dict(asset.get("summary"))
    summary.update(
        {
            "behavior_implementation_binding_count": len(bindings),
            "implementation_scenario_ready_binding_count": int(
                metrics.get("scenario_ready_binding_count") or 0
            ),
            "implementation_bound_binding_count": int(
                metrics.get("bound_binding_count") or 0
            ),
            "implementation_partial_binding_count": int(
                metrics.get("partial_binding_count") or 0
            ),
            "implementation_unbound_binding_count": int(
                metrics.get("unbound_binding_count") or 0
            ),
            "implementation_ambiguous_binding_count": int(
                metrics.get("ambiguous_binding_count") or 0
            ),
            "implementation_conflicted_binding_count": int(
                metrics.get("conflicted_binding_count") or 0
            ),
            "implementation_binding_unknown_count": len(unknowns),
            "implementation_binding_conflict_count": len(conflicts),
            "implementation_binding_status": implementation_gate.get("status"),
            "implementation_binding_ready": bool(
                implementation_gate.get("scenario_planning_allowed")
            ),
            "scenario_planning_status": scenario_gate.get("status"),
            "scenario_planning_ready": bool(scenario_gate.get("entry_allowed")),
            "implementation_execution_ready": False,
        }
    )
    asset["summary"] = summary

    governance = as_dict(asset.get("governance"))
    governance.update(
        {
            "implementation_binding_is_separate_from_semantic_understanding": True,
            "scenario_planning_requires_semantic_and_implementation_gates": True,
            "implementation_binding_requires_authoritative_action_entry": True,
            "implementation_binding_requires_observable_preconditions": True,
            "implementation_binding_requires_effect_or_outcome_channel": True,
            "token_overlap_cannot_select_implementation_endpoint": True,
            "arbitrary_endpoint_fallback_allowed": False,
            "ui_design_label_is_not_executable_locator": True,
            "automatic_field_alias_binding_allowed": False,
            "implementation_binding_compiles_request_payload": False,
            "implementation_binding_compiles_expected_assertion": False,
            "implementation_binding_allows_execution": False,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = [
    "SCENARIO_PLANNING_GATE_SCHEMA",
    "project_implementation_binding_to_asset",
]
