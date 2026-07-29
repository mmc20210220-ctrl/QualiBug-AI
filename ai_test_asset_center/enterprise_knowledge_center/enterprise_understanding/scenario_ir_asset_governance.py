"""Project Scenario IR closure into asset gaps and relationship graph."""
from __future__ import annotations

from functools import wraps
from typing import Any

from .schema import as_dict, as_list, stable_id, text

_SCENARIO_GAP_KINDS = {
    "SCENARIO_IR_UPSTREAM_BLOCKED",
    "SCENARIO_IR_INCOMPLETE",
    "SCENARIO_IR_NOT_COMPILED",
}


def _relationships(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for scenario in scenarios:
        scenario_id = text(scenario.get("scenario_id"))
        behavior_id = text(scenario.get("behavior_ref"))
        status = text(scenario.get("status"))
        accepted = status == "PLANNABLE"
        if scenario_id and behavior_id:
            edges.append(
                {
                    "edge_id": stable_id(
                        "edge", "behavior_to_scenario_ir", behavior_id, scenario_id
                    ),
                    "from": behavior_id,
                    "to": scenario_id,
                    "relation": "behavior_to_scenario_ir",
                    "status": "accepted" if accepted else "candidate",
                    "confidence": 1.0 if accepted else 0.0,
                    "derivation": "scenario_ir_compiler",
                    "evidence_gate": (
                        "confirmed_behavior_and_governed_binding"
                        if accepted
                        else "scenario_ir_unresolved"
                    ),
                    "evidence": {
                        "scenario_type": scenario.get("scenario_type"),
                        "implementation_binding_ref": scenario.get(
                            "implementation_binding_ref"
                        ),
                        "source_evidence": as_list(scenario.get("evidence")),
                    },
                }
            )
        action = as_dict(scenario.get("action_entry"))
        interface_id = text(action.get("interface_id"))
        if scenario_id and interface_id:
            authoritative = bool(action.get("authoritative")) and accepted
            edges.append(
                {
                    "edge_id": stable_id(
                        "edge", "scenario_ir_to_interface", scenario_id, interface_id
                    ),
                    "from": scenario_id,
                    "to": interface_id,
                    "relation": "scenario_ir_to_interface",
                    "status": "accepted" if authoritative else "candidate",
                    "confidence": 1.0 if authoritative else 0.0,
                    "derivation": text(action.get("derivation"))
                    or "governed_behavior_action_binding",
                    "evidence_gate": (
                        "authoritative_action_entry"
                        if authoritative
                        else "scenario_not_plannable"
                    ),
                    "evidence": {
                        "method": action.get("method"),
                        "path": action.get("path"),
                        "execution_ready": False,
                    },
                }
            )
    return list(
        {
            text(row.get("edge_id")): row
            for row in edges
            if text(row.get("edge_id"))
        }.values()
    )


def _install_probe_gate_guard() -> None:
    """Prevent legacy risk-to-probe generation from bypassing Scenario IR closure."""
    from .. import _api

    current = _api._probes_from_asset
    if getattr(current, "_qualibug_scenario_ir_gate_guard", False):
        return
    original = current

    @wraps(original)
    def guarded(asset: dict[str, Any], max_count: int = 140):
        planning_gate = as_dict(asset.get("scenario_planning_gate"))
        scenario_gate = as_dict(asset.get("scenario_ir_gate"))
        if planning_gate:
            if not bool(planning_gate.get("scenario_planning_allowed")):
                return []
            # Once an asset declares the new planning gate, absence of Scenario IR is a
            # downstream gap, not permission to fall back to the legacy risk compiler.
            if not scenario_gate or not bool(scenario_gate.get("entry_allowed")):
                return []
        return original(asset, max_count)

    guarded._qualibug_scenario_ir_gate_guard = True  # type: ignore[attr-defined]
    guarded._qualibug_original_probe_builder = original  # type: ignore[attr-defined]
    _api._probes_from_asset = guarded


def project_scenario_ir_asset_governance(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    scenarios = [
        dict(row) for row in as_list(asset.get("scenario_ir")) if isinstance(row, dict)
    ]
    gate = as_dict(asset.get("scenario_ir_gate"))
    relationships = _relationships(scenarios)
    asset["scenario_ir_relationships"] = relationships
    asset["relationships"] = list(
        {
            text(row.get("edge_id")): dict(row)
            for row in [*as_list(asset.get("relationships")), *relationships]
            if isinstance(row, dict) and text(row.get("edge_id"))
        }.values()
    )

    gaps = [
        dict(row)
        for row in as_list(asset.get("coverage_gaps"))
        if isinstance(row, dict) and text(row.get("kind")) not in _SCENARIO_GAP_KINDS
    ]
    status = text(gate.get("status")) or "NOT_BUILT"
    if status != "PASS":
        if status == "BLOCKED_SCENARIO_IR_UPSTREAM_GATE":
            kind = "SCENARIO_IR_UPSTREAM_BLOCKED"
            gap_type = "upstream_scenario_planning_gate_closed"
        elif status == "NO_SCENARIO_IR_COMPILED":
            kind = "SCENARIO_IR_NOT_COMPILED"
            gap_type = "no_source_backed_scenario_ir_compiled"
        else:
            kind = "SCENARIO_IR_INCOMPLETE"
            gap_type = "scenario_ir_has_critical_unknowns"
        gaps.append(
            {
                "kind": kind,
                "gap_type": gap_type,
                "source_id": "*",
                "scenario_ir_status": status,
                "upstream_scenario_planning_status": gate.get(
                    "upstream_scenario_planning_status"
                ),
                "scenario_ir_metrics": dict(as_dict(gate.get("metrics"))),
                "scenario_ir_unknown_count": len(
                    as_list(asset.get("scenario_ir_unknowns"))
                ),
                "execution_allowed": False,
                "operator_action": (
                    "resolve source-backed scenario semantics or upstream binding gaps; "
                    "do not invent requests, assertions, credentials or adjacent boundary values"
                ),
            }
        )
    asset["coverage_gaps"] = gaps

    metrics = as_dict(gate.get("metrics"))
    scenario_metrics = {
        "scenario_ir_status": status,
        "scenario_ir_count": int(metrics.get("scenario_count") or 0),
        "scenario_ir_plannable_count": int(
            metrics.get("plannable_scenario_count") or 0
        ),
        "scenario_ir_incomplete_count": int(
            metrics.get("incomplete_scenario_count") or 0
        ),
        "scenario_ir_positive_count": int(
            metrics.get("positive_scenario_count") or 0
        ),
        "scenario_ir_rejection_count": int(
            metrics.get("rejection_scenario_count") or 0
        ),
        "scenario_ir_unauthorized_count": int(
            metrics.get("unauthorized_scenario_count") or 0
        ),
        "scenario_ir_boundary_count": int(
            metrics.get("boundary_scenario_count") or 0
        ),
        "scenario_ir_state_transition_count": int(
            metrics.get("state_transition_scenario_count") or 0
        ),
        "scenario_ir_unknown_count": len(
            as_list(asset.get("scenario_ir_unknowns"))
        ),
        "scenario_ir_relationship_count": len(relationships),
    }
    summary = as_dict(asset.get("summary"))
    summary.update(scenario_metrics)
    asset["summary"] = summary

    source_summary = as_dict(model.get("source_summary"))
    source_summary.update(scenario_metrics)
    model["source_summary"] = source_summary
    model_metrics = as_dict(model.get("metrics"))
    model_metrics.update(scenario_metrics)
    model_metrics["scenario_execution_allowed"] = False
    model["metrics"] = model_metrics

    governance = as_dict(asset.get("governance"))
    governance.update(
        {
            "scenario_ir_relationship_graph_enabled": True,
            "scenario_ir_gaps_fail_visible": True,
            "scenario_ir_projection_is_idempotent": True,
            "scenario_ir_relationships_do_not_imply_execution": True,
            "legacy_probe_generation_requires_scenario_ir_gate": True,
        }
    )
    asset["governance"] = governance
    model["scenario_ir_relationships"] = relationships
    _install_probe_gate_guard()
    return asset


__all__ = ["project_scenario_ir_asset_governance"]
