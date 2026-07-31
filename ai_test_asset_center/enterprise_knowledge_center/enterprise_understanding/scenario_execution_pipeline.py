"""Governed Scenario IR to Runtime Materialization composition pipeline."""
from __future__ import annotations

from typing import Any

from ..database_observer_runtime_materialization_projection import (
    project_database_observer_runtime_materializations,
)
from ..database_observer_runtime_plan_projection import (
    project_database_observers_into_runtime_plans,
)
from .binding_identity_asset_projection import finalize_binding_identity_projection
from .binding_identity_projection import (
    project_binding_identities_to_execution_contracts,
    project_binding_identities_to_materializations,
    project_binding_identities_to_runtime_plans,
    project_binding_identities_to_scenario_ir,
)
from .binding_identity_runtime_closure import (
    close_execution_contract_binding_identities,
    close_materialization_binding_identities,
    close_runtime_plan_binding_identities,
)
from .contract_field_identity_audit import enforce_exact_contract_field_identity
from .event_observer_runtime_projection import (
    project_event_observers_into_materializations,
    project_event_observers_into_runtime_plans,
)
from .event_observer_scenario_projection import (
    project_event_requirements_to_execution_contracts,
    project_event_requirements_to_scenarios,
)
from .observer_binding_identity_projection import (
    project_observer_identities_to_materializations,
    project_observer_identities_to_runtime_plans,
)
from .runtime_materialization_security import (
    project_secure_runtime_materializations_to_asset,
)
from .runtime_plan_governance import project_governed_runtime_plans_to_asset
from .schema import as_dict, as_list, text, unique_text
from .scenario_execution_contract import project_scenario_execution_contracts


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
    """Run the single governed non-executable scenario-to-runtime composition root."""
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
        binding = as_dict(
            bindings.get(text(scenario.get("implementation_binding_ref")))
        )
        api = _authoritative_api_binding(binding)
        action = dict(as_dict(scenario.get("action_entry")))
        if api and text(api.get("interface_id")) == text(action.get("interface_id")):
            action["contract_fields"] = unique_text(as_list(api.get("contract_fields")))
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

    # Event contracts are semantic expectations as well as observer definitions.
    # Project their source-declared type/count/window before any execution contract
    # is compiled so the oracle is visible at every downstream layer.
    project_event_requirements_to_scenarios(asset, model)
    project_binding_identities_to_scenario_ir(asset, model)
    governance = as_dict(asset.get("governance"))
    governance.update(
        {
            "scenario_contract_fields_use_governed_binding_authority": True,
            "scenario_contract_fields_reparsed_from_documents": False,
            "scenario_request_field_locations_inferred": False,
            "scenario_contract_field_projection_count": projected_count,
            "scenario_source_declared_location_metadata_count": location_metadata_count,
            "scenario_runtime_contract_metadata_retains_secret_values": False,
            "probe_admission_is_final_composition_responsibility": True,
            "binding_identity_graph_is_single_downstream_authority": True,
            "downstream_binding_reselection_allowed": False,
        }
    )
    asset["governance"] = governance

    project_scenario_execution_contracts(asset, model)
    project_event_requirements_to_execution_contracts(asset, model)
    project_binding_identities_to_execution_contracts(asset, model)
    close_execution_contract_binding_identities(asset, model)

    project_governed_runtime_plans_to_asset(asset, model)
    project_event_observers_into_runtime_plans(asset, model)
    project_binding_identities_to_runtime_plans(asset, model)
    project_observer_identities_to_runtime_plans(asset, model)
    close_runtime_plan_binding_identities(asset, model)

    # Approved database and source-event observers extend the same runtime plan.
    # Generic action/field projection runs first; observer identity is compiled
    # afterwards so an additive observer ref can never be erased.
    project_database_observers_into_runtime_plans(asset, model)
    project_event_observers_into_runtime_plans(asset, model)
    project_binding_identities_to_runtime_plans(asset, model)
    project_observer_identities_to_runtime_plans(asset, model)
    close_runtime_plan_binding_identities(asset, model)

    project_secure_runtime_materializations_to_asset(asset, model)
    project_database_observer_runtime_materializations(asset, model)
    project_event_observers_into_materializations(asset, model)
    project_binding_identities_to_materializations(asset, model)
    project_observer_identities_to_materializations(asset, model)
    close_materialization_binding_identities(asset, model)

    # A copied reference cannot validate itself. The final authority compares every
    # selected field to its original semantic source and complete interface schema path.
    enforce_exact_contract_field_identity(asset, model)
    finalize_binding_identity_projection(asset, model)

    governance = as_dict(asset.get("governance"))
    governance.update(
        {
            "legacy_probe_generation_requires_runtime_plan_gate": True,
            "legacy_probe_generation_requires_runtime_materialization_gate": True,
            "runtime_projection_mutates_probe_compiler": False,
            "approved_database_observer_projection_precedes_runtime_materialization": True,
            "approved_database_observer_phase_projection_follows_materialization": True,
            "formal_event_observer_projection_precedes_runtime_materialization": True,
            "formal_event_observer_assertion_draft_follows_materialization": True,
            "formal_event_semantics_projected_before_execution_contract": True,
            "observer_identity_projection_follows_action_field_projection": True,
            "scenario_contract_runtime_plan_materialization_share_binding_ids": True,
            "formal_ui_contracts_reuse_source_ui_contract_authority": True,
            "formal_event_contracts_reuse_source_event_contract_authority": True,
            "ui_locator_generation_from_labels_allowed": False,
            "event_topic_or_broker_inference_allowed": False,
            "required_request_fields_use_exact_contract_field_binding": True,
            "copied_contract_field_refs_require_source_identity_revalidation": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["project_governed_scenario_execution_contracts"]
