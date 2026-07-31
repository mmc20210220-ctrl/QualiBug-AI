"""Project the final combined scenario-planning gate.

The base implementation-binding closure already exposes bindings, unknowns, conflicts,
relationships, summary metrics and governance. This module runs only after the final semantic
and document-structure gate is known. It combines the two authorities without changing either
one and never enables execution.
"""
from __future__ import annotations

from typing import Any

from .event_observer_implementation_projection import project_formal_event_observers
from .schema import as_dict, as_list, text

SCENARIO_PLANNING_GATE_SCHEMA = "qualibug.business-behavior-scenario-planning-gate.v1"
_SEMANTIC_SCENARIO_GAP = "BLOCKED_SCENARIO_PLANNING_SEMANTIC_GATE"
_PARTIAL_PASS = "PARTIAL_PASS_SCENARIO_PLANNING"


def _project_event_observer_authority(
    asset: dict[str, Any], model: dict[str, Any]
) -> None:
    bindings, unknowns, conflicts, gate = project_formal_event_observers(
        asset,
        as_list(model.get("business_behaviors")),
        as_list(model.get("behavior_implementation_bindings")),
        as_list(model.get("implementation_binding_unknowns")),
        as_list(model.get("implementation_binding_conflicts")),
        as_dict(model.get("implementation_binding_gate")),
    )
    model["behavior_implementation_bindings"] = bindings
    model["implementation_binding_unknowns"] = unknowns
    model["implementation_binding_conflicts"] = conflicts
    model["implementation_binding_gate"] = gate
    asset["behavior_implementation_bindings"] = [dict(row) for row in bindings]
    asset["implementation_binding_unknowns"] = [dict(row) for row in unknowns]
    asset["implementation_binding_conflicts"] = [dict(row) for row in conflicts]
    asset["implementation_binding_gate"] = dict(gate)

    metrics = as_dict(gate.get("metrics"))
    summary = dict(as_dict(asset.get("summary")))
    summary.update(
        {
            "implementation_binding_status": gate.get("status"),
            "implementation_binding_ready": bool(gate.get("entry_allowed")),
            "scenario_ready_binding_count": int(
                metrics.get("scenario_ready_binding_count") or 0
            ),
            "formal_event_contract_count": int(
                metrics.get("formal_event_contract_count") or 0
            ),
            "formal_event_observer_binding_count": int(
                metrics.get("formal_event_observer_binding_count") or 0
            ),
            "formal_event_contract_bound_count": int(
                metrics.get("formal_event_contract_bound_count") or 0
            ),
        }
    )
    asset["summary"] = summary
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "formal_event_observer_binding_enabled": True,
            "formal_event_observer_reuses_existing_event_contract_authority": True,
            "formal_event_contract_is_effect_observer_not_action_surface": True,
            "event_topic_or_broker_inference_allowed": False,
        }
    )
    asset["governance"] = governance


def build_final_scenario_planning_gate(model: dict[str, Any]) -> dict[str, Any]:
    semantic_gate = as_dict(model.get("gate"))
    implementation_gate = as_dict(model.get("implementation_binding_gate"))
    implementation_metrics = dict(as_dict(implementation_gate.get("metrics")))
    semantic_ready = bool(semantic_gate.get("entry_allowed"))
    implementation_full_ready = bool(
        implementation_gate.get("scenario_planning_allowed")
        or implementation_gate.get("entry_allowed")
    )
    implementation_status = text(implementation_gate.get("status")) or "NOT_BUILT"
    behavior_binding_count = int(
        implementation_metrics.get("behavior_binding_count") or 0
    )
    ready_binding_count = int(
        implementation_metrics.get("scenario_ready_binding_count") or 0
    )
    partial_admission_ready = (
        not implementation_full_ready
        and behavior_binding_count > 0
        and ready_binding_count > 0
    )

    if not semantic_ready:
        status = _SEMANTIC_SCENARIO_GAP
    elif implementation_full_ready:
        status = "PASS"
    elif partial_admission_ready:
        status = _PARTIAL_PASS
    elif implementation_status.startswith("BLOCKED"):
        status = implementation_status
    else:
        status = "PARTIAL_SCENARIO_PLANNING_IMPLEMENTATION_BINDING"

    ready = status in {"PASS", _PARTIAL_PASS}
    isolated_unready_binding_count = max(
        behavior_binding_count - ready_binding_count,
        0,
    )
    return {
        "schema": SCENARIO_PLANNING_GATE_SCHEMA,
        "status": status,
        "entry_allowed": ready,
        "scenario_planning_allowed": ready,
        "execution_allowed": False,
        "semantic_understanding_status": text(semantic_gate.get("status")) or "UNKNOWN",
        "semantic_understanding_ready": semantic_ready,
        "implementation_binding_status": implementation_status,
        "implementation_binding_ready": implementation_full_ready,
        "implementation_binding_full_ready": implementation_full_ready,
        "partial_binding_admission": status == _PARTIAL_PASS,
        "admitted_ready_binding_count": ready_binding_count if ready else 0,
        "isolated_unready_binding_count": isolated_unready_binding_count,
        "implementation_binding_metrics": implementation_metrics,
        "blocking_reasons": [
            reason
            for reason in (
                "SEMANTIC_UNDERSTANDING_NOT_CLOSED" if not semantic_ready else "",
                (
                    "NO_SCENARIO_READY_IMPLEMENTATION_BINDING"
                    if semantic_ready and not ready
                    else ""
                ),
            )
            if reason
        ],
        "isolated_reasons": [
            reason
            for reason in (
                (
                    "UNREADY_IMPLEMENTATION_BINDINGS_ISOLATED"
                    if status == _PARTIAL_PASS and isolated_unready_binding_count
                    else ""
                ),
                (
                    "IMPLEMENTATION_BINDING_CONFLICT_ISOLATED"
                    if status == _PARTIAL_PASS
                    and implementation_status.startswith("BLOCKED")
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
            "admission_scope": "PER_GOVERNED_BEHAVIOR_BINDING",
            "unready_binding_can_block_ready_binding": False,
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
    _project_event_observer_authority(asset, model)
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
            "implementation_binding_full_ready": bool(
                scenario_gate.get("implementation_binding_full_ready")
            ),
            "implementation_binding_partial_admission": bool(
                scenario_gate.get("partial_binding_admission")
            ),
            "implementation_binding_admitted_ready_count": int(
                scenario_gate.get("admitted_ready_binding_count") or 0
            ),
            "implementation_binding_isolated_unready_count": int(
                scenario_gate.get("isolated_unready_binding_count") or 0
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
            "scenario_planning_admission_is_per_governed_behavior_binding": True,
            "unready_behavior_binding_cannot_block_ready_behavior_binding": True,
            "implementation_binding_cannot_override_semantic_unknowns": True,
            "semantic_understanding_cannot_substitute_for_system_binding": True,
            "scenario_planning_gate_does_not_enable_execution": True,
        }
    )
    asset["governance"] = governance

    from .scenario_execution_pipeline import (
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
