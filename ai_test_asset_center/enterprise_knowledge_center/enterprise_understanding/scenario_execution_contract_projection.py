"""Prepare governed Scenario IR inputs and project Scenario Execution Contract v1."""
from __future__ import annotations

from typing import Any

from .schema import as_dict, as_list, text, unique_text
from .scenario_execution_contract import project_scenario_execution_contracts
from .scenario_execution_probe_guard import install_scenario_execution_probe_guard


def _authoritative_api_binding(binding: dict[str, Any]) -> dict[str, Any]:
    primary = text(binding.get("primary_api_interface_ref"))
    rows = [
        dict(row)
        for row in as_list(binding.get("api_operation_bindings"))
        if isinstance(row, dict)
        and bool(row.get("authoritative"))
        and text(row.get("status")) == "BOUND"
    ]
    if primary:
        selected = [row for row in rows if text(row.get("interface_id")) == primary]
        if len(selected) == 1:
            return selected[0]
    return rows[0] if len(rows) == 1 else {}


def project_governed_scenario_execution_contracts(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    """Copy governed interface fields into Scenario IR, then compile requirements.

    This is a projection only. It never changes behavior semantics, chooses field locations,
    materializes values or enables execution.
    """
    bindings = {
        text(row.get("binding_id")): row
        for row in as_list(model.get("behavior_implementation_bindings"))
        if isinstance(row, dict) and text(row.get("binding_id"))
    }
    scenarios: list[dict[str, Any]] = []
    projected_count = 0
    for raw in as_list(asset.get("scenario_ir") or model.get("scenario_ir")):
        if not isinstance(raw, dict):
            continue
        scenario = dict(raw)
        binding = as_dict(bindings.get(text(scenario.get("implementation_binding_ref"))))
        api = _authoritative_api_binding(binding)
        action = dict(as_dict(scenario.get("action_entry")))
        if api and text(api.get("interface_id")) == text(action.get("interface_id")):
            fields = unique_text(as_list(api.get("contract_fields")))
            action["contract_fields"] = fields
            action["contract_fields_derivation"] = "GOVERNED_IMPLEMENTATION_BINDING"
            action["contract_field_locations_resolved"] = False
            scenario["action_entry"] = action
            projected_count += 1
        scenarios.append(scenario)
    asset["scenario_ir"] = scenarios
    model["scenario_ir"] = [dict(row) for row in scenarios]
    governance = as_dict(asset.get("governance"))
    governance.update(
        {
            "scenario_contract_fields_use_governed_binding_authority": True,
            "scenario_contract_fields_reparsed_from_documents": False,
            "scenario_request_field_locations_inferred": False,
            "scenario_contract_field_projection_count": projected_count,
        }
    )
    asset["governance"] = governance
    project_scenario_execution_contracts(asset, model)
    install_scenario_execution_probe_guard()
    return asset


__all__ = ["project_governed_scenario_execution_contracts"]
