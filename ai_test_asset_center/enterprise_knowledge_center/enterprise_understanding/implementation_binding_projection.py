"""Project the final combined scenario-planning gate.

The base implementation-binding closure already exposes bindings, unknowns, conflicts,
relationships, summary metrics and governance. This module runs only after the final semantic
and document-structure gate is known. It combines the two authorities without changing either
one and never enables execution.
"""
from __future__ import annotations

from typing import Any

from .schema import as_dict, as_list, text

SCENARIO_PLANNING_GATE_SCHEMA = "qualibug.business-behavior-scenario-planning-gate.v1"
_SEMANTIC_SCENARIO_GAP = "BLOCKED_SCENARIO_PLANNING_SEMANTIC_GATE"


def build_final_scenario_planning_gate(model: dict[str, Any]) -> dict[str, Any]:
    semantic_gate = as_dict(model.get("gate"))
    implementation_gate = as_dict(model.get("implementation_binding_gate"))
    semantic_ready = bool(semantic_gate.get("entry_allowed"))
    implementation_ready = bool(
        implementation_gate.get("scenario_planning_allowed")
        or implementation_gate.get("entry_allowed")
    )
    implementation_status = text(implementation_gate.get("status")) or "NOT_BUILT"

    if not semantic_ready:
        status = _SEMANTIC_SCENARIO_GAP
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


def project_final_scenario_planning_gate(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    """Expose final gates, Scenario IR and non-executable execution requirements."""
    scenario_gate = build_final_scenario_planning_gate(model)
    asset["scenario_planning_gate"] = scenario_gate

    gaps = [
        dict(row)
        for row in as_list(asset.get("coverage_gaps"))
        if isinstance(row, dict) and text(row.get("kind")) != _SEMANTIC_SCENARIO_GAP
    ]
    if not bool(scenario_gate.get("semantic_understanding_ready")):
        gaps.append(
            {
                "kind": _SEMANTIC_SCENARIO_GAP,
                "gap_type": "semantic_understanding_not_closed_for_scenario_planning",
                "source_id": "*",
                "scenario_planning_status": scenario_gate.get("status"),
                "semantic_understanding_status": scenario_gate.get(
                    "semantic_understanding_status"
                ),
                "implementation_binding_status": scenario_gate.get(
                    "implementation_binding_status"
                ),
                "operator_action": (
                    "resolve source-backed semantic or document-structure gaps before "
                    "using otherwise valid implementation bindings"
                ),
                "semantic_and_implementation_gates_are_separate": True,
            }
        )
    asset["coverage_gaps"] = gaps

    summary = as_dict(asset.get("summary"))
    summary.update(
        {
            "scenario_planning_status": scenario_gate.get("status"),
            "scenario_planning_ready": bool(scenario_gate.get("entry_allowed")),
            "scenario_planning_allowed": bool(
                scenario_gate.get("scenario_planning_allowed")
            ),
            "implementation_binding_status": scenario_gate.get(
                "implementation_binding_status"
            ),
            "implementation_binding_ready": bool(
                scenario_gate.get("implementation_binding_ready")
            ),
            "implementation_execution_allowed": False,
        }
    )
    asset["summary"] = summary

    governance = as_dict(asset.get("governance"))
    governance.update(
        {
            "scenario_planning_uses_final_semantic_gate": True,
            "scenario_planning_requires_semantic_and_implementation_gates": True,
            "implementation_binding_cannot_override_semantic_unknowns": True,
            "semantic_understanding_cannot_substitute_for_system_binding": True,
            "scenario_planning_gate_does_not_enable_execution": True,
        }
    )
    asset["governance"] = governance

    from .scenario_execution_contract_projection import (
        project_governed_scenario_execution_contracts,
    )
    from .scenario_ir import project_scenario_ir_to_asset
    from .scenario_ir_asset_governance import project_scenario_ir_asset_governance

    project_scenario_ir_to_asset(asset, model)
    project_scenario_ir_asset_governance(asset, model)
    return project_governed_scenario_execution_contracts(asset, model)


__all__ = [
    "SCENARIO_PLANNING_GATE_SCHEMA",
    "build_final_scenario_planning_gate",
    "project_final_scenario_planning_gate",
]
