"""Project final binding-identity relationships, metrics and gate truth."""
from __future__ import annotations

from typing import Any, Iterable

from .schema import as_dict, as_list, stable_id, text, unique_text

_REQUEST_COLLECTIONS = (
    "path_parameters",
    "query_parameters",
    "header_parameters",
    "cookie_parameters",
    "body_fields",
    "form_fields",
)


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _plan_slots(plan: dict[str, Any]) -> Iterable[dict[str, Any]]:
    request = as_dict(plan.get("request_template"))
    for collection in _REQUEST_COLLECTIONS:
        yield from _dicts(request.get(collection))


def _identity_relationships(asset: dict[str, Any]) -> list[dict[str, Any]]:
    graph = as_dict(asset.get("binding_identity_graph"))
    rows: list[dict[str, Any]] = []
    for binding in _dicts(asset.get("behavior_implementation_bindings")):
        binding_id = text(binding.get("binding_id"))
        for surface in _dicts(binding.get("action_surface_bindings")):
            surface_id = text(surface.get("action_surface_binding_id"))
            if binding_id and surface_id:
                rows.append(
                    {
                        "edge_id": stable_id("edge", "implementation_binding_to_action_surface", binding_id, surface_id),
                        "from": binding_id,
                        "to": surface_id,
                        "relation": "implementation_binding_to_action_surface",
                        "status": "accepted",
                        "confidence": 1.0,
                        "derivation": "binding_identity_compiler",
                    }
                )
    for field in _dicts(graph.get("contract_field_bindings")):
        surface_ref = text(field.get("action_surface_binding_ref"))
        field_id = text(field.get("contract_field_binding_id"))
        if surface_ref and field_id:
            rows.append(
                {
                    "edge_id": stable_id("edge", "action_surface_to_contract_field", surface_ref, field_id),
                    "from": surface_ref,
                    "to": field_id,
                    "relation": "action_surface_to_contract_field",
                    "status": "accepted",
                    "confidence": 1.0,
                    "derivation": "source_declared_contract_identity",
                }
            )
    for value in _dicts(graph.get("runtime_value_bindings")):
        value_id = text(value.get("runtime_value_binding_id"))
        for field_ref in unique_text(as_list(value.get("contract_field_binding_refs"))):
            if value_id and field_ref:
                rows.append(
                    {
                        "edge_id": stable_id("edge", "runtime_value_to_contract_field", value_id, field_ref),
                        "from": value_id,
                        "to": field_ref,
                        "relation": "runtime_value_to_contract_field",
                        "status": "accepted",
                        "confidence": 1.0,
                        "derivation": "binding_identity_compiler",
                    }
                )
    return rows


def finalize_binding_identity_projection(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    graph = as_dict(asset.get("binding_identity_graph"))
    plans = _dicts(asset.get("runtime_plans"))
    materializations = _dicts(asset.get("runtime_materializations"))
    plan_slots = [slot for plan in plans for slot in _plan_slots(plan)]
    required_slots = [row for row in plan_slots if bool(row.get("required"))]
    materialized_values = [
        row
        for materialization in materializations
        for row in _dicts(materialization.get("request_value_bindings"))
    ]
    formal_plans = [row for row in plans if bool(row.get("formal_runtime_plan"))]
    formal_materializations = [
        row for row in materializations if bool(row.get("formal_runtime_materialization"))
    ]
    missing_plan_action = sum(
        1
        for row in formal_plans
        if not text(as_dict(row.get("action_entry")).get("action_surface_binding_ref"))
    )
    missing_plan_fields = sum(
        1 for row in required_slots if not text(row.get("contract_field_binding_ref"))
    )
    missing_materialization_action = sum(
        1
        for row in formal_materializations
        if not text(as_dict(row.get("binding_identity_refs")).get("action_surface_binding_ref"))
    )
    missing_materialized_fields = sum(
        1
        for row in materialized_values
        if not text(row.get("contract_field_binding_ref"))
    )
    unknowns = _dicts(asset.get("binding_identity_unknowns"))
    blocked = bool(
        unknowns
        or missing_plan_action
        or missing_plan_fields
        or missing_materialization_action
        or missing_materialized_fields
    )
    prior = dict(as_dict(asset.get("binding_identity_gate")))
    status = "BLOCKED_BINDING_IDENTITY_INCOMPLETE" if blocked else "PASS"
    metrics = dict(as_dict(prior.get("metrics")))
    metrics.update(
        {
            "action_surface_binding_count": len(_dicts(graph.get("action_surface_bindings"))),
            "contract_field_binding_count": len(_dicts(graph.get("contract_field_bindings"))),
            "runtime_value_binding_count": len(_dicts(graph.get("runtime_value_bindings"))),
            "observer_binding_count": len(_dicts(graph.get("observer_bindings"))),
            "formal_ui_surface_binding_count": len(_dicts(graph.get("formal_ui_surface_bindings"))),
            "formal_runtime_plan_count": len(formal_plans),
            "runtime_plan_request_slot_count": len(plan_slots),
            "required_runtime_plan_request_slot_count": len(required_slots),
            "runtime_plan_slot_with_contract_field_ref_count": sum(
                1 for row in plan_slots if text(row.get("contract_field_binding_ref"))
            ),
            "runtime_plan_slot_with_runtime_value_ref_count": sum(
                1 for row in plan_slots if text(row.get("runtime_value_binding_ref"))
            ),
            "formal_runtime_materialization_count": len(formal_materializations),
            "materialized_request_value_count": len(materialized_values),
            "materialized_value_with_contract_field_ref_count": sum(
                1 for row in materialized_values if text(row.get("contract_field_binding_ref"))
            ),
            "materialized_value_with_runtime_value_ref_count": sum(
                1 for row in materialized_values if text(row.get("runtime_value_binding_ref"))
            ),
            "missing_runtime_plan_action_identity_count": missing_plan_action,
            "missing_required_plan_field_identity_count": missing_plan_fields,
            "missing_materialization_action_identity_count": missing_materialization_action,
            "missing_materialized_field_identity_count": missing_materialized_fields,
            "binding_identity_unknown_count": len(unknowns),
        }
    )
    gate = {
        **prior,
        "status": status,
        "entry_allowed": status == "PASS",
        "binding_identity_ready": status == "PASS",
        "execution_allowed": False,
        "metrics": metrics,
        "quality_claim": "IDENTITY_CLOSURE_NOT_RUNTIME_EXECUTION",
    }
    asset["binding_identity_gate"] = gate
    model["binding_identity_gate"] = dict(gate)

    relationships = _identity_relationships(asset)
    asset["binding_identity_relationships"] = relationships
    model["binding_identity_relationships"] = [dict(row) for row in relationships]
    asset["relationships"] = list(
        {
            text(row.get("edge_id")): dict(row)
            for row in [*_dicts(asset.get("relationships")), *relationships]
            if text(row.get("edge_id"))
        }.values()
    )

    projected = {
        "binding_identity_status": status,
        "binding_identity_ready": status == "PASS",
        "binding_identity_action_surface_count": metrics["action_surface_binding_count"],
        "binding_identity_contract_field_count": metrics["contract_field_binding_count"],
        "binding_identity_runtime_value_count": metrics["runtime_value_binding_count"],
        "binding_identity_observer_count": metrics["observer_binding_count"],
        "binding_identity_formal_ui_surface_count": metrics["formal_ui_surface_binding_count"],
        "binding_identity_unknown_count": len(unknowns),
        "runtime_plan_status": as_dict(asset.get("runtime_plan_gate")).get("status"),
        "runtime_plan_ready": bool(as_dict(asset.get("runtime_plan_gate")).get("entry_allowed")),
        "runtime_materialization_status": as_dict(asset.get("runtime_materialization_gate")).get("status"),
        "runtime_materialization_ready": bool(
            as_dict(asset.get("runtime_materialization_gate")).get("entry_allowed")
        ),
        "implementation_execution_allowed": False,
    }
    summary = dict(as_dict(asset.get("summary")))
    summary.update(projected)
    asset["summary"] = summary
    source_summary = dict(as_dict(model.get("source_summary")))
    source_summary.update(projected)
    model["source_summary"] = source_summary
    model_metrics = dict(as_dict(model.get("metrics")))
    model_metrics.update(projected)
    model["metrics"] = model_metrics

    gaps = [
        dict(row)
        for row in as_list(asset.get("coverage_gaps"))
        if isinstance(row, dict) and text(row.get("kind")) != "BINDING_IDENTITY_INCOMPLETE"
    ]
    if blocked:
        gaps.append(
            {
                "kind": "BINDING_IDENTITY_INCOMPLETE",
                "gap_type": "downstream_binding_identity_not_closed",
                "source_id": "*",
                "binding_identity_status": status,
                "binding_identity_metrics": metrics,
                "execution_allowed": False,
                "operator_action": (
                    "provide one exact source-backed interface/location/schema identity for every "
                    "required request field and preserve it through Runtime Plan and Runtime "
                    "Materialization; do not select by leaf name or source order"
                ),
            }
        )
    asset["coverage_gaps"] = gaps
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "binding_identity_closure_enabled": True,
            "binding_identity_exact_interface_location_schema_required": True,
            "binding_identity_leaf_name_selection_allowed": False,
            "binding_identity_source_order_selection_allowed": False,
            "binding_identity_relationships_projected": True,
            "binding_identity_gate_required_before_probe_compilation": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["finalize_binding_identity_projection"]
