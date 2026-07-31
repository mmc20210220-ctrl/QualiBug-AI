"""Compile and validate ObserverBinding identities across runtime representations.

Action and request-field identity remain owned by the existing binding projector.
This module owns only observers. It derives refs from formal Oracle Templates and
requires corresponding Materialization Drafts, so additive observer identities cannot
be erased when another projection rebuilds action/field refs.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .schema import as_dict, as_list, stable_id, text, unique_text

_EVENT_KIND = "SOURCE_EVENT_DELIVERY_OBSERVER"
_DATABASE_KIND = "DATABASE_FIELD"
_RESPONSE_KIND = "API_RESPONSE_OUTCOME_CHANNEL"


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _observers(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return _dicts(as_dict(asset.get("binding_identity_graph")).get("observer_bindings"))


def _observers_by_binding(asset: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observer in _observers(asset):
        binding_ref = text(observer.get("implementation_binding_ref"))
        if binding_ref:
            result[binding_ref].append(observer)
    return dict(result)


def _unknown(scope: str, scope_ref: str, reason: str, **details: Any) -> dict[str, Any]:
    return {
        "unknown_id": stable_id(
            "observer_binding_identity_unknown", scope, scope_ref, reason, details
        ),
        "kind": reason,
        "reason_code": reason,
        "scope": scope,
        "scope_ref": scope_ref,
        "blocks_binding_identity": True,
        "automatic_resolution_allowed": False,
        "execution_allowed": False,
        **details,
    }


def _merge_unknowns(
    asset: dict[str, Any], model: dict[str, Any], rows: Iterable[dict[str, Any]]
) -> None:
    merged = list(
        {
            text(row.get("unknown_id")): dict(row)
            for row in [*_dicts(asset.get("binding_identity_unknowns")), *rows]
            if text(row.get("unknown_id"))
        }.values()
    )
    asset["binding_identity_unknowns"] = merged
    model["binding_identity_unknowns"] = [dict(row) for row in merged]


def _close_gate(
    asset: dict[str, Any],
    model: dict[str, Any],
    key: str,
    *,
    status: str,
    ready_field: str,
    count: int,
) -> None:
    gate = dict(as_dict(asset.get(key)))
    gate.update(
        {
            "status": status,
            "entry_allowed": False,
            ready_field: False,
            "execution_allowed": False,
        }
    )
    metrics = dict(as_dict(gate.get("metrics")))
    metrics["observer_binding_identity_unknown_count"] = count
    gate["metrics"] = metrics
    asset[key] = gate
    model[key] = dict(gate)


def _matching_database_refs(
    observers: list[dict[str, Any]], template: dict[str, Any]
) -> list[str]:
    contract_ref = text(template.get("observer_contract_ref"))
    if contract_ref:
        return unique_text(
            row.get("observer_binding_id")
            for row in observers
            if text(row.get("binding_kind")) == _DATABASE_KIND
            and text(row.get("observer_id")) == contract_ref
        )
    field_ref = text(template.get("field_ref"))
    table_ref = text(template.get("table_ref") or template.get("table"))
    field = text(template.get("field"))
    return unique_text(
        row.get("observer_binding_id")
        for row in observers
        if text(row.get("binding_kind")) == _DATABASE_KIND
        and (
            (field_ref and text(row.get("field_id")) == field_ref)
            or (
                table_ref
                and field
                and table_ref
                in {text(row.get("table_id")), text(row.get("table"))}
                and field == text(row.get("field"))
            )
        )
    )


def _matching_response_refs(
    observers: list[dict[str, Any]], interface_id: str
) -> list[str]:
    return unique_text(
        row.get("observer_binding_id")
        for row in observers
        if text(row.get("binding_kind")) == _RESPONSE_KIND
        and text(row.get("interface_id")) in {"", interface_id}
    )


def _plan_observer_refs(
    plan: dict[str, Any], observers: list[dict[str, Any]]
) -> tuple[list[str], list[dict[str, Any]]]:
    known = {
        text(row.get("observer_binding_id"))
        for row in observers
        if text(row.get("observer_binding_id"))
    }
    interface_id = text(as_dict(plan.get("action_entry")).get("interface_id"))
    refs: list[str] = []
    unknowns: list[dict[str, Any]] = []
    for template in _dicts(as_dict(plan.get("oracle_query_templates")).get("templates")):
        kind = text(template.get("template_kind"))
        direct = text(template.get("observer_binding_ref"))
        if direct:
            if direct in known:
                refs.append(direct)
            else:
                unknowns.append(
                    _unknown(
                        "RUNTIME_PLAN",
                        text(plan.get("plan_id")),
                        "RUNTIME_PLAN_OBSERVER_BINDING_REF_UNRESOLVED",
                        observer_binding_ref=direct,
                        oracle_template_ref=template.get("template_id"),
                    )
                )
            continue
        if kind in {
            "APPROVED_DATABASE_OBSERVER_SNAPSHOT",
            "DATABASE_FIELD_SNAPSHOT",
        }:
            matches = _matching_database_refs(observers, template)
            if matches:
                refs.extend(matches)
            elif kind == "APPROVED_DATABASE_OBSERVER_SNAPSHOT":
                unknowns.append(
                    _unknown(
                        "RUNTIME_PLAN",
                        text(plan.get("plan_id")),
                        "RUNTIME_PLAN_DATABASE_OBSERVER_IDENTITY_UNRESOLVED",
                        observer_contract_ref=template.get("observer_contract_ref"),
                        oracle_template_ref=template.get("template_id"),
                    )
                )
        elif kind == "HTTP_RESPONSE_CAPTURE":
            refs.extend(_matching_response_refs(observers, interface_id))
    return unique_text(refs), unknowns


def project_observer_identities_to_runtime_plans(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    by_binding = _observers_by_binding(asset)
    plans: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    for raw_plan in _dicts(asset.get("runtime_plans")):
        plan = dict(raw_plan)
        plan_ref = text(plan.get("plan_id"))
        observers = by_binding.get(text(plan.get("implementation_binding_ref")), [])
        refs, rows = _plan_observer_refs(plan, observers)
        unknowns.extend(rows)
        identity_refs = dict(as_dict(plan.get("binding_identity_refs")))
        identity_refs["observer_binding_refs"] = refs
        plan["binding_identity_refs"] = identity_refs
        plan["observer_binding_refs"] = refs
        plan["observer_binding_identity_compiled"] = True
        if rows:
            plan["status"] = "INCOMPLETE"
            plan["formal_runtime_plan"] = False
        plans.append(plan)
    asset["runtime_plans"] = plans
    model["runtime_plans"] = [dict(row) for row in plans]
    _merge_unknowns(asset, model, unknowns)
    if unknowns:
        _close_gate(
            asset,
            model,
            "runtime_plan_gate",
            status="BLOCKED_RUNTIME_PLAN_OBSERVER_IDENTITY_INCOMPLETE",
            ready_field="runtime_plan_ready",
            count=len(unknowns),
        )
    else:
        gate = dict(as_dict(asset.get("runtime_plan_gate")))
        metrics = dict(as_dict(gate.get("metrics")))
        metrics["runtime_plan_with_observer_identity_count"] = sum(
            1 for row in plans if as_list(row.get("observer_binding_refs"))
        )
        metrics["runtime_plan_observer_binding_ref_count"] = sum(
            len(as_list(row.get("observer_binding_refs"))) for row in plans
        )
        gate["metrics"] = metrics
        asset["runtime_plan_gate"] = gate
        model["runtime_plan_gate"] = dict(gate)
    return asset


def _draft_proves_observer(
    observer: dict[str, Any], materialization: dict[str, Any]
) -> bool:
    observer_ref = text(observer.get("observer_binding_id"))
    kind = text(observer.get("binding_kind"))
    if kind == _EVENT_KIND:
        return any(
            text(row.get("observer_binding_ref")) == observer_ref
            for row in _dicts(materialization.get("assertion_drafts"))
            if text(row.get("draft_kind"))
            == "SOURCE_EVENT_DELIVERY_ASSERTION_DRAFT"
        )
    if kind == _DATABASE_KIND:
        observer_id = text(observer.get("observer_id"))
        if observer_id and any(
            text(row.get("observer_contract_ref")) == observer_id
            for row in _dicts(materialization.get("database_observer_execution_drafts"))
        ):
            return True
        field_id = text(observer.get("field_id"))
        table_ref = text(observer.get("table_id") or observer.get("table"))
        field = text(observer.get("field"))
        return any(
            (
                field_id
                and field_id
                in {
                    text(value)
                    for value in as_list(row.get("field_binding_refs"))
                    if text(value)
                }
            )
            or (
                table_ref
                and field
                and table_ref
                in {text(row.get("table_ref")), text(row.get("table"))}
                and field in {
                    text(value)
                    for value in as_list(row.get("select_fields"))
                    if text(value)
                }
            )
            for row in _dicts(materialization.get("assertion_drafts"))
            if text(row.get("draft_kind")) == "DATABASE_SNAPSHOT_QUERY_AST"
        )
    if kind == _RESPONSE_KIND:
        return any(
            text(row.get("draft_kind"))
            == "HTTP_RESPONSE_SEMANTIC_ASSERTION_DRAFT"
            for row in _dicts(materialization.get("assertion_drafts"))
        )
    return False


def project_observer_identities_to_materializations(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    plans = {
        text(row.get("plan_id")): row
        for row in _dicts(asset.get("runtime_plans"))
        if text(row.get("plan_id"))
    }
    observer_index = {
        text(row.get("observer_binding_id")): row
        for row in _observers(asset)
        if text(row.get("observer_binding_id"))
    }
    materializations: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    for raw_materialization in _dicts(asset.get("runtime_materializations")):
        materialization = dict(raw_materialization)
        materialization_ref = text(materialization.get("materialization_id"))
        plan = plans.get(text(materialization.get("runtime_plan_ref"))) or {}
        expected = unique_text(
            as_list(as_dict(plan.get("binding_identity_refs")).get("observer_binding_refs"))
        )
        actual: list[str] = []
        for ref in expected:
            observer = observer_index.get(ref) or {}
            if observer and _draft_proves_observer(observer, materialization):
                actual.append(ref)
            else:
                unknowns.append(
                    _unknown(
                        "RUNTIME_MATERIALIZATION",
                        materialization_ref,
                        "RUNTIME_MATERIALIZATION_OBSERVER_DRAFT_MISSING",
                        observer_binding_ref=ref,
                        runtime_plan_ref=materialization.get("runtime_plan_ref"),
                    )
                )
        actual = unique_text(actual)
        refs = dict(as_dict(materialization.get("binding_identity_refs")))
        refs["observer_binding_refs"] = actual
        materialization["binding_identity_refs"] = refs
        materialization["observer_binding_refs"] = actual
        materialization["observer_binding_identity_compiled"] = True
        if actual != expected:
            materialization["status"] = "INCOMPLETE"
            materialization["formal_runtime_materialization"] = False
        materializations.append(materialization)
    asset["runtime_materializations"] = materializations
    model["runtime_materializations"] = [dict(row) for row in materializations]
    _merge_unknowns(asset, model, unknowns)
    if unknowns:
        _close_gate(
            asset,
            model,
            "runtime_materialization_gate",
            status="BLOCKED_RUNTIME_MATERIALIZATION_OBSERVER_IDENTITY_INCOMPLETE",
            ready_field="runtime_materialization_ready",
            count=len(unknowns),
        )
    else:
        gate = dict(as_dict(asset.get("runtime_materialization_gate")))
        metrics = dict(as_dict(gate.get("metrics")))
        metrics["materialization_with_observer_identity_count"] = sum(
            1 for row in materializations if as_list(row.get("observer_binding_refs"))
        )
        metrics["materialization_observer_binding_ref_count"] = sum(
            len(as_list(row.get("observer_binding_refs")))
            for row in materializations
        )
        gate["metrics"] = metrics
        asset["runtime_materialization_gate"] = gate
        model["runtime_materialization_gate"] = dict(gate)
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "observer_binding_identity_projection_enabled": True,
            "observer_identity_compiled_from_oracle_templates": True,
            "observer_identity_requires_materialization_draft": True,
            "observer_identity_cannot_be_erased_by_action_field_projection": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = [
    "project_observer_identities_to_runtime_plans",
    "project_observer_identities_to_materializations",
]
