"""Attach exact Runtime Plan observer identities to admitted formal Probes."""
from __future__ import annotations

from typing import Any, Iterable

from .schema import as_dict, as_list, text, unique_text


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _observer_refs(row: dict[str, Any]) -> list[str]:
    return unique_text(
        as_list(as_dict(row.get("binding_identity_refs")).get("observer_binding_refs"))
    )


def _event_contract_refs(plan: dict[str, Any], observer_refs: set[str]) -> list[str]:
    return unique_text(
        row.get("event_contract_ref")
        for row in _dicts(as_dict(plan.get("oracle_query_templates")).get("templates"))
        if text(row.get("template_kind")) == "SOURCE_EVENT_DELIVERY_OBSERVATION"
        and text(row.get("observer_binding_ref")) in observer_refs
        and text(row.get("event_contract_ref"))
    )


def attach_runtime_observer_lineage(
    asset: dict[str, Any], probes: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Drop observer-identity drift and enrich admitted formal Probe lineage."""
    plans = {
        text(row.get("plan_id")): row
        for row in _dicts(asset.get("runtime_plans"))
        if text(row.get("plan_id"))
    }
    materializations = {
        text(row.get("materialization_id")): row
        for row in _dicts(asset.get("runtime_materializations"))
        if text(row.get("materialization_id"))
    }
    result: list[dict[str, Any]] = []
    for raw in probes:
        if not isinstance(raw, dict):
            continue
        probe = dict(raw)
        if text(probe.get("source")) != "enterprise_understanding_runtime_plan":
            result.append(probe)
            continue
        plan = plans.get(text(probe.get("runtime_plan_ref"))) or {}
        materialization = materializations.get(
            text(probe.get("runtime_materialization_ref"))
        ) or {}
        plan_refs = set(_observer_refs(plan))
        materialization_refs = set(_observer_refs(materialization))
        # Empty on both sides means this scenario has no formal non-HTTP observer.
        # Any one-sided or unequal identity set is drift and cannot produce a Probe.
        if plan_refs != materialization_refs:
            continue
        observer_refs = sorted(plan_refs)
        event_contract_refs = _event_contract_refs(plan, plan_refs)
        probe["observer_binding_refs"] = observer_refs
        probe["formal_event_contract_refs"] = event_contract_refs
        lineage = dict(as_dict(probe.get("knowledge_lineage")))
        lineage["observer_binding_refs"] = observer_refs
        lineage["formal_event_contract_refs"] = event_contract_refs
        lineage["observer_identity_materialization_match"] = True
        probe["knowledge_lineage"] = lineage
        evidence = unique_text(
            [
                *as_list(probe.get("evidence_requirements")),
                *(
                    [
                        "formal_event_contract",
                        "formal_event_observer_binding",
                        "event_observation_receipt",
                    ]
                    if event_contract_refs
                    else []
                ),
            ]
        )
        probe["evidence_requirements"] = evidence
        result.append(probe)
    return result


__all__ = ["attach_runtime_observer_lineage"]
