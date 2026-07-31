"""Bridge enterprise Binding Identity into the existing formal event mainline.

The source event contract remains the semantic and execution authority. This module only
projects durable implementation identities already compiled by enterprise understanding onto
the matching Behavior IR invariant. It never selects an endpoint, actor, observer, Runtime Plan
or Materialization by token overlap or source order.
"""
from __future__ import annotations

import copy
from collections import Counter
from typing import Any

SCHEMA_VERSION = "qualibug.formal-event-binding-identity-bridge.v1"
_EVENT_BINDING_KIND = "SOURCE_EVENT_DELIVERY_OBSERVER"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _rows(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in _list(value) if isinstance(row, dict)]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique(values: Any) -> list[str]:
    return list(dict.fromkeys(_text(value) for value in values if _text(value)))


def _event_invariants(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _rows(model.get("invariants"))
        if _text(_dict(row.get("expression")).get("kind"))
        == "event_delivery_contract"
        and _text(row.get("event_contract_id"))
    ]


def _managed_contract_refs(asset: dict[str, Any], graph: dict[str, Any]) -> set[str]:
    refs = {
        _text(row.get("event_contract_ref"))
        for row in _rows(graph.get("observer_bindings"))
        if _text(row.get("binding_kind")) == _EVENT_BINDING_KIND
        and _text(row.get("event_contract_ref"))
    }
    refs.update(
        _text(row.get("contract_id") or row.get("id"))
        for row in _rows(asset.get("event_formal_contract_candidates"))
        if _text(row.get("contract_id") or row.get("id"))
    )
    refs.update(
        _text(row.get("contract_id"))
        for row in _rows(asset.get("event_formal_contract_validation_failures"))
        if _text(row.get("contract_id"))
    )
    return refs


def _observer_refs(row: dict[str, Any]) -> set[str]:
    return {
        _text(value)
        for value in _list(
            _dict(row.get("binding_identity_refs")).get("observer_binding_refs")
        )
        if _text(value)
    }


def _action_ref(row: dict[str, Any]) -> str:
    return _text(
        _dict(row.get("action_entry")).get("action_surface_binding_ref")
        or _dict(row.get("binding_identity_refs")).get(
            "action_surface_binding_ref"
        )
        or _dict(row.get("request_draft")).get("action_surface_binding_ref")
    )


def _linked_action_surface_refs(
    asset: dict[str, Any], implementation_ref: str
) -> set[str]:
    rows = [
        *_rows(asset.get("binding_identity_relationships")),
        *_rows(asset.get("relationships")),
    ]
    return {
        _text(row.get("to"))
        for row in rows
        if _text(row.get("relation"))
        == "implementation_binding_to_action_surface"
        and _text(row.get("from")) == implementation_ref
        and _text(row.get("status") or "accepted") == "accepted"
        and _text(row.get("to"))
    }


def _binding_identity(
    *,
    asset: dict[str, Any],
    invariant: dict[str, Any],
    graph: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    contract_ref = _text(invariant.get("event_contract_id"))
    actor_ref = _text(invariant.get("event_actor_ref"))
    observer_candidates = [
        row
        for row in _rows(graph.get("observer_bindings"))
        if _text(row.get("binding_kind")) == _EVENT_BINDING_KIND
        and _text(row.get("event_contract_ref")) == contract_ref
        and bool(row.get("authoritative"))
        and _text(row.get("status")) == "BOUND"
        and (not actor_ref or _text(row.get("actor_ref")) == actor_ref)
    ]
    if len(observer_candidates) != 1:
        return {}, (
            "FORMAL_EVENT_OBSERVER_BINDING_AMBIGUOUS"
            if len(observer_candidates) > 1
            else "FORMAL_EVENT_OBSERVER_BINDING_NOT_FOUND"
        )
    observer = observer_candidates[0]
    observer_ref = _text(observer.get("observer_binding_id"))
    implementation_ref = _text(observer.get("implementation_binding_ref"))
    interface_id = _text(observer.get("interface_id"))
    if not observer_ref or not implementation_ref or not interface_id:
        return {}, "FORMAL_EVENT_OBSERVER_BINDING_IDENTITY_INCOMPLETE"

    linked_surface_refs = _linked_action_surface_refs(asset, implementation_ref)
    if not linked_surface_refs:
        return {}, "FORMAL_EVENT_ACTION_SURFACE_RELATION_NOT_FOUND"
    surfaces = [
        row
        for row in _rows(graph.get("action_surface_bindings"))
        if _text(row.get("action_surface_binding_id")) in linked_surface_refs
        and _text(row.get("interface_id")) == interface_id
        and _text(row.get("surface_kind")) == "HTTP_API"
        and bool(row.get("authoritative"))
        and bool(row.get("primary"))
        and _text(row.get("status")) == "BOUND"
    ]
    if len(surfaces) != 1:
        return {}, (
            "FORMAL_EVENT_ACTION_SURFACE_AMBIGUOUS"
            if len(surfaces) > 1
            else "FORMAL_EVENT_ACTION_SURFACE_NOT_FOUND"
        )
    action_ref = _text(surfaces[0].get("action_surface_binding_id"))

    plans = [
        row
        for row in _rows(asset.get("runtime_plans"))
        if _text(row.get("implementation_binding_ref")) == implementation_ref
        and observer_ref in _observer_refs(row)
        and _text(row.get("status")) == "TEMPLATE_READY"
        and bool(row.get("formal_runtime_plan"))
    ]
    if len(plans) != 1:
        return {}, (
            "FORMAL_EVENT_RUNTIME_PLAN_AMBIGUOUS"
            if len(plans) > 1
            else "FORMAL_EVENT_RUNTIME_PLAN_NOT_FOUND"
        )
    plan = plans[0]
    if _action_ref(plan) != action_ref:
        return {}, "FORMAL_EVENT_RUNTIME_PLAN_ACTION_IDENTITY_DRIFT"
    plan_ref = _text(plan.get("plan_id"))

    materializations = [
        row
        for row in _rows(asset.get("runtime_materializations"))
        if _text(row.get("runtime_plan_ref")) == plan_ref
        and observer_ref in _observer_refs(row)
        and _text(row.get("status")) == "DRAFT_READY"
        and bool(row.get("formal_runtime_materialization"))
    ]
    if len(materializations) != 1:
        return {}, (
            "FORMAL_EVENT_RUNTIME_MATERIALIZATION_AMBIGUOUS"
            if len(materializations) > 1
            else "FORMAL_EVENT_RUNTIME_MATERIALIZATION_NOT_FOUND"
        )
    materialization = materializations[0]
    if _action_ref(materialization) != action_ref:
        return {}, "FORMAL_EVENT_MATERIALIZATION_ACTION_IDENTITY_DRIFT"

    plan_refs = _dict(plan.get("binding_identity_refs"))
    materialization_refs = _dict(materialization.get("binding_identity_refs"))
    contract_field_refs = _unique(plan_refs.get("contract_field_binding_refs") or [])
    runtime_value_refs = _unique(plan_refs.get("runtime_value_binding_refs") or [])
    if contract_field_refs != _unique(
        materialization_refs.get("contract_field_binding_refs") or []
    ):
        return {}, "FORMAL_EVENT_MATERIALIZATION_FIELD_IDENTITY_DRIFT"
    if runtime_value_refs != _unique(
        materialization_refs.get("runtime_value_binding_refs") or []
    ):
        return {}, "FORMAL_EVENT_MATERIALIZATION_VALUE_IDENTITY_DRIFT"

    return {
        "schema_version": SCHEMA_VERSION,
        "status": "BOUND",
        "event_contract_ref": contract_ref,
        "implementation_binding_ref": implementation_ref,
        "action_surface_binding_ref": action_ref,
        "observer_binding_ref": observer_ref,
        "interface_id": interface_id,
        "actor_ref": actor_ref or observer.get("actor_ref"),
        "scenario_ref": plan.get("scenario_ref"),
        "runtime_plan_ref": plan_ref,
        "runtime_materialization_ref": materialization.get("materialization_id"),
        "contract_field_binding_refs": contract_field_refs,
        "runtime_value_binding_refs": runtime_value_refs,
        "binding_authority": "enterprise_binding_identity_graph",
        "identity_reselection_allowed": False,
        "token_overlap_is_authoritative": False,
    }, ""


def project_formal_event_binding_identities(
    behavior_ir: dict[str, Any],
    asset: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Attach exact durable event identities to matching Behavior IR invariants."""
    model = copy.deepcopy(_dict(behavior_ir))
    asset_row = _dict(asset)
    graph = _dict(asset_row.get("binding_identity_graph"))
    invariants = _event_invariants(model)
    if not invariants:
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "status": "NOT_REQUESTED",
            "identity_required": False,
            "event_invariant_count": 0,
            "managed_event_invariant_count": 0,
            "runtime_overlay_event_invariant_count": 0,
            "bound_count": 0,
            "blocked_count": 0,
            "reason_counts": {},
        }
        model["formal_event_binding_identity_receipt"] = receipt
        return model, receipt
    if not graph:
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "status": "LEGACY_IDENTITY_GRAPH_ABSENT",
            "identity_required": False,
            "event_invariant_count": len(invariants),
            "managed_event_invariant_count": 0,
            "runtime_overlay_event_invariant_count": len(invariants),
            "bound_count": 0,
            "blocked_count": 0,
            "reason_counts": {},
        }
        model["formal_event_binding_identity_receipt"] = receipt
        return model, receipt

    managed_refs = _managed_contract_refs(asset_row, graph)
    projected: list[dict[str, Any]] = []
    reasons: list[str] = []
    bound = 0
    blocked = 0
    managed = 0
    runtime_overlay = 0
    event_ids = {_text(row.get("id")) for row in invariants}
    for raw in _rows(model.get("invariants")):
        invariant = dict(raw)
        if _text(invariant.get("id")) not in event_ids:
            projected.append(invariant)
            continue
        contract_ref = _text(invariant.get("event_contract_id"))
        if contract_ref not in managed_refs:
            invariant["event_binding_identity_required"] = False
            invariant["event_binding_identity_status"] = "RUNTIME_OVERLAY_ONLY"
            runtime_overlay += 1
            projected.append(invariant)
            continue
        managed += 1
        invariant["event_binding_identity_required"] = True
        identity, reason = _binding_identity(
            asset=asset_row,
            invariant=invariant,
            graph=graph,
        )
        if identity:
            invariant["formal_event_binding_identity"] = identity
            invariant["event_binding_identity_status"] = "BOUND"
            invariant["observer_binding_ref"] = identity["observer_binding_ref"]
            invariant["action_surface_binding_ref"] = identity[
                "action_surface_binding_ref"
            ]
            invariant["implementation_binding_ref"] = identity[
                "implementation_binding_ref"
            ]
            invariant["runtime_plan_ref"] = identity["runtime_plan_ref"]
            invariant["runtime_materialization_ref"] = identity[
                "runtime_materialization_ref"
            ]
            bound += 1
        else:
            invariant["event_binding_identity_status"] = "BLOCKED"
            invariant["event_binding_identity_reason_code"] = reason
            reasons.append(reason)
            blocked += 1
        projected.append(invariant)
    model["invariants"] = projected
    status = (
        "BLOCKED"
        if blocked
        else "BOUND"
        if managed
        else "RUNTIME_OVERLAY_ONLY"
    )
    receipt = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "identity_required": managed > 0,
        "event_invariant_count": len(invariants),
        "managed_event_invariant_count": managed,
        "runtime_overlay_event_invariant_count": runtime_overlay,
        "bound_count": bound,
        "blocked_count": blocked,
        "reason_counts": dict(sorted(Counter(reasons).items())),
        "identity_reselection_allowed": False,
        "token_overlap_is_authoritative": False,
    }
    model["formal_event_binding_identity_receipt"] = receipt
    return model, receipt


__all__ = [
    "SCHEMA_VERSION",
    "project_formal_event_binding_identities",
]
