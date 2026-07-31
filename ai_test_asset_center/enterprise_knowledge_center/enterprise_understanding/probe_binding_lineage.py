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


def _event_templates(
    plan: dict[str, Any], observer_refs: set[str]
) -> list[dict[str, Any]]:
    return [
        row
        for row in _dicts(as_dict(plan.get("oracle_query_templates")).get("templates"))
        if text(row.get("template_kind")) == "SOURCE_EVENT_DELIVERY_OBSERVATION"
        and text(row.get("observer_binding_ref")) in observer_refs
    ]


def _event_requirement(template: dict[str, Any]) -> dict[str, Any]:
    contract = as_dict(template.get("event_contract"))
    return {
        "observer_binding_ref": template.get("observer_binding_ref"),
        "event_contract_ref": template.get("event_contract_ref")
        or contract.get("contract_id"),
        "expected_event_type": template.get("expected_event_type")
        or contract.get("expected_event_type"),
        "expected_min_count": template.get("expected_min_count")
        if template.get("expected_min_count") is not None
        else contract.get("expected_min_count"),
        "expected_max_count": template.get("expected_max_count")
        if template.get("expected_max_count") is not None
        else contract.get("expected_max_count"),
        "observation_window_ms": template.get("observation_window_ms")
        if template.get("observation_window_ms") is not None
        else contract.get("observation_window_ms"),
        "source_declared": True,
    }


def _event_assertion_text(requirement: dict[str, Any]) -> str:
    event_type = text(requirement.get("expected_event_type")) or "DECLARED_EVENT"
    minimum = requirement.get("expected_min_count")
    maximum = requirement.get("expected_max_count")
    window = requirement.get("observation_window_ms")
    count = (
        str(minimum)
        if minimum is not None and minimum == maximum
        else f"{minimum}..{maximum}"
        if minimum is not None or maximum is not None
        else "DECLARED_RANGE"
    )
    return f"event={event_type},count={count},window_ms={window}"


def _append_assertion(base: Any, additions: list[str]) -> str:
    values = unique_text([text(base), *additions])
    return "；".join(values)


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
        templates = _event_templates(plan, plan_refs)
        event_requirements = [_event_requirement(row) for row in templates]
        event_contract_refs = unique_text(
            row.get("event_contract_ref") for row in event_requirements
        )
        probe["observer_binding_refs"] = observer_refs
        probe["formal_event_contract_refs"] = event_contract_refs
        probe["formal_event_assertion_requirements"] = event_requirements
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
        if event_requirements:
            assertions = [_event_assertion_text(row) for row in event_requirements]
            probe["expected"] = _append_assertion(probe.get("expected"), assertions)
            probe["oracle_assertion"] = _append_assertion(
                probe.get("oracle_assertion"), assertions
            )
            probe["oracle_family"] = "event_delivery_consistency"
            probe["bug_signal"] = (
                "来源声明的事件类型、相关性、投递数量或观察窗口与运行时观察不一致。"
            )
        result.append(probe)
    return result


__all__ = ["attach_runtime_observer_lineage"]
