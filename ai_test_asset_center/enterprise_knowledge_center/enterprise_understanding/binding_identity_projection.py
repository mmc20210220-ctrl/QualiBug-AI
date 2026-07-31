"""Compile one durable implementation-binding identity graph across the runtime mainline.

This stage does not select an endpoint, infer a request field, manufacture a UI locator,
read a secret or enable execution. It converts already-governed implementation bindings
into stable references and verifies that downstream projections preserve those references.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from ...source_ui_contract_binding import (
    _contracts as _source_ui_contracts,
    _resolve_actor as _resolve_source_ui_actor,
    _resolve_operation as _resolve_source_ui_operation,
    _validated_request as _validated_source_ui_request,
)
from .schema import as_dict, as_list, stable_id, text, unique_text

BINDING_IDENTITY_SCHEMA = "qualibug.implementation-binding-identity-graph.v1"
BINDING_IDENTITY_GATE_SCHEMA = "qualibug.implementation-binding-identity-gate.v1"
_REQUEST_COLLECTIONS = (
    "path_parameters",
    "query_parameters",
    "header_parameters",
    "cookie_parameters",
    "body_fields",
    "form_fields",
)
_LOCATION_BY_COLLECTION = {
    "path_parameters": "PATH",
    "query_parameters": "QUERY",
    "header_parameters": "HEADER",
    "cookie_parameters": "COOKIE",
    "body_fields": "BODY",
    "form_fields": "FORM",
}
_PATH_PARAMETER_RE = re.compile(r"\{([^{}]+)\}")


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text(value).lower())


def _field_names(row: dict[str, Any]) -> set[str]:
    return {
        value
        for raw in (
            row.get("field"),
            row.get("name"),
            row.get("field_path"),
            row.get("schema_path"),
            text(row.get("field")).split(".")[-1],
            text(row.get("name")).split(".")[-1],
            text(row.get("field_path")).split(".")[-1],
            text(row.get("schema_path")).split(".")[-1],
        )
        if (value := _norm(raw))
    }


def _json_pointer(path: str) -> str:
    tokens = [token.replace("[]", "*") for token in text(path).split(".") if token]
    return "/" + "/".join(tokens) if tokens else ""


def _interface_descriptors(interface: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in [
        *_dicts(interface.get("parameter_contracts")),
        *_dicts(interface.get("request_body_fields")),
    ]:
        location = text(raw.get("location")).upper()
        if not location:
            continue
        row = dict(raw)
        row["location"] = location
        rows.append(row)

    declared_path = {
        _norm(row.get("field") or row.get("name"))
        for row in rows
        if text(row.get("location")) == "PATH"
    }
    for name in unique_text(_PATH_PARAMETER_RE.findall(text(interface.get("path")))):
        if _norm(name) in declared_path:
            continue
        rows.append(
            {
                "name": name,
                "field": name,
                "field_path": name,
                "location": "PATH",
                "required": True,
                "schema_type": "UNSPECIFIED",
                "source": "ACTION_PATH_TEMPLATE",
            }
        )
    return rows


def _contract_field_bindings(
    binding_id: str,
    action_surface_ref: str,
    interface: dict[str, Any],
) -> list[dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    interface_id = text(interface.get("interface_id"))
    for descriptor in _interface_descriptors(interface):
        location = text(descriptor.get("location")).upper()
        schema_path = text(
            descriptor.get("schema_path")
            or descriptor.get("field_path")
            or descriptor.get("field")
            or descriptor.get("name")
        )
        if not (interface_id and location and schema_path):
            continue
        media_type = text(descriptor.get("media_type"))
        field_id = stable_id(
            "contract_field_binding",
            binding_id,
            interface_id,
            "REQUEST",
            location,
            schema_path,
            media_type,
        )
        result[field_id] = {
            "contract_field_binding_id": field_id,
            "action_surface_binding_ref": action_surface_ref,
            "interface_id": interface_id,
            "direction": "REQUEST",
            "location": location,
            "field": text(descriptor.get("field") or descriptor.get("name") or schema_path),
            "schema_path": schema_path,
            "json_pointer": text(descriptor.get("json_pointer")) or _json_pointer(schema_path),
            "media_type": media_type,
            "schema_type": text(descriptor.get("schema_type")) or "UNSPECIFIED",
            "format": descriptor.get("format"),
            "required": bool(descriptor.get("required")),
            "enum": as_list(descriptor.get("enum")),
            "source_contract_ref": (
                descriptor.get("descriptor_id")
                or descriptor.get("parameter_id")
                or descriptor.get("field_id")
            ),
            "source_derivation": descriptor.get("source")
            or "SOURCE_DECLARED_INTERFACE_CONTRACT",
            "status": "BOUND",
            "authoritative": True,
            "automatic_alias_inference_allowed": False,
        }
    return sorted(
        result.values(),
        key=lambda row: (
            text(row.get("location")),
            text(row.get("schema_path")),
            text(row.get("contract_field_binding_id")),
        ),
    )


def _action_surface_binding(
    binding: dict[str, Any], api: dict[str, Any]
) -> dict[str, Any]:
    binding_id = text(binding.get("binding_id"))
    interface_id = text(api.get("interface_id"))
    surface_id = stable_id(
        "action_surface_binding",
        binding_id,
        "HTTP_API",
        interface_id,
    )
    return {
        "action_surface_binding_id": surface_id,
        "surface_kind": "HTTP_API",
        "interface_id": interface_id,
        "method": text(api.get("method")).upper(),
        "path": api.get("path"),
        "operation_id": api.get("operation_id"),
        "status": "BOUND",
        "authoritative": True,
        "primary": True,
        "runtime_adapter": "http_api",
        "runtime_supported": True,
        "derivation": api.get("derivation"),
        "source_binding_ref": api.get("binding_id"),
        "evidence": _dicts(api.get("evidence")),
    }


def _adapt_ui_operations(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            **row,
            "id": text(row.get("interface_id")),
            "source_operation_refs": unique_text(
                [
                    row.get("interface_id"),
                    row.get("operation_id"),
                    *as_list(row.get("source_operation_refs")),
                ]
            ),
        }
        for row in _dicts(asset.get("interfaces"))
        if text(row.get("interface_id"))
    ]


def _adapt_ui_actors(asset: dict[str, Any], model: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        *_dicts(model.get("actors")),
        *_dicts(asset.get("actors")),
        *_dicts(asset.get("roles")),
    ]
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        actor_id = text(
            row.get("id")
            or row.get("actor_id")
            or row.get("role_id")
            or row.get("actor_ref")
        )
        if not actor_id:
            continue
        secret_ref = text(
            row.get("credential_secret_ref")
            or row.get("secret_ref")
            or row.get("credential_ref")
        )
        result[actor_id] = {
            **row,
            "id": actor_id,
            "role": row.get("role") or row.get("name") or row.get("actor_name"),
            "role_key": row.get("role_key") or row.get("canonical_name"),
            "credential_secret_ref": secret_ref,
            "runtime_bound": bool(row.get("runtime_bound") or secret_ref),
        }
    return list(result.values())


def _formal_ui_surfaces(
    asset: dict[str, Any],
    model: dict[str, Any],
    *,
    binding: dict[str, Any],
    behavior: dict[str, Any],
    primary_api: dict[str, Any],
    primary_surface_ref: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    operations = _adapt_ui_operations(asset)
    actors = _adapt_ui_actors(asset, model)
    behavior_actor_refs = {
        _norm(value) for value in as_list(behavior.get("actor_refs")) if _norm(value)
    }
    primary_interface_id = text(primary_api.get("interface_id"))
    surfaces: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for contract in _source_ui_contracts(asset):
        contract_id = text(contract.get("contract_id"))
        request, request_reason = _validated_source_ui_request(contract)
        operation, operation_reason = _resolve_source_ui_operation(contract, operations)
        actor, actor_reason = _resolve_source_ui_actor(contract, actors)
        reason = request_reason or operation_reason or actor_reason
        operation_id = text(as_dict(operation).get("id"))
        actor_id = text(as_dict(actor).get("id"))
        if reason:
            continue
        if operation_id != primary_interface_id:
            continue
        actor_names = {
            _norm(actor_id),
            _norm(as_dict(actor).get("role")),
            _norm(as_dict(actor).get("role_key")),
        }
        if behavior_actor_refs and not (behavior_actor_refs & actor_names):
            continue
        assert request is not None and operation is not None and actor is not None
        surface_id = stable_id(
            "action_surface_binding",
            binding.get("binding_id"),
            "UI_BROWSER",
            contract_id,
            actor_id,
        )
        surfaces.append(
            {
                "action_surface_binding_id": surface_id,
                "surface_kind": "UI_BROWSER",
                "ui_contract_ref": contract_id,
                "operation_action_surface_ref": primary_surface_ref,
                "interface_id": primary_interface_id,
                "actor_ref": actor_id,
                "status": "BOUND",
                "authoritative": True,
                "primary": False,
                "runtime_adapter": "ui_browser",
                "runtime_supported": True,
                "locator_authority": "SOURCE_DECLARED_BROWSER_PLAN",
                "executable_locator_available": True,
                "ui_request": request,
                "derivation": "EXACT_FORMAL_UI_CONTRACT_IDENTITY",
                "automatic_locator_generation_allowed": False,
            }
        )
    return surfaces, gaps


def _candidate_identity(
    *,
    binding_id: str,
    slot_ref: str,
    candidate: dict[str, Any],
) -> dict[str, Any]:
    row = dict(candidate)
    identity = (
        row.get("observer_id")
        or row.get("field_id")
        or row.get("interface_id")
        or row.get("ui_contract_ref")
        or row.get("field")
        or row.get("table_id")
    )
    row["observer_binding_id"] = row.get("observer_binding_id") or stable_id(
        "observer_binding",
        binding_id,
        slot_ref,
        row.get("binding_kind"),
        identity,
    )
    return row


def _match_contract_fields(
    candidate: dict[str, Any],
    contract_fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    interface_id = text(candidate.get("interface_id"))
    target_full = _norm(candidate.get("field"))
    target_leaf = _norm(text(candidate.get("field")).split(".")[-1])
    exact_full = [
        row
        for row in contract_fields
        if text(row.get("interface_id")) == interface_id
        and target_full
        and target_full in _field_names(row)
    ]
    if exact_full:
        return exact_full
    return [
        row
        for row in contract_fields
        if text(row.get("interface_id")) == interface_id
        and target_leaf
        and target_leaf in _field_names(row)
    ]


def _compile_slot_identities(
    binding_id: str,
    slots: Any,
    contract_fields: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in _dicts(slots):
        slot = dict(raw)
        slot_ref = text(slot.get("slot_ref"))
        candidates: list[dict[str, Any]] = []
        field_refs: list[str] = []
        for raw_candidate in _dicts(slot.get("bindings")):
            candidate = _candidate_identity(
                binding_id=binding_id,
                slot_ref=slot_ref,
                candidate=raw_candidate,
            )
            if text(candidate.get("binding_kind")) == "API_CONTRACT_FIELD":
                matches = _match_contract_fields(candidate, contract_fields)
                if len(matches) == 1:
                    ref = text(matches[0].get("contract_field_binding_id"))
                    candidate["contract_field_binding_ref"] = ref
                    candidate["contract_field_location"] = matches[0].get("location")
                    candidate["contract_field_schema_path"] = matches[0].get("schema_path")
                    candidate["contract_field_identity_status"] = "BOUND"
                    field_refs.append(ref)
                elif len(matches) > 1:
                    candidate["contract_field_identity_status"] = "AMBIGUOUS"
                    candidate["candidate_contract_field_binding_refs"] = unique_text(
                        row.get("contract_field_binding_id") for row in matches
                    )
                else:
                    candidate["contract_field_identity_status"] = "UNRESOLVED"
            candidates.append(candidate)
        slot["bindings"] = candidates
        slot["runtime_value_binding_id"] = slot.get("runtime_value_binding_id") or stable_id(
            "runtime_value_binding",
            binding_id,
            slot_ref,
            slot.get("purpose"),
        )
        slot["contract_field_binding_refs"] = unique_text(field_refs)
        slot["binding_identity_locked"] = True
        result.append(slot)
    return result


def _compile_binding_identities(
    asset: dict[str, Any],
    model: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    interfaces = {
        text(row.get("interface_id")): row
        for row in _dicts(asset.get("interfaces"))
        if text(row.get("interface_id"))
    }
    behaviors = {
        text(row.get("behavior_id")): row
        for row in _dicts(model.get("business_behaviors"))
        if text(row.get("behavior_id"))
    }
    compiled: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for raw in _dicts(model.get("behavior_implementation_bindings")):
        binding = dict(raw)
        binding_id = text(binding.get("binding_id"))
        api_rows = [
            row
            for row in _dicts(binding.get("api_operation_bindings"))
            if bool(row.get("authoritative"))
            and text(row.get("status")) == "BOUND"
        ]
        primary_interface = text(binding.get("primary_api_interface_ref"))
        selected = [
            row for row in api_rows if text(row.get("interface_id")) == primary_interface
        ]
        api = selected[0] if len(selected) == 1 else api_rows[0] if len(api_rows) == 1 else {}
        action_surfaces: list[dict[str, Any]] = []
        contract_fields: list[dict[str, Any]] = []
        if api:
            action = _action_surface_binding(binding, api)
            action_surfaces.append(action)
            interface = interfaces.get(text(api.get("interface_id"))) or {}
            contract_fields = _contract_field_bindings(
                binding_id,
                text(action.get("action_surface_binding_id")),
                interface,
            )
            behavior = behaviors.get(text(binding.get("behavior_ref"))) or {}
            ui_surfaces, ui_gaps = _formal_ui_surfaces(
                asset,
                model,
                binding=binding,
                behavior=behavior,
                primary_api=api,
                primary_surface_ref=text(action.get("action_surface_binding_id")),
            )
            action_surfaces.extend(ui_surfaces)
            gaps.extend(ui_gaps)

        binding["action_surface_bindings"] = action_surfaces
        binding["primary_action_surface_binding_ref"] = next(
            (
                text(row.get("action_surface_binding_id"))
                for row in action_surfaces
                if bool(row.get("primary"))
            ),
            "",
        )
        binding["contract_field_bindings"] = contract_fields
        binding["condition_observer_bindings"] = _compile_slot_identities(
            binding_id,
            binding.get("condition_observer_bindings"),
            contract_fields,
        )
        binding["effect_observer_bindings"] = _compile_slot_identities(
            binding_id,
            binding.get("effect_observer_bindings"),
            contract_fields,
        )
        binding["response_observer_bindings"] = [
            _candidate_identity(
                binding_id=binding_id,
                slot_ref="response",
                candidate=row,
            )
            for row in _dicts(binding.get("response_observer_bindings"))
        ]
        binding["formal_ui_surface_bindings"] = [
            row
            for row in action_surfaces
            if text(row.get("surface_kind")) == "UI_BROWSER"
        ]
        binding["binding_identity_schema"] = BINDING_IDENTITY_SCHEMA
        binding["binding_identity_locked"] = bool(
            binding.get("primary_action_surface_binding_ref")
        )
        compiled.append(binding)
    return compiled, gaps


def _identity_graph(bindings: list[dict[str, Any]]) -> dict[str, Any]:
    action_surfaces = [
        row
        for binding in bindings
        for row in _dicts(binding.get("action_surface_bindings"))
    ]
    contract_fields = [
        row
        for binding in bindings
        for row in _dicts(binding.get("contract_field_bindings"))
    ]
    runtime_values = [
        {
            "runtime_value_binding_id": slot.get("runtime_value_binding_id"),
            "implementation_binding_ref": binding.get("binding_id"),
            "slot_ref": slot.get("slot_ref"),
            "purpose": slot.get("purpose"),
            "contract_field_binding_refs": unique_text(
                as_list(slot.get("contract_field_binding_refs"))
            ),
        }
        for binding in bindings
        for slot in [
            *_dicts(binding.get("condition_observer_bindings")),
            *_dicts(binding.get("effect_observer_bindings")),
        ]
        if text(slot.get("runtime_value_binding_id"))
    ]
    observers = [
        {
            **candidate,
            "implementation_binding_ref": binding.get("binding_id"),
            "slot_ref": slot.get("slot_ref"),
            "purpose": slot.get("purpose"),
        }
        for binding in bindings
        for slot in [
            *_dicts(binding.get("condition_observer_bindings")),
            *_dicts(binding.get("effect_observer_bindings")),
        ]
        for candidate in _dicts(slot.get("bindings"))
        if text(candidate.get("observer_binding_id"))
    ]
    observers.extend(
        {
            **row,
            "implementation_binding_ref": binding.get("binding_id"),
            "slot_ref": "response",
            "purpose": "RESPONSE_OBSERVER",
        }
        for binding in bindings
        for row in _dicts(binding.get("response_observer_bindings"))
        if text(row.get("observer_binding_id"))
    )
    return {
        "schema": BINDING_IDENTITY_SCHEMA,
        "action_surface_bindings": action_surfaces,
        "contract_field_bindings": contract_fields,
        "runtime_value_bindings": runtime_values,
        "observer_bindings": observers,
        "formal_ui_surface_bindings": [
            row
            for row in action_surfaces
            if text(row.get("surface_kind")) == "UI_BROWSER"
        ],
    }


def _install_graph(
    asset: dict[str, Any],
    model: dict[str, Any],
    bindings: list[dict[str, Any]],
    gaps: list[dict[str, Any]],
) -> None:
    graph = _identity_graph(bindings)
    scenario_ready = [
        row for row in bindings if bool(row.get("scenario_planning_ready"))
    ]
    missing_primary = [
        text(row.get("binding_id"))
        for row in scenario_ready
        if not text(row.get("primary_action_surface_binding_ref"))
    ]
    status = "PASS" if not missing_primary else "BLOCKED_BINDING_IDENTITY_INCOMPLETE"
    gate = {
        "schema": BINDING_IDENTITY_GATE_SCHEMA,
        "status": status,
        "entry_allowed": status == "PASS",
        "execution_allowed": False,
        "metrics": {
            "implementation_binding_count": len(bindings),
            "scenario_ready_binding_count": len(scenario_ready),
            "action_surface_binding_count": len(graph["action_surface_bindings"]),
            "contract_field_binding_count": len(graph["contract_field_bindings"]),
            "runtime_value_binding_count": len(graph["runtime_value_bindings"]),
            "observer_binding_count": len(graph["observer_bindings"]),
            "formal_ui_surface_binding_count": len(
                graph["formal_ui_surface_bindings"]
            ),
            "missing_primary_action_surface_count": len(missing_primary),
        },
        "missing_primary_action_surface_binding_refs": missing_primary,
        "quality_claim": "IDENTITY_CLOSURE_NOT_RUNTIME_EXECUTION",
    }
    asset["binding_identity_graph"] = graph
    asset["binding_identity_gate"] = gate
    model["binding_identity_graph"] = graph
    model["binding_identity_gate"] = dict(gate)
    if gaps:
        asset["binding_identity_unknowns"] = gaps
        model["binding_identity_unknowns"] = [dict(row) for row in gaps]


def _binding_index(model: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        text(row.get("binding_id")): row
        for row in _dicts(model.get("behavior_implementation_bindings"))
        if text(row.get("binding_id"))
    }


def _runtime_value_index(binding: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        text(row.get("slot_ref")): row
        for row in [
            *_dicts(binding.get("condition_observer_bindings")),
            *_dicts(binding.get("effect_observer_bindings")),
        ]
        if text(row.get("slot_ref"))
    }


def _contract_field_index(binding: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        text(row.get("contract_field_binding_id")): row
        for row in _dicts(binding.get("contract_field_bindings"))
        if text(row.get("contract_field_binding_id"))
    }


def project_binding_identities_to_scenario_ir(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    bindings, gaps = _compile_binding_identities(asset, model)
    model["behavior_implementation_bindings"] = bindings
    asset["behavior_implementation_bindings"] = [dict(row) for row in bindings]
    _install_graph(asset, model, bindings, gaps)
    by_id = _binding_index(model)
    scenarios: list[dict[str, Any]] = []
    for raw in _dicts(asset.get("scenario_ir") or model.get("scenario_ir")):
        scenario = dict(raw)
        binding = by_id.get(text(scenario.get("implementation_binding_ref"))) or {}
        primary = text(binding.get("primary_action_surface_binding_ref"))
        action = dict(as_dict(scenario.get("action_entry")))
        if primary:
            action["action_surface_binding_ref"] = primary
            action["action_surface_kind"] = "HTTP_API"
            action["contract_field_binding_refs"] = unique_text(
                row.get("contract_field_binding_id")
                for row in _dicts(binding.get("contract_field_bindings"))
            )
            action["binding_identity_locked"] = True
        scenario["action_entry"] = action
        scenario["runtime_value_bindings"] = [
            {
                "runtime_value_binding_id": row.get("runtime_value_binding_id"),
                "slot_ref": row.get("slot_ref"),
                "purpose": row.get("purpose"),
                "contract_field_binding_refs": unique_text(
                    as_list(row.get("contract_field_binding_refs"))
                ),
            }
            for row in _runtime_value_index(binding).values()
        ]
        scenario["formal_ui_surface_bindings"] = _dicts(
            binding.get("formal_ui_surface_bindings")
        )
        scenario["binding_identity_locked"] = bool(primary)
        scenarios.append(scenario)
    asset["scenario_ir"] = scenarios
    model["scenario_ir"] = [dict(row) for row in scenarios]
    return asset


def _bind_requirement_identity(
    requirement: dict[str, Any],
    runtime_values: dict[str, dict[str, Any]],
    contract_fields: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    row = dict(requirement)
    slot_ref = text(row.get("source_slot_ref") or row.get("slot_ref"))
    runtime_value = runtime_values.get(slot_ref) or {}
    row["runtime_value_binding_ref"] = runtime_value.get(
        "runtime_value_binding_id"
    )
    refs = unique_text(as_list(runtime_value.get("contract_field_binding_refs")))
    if len(refs) == 1 and refs[0] in contract_fields:
        field_binding = contract_fields[refs[0]]
        row["contract_field_binding_ref"] = refs[0]
        row["field"] = field_binding.get("schema_path") or row.get("field")
        row["location"] = field_binding.get("location")
        row["location_resolution"] = "GOVERNED_CONTRACT_FIELD_BINDING"
        row["binding_identity_locked"] = True
    elif len(refs) > 1:
        row["candidate_contract_field_binding_refs"] = refs
        row["binding_identity_status"] = "AMBIGUOUS"
    else:
        row["binding_identity_status"] = "NO_REQUEST_FIELD_BINDING"
    return row


def project_binding_identities_to_execution_contracts(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    bindings = _binding_index(model)
    contracts: list[dict[str, Any]] = []
    for raw in _dicts(asset.get("scenario_execution_contracts")):
        contract = dict(raw)
        binding = bindings.get(text(contract.get("implementation_binding_ref"))) or {}
        runtime_values = _runtime_value_index(binding)
        contract_fields = _contract_field_index(binding)
        action = dict(as_dict(contract.get("action_contract")))
        primary = text(binding.get("primary_action_surface_binding_ref"))
        if primary:
            action["action_surface_binding_ref"] = primary
            action["binding_identity_locked"] = True
        action["contract_field_binding_refs"] = unique_text(contract_fields.keys())
        contract["action_contract"] = action
        request = dict(as_dict(contract.get("request_contract")))
        request["path_parameter_requirements"] = [
            _bind_requirement_identity(row, runtime_values, contract_fields)
            for row in _dicts(request.get("path_parameter_requirements"))
        ]
        request["request_field_requirements"] = [
            _bind_requirement_identity(row, runtime_values, contract_fields)
            for row in _dicts(request.get("request_field_requirements"))
        ]
        request["binding_identity_locked"] = bool(primary)
        contract["request_contract"] = request
        contract["binding_identity_refs"] = {
            "implementation_binding_ref": contract.get("implementation_binding_ref"),
            "action_surface_binding_ref": primary,
            "runtime_value_binding_refs": unique_text(
                row.get("runtime_value_binding_id")
                for row in runtime_values.values()
            ),
            "contract_field_binding_refs": unique_text(contract_fields.keys()),
        }
        contracts.append(contract)
    asset["scenario_execution_contracts"] = contracts
    model["scenario_execution_contracts"] = [dict(row) for row in contracts]
    return asset


def _plan_request_slots(plan: dict[str, Any]) -> Iterable[tuple[str, dict[str, Any]]]:
    request = as_dict(plan.get("request_template"))
    for collection in _REQUEST_COLLECTIONS:
        for slot in _dicts(request.get(collection)):
            yield collection, slot


def project_binding_identities_to_runtime_plans(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    contracts = {
        text(row.get("contract_id")): row
        for row in _dicts(asset.get("scenario_execution_contracts"))
        if text(row.get("contract_id"))
    }
    plans: list[dict[str, Any]] = []
    drift_unknowns: list[dict[str, Any]] = []
    for raw in _dicts(asset.get("runtime_plans")):
        plan = dict(raw)
        contract = contracts.get(text(plan.get("execution_contract_ref"))) or {}
        action_contract = as_dict(contract.get("action_contract"))
        action = dict(as_dict(plan.get("action_entry")))
        surface_ref = text(action_contract.get("action_surface_binding_ref"))
        if surface_ref:
            action["action_surface_binding_ref"] = surface_ref
            action["binding_identity_locked"] = True
        plan["action_entry"] = action
        requirements = [
            *_dicts(as_dict(contract.get("request_contract")).get(
                "path_parameter_requirements"
            )),
            *_dicts(as_dict(contract.get("request_contract")).get(
                "request_field_requirements"
            )),
        ]
        by_slot = {
            text(row.get("source_slot_ref") or row.get("slot_ref")): row
            for row in requirements
            if text(row.get("source_slot_ref") or row.get("slot_ref"))
        }
        request = dict(as_dict(plan.get("request_template")))
        for collection in _REQUEST_COLLECTIONS:
            slots: list[dict[str, Any]] = []
            for raw_slot in _dicts(request.get(collection)):
                slot = dict(raw_slot)
                source_slot = text(
                    as_dict(slot.get("value_source")).get("source_slot_ref")
                )
                requirement = by_slot.get(source_slot) or {}
                field_ref = text(requirement.get("contract_field_binding_ref"))
                value_ref = text(requirement.get("runtime_value_binding_ref"))
                if field_ref:
                    slot["contract_field_binding_ref"] = field_ref
                if value_ref:
                    slot["runtime_value_binding_ref"] = value_ref
                expected_location = text(requirement.get("location")).upper()
                actual_location = text(slot.get("location")).upper()
                if (
                    field_ref
                    and expected_location
                    and actual_location
                    and expected_location != actual_location
                ):
                    reason = "RUNTIME_PLAN_BINDING_IDENTITY_DRIFT"
                    drift_unknowns.append(
                        {
                            "unknown_id": stable_id(
                                "runtime_plan_unknown",
                                plan.get("plan_id"),
                                reason,
                                field_ref,
                                actual_location,
                                expected_location,
                            ),
                            "kind": reason,
                            "reason_code": reason,
                            "runtime_plan_ref": plan.get("plan_id"),
                            "execution_contract_ref": plan.get(
                                "execution_contract_ref"
                            ),
                            "contract_field_binding_ref": field_ref,
                            "actual_location": actual_location,
                            "expected_location": expected_location,
                            "blocks_runtime_plan": True,
                            "execution_allowed": False,
                        }
                    )
                    plan["status"] = "INCOMPLETE"
                    plan["formal_runtime_plan"] = False
                slot["binding_identity_locked"] = bool(field_ref or value_ref)
                slots.append(slot)
            request[collection] = slots
        request["action_surface_binding_ref"] = surface_ref
        request["binding_identity_locked"] = bool(surface_ref)
        plan["request_template"] = request
        plan["binding_identity_refs"] = {
            "action_surface_binding_ref": surface_ref,
            "contract_field_binding_refs": unique_text(
                slot.get("contract_field_binding_ref")
                for _collection, slot in _plan_request_slots(plan)
            ),
            "runtime_value_binding_refs": unique_text(
                slot.get("runtime_value_binding_ref")
                for _collection, slot in _plan_request_slots(plan)
            ),
        }
        plans.append(plan)
    all_unknowns = list(
        {
            text(row.get("unknown_id")): row
            for row in [
                *_dicts(asset.get("runtime_plan_unknowns")),
                *drift_unknowns,
            ]
            if text(row.get("unknown_id"))
        }.values()
    )
    asset["runtime_plans"] = plans
    asset["runtime_plan_unknowns"] = all_unknowns
    model["runtime_plans"] = [dict(row) for row in plans]
    model["runtime_plan_unknowns"] = [dict(row) for row in all_unknowns]
    if drift_unknowns:
        gate = dict(as_dict(asset.get("runtime_plan_gate")))
        gate["status"] = "BLOCKED_RUNTIME_PLAN_BINDING_IDENTITY_DRIFT"
        gate["entry_allowed"] = False
        gate["runtime_plan_ready"] = False
        gate["execution_allowed"] = False
        metrics = dict(as_dict(gate.get("metrics")))
        metrics["binding_identity_drift_count"] = len(drift_unknowns)
        gate["metrics"] = metrics
        asset["runtime_plan_gate"] = gate
        model["runtime_plan_gate"] = dict(gate)
    return asset


def _plan_slot_index(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        text(slot.get("slot_id")): slot
        for _collection, slot in _plan_request_slots(plan)
        if text(slot.get("slot_id"))
    }


def project_binding_identities_to_materializations(
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
        plan = plans.get(text(materialization.get("runtime_plan_ref"))) or {}
        slots = _plan_slot_index(plan)
        action_ref = text(
            as_dict(plan.get("action_entry")).get("action_surface_binding_ref")
        )
        request_bindings: list[dict[str, Any]] = []
        for raw_binding in _dicts(materialization.get("request_value_bindings")):
            value_binding = dict(raw_binding)
            slot = slots.get(text(value_binding.get("slot_id"))) or {}
            field_ref = text(slot.get("contract_field_binding_ref"))
            runtime_value_ref = text(slot.get("runtime_value_binding_ref"))
            if field_ref:
                value_binding["contract_field_binding_ref"] = field_ref
            if runtime_value_ref:
                value_binding["runtime_value_binding_ref"] = runtime_value_ref
            value_binding["binding_identity_locked"] = bool(
                field_ref or runtime_value_ref
            )
            request_bindings.append(value_binding)
        materialization["request_value_bindings"] = request_bindings
        request = dict(as_dict(materialization.get("request_draft")))
        request["action_surface_binding_ref"] = action_ref
        request["binding_identity_locked"] = bool(action_ref)
        materialization["request_draft"] = request
        materialization["binding_identity_refs"] = {
            "action_surface_binding_ref": action_ref,
            "contract_field_binding_refs": unique_text(
                row.get("contract_field_binding_ref")
                for row in request_bindings
            ),
            "runtime_value_binding_refs": unique_text(
                row.get("runtime_value_binding_ref")
                for row in request_bindings
            ),
        }
        if bool(plan.get("formal_runtime_plan")) and not action_ref:
            reason = "RUNTIME_MATERIALIZATION_ACTION_BINDING_IDENTITY_MISSING"
            unknowns.append(
                {
                    "unknown_id": stable_id(
                        "runtime_materialization_unknown",
                        materialization.get("materialization_id"),
                        reason,
                    ),
                    "kind": reason,
                    "reason_code": reason,
                    "runtime_materialization_ref": materialization.get(
                        "materialization_id"
                    ),
                    "runtime_plan_ref": materialization.get("runtime_plan_ref"),
                    "blocks_runtime_materialization": True,
                    "execution_allowed": False,
                }
            )
            materialization["status"] = "INCOMPLETE"
            materialization["formal_runtime_materialization"] = False
        materializations.append(materialization)
    all_unknowns = list(
        {
            text(row.get("unknown_id")): row
            for row in [
                *_dicts(asset.get("runtime_materialization_unknowns")),
                *unknowns,
            ]
            if text(row.get("unknown_id"))
        }.values()
    )
    asset["runtime_materializations"] = materializations
    asset["runtime_materialization_unknowns"] = all_unknowns
    model["runtime_materializations"] = [dict(row) for row in materializations]
    model["runtime_materialization_unknowns"] = [
        dict(row) for row in all_unknowns
    ]
    if unknowns:
        gate = dict(as_dict(asset.get("runtime_materialization_gate")))
        gate["status"] = "BLOCKED_RUNTIME_MATERIALIZATION_BINDING_IDENTITY_INCOMPLETE"
        gate["entry_allowed"] = False
        gate["runtime_materialization_ready"] = False
        gate["execution_allowed"] = False
        metrics = dict(as_dict(gate.get("metrics")))
        metrics["binding_identity_missing_count"] = len(unknowns)
        gate["metrics"] = metrics
        asset["runtime_materialization_gate"] = gate
        model["runtime_materialization_gate"] = dict(gate)
    return asset


__all__ = [
    "BINDING_IDENTITY_SCHEMA",
    "BINDING_IDENTITY_GATE_SCHEMA",
    "project_binding_identities_to_scenario_ir",
    "project_binding_identities_to_execution_contracts",
    "project_binding_identities_to_runtime_plans",
    "project_binding_identities_to_materializations",
]
