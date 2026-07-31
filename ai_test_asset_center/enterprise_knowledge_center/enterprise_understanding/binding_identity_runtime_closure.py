"""Close exact binding identities across execution contracts and runtime drafts."""
from __future__ import annotations

import re
from typing import Any, Iterable

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


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text(value).lower())


def _field_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return _dicts(as_dict(asset.get("binding_identity_graph")).get("contract_field_bindings"))


def _value_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return _dicts(as_dict(asset.get("binding_identity_graph")).get("runtime_value_bindings"))


def _exact_fields(
    asset: dict[str, Any], interface_id: str, location: str, field: str
) -> list[dict[str, Any]]:
    target = _norm(field)
    if not (interface_id and location and target):
        return []
    return [
        row
        for row in _field_rows(asset)
        if text(row.get("interface_id")) == interface_id
        and text(row.get("location")).upper() == location.upper()
        and target in {_norm(row.get("schema_path")), _norm(row.get("field"))}
    ]


def _value_ref(asset: dict[str, Any], slot_ref: str) -> str:
    matches = [
        text(row.get("runtime_value_binding_id"))
        for row in _value_rows(asset)
        if text(row.get("slot_ref")) == slot_ref
        and text(row.get("runtime_value_binding_id"))
    ]
    return matches[0] if len(matches) == 1 else ""


def _unknown(scope: str, scope_ref: str, reason: str, **details: Any) -> dict[str, Any]:
    return {
        "unknown_id": stable_id("binding_identity_unknown", scope, scope_ref, reason, details),
        "kind": reason,
        "reason_code": reason,
        "scope": scope,
        "scope_ref": scope_ref,
        "blocks_binding_identity": True,
        "execution_allowed": False,
        **details,
    }


def _append_unknowns(
    asset: dict[str, Any], model: dict[str, Any], rows: Iterable[dict[str, Any]]
) -> None:
    merged = list(
        {
            text(row.get("unknown_id")): row
            for row in [*_dicts(asset.get("binding_identity_unknowns")), *rows]
            if isinstance(row, dict) and text(row.get("unknown_id"))
        }.values()
    )
    asset["binding_identity_unknowns"] = merged
    model["binding_identity_unknowns"] = [dict(row) for row in merged]


def close_execution_contract_binding_identities(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    contracts: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    for raw in _dicts(asset.get("scenario_execution_contracts")):
        contract = dict(raw)
        contract_id = text(contract.get("contract_id"))
        action = as_dict(contract.get("action_contract"))
        interface_id = text(action.get("interface_id"))
        request = dict(as_dict(contract.get("request_contract")))
        for key in ("path_parameter_requirements", "request_field_requirements"):
            resolved: list[dict[str, Any]] = []
            for raw_requirement in _dicts(request.get(key)):
                requirement = dict(raw_requirement)
                location = text(requirement.get("location")).upper()
                field = text(requirement.get("field") or requirement.get("field_candidate"))
                existing = text(requirement.get("contract_field_binding_ref"))
                candidates = (
                    [row for row in _field_rows(asset) if text(row.get("contract_field_binding_id")) == existing]
                    if existing
                    else _exact_fields(asset, interface_id, location, field)
                )
                if len(candidates) == 1:
                    selected = candidates[0]
                    requirement["contract_field_binding_ref"] = selected.get("contract_field_binding_id")
                    requirement["field"] = selected.get("schema_path") or field
                    requirement["location"] = selected.get("location")
                    requirement["location_resolution"] = "EXACT_GOVERNED_CONTRACT_FIELD_IDENTITY"
                    requirement["binding_identity_locked"] = True
                else:
                    reason = (
                        "EXECUTION_CONTRACT_FIELD_IDENTITY_UNRESOLVED"
                        if not candidates
                        else "EXECUTION_CONTRACT_FIELD_IDENTITY_AMBIGUOUS"
                    )
                    unknowns.append(
                        _unknown(
                            "SCENARIO_EXECUTION_CONTRACT",
                            contract_id,
                            reason,
                            interface_id=interface_id,
                            field=field,
                            location=location,
                            candidate_contract_field_binding_refs=unique_text(
                                row.get("contract_field_binding_id") for row in candidates
                            ),
                        )
                    )
                    requirement["binding_identity_status"] = "UNRESOLVED" if not candidates else "AMBIGUOUS"
                    contract["status"] = "INCOMPLETE"
                    contract["formal_execution_contract"] = False
                source_slot = text(requirement.get("source_slot_ref") or requirement.get("slot_ref"))
                if source_slot and not text(requirement.get("runtime_value_binding_ref")):
                    value_ref = _value_ref(asset, source_slot)
                    if value_ref:
                        requirement["runtime_value_binding_ref"] = value_ref
                resolved.append(requirement)
            request[key] = resolved
        request["binding_identity_closed"] = not any(
            text(row.get("scope_ref")) == contract_id for row in unknowns
        )
        contract["request_contract"] = request
        contracts.append(contract)
    asset["scenario_execution_contracts"] = contracts
    model["scenario_execution_contracts"] = [dict(row) for row in contracts]
    _append_unknowns(asset, model, unknowns)
    if unknowns:
        gate = dict(as_dict(asset.get("scenario_execution_contract_gate")))
        gate.update(
            {
                "status": "BLOCKED_EXECUTION_CONTRACT_BINDING_IDENTITY_INCOMPLETE",
                "entry_allowed": False,
                "execution_contract_ready": False,
                "execution_allowed": False,
            }
        )
        metrics = dict(as_dict(gate.get("metrics")))
        metrics["binding_identity_unknown_count"] = len(unknowns)
        gate["metrics"] = metrics
        asset["scenario_execution_contract_gate"] = gate
        model["scenario_execution_contract_gate"] = dict(gate)
    return asset


def _plan_slots(plan: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    request = as_dict(plan.get("request_template"))
    for collection in _REQUEST_COLLECTIONS:
        for slot in _dicts(request.get(collection)):
            yield collection, slot


def close_runtime_plan_binding_identities(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    plans: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    for raw in _dicts(asset.get("runtime_plans")):
        plan = dict(raw)
        plan_id = text(plan.get("plan_id"))
        interface_id = text(as_dict(plan.get("action_entry")).get("interface_id"))
        request = dict(as_dict(plan.get("request_template")))
        for collection in _REQUEST_COLLECTIONS:
            resolved: list[dict[str, Any]] = []
            for raw_slot in _dicts(request.get(collection)):
                slot = dict(raw_slot)
                field = text(slot.get("field"))
                location = text(slot.get("location")).upper()
                existing = text(slot.get("contract_field_binding_ref"))
                candidates = (
                    [
                        row
                        for row in _field_rows(asset)
                        if text(row.get("contract_field_binding_id")) == existing
                        and text(row.get("interface_id")) == interface_id
                        and text(row.get("location")).upper() == location
                    ]
                    if existing
                    else _exact_fields(asset, interface_id, location, field)
                )
                if len(candidates) == 1:
                    slot["contract_field_binding_ref"] = candidates[0].get("contract_field_binding_id")
                    slot["binding_identity_locked"] = True
                elif bool(slot.get("required")):
                    reason = (
                        "RUNTIME_PLAN_CONTRACT_FIELD_IDENTITY_UNRESOLVED"
                        if not candidates
                        else "RUNTIME_PLAN_CONTRACT_FIELD_IDENTITY_AMBIGUOUS"
                    )
                    unknowns.append(
                        _unknown(
                            "RUNTIME_PLAN",
                            plan_id,
                            reason,
                            interface_id=interface_id,
                            slot_id=slot.get("slot_id"),
                            field=field,
                            location=location,
                            candidate_contract_field_binding_refs=unique_text(
                                row.get("contract_field_binding_id") for row in candidates
                            ),
                        )
                    )
                    slot["binding_identity_status"] = "UNRESOLVED" if not candidates else "AMBIGUOUS"
                    plan["status"] = "INCOMPLETE"
                    plan["formal_runtime_plan"] = False
                source_slot = text(as_dict(slot.get("value_source")).get("source_slot_ref"))
                if source_slot and not text(slot.get("runtime_value_binding_ref")):
                    value_ref = _value_ref(asset, source_slot)
                    if value_ref:
                        slot["runtime_value_binding_ref"] = value_ref
                resolved.append(slot)
            request[collection] = resolved
        request["binding_identity_closed"] = not any(
            text(row.get("scope_ref")) == plan_id for row in unknowns
        )
        plan["request_template"] = request
        refs = dict(as_dict(plan.get("binding_identity_refs")))
        refs["contract_field_binding_refs"] = unique_text(
            slot.get("contract_field_binding_ref") for _collection, slot in _plan_slots(plan)
        )
        refs["runtime_value_binding_refs"] = unique_text(
            slot.get("runtime_value_binding_ref") for _collection, slot in _plan_slots(plan)
        )
        plan["binding_identity_refs"] = refs
        plans.append(plan)
    asset["runtime_plans"] = plans
    model["runtime_plans"] = [dict(row) for row in plans]
    _append_unknowns(asset, model, unknowns)
    if unknowns:
        gate = dict(as_dict(asset.get("runtime_plan_gate")))
        gate.update(
            {
                "status": "BLOCKED_RUNTIME_PLAN_BINDING_IDENTITY_INCOMPLETE",
                "entry_allowed": False,
                "runtime_plan_ready": False,
                "execution_allowed": False,
            }
        )
        metrics = dict(as_dict(gate.get("metrics")))
        metrics["binding_identity_unknown_count"] = len(unknowns)
        gate["metrics"] = metrics
        asset["runtime_plan_gate"] = gate
        model["runtime_plan_gate"] = dict(gate)
    return asset


def close_materialization_binding_identities(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    plans = {
        text(row.get("plan_id")): row
        for row in _dicts(asset.get("runtime_plans"))
        if text(row.get("plan_id"))
    }
    materializations: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    for raw in _dicts(asset.get("runtime_materializations")):
        materialization = dict(raw)
        materialization_id = text(materialization.get("materialization_id"))
        plan = plans.get(text(materialization.get("runtime_plan_ref"))) or {}
        slots = {
            text(slot.get("slot_id")): slot
            for _collection, slot in _plan_slots(plan)
            if text(slot.get("slot_id"))
        }
        values: list[dict[str, Any]] = []
        for raw_value in _dicts(materialization.get("request_value_bindings")):
            value = dict(raw_value)
            slot = slots.get(text(value.get("slot_id"))) or {}
            expected_field = text(slot.get("contract_field_binding_ref"))
            expected_value = text(slot.get("runtime_value_binding_ref"))
            actual_field = text(value.get("contract_field_binding_ref"))
            actual_value = text(value.get("runtime_value_binding_ref"))
            if expected_field and not actual_field:
                value["contract_field_binding_ref"] = expected_field
                actual_field = expected_field
            if expected_value and not actual_value:
                value["runtime_value_binding_ref"] = expected_value
                actual_value = expected_value
            if expected_field and actual_field != expected_field:
                unknowns.append(
                    _unknown(
                        "RUNTIME_MATERIALIZATION",
                        materialization_id,
                        "RUNTIME_MATERIALIZATION_CONTRACT_FIELD_IDENTITY_DRIFT",
                        slot_id=value.get("slot_id"),
                        expected_contract_field_binding_ref=expected_field,
                        actual_contract_field_binding_ref=actual_field,
                    )
                )
            if expected_value and actual_value != expected_value:
                unknowns.append(
                    _unknown(
                        "RUNTIME_MATERIALIZATION",
                        materialization_id,
                        "RUNTIME_MATERIALIZATION_VALUE_IDENTITY_DRIFT",
                        slot_id=value.get("slot_id"),
                        expected_runtime_value_binding_ref=expected_value,
                        actual_runtime_value_binding_ref=actual_value,
                    )
                )
            value["binding_identity_locked"] = bool(actual_field or actual_value)
            values.append(value)
        materialization["request_value_bindings"] = values
        refs = dict(as_dict(materialization.get("binding_identity_refs")))
        refs["contract_field_binding_refs"] = unique_text(
            row.get("contract_field_binding_ref") for row in values
        )
        refs["runtime_value_binding_refs"] = unique_text(
            row.get("runtime_value_binding_ref") for row in values
        )
        materialization["binding_identity_refs"] = refs
        if any(text(row.get("scope_ref")) == materialization_id for row in unknowns):
            materialization["status"] = "INCOMPLETE"
            materialization["formal_runtime_materialization"] = False
        materializations.append(materialization)
    asset["runtime_materializations"] = materializations
    model["runtime_materializations"] = [dict(row) for row in materializations]
    _append_unknowns(asset, model, unknowns)
    if unknowns:
        gate = dict(as_dict(asset.get("runtime_materialization_gate")))
        gate.update(
            {
                "status": "BLOCKED_RUNTIME_MATERIALIZATION_BINDING_IDENTITY_DRIFT",
                "entry_allowed": False,
                "runtime_materialization_ready": False,
                "execution_allowed": False,
            }
        )
        metrics = dict(as_dict(gate.get("metrics")))
        metrics["binding_identity_unknown_count"] = len(unknowns)
        gate["metrics"] = metrics
        asset["runtime_materialization_gate"] = gate
        model["runtime_materialization_gate"] = dict(gate)
    return asset


__all__ = [
    "close_execution_contract_binding_identities",
    "close_runtime_plan_binding_identities",
    "close_materialization_binding_identities",
]
