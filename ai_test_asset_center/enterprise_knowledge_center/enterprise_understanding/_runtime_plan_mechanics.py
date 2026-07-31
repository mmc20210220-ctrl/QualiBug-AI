"""Compile Scenario Execution Contract v1 into non-executable Runtime Plan v1 templates.

A Runtime Plan describes exact request locations, runtime value sources, credential-reference
requirements, observer query templates, snapshot phases and cleanup-step templates.  It never
reads a secret, materializes a test value, opens a connection, sends a request, executes cleanup
or reports a Bug.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .schema import as_dict, as_list, dedupe_evidence, stable_id, text, unique_text

RUNTIME_PLAN_SCHEMA = "qualibug.runtime-plan.v1"
RUNTIME_PLAN_GATE_SCHEMA = "qualibug.runtime-plan-gate.v1"

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_PATH_PARAMETER_RE = re.compile(r"\{([^{}]+)\}")
_SUPPORTED_LOCATIONS = {"PATH", "QUERY", "HEADER", "COOKIE", "BODY", "FORM"}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text(value).lower())


def _interface_index(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        text(row.get("interface_id")): dict(row)
        for row in _dicts(asset.get("interfaces"))
        if text(row.get("interface_id"))
    }


def _field_names(row: dict[str, Any]) -> set[str]:
    values = unique_text(
        [
            row.get("field"),
            row.get("name"),
            text(row.get("field")).split(".")[-1],
            text(row.get("name")).split(".")[-1],
        ]
    )
    return {_norm(value) for value in values if _norm(value)}


def _interface_descriptors(interface: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in [
        *_dicts(interface.get("parameter_contracts")),
        *_dicts(interface.get("request_body_fields")),
    ]:
        location = text(raw.get("location")).upper()
        if location not in _SUPPORTED_LOCATIONS:
            continue
        row = dict(raw)
        row["location"] = location
        rows.append(row)
    path = text(interface.get("path"))
    declared = {
        _norm(row.get("name") or row.get("field"))
        for row in rows
        if text(row.get("location")) == "PATH"
    }
    for name in unique_text(_PATH_PARAMETER_RE.findall(path)):
        if _norm(name) in declared:
            continue
        rows.append(
            {
                "name": name,
                "field": name,
                "location": "PATH",
                "required": True,
                "schema_type": "UNSPECIFIED",
                "source": "ACTION_PATH_TEMPLATE",
            }
        )
    return rows


def _descriptor_matches(
    field: str, descriptors: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    target = _norm(text(field).split(".")[-1])
    full = _norm(field)
    if not target:
        return []
    exact_full = [row for row in descriptors if full and full in _field_names(row)]
    if exact_full:
        return exact_full
    return [row for row in descriptors if target in _field_names(row)]


def _value_source(requirement: dict[str, Any]) -> dict[str, Any]:
    semantic = as_dict(requirement.get("semantic_value_requirement"))
    if semantic:
        return {
            "source_kind": "SOURCE_BACKED_SEMANTIC_VALUE",
            "source_slot_ref": requirement.get("slot_ref")
            or requirement.get("source_slot_ref"),
            "raw": semantic.get("raw"),
            "value_type": semantic.get("value_type"),
            "normalized_value": semantic.get("normalized_value"),
            "unit": semantic.get("unit"),
            "runtime_value_materialized": False,
        }
    runtime_source = text(requirement.get("runtime_value_source"))
    return {
        "source_kind": runtime_source or "RUNTIME_REQUIRED_INPUT",
        "source_slot_ref": requirement.get("slot_ref")
        or requirement.get("source_slot_ref"),
        "runtime_value_materialized": False,
    }


def _request_template(
    contract: dict[str, Any], interface: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    request_contract = as_dict(contract.get("request_contract"))
    action = as_dict(contract.get("action_contract"))
    descriptors = _interface_descriptors(interface)
    unknowns: list[dict[str, Any]] = []
    slots: list[dict[str, Any]] = []
    covered: set[tuple[str, str]] = set()

    for requirement in _dicts(request_contract.get("path_parameter_requirements")):
        field = text(requirement.get("field"))
        matches = [
            row
            for row in _descriptor_matches(field, descriptors)
            if text(row.get("location")) == "PATH"
        ]
        descriptor = matches[0] if len(matches) == 1 else {}
        slot = {
            "slot_id": stable_id("runtime_request_slot", contract.get("contract_id"), "PATH", field),
            "field": field,
            "location": "PATH",
            "required": True,
            "schema_type": descriptor.get("schema_type") or "UNSPECIFIED",
            "format": descriptor.get("format"),
            "value_source": _value_source(requirement),
            "location_derivation": (
                descriptor.get("source") or "ACTION_PATH_TEMPLATE"
            ),
            "runtime_value_materialized": False,
        }
        slots.append(slot)
        covered.add(("PATH", _norm(field)))

    for requirement in _dicts(request_contract.get("request_field_requirements")):
        field = text(requirement.get("field") or requirement.get("field_candidate"))
        matches = _descriptor_matches(field, descriptors)
        locations = {text(row.get("location")) for row in matches}
        if len(locations) != 1:
            reason = (
                "RUNTIME_PLAN_REQUEST_FIELD_LOCATION_UNRESOLVED"
                if not locations
                else "RUNTIME_PLAN_REQUEST_FIELD_LOCATION_AMBIGUOUS"
            )
            unknowns.append(
                {
                    "kind": reason,
                    "reason_code": reason,
                    "contract_ref": contract.get("contract_id"),
                    "field": field,
                    "candidate_locations": sorted(locations),
                    "blocks_runtime_plan": True,
                }
            )
            continue
        location = next(iter(locations))
        exact = [row for row in matches if text(row.get("location")) == location]
        descriptor = exact[0] if len(exact) == 1 else exact[0]
        canonical = text(descriptor.get("field") or descriptor.get("name") or field)
        slots.append(
            {
                "slot_id": stable_id(
                    "runtime_request_slot",
                    contract.get("contract_id"),
                    location,
                    canonical,
                ),
                "field": canonical,
                "location": location,
                "required": bool(descriptor.get("required", requirement.get("required", True))),
                "schema_type": descriptor.get("schema_type") or "UNSPECIFIED",
                "format": descriptor.get("format"),
                "enum": as_list(descriptor.get("enum")),
                "media_type": descriptor.get("media_type"),
                "value_source": _value_source(requirement),
                "location_derivation": descriptor.get("source")
                or "SOURCE_DECLARED_INTERFACE_CONTRACT",
                "runtime_value_materialized": False,
            }
        )
        covered.add((location, _norm(canonical)))

    for descriptor in descriptors:
        location = text(descriptor.get("location"))
        field = text(descriptor.get("field") or descriptor.get("name"))
        identity = (location, _norm(field))
        if not bool(descriptor.get("required")) or identity in covered or not field:
            continue
        slots.append(
            {
                "slot_id": stable_id(
                    "runtime_request_slot",
                    contract.get("contract_id"),
                    location,
                    field,
                ),
                "field": field,
                "location": location,
                "required": True,
                "schema_type": descriptor.get("schema_type") or "UNSPECIFIED",
                "format": descriptor.get("format"),
                "enum": as_list(descriptor.get("enum")),
                "media_type": descriptor.get("media_type"),
                "value_source": {
                    "source_kind": "RUNTIME_REQUIRED_INPUT",
                    "runtime_value_materialized": False,
                },
                "location_derivation": descriptor.get("source")
                or "SOURCE_DECLARED_REQUIRED_INTERFACE_FIELD",
                "runtime_value_materialized": False,
            }
        )
        covered.add(identity)

    deduped = list(
        {
            text(row.get("slot_id")): row
            for row in slots
            if text(row.get("slot_id"))
        }.values()
    )
    grouped = {
        location.lower(): [
            row for row in deduped if text(row.get("location")) == location
        ]
        for location in sorted(_SUPPORTED_LOCATIONS)
    }
    template = {
        "method": text(action.get("method")).upper(),
        "interface_id": action.get("interface_id"),
        "operation_id": action.get("operation_id"),
        "path_template": action.get("path"),
        "path_parameters": grouped.get("path", []),
        "query_parameters": grouped.get("query", []),
        "header_parameters": grouped.get("header", []),
        "cookie_parameters": grouped.get("cookie", []),
        "body_fields": grouped.get("body", []),
        "form_fields": grouped.get("form", []),
        "request_body_media_types": unique_text(
            as_list(interface.get("request_body_media_types"))
        ),
        "field_locations_resolved": not bool(unknowns),
        "request_template_compiled": not bool(unknowns),
        "concrete_request_compiled": False,
        "runtime_values_materialized": False,
        "undeclared_fields_allowed": False,
    }
    return template, unknowns


def _credential_catalog(asset: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    containers = [
        asset,
        as_dict(asset.get("environment_contract")),
        as_dict(asset.get("runtime_environment")),
        as_dict(asset.get("project_configuration")),
    ]
    for container in containers:
        for key in ("credential_refs", "test_account_refs"):
            for raw in _dicts(container.get(key)):
                credential_ref = text(raw.get("credential_ref"))
                if not credential_ref:
                    continue
                rows.append(
                    {
                        "credential_ref": credential_ref,
                        "actor_ref": text(raw.get("actor_ref") or raw.get("role")),
                        "roles": unique_text(
                            [raw.get("actor_ref"), raw.get("role"), *as_list(raw.get("roles"))]
                        ),
                        "environment_ref": text(raw.get("environment_ref")),
                        "secret_value_retained": False,
                    }
                )
    return list(
        {
            text(row.get("credential_ref")): row
            for row in rows
            if text(row.get("credential_ref"))
        }.values()
    )


def _credential_template(
    contract: dict[str, Any], interface: dict[str, Any], asset: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    catalog = _credential_catalog(asset)
    unknowns: list[dict[str, Any]] = []
    slots: list[dict[str, Any]] = []
    for requirement in _dicts(contract.get("credential_requirements")):
        actor = text(requirement.get("actor_ref"))
        matches = [
            row
            for row in catalog
            if actor
            and _norm(actor)
            in {_norm(value) for value in as_list(row.get("roles")) if _norm(value)}
        ]
        if len(matches) > 1:
            unknowns.append(
                {
                    "kind": "RUNTIME_PLAN_CREDENTIAL_REF_AMBIGUOUS",
                    "reason_code": "RUNTIME_PLAN_CREDENTIAL_REF_AMBIGUOUS",
                    "contract_ref": contract.get("contract_id"),
                    "actor_ref": actor,
                    "candidate_credential_refs": sorted(
                        text(row.get("credential_ref")) for row in matches
                    ),
                    "blocks_runtime_plan": True,
                }
            )
        selected = matches[0] if len(matches) == 1 else {}
        slots.append(
            {
                "slot_id": stable_id(
                    "runtime_credential_slot", contract.get("contract_id"), actor
                ),
                "actor_ref": actor or "UNSPECIFIED_ACTOR",
                "credential_ref": selected.get("credential_ref"),
                "environment_ref": selected.get("environment_ref"),
                "resolution_status": (
                    "CREDENTIAL_REF_RESOLVED"
                    if selected
                    else "RUNTIME_CREDENTIAL_REF_REQUIRED"
                ),
                "credential_value_loaded": False,
                "automatic_role_substitution_allowed": False,
            }
        )
    return (
        {
            "credential_slots": slots,
            "security_requirements": _dicts(interface.get("security_requirements")),
            "credential_refs_only": True,
            "plaintext_credentials_allowed": False,
            "credential_values_loaded": False,
            "credentials_selected": bool(slots)
            and all(text(row.get("credential_ref")) for row in slots),
        },
        unknowns,
    )


def _observer_candidates(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for slot in _dicts(value):
        if text(slot.get("binding_kind")):
            rows.append(slot)
        for binding in _dicts(slot.get("bindings")):
            candidate = dict(binding)
            candidate["slot_ref"] = slot.get("slot_ref")
            candidate["purpose"] = slot.get("purpose")
            rows.append(candidate)
    return rows


def _oracle_templates(
    contract: dict[str, Any], interface: dict[str, Any]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    oracle = as_dict(contract.get("oracle_plan"))
    rows: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    for observer in _observer_candidates(
        [
            *_dicts(oracle.get("condition_observers")),
            *_dicts(oracle.get("effect_observers")),
        ]
    ):
        if text(observer.get("binding_kind")) != "DATABASE_FIELD":
            continue
        table = text(observer.get("table") or observer.get("table_id"))
        field = text(observer.get("field"))
        if not table or not field:
            continue
        rows.append(
            {
                "template_id": stable_id(
                    "runtime_oracle_template",
                    contract.get("contract_id"),
                    "DATABASE_FIELD_SNAPSHOT",
                    table,
                    field,
                    observer.get("slot_ref"),
                ),
                "template_kind": "DATABASE_FIELD_SNAPSHOT",
                "phase": (
                    "BEFORE_AND_AFTER"
                    if text(observer.get("purpose")) in {
                        "EFFECT_OBSERVER",
                        "STATE_EFFECT_OBSERVER",
                    }
                    else "BEFORE"
                ),
                "table_ref": observer.get("table_id") or table,
                "table": table,
                "field": field,
                "entity_identity_source": "SAME_SCENARIO_ENTITY_IDENTITY",
                "query_template_compiled": True,
                "sql_compiled": False,
                "database_connection_opened": False,
            }
        )
    response_observers = _dicts(oracle.get("response_observers"))
    permission = text(oracle.get("permission_decision_requirement"))
    if response_observers or permission not in {"", "UNSPECIFIED"}:
        rows.append(
            {
                "template_id": stable_id(
                    "runtime_oracle_template",
                    contract.get("contract_id"),
                    "HTTP_RESPONSE_CAPTURE",
                    as_dict(contract.get("action_contract")).get("interface_id"),
                ),
                "template_kind": "HTTP_RESPONSE_CAPTURE",
                "phase": "AFTER",
                "interface_id": as_dict(contract.get("action_contract")).get(
                    "interface_id"
                ),
                "declared_response_contracts": _dicts(
                    interface.get("response_contracts")
                ),
                "permission_decision_requirement": permission,
                "capture_status": True,
                "capture_headers": True,
                "capture_body": True,
                "query_template_compiled": True,
                "concrete_assertion_compiled": False,
                "network_call_compiled": False,
            }
        )
    if not rows:
        unknowns.append(
            {
                "kind": "RUNTIME_PLAN_ORACLE_TEMPLATE_UNRESOLVED",
                "reason_code": "RUNTIME_PLAN_ORACLE_TEMPLATE_UNRESOLVED",
                "contract_ref": contract.get("contract_id"),
                "blocks_runtime_plan": True,
            }
        )
    return (
        {
            "templates": rows,
            "oracle_templates_compiled": bool(rows),
            "concrete_assertions_compiled": False,
            "http_status_assertion_compiled": False,
            "response_body_assertion_compiled": False,
            "database_assertion_compiled": False,
        },
        unknowns,
    )


def _snapshot_template(contract: dict[str, Any], oracle_template: dict[str, Any]) -> dict[str, Any]:
    required = as_dict(contract.get("snapshot_plan"))
    templates = _dicts(oracle_template.get("templates"))
    before_refs = [
        row.get("template_id")
        for row in templates
        if text(row.get("phase")) in {"BEFORE", "BEFORE_AND_AFTER"}
    ]
    after_refs = [
        row.get("template_id")
        for row in templates
        if text(row.get("phase")) in {"AFTER", "BEFORE_AND_AFTER"}
    ]
    return {
        "before_snapshot_required": bool(required.get("before_snapshot_required")),
        "after_snapshot_required": bool(required.get("after_snapshot_required")),
        "before_oracle_template_refs": unique_text(before_refs),
        "after_oracle_template_refs": unique_text(after_refs),
        "consistency_scope": required.get("snapshot_consistency_scope")
        or "SAME_SCENARIO_ENTITY_IDENTITY",
        "snapshot_templates_compiled": True,
        "snapshot_queries_executed": False,
        "snapshots_materialized": False,
    }


def _cleanup_template(contract: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cleanup = as_dict(contract.get("cleanup_requirements"))
    write = bool(cleanup.get("write_action"))
    strategy = text(cleanup.get("strategy_requirement"))
    candidates = unique_text(
        as_list(cleanup.get("source_backed_compensation_candidates"))
    )
    unknowns: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []
    if not write:
        steps.append(
            {
                "step_index": 1,
                "step_kind": "NO_CLEANUP_REQUIRED",
                "reason": "READ_ONLY_ACTION",
                "template_compiled": True,
                "action_executed": False,
            }
        )
    elif strategy == "SOURCE_BACKED_COMPENSATION_REQUIRED" and candidates:
        steps.extend(
            [
                {
                    "step_index": 1,
                    "step_kind": "CAPTURE_MUTATED_ENTITY_IDENTITY",
                    "identity_source": "SAME_SCENARIO_ENTITY_IDENTITY",
                    "template_compiled": True,
                    "action_executed": False,
                },
                {
                    "step_index": 2,
                    "step_kind": "BIND_SOURCE_BACKED_COMPENSATION_OPERATION",
                    "operation_candidates": candidates,
                    "automatic_operation_selection_allowed": False,
                    "template_compiled": True,
                    "action_bound": False,
                    "action_executed": False,
                },
                {
                    "step_index": 3,
                    "step_kind": "VERIFY_CLEANUP_RESTORED_STATE",
                    "observer_scope": "SAME_SCENARIO_ENTITY_IDENTITY",
                    "template_compiled": True,
                    "verification_executed": False,
                },
            ]
        )
    elif strategy == "REVERSIBLE_CLEANUP_OR_ISOLATED_SANDBOX_REQUIRED":
        steps.extend(
            [
                {
                    "step_index": 1,
                    "step_kind": "CAPTURE_MUTATED_ENTITY_IDENTITY",
                    "identity_source": "SAME_SCENARIO_ENTITY_IDENTITY",
                    "template_compiled": True,
                    "action_executed": False,
                },
                {
                    "step_index": 2,
                    "step_kind": "REQUIRE_ISOLATED_SANDBOX_RESET_OR_BOUND_REVERSAL",
                    "environment_capability_required": "DISPOSABLE_SANDBOX_OR_REVERSIBLE_WRITE",
                    "automatic_database_deletion_allowed": False,
                    "template_compiled": True,
                    "action_bound": False,
                    "action_executed": False,
                },
                {
                    "step_index": 3,
                    "step_kind": "VERIFY_CLEANUP_RESTORED_STATE",
                    "observer_scope": "SAME_SCENARIO_ENTITY_IDENTITY",
                    "template_compiled": True,
                    "verification_executed": False,
                },
            ]
        )
    else:
        unknowns.append(
            {
                "kind": "RUNTIME_PLAN_CLEANUP_TEMPLATE_UNRESOLVED",
                "reason_code": "RUNTIME_PLAN_CLEANUP_TEMPLATE_UNRESOLVED",
                "contract_ref": contract.get("contract_id"),
                "strategy_requirement": strategy,
                "blocks_runtime_plan": True,
            }
        )
    return (
        {
            "write_action": write,
            "strategy_requirement": strategy,
            "steps": steps,
            "cleanup_step_templates_compiled": bool(steps),
            "cleanup_actions_bound": not write,
            "cleanup_executed": False,
            "destructive_execution_allowed_without_cleanup": False,
        },
        unknowns,
    )


def _environment_template(asset: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    environment = as_dict(asset.get("runtime_environment"))
    environment_ref = text(
        asset.get("environment_ref")
        or environment.get("environment_ref")
        or as_dict(asset.get("environment_contract")).get("environment_ref")
    )
    write = bool(as_dict(contract.get("cleanup_requirements")).get("write_action"))
    return {
        "environment_ref": environment_ref,
        "environment_ref_resolution_status": (
            "RESOLVED" if environment_ref else "RUNTIME_ENVIRONMENT_REF_REQUIRED"
        ),
        "non_production_required": write,
        "isolated_data_scope_required": write,
        "network_access_allowed": False,
        "runtime_environment_validated": False,
        "production_execution_allowed": False,
    }


def _compile_runtime_plan(
    contract: dict[str, Any], interface: dict[str, Any], asset: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if text(contract.get("status")) != "REQUIREMENTS_READY":
        return None, []
    action = as_dict(contract.get("action_contract"))
    request, request_unknowns = _request_template(contract, interface)
    credentials, credential_unknowns = _credential_template(contract, interface, asset)
    oracle, oracle_unknowns = _oracle_templates(contract, interface)
    cleanup, cleanup_unknowns = _cleanup_template(contract)
    unknowns = [
        *request_unknowns,
        *credential_unknowns,
        *oracle_unknowns,
        *cleanup_unknowns,
    ]
    plan_id = stable_id(
        "runtime_plan", contract.get("contract_id"), action.get("interface_id")
    )
    evidence = dedupe_evidence(as_list(contract.get("evidence")))
    if not evidence:
        unknowns.append(
            {
                "kind": "RUNTIME_PLAN_SOURCE_EVIDENCE_MISSING",
                "reason_code": "RUNTIME_PLAN_SOURCE_EVIDENCE_MISSING",
                "contract_ref": contract.get("contract_id"),
                "blocks_runtime_plan": True,
            }
        )
    critical = [row for row in unknowns if bool(row.get("blocks_runtime_plan"))]
    plan = {
        "schema": RUNTIME_PLAN_SCHEMA,
        "plan_id": plan_id,
        "execution_contract_ref": contract.get("contract_id"),
        "scenario_ref": contract.get("scenario_ref"),
        "behavior_ref": contract.get("behavior_ref"),
        "implementation_binding_ref": contract.get("implementation_binding_ref"),
        "scenario_type": contract.get("scenario_type"),
        "action_entry": {
            "interface_id": action.get("interface_id"),
            "method": action.get("method"),
            "path": action.get("path"),
            "operation_id": action.get("operation_id"),
            "authoritative": bool(action.get("authoritative")),
        },
        "request_template": request,
        "credential_template": credentials,
        "test_data_setup_templates": _dicts(contract.get("test_data_requirements")),
        "oracle_query_templates": oracle,
        "snapshot_template": _snapshot_template(contract, oracle),
        "cleanup_step_templates": cleanup,
        "environment_template": _environment_template(asset, contract),
        "evidence": evidence,
        "unresolved_runtime_plan_semantics": unique_text(
            row.get("reason_code") for row in unknowns
        ),
        "status": "INCOMPLETE" if critical else "TEMPLATE_READY",
        "formal_runtime_plan": not bool(critical),
        "execution_allowed": False,
        "network_calls_allowed": False,
        "request_values_materialized": False,
        "http_request_compiled": False,
        "credentials_loaded": False,
        "test_data_materialized": False,
        "database_queries_executable": False,
        "oracle_assertions_compiled": False,
        "snapshots_materialized": False,
        "cleanup_actions_executable": False,
        "runtime_environment_validated": False,
    }
    normalized_unknowns: list[dict[str, Any]] = []
    for row in unknowns:
        item = dict(row)
        item["unknown_id"] = stable_id(
            "runtime_plan_unknown",
            plan_id,
            item.get("reason_code"),
            item.get("field"),
            item.get("actor_ref"),
        )
        item["runtime_plan_ref"] = plan_id
        item["execution_allowed"] = False
        normalized_unknowns.append(item)
    return plan, normalized_unknowns


def build_runtime_plans_v1(
    asset: dict[str, Any], model: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    upstream = as_dict(asset.get("scenario_execution_contract_gate"))
    contracts = _dicts(
        asset.get("scenario_execution_contracts")
        or model.get("scenario_execution_contracts")
    )
    if not bool(upstream.get("entry_allowed")):
        return [], [], {
            "schema": RUNTIME_PLAN_GATE_SCHEMA,
            "status": "BLOCKED_RUNTIME_PLAN_UPSTREAM_EXECUTION_CONTRACT_GATE",
            "entry_allowed": False,
            "runtime_plan_ready": False,
            "execution_allowed": False,
            "upstream_execution_contract_status": text(upstream.get("status"))
            or "NOT_BUILT",
            "metrics": {
                "runtime_plan_count": 0,
                "ready_runtime_plan_count": 0,
                "incomplete_runtime_plan_count": 0,
            },
            "quality_claim": "RUNTIME_PLAN_NOT_BUILT_WHEN_EXECUTION_CONTRACT_GATE_CLOSED",
        }
    interfaces = _interface_index(asset)
    plans: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    ready_contracts = [
        row for row in contracts if text(row.get("status")) == "REQUIREMENTS_READY"
    ]
    for contract in ready_contracts:
        interface_id = text(as_dict(contract.get("action_contract")).get("interface_id"))
        interface = interfaces.get(interface_id) or {
            **as_dict(contract.get("action_contract")),
            "interface_id": interface_id,
        }
        plan, rows = _compile_runtime_plan(contract, interface, asset)
        unknowns.extend(rows)
        if plan is not None:
            plans.append(plan)
    plans = list(
        {
            text(row.get("plan_id")): row
            for row in plans
            if text(row.get("plan_id"))
        }.values()
    )
    unknowns = list(
        {
            text(row.get("unknown_id")): row
            for row in unknowns
            if text(row.get("unknown_id"))
        }.values()
    )
    ready = sum(1 for row in plans if text(row.get("status")) == "TEMPLATE_READY")
    incomplete = sum(1 for row in plans if text(row.get("status")) == "INCOMPLETE")
    covered = {text(row.get("execution_contract_ref")) for row in plans}
    if incomplete or len(covered) < len(ready_contracts):
        status = "BLOCKED_RUNTIME_PLAN_INCOMPLETE"
    elif plans:
        status = "PASS"
    else:
        status = "NO_RUNTIME_PLAN_COMPILED"
    gate = {
        "schema": RUNTIME_PLAN_GATE_SCHEMA,
        "status": status,
        "entry_allowed": status == "PASS",
        "runtime_plan_ready": status == "PASS",
        "execution_allowed": False,
        "upstream_execution_contract_status": upstream.get("status"),
        "metrics": {
            "runtime_plan_count": len(plans),
            "ready_runtime_plan_count": ready,
            "incomplete_runtime_plan_count": incomplete,
            "covered_execution_contract_count": len(covered),
            "ready_execution_contract_count": len(ready_contracts),
            "request_slot_count": sum(
                sum(
                    len(as_list(as_dict(row.get("request_template")).get(key)))
                    for key in (
                        "path_parameters",
                        "query_parameters",
                        "header_parameters",
                        "cookie_parameters",
                        "body_fields",
                        "form_fields",
                    )
                )
                for row in plans
            ),
            "credential_slot_count": sum(
                len(
                    as_list(
                        as_dict(row.get("credential_template")).get(
                            "credential_slots"
                        )
                    )
                )
                for row in plans
            ),
            "oracle_query_template_count": sum(
                len(
                    as_list(
                        as_dict(row.get("oracle_query_templates")).get(
                            "templates"
                        )
                    )
                )
                for row in plans
            ),
            "cleanup_step_template_count": sum(
                len(
                    as_list(
                        as_dict(row.get("cleanup_step_templates")).get("steps")
                    )
                )
                for row in plans
            ),
            "runtime_plan_unknown_count": len(unknowns),
        },
        "network_calls_allowed": False,
        "credentials_loaded": False,
        "request_values_materialized": False,
        "http_requests_compiled": False,
        "database_queries_executable": False,
        "oracle_assertions_compiled": False,
        "cleanup_actions_executable": False,
        "runtime_environment_validated": False,
        "quality_claim": "RUNTIME_TEMPLATE_CLOSURE_NOT_EXECUTION_READINESS_OR_BUG_FINDING",
    }
    return plans, unknowns, gate


def _relationships(plans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for plan in plans:
        plan_id = text(plan.get("plan_id"))
        contract_id = text(plan.get("execution_contract_ref"))
        interface_id = text(as_dict(plan.get("action_entry")).get("interface_id"))
        accepted = text(plan.get("status")) == "TEMPLATE_READY"
        if plan_id and contract_id:
            rows.append(
                {
                    "edge_id": stable_id(
                        "edge", "execution_contract_to_runtime_plan", contract_id, plan_id
                    ),
                    "from": contract_id,
                    "to": plan_id,
                    "relation": "execution_contract_to_runtime_plan",
                    "status": "accepted" if accepted else "candidate",
                    "confidence": 1.0 if accepted else 0.0,
                    "derivation": "runtime_plan_compiler",
                    "evidence": {"execution_allowed": False},
                }
            )
        if plan_id and interface_id:
            rows.append(
                {
                    "edge_id": stable_id(
                        "edge", "runtime_plan_to_interface", plan_id, interface_id
                    ),
                    "from": plan_id,
                    "to": interface_id,
                    "relation": "runtime_plan_to_interface",
                    "status": "accepted" if accepted else "candidate",
                    "confidence": 1.0 if accepted else 0.0,
                    "derivation": "authoritative_execution_contract_action",
                    "evidence": {"execution_allowed": False},
                }
            )
    return list(
        {
            text(row.get("edge_id")): row
            for row in rows
            if text(row.get("edge_id"))
        }.values()
    )


def project_runtime_plans_to_asset(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    plans, unknowns, gate = build_runtime_plans_v1(asset, model)
    relationships = _relationships(plans)
    evidence = dedupe_evidence(
        [row for plan in plans for row in as_list(plan.get("evidence")) if isinstance(row, dict)]
    )
    asset["runtime_plans"] = plans
    asset["runtime_plan_unknowns"] = unknowns
    asset["runtime_plan_evidence_index"] = evidence
    asset["runtime_plan_relationships"] = relationships
    asset["runtime_plan_gate"] = gate
    asset["relationships"] = list(
        {
            text(row.get("edge_id")): dict(row)
            for row in [*as_list(asset.get("relationships")), *relationships]
            if isinstance(row, dict) and text(row.get("edge_id"))
        }.values()
    )
    model["runtime_plans"] = plans
    model["runtime_plan_unknowns"] = unknowns
    model["runtime_plan_evidence_index"] = evidence
    model["runtime_plan_relationships"] = relationships
    model["runtime_plan_gate"] = gate

    metrics = as_dict(gate.get("metrics"))
    projected = {
        "runtime_plan_status": gate.get("status"),
        "runtime_plan_ready": bool(gate.get("entry_allowed")),
        "runtime_plan_count": int(metrics.get("runtime_plan_count") or 0),
        "runtime_plan_incomplete_count": int(
            metrics.get("incomplete_runtime_plan_count") or 0
        ),
        "runtime_plan_unknown_count": len(unknowns),
        "runtime_plan_relationship_count": len(relationships),
        "runtime_execution_allowed": False,
    }
    summary = as_dict(asset.get("summary"))
    summary.update(projected)
    asset["summary"] = summary
    source_summary = as_dict(model.get("source_summary"))
    source_summary.update(projected)
    model["source_summary"] = source_summary
    model_metrics = as_dict(model.get("metrics"))
    model_metrics.update(projected)
    model["metrics"] = model_metrics

    gap_kinds = {
        "RUNTIME_PLAN_UPSTREAM_BLOCKED",
        "RUNTIME_PLAN_INCOMPLETE",
        "RUNTIME_PLAN_NOT_COMPILED",
    }
    gaps = [
        dict(row)
        for row in as_list(asset.get("coverage_gaps"))
        if isinstance(row, dict) and text(row.get("kind")) not in gap_kinds
    ]
    status = text(gate.get("status"))
    if status != "PASS":
        if status == "BLOCKED_RUNTIME_PLAN_UPSTREAM_EXECUTION_CONTRACT_GATE":
            kind = "RUNTIME_PLAN_UPSTREAM_BLOCKED"
        elif status == "NO_RUNTIME_PLAN_COMPILED":
            kind = "RUNTIME_PLAN_NOT_COMPILED"
        else:
            kind = "RUNTIME_PLAN_INCOMPLETE"
        gaps.append(
            {
                "kind": kind,
                "gap_type": "runtime_plan_template_not_closed",
                "source_id": "*",
                "runtime_plan_status": status,
                "runtime_plan_metrics": dict(metrics),
                "execution_allowed": False,
                "operator_action": (
                    "resolve source-declared request locations, ambiguous credential refs, "
                    "oracle templates or cleanup templates; do not invent values or execute"
                ),
            }
        )
    asset["coverage_gaps"] = gaps
    governance = as_dict(asset.get("governance"))
    governance.update(
        {
            "runtime_plan_v1_enabled": True,
            "runtime_plan_requires_execution_contract_gate": True,
            "runtime_plan_request_locations_require_source_contract": True,
            "runtime_plan_uses_credential_refs_only": True,
            "runtime_plan_plaintext_credentials_allowed": False,
            "runtime_plan_values_are_not_materialized": True,
            "runtime_plan_oracles_are_templates_not_assertions": True,
            "runtime_plan_cleanup_steps_are_templates_not_actions": True,
            "runtime_plan_does_not_enable_execution": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = [
    "RUNTIME_PLAN_SCHEMA",
    "RUNTIME_PLAN_GATE_SCHEMA",
    "build_runtime_plans_v1",
    "project_runtime_plans_to_asset",
]
