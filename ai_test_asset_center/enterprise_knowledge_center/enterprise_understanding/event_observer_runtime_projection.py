"""Project formal event observers into Runtime Plan and Materialization drafts.

This extension follows the same post-core projection pattern as approved database
observers. It removes only the generic 'oracle unresolved' gap when an exact formal
event ObserverBinding exists. All request, credential, cleanup and environment gaps
remain authoritative and closed.
"""
from __future__ import annotations

from typing import Any

from .schema import as_dict, as_list, stable_id, text, unique_text

_EVENT_KIND = "SOURCE_EVENT_DELIVERY_OBSERVER"
_ORACLE_UNRESOLVED = "RUNTIME_PLAN_ORACLE_TEMPLATE_UNRESOLVED"


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _event_observers(contract: dict[str, Any]) -> list[dict[str, Any]]:
    oracle = as_dict(contract.get("oracle_plan"))
    result: dict[str, dict[str, Any]] = {}
    for slot in _dicts(oracle.get("effect_observers")):
        for candidate in _dicts(slot.get("bindings")):
            if text(candidate.get("binding_kind")) != _EVENT_KIND:
                continue
            ref = text(candidate.get("observer_binding_id"))
            if ref:
                result[ref] = candidate
    return list(result.values())


def _event_template(plan: dict[str, Any], observer: dict[str, Any]) -> dict[str, Any]:
    observer_ref = text(observer.get("observer_binding_id"))
    return {
        "template_id": stable_id(
            "runtime_oracle_template",
            plan.get("plan_id"),
            "SOURCE_EVENT_DELIVERY_OBSERVATION",
            observer_ref,
        ),
        "template_kind": "SOURCE_EVENT_DELIVERY_OBSERVATION",
        "phase": "AFTER",
        "observer_binding_ref": observer_ref,
        "observer_id": observer.get("observer_id"),
        "surface": observer.get("surface"),
        "adapter": observer.get("adapter"),
        "event_contract_ref": observer.get("event_contract_ref"),
        "interface_id": observer.get("interface_id"),
        "actor_ref": observer.get("actor_ref"),
        "observer_path": observer.get("observer_path"),
        "expected_event_type": observer.get("expected_event_type"),
        "expected_min_count": observer.get("expected_min_count"),
        "expected_max_count": observer.get("expected_max_count"),
        "observation_window_ms": observer.get("observation_window_ms"),
        "event_contract": dict(as_dict(observer.get("event_contract"))),
        "query_template_compiled": True,
        "observer_request_compiled": False,
        "concrete_assertion_compiled": False,
        "network_call_compiled": False,
        "execution_allowed": False,
    }


def project_event_observers_into_runtime_plans(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    contracts = {
        text(row.get("contract_id")): row
        for row in _dicts(asset.get("scenario_execution_contracts"))
        if text(row.get("contract_id"))
    }
    unknowns = _dicts(asset.get("runtime_plan_unknowns"))
    plans: list[dict[str, Any]] = []
    event_template_count = 0
    resolved_plan_ids: set[str] = set()

    for raw_plan in _dicts(asset.get("runtime_plans")):
        plan = dict(raw_plan)
        plan_id = text(plan.get("plan_id"))
        contract = contracts.get(text(plan.get("execution_contract_ref"))) or {}
        observers = _event_observers(contract)
        if not observers:
            plans.append(plan)
            continue
        oracle = dict(as_dict(plan.get("oracle_query_templates")))
        templates = _dicts(oracle.get("templates"))
        additions = [_event_template(plan, observer) for observer in observers]
        templates = list(
            {
                text(row.get("template_id")): row
                for row in [*templates, *additions]
                if text(row.get("template_id"))
            }.values()
        )
        event_template_count += len(additions)
        oracle.update(
            {
                "templates": templates,
                "oracle_templates_compiled": bool(templates),
                "formal_event_observer_template_count": len(additions),
                "concrete_assertions_compiled": False,
            }
        )
        plan["oracle_query_templates"] = oracle
        snapshot = dict(as_dict(plan.get("snapshot_template")))
        event_refs = [row.get("template_id") for row in additions]
        snapshot["after_snapshot_required"] = True
        snapshot["after_oracle_template_refs"] = unique_text(
            [*as_list(snapshot.get("after_oracle_template_refs")), *event_refs]
        )
        plan["snapshot_template"] = snapshot
        refs = dict(as_dict(plan.get("binding_identity_refs")))
        refs["observer_binding_refs"] = unique_text(
            [
                *as_list(refs.get("observer_binding_refs")),
                *[row.get("observer_binding_id") for row in observers],
            ]
        )
        plan["binding_identity_refs"] = refs
        plan["formal_event_observer_bindings"] = observers
        plan["unresolved_runtime_plan_semantics"] = [
            value
            for value in as_list(plan.get("unresolved_runtime_plan_semantics"))
            if text(value) != _ORACLE_UNRESOLVED
        ]
        resolved_plan_ids.add(plan_id)
        plans.append(plan)

    filtered_unknowns = [
        row
        for row in unknowns
        if not (
            text(row.get("runtime_plan_ref")) in resolved_plan_ids
            and text(row.get("reason_code")) == _ORACLE_UNRESOLVED
        )
    ]
    critical_by_plan = {
        text(row.get("runtime_plan_ref"))
        for row in filtered_unknowns
        if bool(row.get("blocks_runtime_plan"))
    }
    for plan in plans:
        plan_id = text(plan.get("plan_id"))
        if plan_id in resolved_plan_ids and plan_id not in critical_by_plan:
            plan["status"] = "TEMPLATE_READY"
            plan["formal_runtime_plan"] = True

    ready_contracts = [
        row
        for row in contracts.values()
        if text(row.get("status")) == "REQUIREMENTS_READY"
    ]
    ready = sum(1 for row in plans if text(row.get("status")) == "TEMPLATE_READY")
    incomplete = sum(1 for row in plans if text(row.get("status")) == "INCOMPLETE")
    covered = {text(row.get("execution_contract_ref")) for row in plans}
    status = (
        "BLOCKED_RUNTIME_PLAN_INCOMPLETE"
        if incomplete or len(covered) < len(ready_contracts)
        else "PASS"
        if plans
        else "NO_RUNTIME_PLAN_COMPILED"
    )
    gate = dict(as_dict(asset.get("runtime_plan_gate")))
    metrics = dict(as_dict(gate.get("metrics")))
    metrics.update(
        {
            "runtime_plan_count": len(plans),
            "ready_runtime_plan_count": ready,
            "incomplete_runtime_plan_count": incomplete,
            "covered_execution_contract_count": len(covered),
            "ready_execution_contract_count": len(ready_contracts),
            "runtime_plan_unknown_count": len(filtered_unknowns),
            "formal_event_observer_template_count": event_template_count,
            "runtime_plan_with_formal_event_observer_count": len(resolved_plan_ids),
        }
    )
    gate.update(
        {
            "status": status,
            "entry_allowed": status == "PASS",
            "runtime_plan_ready": status == "PASS",
            "execution_allowed": False,
            "metrics": metrics,
            "formal_event_observer_projection_enabled": True,
            "event_observer_network_call_compiled": False,
        }
    )
    relationships: list[dict[str, Any]] = []
    for raw in _dicts(asset.get("runtime_plan_relationships")):
        row = dict(raw)
        plan_ref = text(row.get("from")) if text(row.get("relation")) == "runtime_plan_to_interface" else text(row.get("to"))
        plan = next((item for item in plans if text(item.get("plan_id")) == plan_ref), {})
        accepted = text(as_dict(plan).get("status")) == "TEMPLATE_READY"
        row["status"] = "accepted" if accepted else "candidate"
        row["confidence"] = 1.0 if accepted else 0.0
        relationships.append(row)

    asset["runtime_plans"] = plans
    asset["runtime_plan_unknowns"] = filtered_unknowns
    asset["runtime_plan_gate"] = gate
    asset["runtime_plan_relationships"] = relationships
    model["runtime_plans"] = [dict(row) for row in plans]
    model["runtime_plan_unknowns"] = [dict(row) for row in filtered_unknowns]
    model["runtime_plan_gate"] = dict(gate)
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "formal_event_observer_runtime_plan_projection_enabled": True,
            "event_observer_uses_existing_registered_adapter": True,
            "event_observer_topic_or_broker_inference_allowed": False,
        }
    )
    asset["governance"] = governance
    return asset


def project_event_observers_into_materializations(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    plans = {
        text(row.get("plan_id")): row
        for row in _dicts(asset.get("runtime_plans"))
        if text(row.get("plan_id"))
    }
    materializations: list[dict[str, Any]] = []
    draft_count = 0
    for raw_materialization in _dicts(asset.get("runtime_materializations")):
        materialization = dict(raw_materialization)
        plan = plans.get(text(materialization.get("runtime_plan_ref"))) or {}
        templates = [
            row
            for row in _dicts(as_dict(plan.get("oracle_query_templates")).get("templates"))
            if text(row.get("template_kind")) == "SOURCE_EVENT_DELIVERY_OBSERVATION"
        ]
        drafts = _dicts(materialization.get("assertion_drafts"))
        additions: list[dict[str, Any]] = []
        for template in templates:
            additions.append(
                {
                    "draft_id": stable_id(
                        "assertion_draft",
                        materialization.get("materialization_id"),
                        template.get("template_id"),
                    ),
                    "draft_kind": "SOURCE_EVENT_DELIVERY_ASSERTION_DRAFT",
                    "phase": "AFTER",
                    "observer_binding_ref": template.get("observer_binding_ref"),
                    "observer_id": template.get("observer_id"),
                    "surface": template.get("surface"),
                    "adapter": template.get("adapter"),
                    "event_contract_ref": template.get("event_contract_ref"),
                    "interface_id": template.get("interface_id"),
                    "actor_ref": template.get("actor_ref"),
                    "event_contract": dict(as_dict(template.get("event_contract"))),
                    "observer_request_compiled": False,
                    "assertion_executable": False,
                    "network_call_allowed": False,
                }
            )
        drafts = list(
            {
                text(row.get("draft_id")): row
                for row in [*drafts, *additions]
                if text(row.get("draft_id"))
            }.values()
        )
        draft_count += len(additions)
        materialization["assertion_drafts"] = drafts
        refs = dict(as_dict(materialization.get("binding_identity_refs")))
        refs["observer_binding_refs"] = unique_text(
            [
                *as_list(refs.get("observer_binding_refs")),
                *[row.get("observer_binding_ref") for row in additions],
            ]
        )
        materialization["binding_identity_refs"] = refs
        materialization["formal_event_assertion_draft_count"] = len(additions)
        materializations.append(materialization)

    asset["runtime_materializations"] = materializations
    model["runtime_materializations"] = [dict(row) for row in materializations]
    gate = dict(as_dict(asset.get("runtime_materialization_gate")))
    metrics = dict(as_dict(gate.get("metrics")))
    metrics["formal_event_assertion_draft_count"] = draft_count
    gate["metrics"] = metrics
    gate["formal_event_assertions_executable"] = False
    asset["runtime_materialization_gate"] = gate
    model["runtime_materialization_gate"] = dict(gate)
    return asset


__all__ = [
    "project_event_observers_into_runtime_plans",
    "project_event_observers_into_materializations",
]
