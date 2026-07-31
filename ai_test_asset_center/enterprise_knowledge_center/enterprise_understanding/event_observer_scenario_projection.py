"""Project source-declared event expectations into Scenario and Execution Contracts."""
from __future__ import annotations

from typing import Any

from .schema import as_dict, as_list, dedupe_evidence, text, unique_text

_EVENT_KIND = "SOURCE_EVENT_DELIVERY_OBSERVER"


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _event_observers(binding: dict[str, Any]) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    direct = _dicts(binding.get("formal_event_observer_bindings"))
    candidates = [
        *direct,
        *[
            candidate
            for slot in _dicts(binding.get("effect_observer_bindings"))
            for candidate in _dicts(slot.get("bindings"))
            if text(candidate.get("binding_kind")) == _EVENT_KIND
        ],
    ]
    for row in candidates:
        observer_ref = text(row.get("observer_binding_id"))
        if observer_ref:
            result[observer_ref] = row
    return list(result.values())


def _event_requirement(observer: dict[str, Any]) -> dict[str, Any]:
    contract = as_dict(observer.get("event_contract"))
    return {
        "observer_binding_ref": observer.get("observer_binding_id"),
        "event_contract_ref": observer.get("event_contract_ref")
        or contract.get("contract_id"),
        "interface_id": observer.get("interface_id"),
        "actor_ref": observer.get("actor_ref"),
        "expected_event_type": observer.get("expected_event_type")
        or contract.get("expected_event_type"),
        "expected_min_count": observer.get("expected_min_count")
        if observer.get("expected_min_count") is not None
        else contract.get("expected_min_count"),
        "expected_max_count": observer.get("expected_max_count")
        if observer.get("expected_max_count") is not None
        else contract.get("expected_max_count"),
        "observation_window_ms": observer.get("observation_window_ms")
        if observer.get("observation_window_ms") is not None
        else contract.get("observation_window_ms"),
        "correlation_source": dict(
            as_dict(observer.get("correlation_source") or contract.get("correlation_source"))
        ),
        "assertion_kind": observer.get("assertion_kind"),
        "risk_family": observer.get("risk_family"),
        "source_declared": True,
        "concrete_assertion_compiled": False,
        "execution_allowed": False,
    }


def project_event_requirements_to_scenarios(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    bindings = {
        text(row.get("binding_id")): row
        for row in _dicts(model.get("behavior_implementation_bindings"))
        if text(row.get("binding_id"))
    }
    scenarios: list[dict[str, Any]] = []
    scenario_count = 0
    requirement_count = 0
    for raw_scenario in _dicts(asset.get("scenario_ir") or model.get("scenario_ir")):
        scenario = dict(raw_scenario)
        binding = bindings.get(text(scenario.get("implementation_binding_ref"))) or {}
        observers = _event_observers(binding)
        if observers:
            requirements = [_event_requirement(row) for row in observers]
            requirements = list(
                {
                    text(row.get("observer_binding_ref")): row
                    for row in requirements
                    if text(row.get("observer_binding_ref"))
                }.values()
            )
            expected = dict(as_dict(scenario.get("expected_outcome")))
            expected["event_effect_requirements"] = requirements
            expected["event_oracle_level"] = (
                "SOURCE_DECLARED_EVENT_DELIVERY_CONTRACT"
            )
            expected["concrete_event_assertion_compiled"] = False
            scenario["expected_outcome"] = expected
            scenario["coverage_dimensions"] = unique_text(
                [*as_list(scenario.get("coverage_dimensions")), "EVENT_DELIVERY"]
            )
            scenario["formal_event_observer_bindings"] = observers
            scenario["evidence"] = dedupe_evidence(
                [
                    *as_list(scenario.get("evidence")),
                    *[
                        evidence
                        for observer in observers
                        for evidence in as_list(observer.get("evidence"))
                        if isinstance(evidence, dict)
                    ],
                ]
            )
            scenario_count += 1
            requirement_count += len(requirements)
        scenarios.append(scenario)
    asset["scenario_ir"] = scenarios
    model["scenario_ir"] = [dict(row) for row in scenarios]
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "formal_event_requirements_projected_to_scenario": True,
            "event_requirements_derived_from_source_contract_only": True,
            "event_expected_type_or_count_inference_allowed": False,
            "event_scenario_count": scenario_count,
            "event_scenario_requirement_count": requirement_count,
        }
    )
    asset["governance"] = governance
    return asset


def project_event_requirements_to_execution_contracts(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    scenarios = {
        text(row.get("scenario_id")): row
        for row in _dicts(asset.get("scenario_ir"))
        if text(row.get("scenario_id"))
    }
    contracts: list[dict[str, Any]] = []
    projected = 0
    for raw_contract in _dicts(asset.get("scenario_execution_contracts")):
        contract = dict(raw_contract)
        scenario = scenarios.get(text(contract.get("scenario_ref"))) or {}
        expected = as_dict(scenario.get("expected_outcome"))
        requirements = _dicts(expected.get("event_effect_requirements"))
        if requirements:
            oracle = dict(as_dict(contract.get("oracle_plan")))
            oracle["event_effect_requirements"] = requirements
            oracle["formal_event_observer_required"] = True
            oracle["concrete_event_assertion_compiled"] = False
            contract["oracle_plan"] = oracle
            snapshot = dict(as_dict(contract.get("snapshot_plan")))
            snapshot["after_snapshot_required"] = True
            snapshot["formal_event_observation_required"] = True
            contract["snapshot_plan"] = snapshot
            contract["formal_event_effect_requirements"] = requirements
            projected += len(requirements)
        contracts.append(contract)
    asset["scenario_execution_contracts"] = contracts
    model["scenario_execution_contracts"] = [dict(row) for row in contracts]
    governance = dict(as_dict(asset.get("governance")))
    governance["formal_event_execution_contract_requirement_count"] = projected
    governance["formal_event_contract_assertions_executable"] = False
    asset["governance"] = governance
    return asset


__all__ = [
    "project_event_requirements_to_scenarios",
    "project_event_requirements_to_execution_contracts",
]
