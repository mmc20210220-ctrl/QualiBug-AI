"""Prepare governed Scenario IR, Execution Contracts, Runtime Plans and materialization drafts."""
from __future__ import annotations

from typing import Any

from .runtime_materialization_governance import (
    project_governed_runtime_materializations_to_asset,
)
from .runtime_plan_governance import project_governed_runtime_plans_to_asset
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
    """Project one governed interface authority through non-executable runtime drafts.

    The projection never changes behavior semantics, reads a secret, sends a request, executes a
    query or enables execution. Request locations are copied only from the source-declared
    interface contract retained on the knowledge asset.
    """
    bindings = {
        text(row.get("binding_id")): row
        for row in as_list(model.get("behavior_implementation_bindings"))
        if isinstance(row, dict) and text(row.get("binding_id"))
    }
    interfaces = {
        text(row.get("interface_id")): row
        for row in as_list(asset.get("interfaces"))
        if isinstance(row, dict) and text(row.get("interface_id"))
    }
    scenarios: list[dict[str, Any]] = []
    projected_count = 0
    location_metadata_count = 0
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
            interface = as_dict(interfaces.get(text(api.get("interface_id"))))
            for key in (
                "runtime_contract_schema",
                "parameter_contracts",
                "request_body_fields",
                "request_body_media_types",
                "request_body_required",
                "response_contracts",
                "security_requirements",
                "request_contract_locations_preserved",
                "credential_values_retained",
            ):
                if key in interface:
                    action[key] = interface.get(key)
            action["contract_field_locations_resolved"] = bool(
                interface.get("request_contract_locations_preserved")
            )
            if action["contract_field_locations_resolved"]:
                location_metadata_count += 1
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
            "scenario_source_declared_location_metadata_count": location_metadata_count,
            "scenario_runtime_contract_metadata_retains_secret_values": False,
        }
    )
    asset["governance"] = governance
    project_scenario_execution_contracts(asset, model)
    project_governed_runtime_plans_to_asset(asset, model)
    project_governed_runtime_materializations_to_asset(asset, model)
    install_scenario_execution_probe_guard()
    governance = as_dict(asset.get("governance"))
    governance.update(
        {
            "legacy_probe_generation_requires_runtime_plan_gate": True,
            "legacy_probe_generation_requires_runtime_materialization_gate": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["project_governed_scenario_execution_contracts"]
