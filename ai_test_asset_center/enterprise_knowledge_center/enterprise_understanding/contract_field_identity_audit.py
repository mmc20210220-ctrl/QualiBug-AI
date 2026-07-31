"""Audit copied contract-field refs against the original source identity."""
from __future__ import annotations

from typing import Any, Iterable

from .contract_field_identity_policy import exact_contract_field_identity
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


def _fields(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    graph = as_dict(asset.get("binding_identity_graph"))
    return {
        text(row.get("contract_field_binding_id")): row
        for row in _dicts(graph.get("contract_field_bindings"))
        if text(row.get("contract_field_binding_id"))
    }


def _slot_source(slot: dict[str, Any]) -> str:
    explicit = text(slot.get("source_field_candidate"))
    if explicit:
        return explicit
    candidates = unique_text(
        row.get("field")
        for row in _dicts(slot.get("bindings"))
        if text(row.get("binding_kind")) == "API_CONTRACT_FIELD"
        and text(row.get("field"))
    )
    return candidates[0] if len(candidates) == 1 else ""


def _source_indexes(
    model: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    by_value: dict[str, str] = {}
    by_slot: dict[str, str] = {}
    for binding in _dicts(model.get("behavior_implementation_bindings")):
        for slot in [
            *_dicts(binding.get("condition_observer_bindings")),
            *_dicts(binding.get("effect_observer_bindings")),
        ]:
            source = _slot_source(slot)
            value_ref = text(slot.get("runtime_value_binding_id"))
            slot_ref = text(slot.get("slot_ref"))
            if value_ref and source:
                by_value[value_ref] = source
            if slot_ref and source:
                by_slot[slot_ref] = source
    return by_value, by_slot


def _source_for_row(
    row: dict[str, Any],
    *,
    by_value: dict[str, str],
    by_slot: dict[str, str],
) -> str:
    value_ref = text(row.get("runtime_value_binding_ref"))
    slot_ref = text(row.get("source_slot_ref") or row.get("slot_ref"))
    return text(
        by_value.get(value_ref)
        or by_slot.get(slot_ref)
        or row.get("field_candidate")
        or row.get("source_field_candidate")
        or row.get("field")
    )


def _unknown(scope: str, scope_ref: str, reason: str, **details: Any) -> dict[str, Any]:
    return {
        "unknown_id": stable_id("contract_field_identity_unknown", scope, scope_ref, reason, details),
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
    metrics["strict_contract_field_identity_unknown_count"] = count
    gate["metrics"] = metrics
    asset[key] = gate
    model[key] = dict(gate)


def _ref_is_exact(
    fields: dict[str, dict[str, Any]],
    ref: str,
    *,
    interface_id: str,
    location: str,
    source: str,
) -> bool:
    return bool(
        ref
        and exact_contract_field_identity(
            fields.get(ref) or {},
            interface_id=interface_id,
            location=location,
            source_field=source,
        )
    )


def _audit_implementation(
    asset: dict[str, Any], model: dict[str, Any]
) -> list[dict[str, Any]]:
    fields = _fields(asset)
    unknowns: list[dict[str, Any]] = []
    bindings: list[dict[str, Any]] = []
    value_refs: dict[str, list[str]] = {}
    for raw_binding in _dicts(model.get("behavior_implementation_bindings")):
        binding = dict(raw_binding)
        binding_ref = text(binding.get("binding_id"))
        interface_id = text(binding.get("primary_api_interface_ref"))
        for key in ("condition_observer_bindings", "effect_observer_bindings"):
            slots: list[dict[str, Any]] = []
            for raw_slot in _dicts(binding.get(key)):
                slot = dict(raw_slot)
                source = _slot_source(slot)
                accepted: list[str] = []
                candidates: list[dict[str, Any]] = []
                for raw_candidate in _dicts(slot.get("bindings")):
                    candidate = dict(raw_candidate)
                    if text(candidate.get("binding_kind")) != "API_CONTRACT_FIELD":
                        candidates.append(candidate)
                        continue
                    ref = text(candidate.get("contract_field_binding_ref"))
                    field = fields.get(ref) or {}
                    location = text(candidate.get("contract_field_location") or field.get("location")).upper()
                    candidate_interface = text(candidate.get("interface_id") or interface_id)
                    if _ref_is_exact(
                        fields,
                        ref,
                        interface_id=candidate_interface,
                        location=location,
                        source=source or text(candidate.get("field")),
                    ):
                        accepted.append(ref)
                        candidate["contract_field_identity_status"] = "BOUND_EXACT"
                    elif ref:
                        unknowns.append(
                            _unknown(
                                "IMPLEMENTATION_BINDING",
                                binding_ref,
                                "IMPLEMENTATION_CONTRACT_FIELD_IDENTITY_NOT_EXACT",
                                slot_ref=slot.get("slot_ref"),
                                source_field_candidate=source or candidate.get("field"),
                                selected_contract_field_binding_ref=ref,
                                selected_location=location,
                                selected_schema_path=field.get("schema_path"),
                            )
                        )
                        candidate.pop("contract_field_binding_ref", None)
                        candidate["contract_field_identity_status"] = "REJECTED_NOT_EXACT"
                    candidates.append(candidate)
                slot["bindings"] = candidates
                slot["contract_field_binding_refs"] = unique_text(accepted)
                value_ref = text(slot.get("runtime_value_binding_id"))
                if value_ref:
                    value_refs[value_ref] = unique_text(accepted)
                slots.append(slot)
            binding[key] = slots
        bindings.append(binding)
    model["behavior_implementation_bindings"] = bindings
    asset["behavior_implementation_bindings"] = [dict(row) for row in bindings]

    graph = dict(as_dict(asset.get("binding_identity_graph")))
    runtime_values: list[dict[str, Any]] = []
    for raw_value in _dicts(graph.get("runtime_value_bindings")):
        value = dict(raw_value)
        value_ref = text(value.get("runtime_value_binding_id"))
        if value_ref in value_refs:
            value["contract_field_binding_refs"] = value_refs[value_ref]
        runtime_values.append(value)
    graph["runtime_value_bindings"] = runtime_values
    asset["binding_identity_graph"] = graph
    model["binding_identity_graph"] = dict(graph)
    return unknowns


def _audit_execution_contracts(
    asset: dict[str, Any], model: dict[str, Any]
) -> list[dict[str, Any]]:
    fields = _fields(asset)
    by_value, by_slot = _source_indexes(model)
    unknowns: list[dict[str, Any]] = []
    contracts: list[dict[str, Any]] = []
    for raw_contract in _dicts(asset.get("scenario_execution_contracts")):
        contract = dict(raw_contract)
        contract_ref = text(contract.get("contract_id"))
        interface_id = text(as_dict(contract.get("action_contract")).get("interface_id"))
        request = dict(as_dict(contract.get("request_contract")))
        for key in ("path_parameter_requirements", "request_field_requirements"):
            requirements: list[dict[str, Any]] = []
            for raw_requirement in _dicts(request.get(key)):
                requirement = dict(raw_requirement)
                ref = text(requirement.get("contract_field_binding_ref"))
                field = fields.get(ref) or {}
                location = text(requirement.get("location") or field.get("location")).upper()
                source = _source_for_row(requirement, by_value=by_value, by_slot=by_slot)
                if _ref_is_exact(fields, ref, interface_id=interface_id, location=location, source=source):
                    requirement["binding_identity_authority"] = "SOURCE_INTERFACE_LOCATION_COMPLETE_SCHEMA_PATH"
                elif ref:
                    unknowns.append(
                        _unknown(
                            "SCENARIO_EXECUTION_CONTRACT",
                            contract_ref,
                            "EXECUTION_CONTRACT_FIELD_IDENTITY_NOT_EXACT",
                            source_field_candidate=source,
                            selected_contract_field_binding_ref=ref,
                            selected_location=location,
                            selected_schema_path=field.get("schema_path"),
                        )
                    )
                    requirement.pop("contract_field_binding_ref", None)
                    requirement["binding_identity_status"] = "REJECTED_NOT_EXACT"
                    contract["status"] = "INCOMPLETE"
                    contract["formal_execution_contract"] = False
                requirements.append(requirement)
            request[key] = requirements
        contract["request_contract"] = request
        contracts.append(contract)
    asset["scenario_execution_contracts"] = contracts
    model["scenario_execution_contracts"] = [dict(row) for row in contracts]
    if unknowns:
        _close_gate(
            asset,
            model,
            "scenario_execution_contract_gate",
            status="BLOCKED_EXECUTION_CONTRACT_FIELD_IDENTITY_NOT_EXACT",
            ready_field="execution_contract_ready",
            count=len(unknowns),
        )
    return unknowns


def _plan_slots(plan: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    request = as_dict(plan.get("request_template"))
    for collection in _REQUEST_COLLECTIONS:
        for slot in _dicts(request.get(collection)):
            yield collection, slot


def _audit_runtime_plans(
    asset: dict[str, Any], model: dict[str, Any]
) -> list[dict[str, Any]]:
    fields = _fields(asset)
    by_value, by_slot = _source_indexes(model)
    unknowns: list[dict[str, Any]] = []
    plans: list[dict[str, Any]] = []
    for raw_plan in _dicts(asset.get("runtime_plans")):
        plan = dict(raw_plan)
        plan_ref = text(plan.get("plan_id"))
        interface_id = text(as_dict(plan.get("action_entry")).get("interface_id"))
        request = dict(as_dict(plan.get("request_template")))
        for collection in _REQUEST_COLLECTIONS:
            slots: list[dict[str, Any]] = []
            for raw_slot in _dicts(request.get(collection)):
                slot = dict(raw_slot)
                ref = text(slot.get("contract_field_binding_ref"))
                field = fields.get(ref) or {}
                location = text(slot.get("location") or field.get("location")).upper()
                source_probe = {
                    **slot,
                    "source_slot_ref": as_dict(slot.get("value_source")).get("source_slot_ref"),
                }
                source = _source_for_row(source_probe, by_value=by_value, by_slot=by_slot)
                if _ref_is_exact(fields, ref, interface_id=interface_id, location=location, source=source):
                    slot["binding_identity_authority"] = "SOURCE_INTERFACE_LOCATION_COMPLETE_SCHEMA_PATH"
                elif ref:
                    unknowns.append(
                        _unknown(
                            "RUNTIME_PLAN",
                            plan_ref,
                            "RUNTIME_PLAN_FIELD_IDENTITY_NOT_EXACT",
                            slot_id=slot.get("slot_id"),
                            source_field_candidate=source,
                            selected_contract_field_binding_ref=ref,
                            selected_location=location,
                            selected_schema_path=field.get("schema_path"),
                        )
                    )
                    slot.pop("contract_field_binding_ref", None)
                    slot["binding_identity_status"] = "REJECTED_NOT_EXACT"
                    plan["status"] = "INCOMPLETE"
                    plan["formal_runtime_plan"] = False
                slots.append(slot)
            request[collection] = slots
        plan["request_template"] = request
        refs = dict(as_dict(plan.get("binding_identity_refs")))
        refs["contract_field_binding_refs"] = unique_text(
            slot.get("contract_field_binding_ref") for _collection, slot in _plan_slots(plan)
        )
        plan["binding_identity_refs"] = refs
        plans.append(plan)
    asset["runtime_plans"] = plans
    model["runtime_plans"] = [dict(row) for row in plans]
    if unknowns:
        _close_gate(
            asset,
            model,
            "runtime_plan_gate",
            status="BLOCKED_RUNTIME_PLAN_FIELD_IDENTITY_NOT_EXACT",
            ready_field="runtime_plan_ready",
            count=len(unknowns),
        )
    return unknowns


def _audit_materializations(
    asset: dict[str, Any], model: dict[str, Any]
) -> list[dict[str, Any]]:
    plans = {
        text(row.get("plan_id")): row
        for row in _dicts(asset.get("runtime_plans"))
        if text(row.get("plan_id"))
    }
    unknowns: list[dict[str, Any]] = []
    materializations: list[dict[str, Any]] = []
    for raw_materialization in _dicts(asset.get("runtime_materializations")):
        materialization = dict(raw_materialization)
        materialization_ref = text(materialization.get("materialization_id"))
        plan = plans.get(text(materialization.get("runtime_plan_ref"))) or {}
        expected = {
            text(slot.get("slot_id")): text(slot.get("contract_field_binding_ref"))
            for _collection, slot in _plan_slots(plan)
            if text(slot.get("slot_id"))
        }
        values: list[dict[str, Any]] = []
        for raw_value in _dicts(materialization.get("request_value_bindings")):
            value = dict(raw_value)
            expected_ref = expected.get(text(value.get("slot_id")), "")
            actual_ref = text(value.get("contract_field_binding_ref"))
            if expected_ref and actual_ref == expected_ref:
                value["binding_identity_authority"] = "RUNTIME_PLAN_EXACT_FIELD_IDENTITY"
            elif actual_ref or expected_ref:
                unknowns.append(
                    _unknown(
                        "RUNTIME_MATERIALIZATION",
                        materialization_ref,
                        "RUNTIME_MATERIALIZATION_FIELD_IDENTITY_NOT_EXACT",
                        slot_id=value.get("slot_id"),
                        expected_contract_field_binding_ref=expected_ref,
                        actual_contract_field_binding_ref=actual_ref,
                    )
                )
                value.pop("contract_field_binding_ref", None)
                value["binding_identity_status"] = "REJECTED_NOT_EXACT"
                materialization["status"] = "INCOMPLETE"
                materialization["formal_runtime_materialization"] = False
            values.append(value)
        materialization["request_value_bindings"] = values
        refs = dict(as_dict(materialization.get("binding_identity_refs")))
        refs["contract_field_binding_refs"] = unique_text(
            row.get("contract_field_binding_ref") for row in values
        )
        materialization["binding_identity_refs"] = refs
        materializations.append(materialization)
    asset["runtime_materializations"] = materializations
    model["runtime_materializations"] = [dict(row) for row in materializations]
    if unknowns:
        _close_gate(
            asset,
            model,
            "runtime_materialization_gate",
            status="BLOCKED_RUNTIME_MATERIALIZATION_FIELD_IDENTITY_NOT_EXACT",
            ready_field="runtime_materialization_ready",
            count=len(unknowns),
        )
    return unknowns


def enforce_exact_contract_field_identity(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    implementation = _audit_implementation(asset, model)
    execution = _audit_execution_contracts(asset, model)
    runtime = _audit_runtime_plans(asset, model)
    materialization = _audit_materializations(asset, model)
    unknowns = [*implementation, *execution, *runtime, *materialization]
    _merge_unknowns(asset, model, unknowns)
    if implementation:
        _close_gate(
            asset,
            model,
            "binding_identity_gate",
            status="BLOCKED_CONTRACT_FIELD_IDENTITY_NOT_EXACT",
            ready_field="binding_identity_ready",
            count=len(unknowns),
        )
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "contract_field_identity_authority_enabled": True,
            "contract_field_identity_requires_original_source_field": True,
            "nested_contract_field_requires_complete_schema_path": True,
            "contract_field_leaf_name_fallback_allowed": False,
            "copied_contract_field_ref_can_self_validate": False,
            "downstream_contract_field_reselection_allowed": False,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["enforce_exact_contract_field_identity"]
