"""Canonical actor-account identity coordinates for runtime credential binding.

This module does not select secrets or infer enterprise identities. It normalizes only
operator/source-declared account coordinates and joins them to the existing Actor
Authorization Contract. Runtime planning must match the full declared coordinate before a
credential reference can become authoritative.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .schema import as_dict, as_list, stable_id, text, unique_text

_IDENTITY_COORDINATE_ALIASES: dict[str, tuple[str, ...]] = {
    "account_ref": (
        "account_ref",
        "account_id",
        "user_ref",
        "user_id",
        "principal_ref",
        "principal_id",
    ),
    "tenant_ref": (
        "tenant_ref",
        "tenant_id",
        "tenant",
        "tenant_scope",
    ),
    "organization_ref": (
        "organization_ref",
        "organization_id",
        "organization",
        "organization_scope",
        "org_ref",
        "org_id",
        "org",
        "org_scope",
    ),
    "department_ref": (
        "department_ref",
        "department_id",
        "department",
        "department_scope",
    ),
    "warehouse_ref": (
        "warehouse_ref",
        "warehouse_id",
        "warehouse",
        "warehouse_scope",
    ),
    "project_ref": (
        "project_ref",
        "project_id",
        "project",
        "project_scope",
    ),
    "region_ref": (
        "region_ref",
        "region_id",
        "region",
        "region_scope",
    ),
    "ownership_scope": (
        "ownership_scope",
        "ownership",
        "owner_scope",
        "data_scope",
        "resource_scope",
        "self_only",
        "own_data_only",
    ),
}
_NESTED_COORDINATE_KEYS = (
    "identity_coordinates",
    "required_identity_coordinates",
    "credential_identity_coordinates",
    "authorization_identity_scope",
    "identity_scope",
    "scope",
)
_ACTIVE_ACCOUNT_STATUSES = {
    "",
    "ACTIVE",
    "APPROVED",
    "READY",
    "VALIDATED",
    "ENABLED",
}
_FORMAL_AUTHORIZATION_DECISIONS = {"ALLOW", "DENY"}
_REPLACED_RUNTIME_PLAN_CREDENTIAL_REASONS = {
    "RUNTIME_PLAN_CREDENTIAL_REF_AMBIGUOUS",
    "RUNTIME_PLAN_CREDENTIAL_REF_UNRESOLVED",
    "RUNTIME_PLAN_CREDENTIAL_IDENTITY_COORDINATE_MISMATCH",
    "RUNTIME_PLAN_AUTHORIZATION_IDENTITY_SCOPE_AMBIGUOUS",
    "RUNTIME_PLAN_AUTHORIZATION_CONTRACT_UNRESOLVED",
    "RUNTIME_PLAN_CREDENTIAL_REQUIREMENT_UNRESOLVED",
}
_MATERIALIZATION_IDENTITY_REASONS = {
    "RUNTIME_MATERIALIZATION_CREDENTIAL_IDENTITY_UNRESOLVED",
    "RUNTIME_MATERIALIZATION_CREDENTIAL_IDENTITY_SUBSTITUTED",
}


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text(value).lower())


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _coordinate_values(value: Any) -> list[str]:
    if isinstance(value, bool):
        return ["TRUE" if value else "FALSE"]
    values = as_list(value) if isinstance(value, (list, tuple, set)) else [value]
    return unique_text(values)


def normalize_identity_coordinates(value: Any) -> dict[str, list[str]]:
    """Return canonical declared identity coordinates without semantic guessing."""
    root = as_dict(value)
    sources = [root]
    for key in _NESTED_COORDINATE_KEYS:
        nested = as_dict(root.get(key))
        if nested:
            sources.append(nested)

    result: dict[str, list[str]] = {}
    for canonical, aliases in _IDENTITY_COORDINATE_ALIASES.items():
        values: list[str] = []
        for source in sources:
            lowered = {text(key).lower(): raw for key, raw in source.items()}
            for alias in aliases:
                values.extend(_coordinate_values(lowered.get(alias.lower())))
        normalized = unique_text(values)
        if normalized:
            result[canonical] = normalized
    return result


def identity_coordinates_match(
    required: dict[str, Any], candidate: dict[str, Any]
) -> bool:
    """Every required coordinate must intersect the declared account coordinate."""
    required_rows = normalize_identity_coordinates(required)
    candidate_rows = normalize_identity_coordinates(candidate)
    for key, required_values in required_rows.items():
        candidate_values = candidate_rows.get(key, [])
        if not candidate_values:
            return False
        if not (
            {_norm(value) for value in required_values}
            & {_norm(value) for value in candidate_values}
        ):
            return False
    return True


def identity_coordinate_fingerprint(value: Any) -> str:
    coordinates = normalize_identity_coordinates(value)
    return json.dumps(
        coordinates,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def credential_identity_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize one operator-declared credential/account row without retaining a secret."""
    credential_ref = text(raw.get("credential_ref") or raw.get("secret_ref"))
    actor_ref = text(raw.get("actor_ref") or raw.get("role"))
    roles = unique_text(
        [raw.get("actor_ref"), raw.get("role"), *as_list(raw.get("roles"))]
    )
    coordinates = normalize_identity_coordinates(raw)
    account_ref = text(raw.get("account_ref") or raw.get("account_id"))
    if account_ref and "account_ref" not in coordinates:
        coordinates["account_ref"] = [account_ref]
    status = text(
        raw.get("status")
        or raw.get("account_status")
        or raw.get("state")
    ).upper()
    return {
        "credential_ref": credential_ref,
        "account_ref": account_ref,
        "actor_ref": actor_ref,
        "roles": roles,
        "environment_ref": text(raw.get("environment_ref")),
        "identity_coordinates": coordinates,
        "identity_coordinate_fingerprint": identity_coordinate_fingerprint(coordinates),
        "account_status": status or "ACTIVE",
        "account_active": status in _ACTIVE_ACCOUNT_STATUSES,
        "source_backed": True,
        "secret_value_retained": False,
    }


def _actor_matches(row: dict[str, Any], actor_ref: str) -> bool:
    target = _norm(actor_ref)
    return bool(
        target
        and target
        in {
            _norm(row.get("actor_id")),
            _norm(row.get("id")),
            _norm(row.get("name")),
            _norm(row.get("role")),
        }
    )


def _coordinate_matches_contract(
    contract: dict[str, Any],
    *,
    operation_ref: str,
    object_refs: Iterable[Any],
    decision: str,
) -> bool:
    if text(contract.get("decision")).upper() != decision:
        return False
    if contract.get("coordinate_complete") is not True:
        return False
    raw_actions = [text(value) for value in as_list(contract.get("actions")) if text(value)]
    raw_resources = [
        text(value) for value in as_list(contract.get("resource_refs")) if text(value)
    ]
    actions = {_norm(value) for value in raw_actions if value != "*" and _norm(value)}
    resources = {
        _norm(value) for value in raw_resources if value != "*" and _norm(value)
    }
    operation = _norm(operation_ref)
    objects = {_norm(value) for value in object_refs if _norm(value)}
    action_match = bool(operation and (operation in actions or "*" in raw_actions))
    resource_match = bool(objects and (objects & resources or "*" in raw_resources))
    return action_match and resource_match


def authorization_identity_requirement(
    contract: dict[str, Any],
    model: dict[str, Any],
    requirement: dict[str, Any],
) -> dict[str, Any]:
    """Resolve the unique Actor Authorization Contract identity scope for a credential slot."""
    explicit = normalize_identity_coordinates(
        requirement.get("required_identity_coordinates")
        or requirement.get("authorization_identity_scope")
    )
    if explicit:
        return {
            "status": "RESOLVED",
            "required_identity_coordinates": explicit,
            "source": "SCENARIO_EXECUTION_CONTRACT",
            "candidate_authorization_contract_refs": [],
        }

    decision = text(
        as_dict(contract.get("oracle_plan")).get("permission_decision_requirement")
    ).upper()
    if decision not in _FORMAL_AUTHORIZATION_DECISIONS:
        return {
            "status": "NOT_REQUIRED",
            "required_identity_coordinates": {},
            "source": "NON_AUTHORIZATION_EXECUTION_CONTRACT",
            "candidate_authorization_contract_refs": [],
        }

    actor_ref = text(requirement.get("actor_ref"))
    actors = [
        row
        for row in as_list(model.get("actors"))
        if isinstance(row, dict) and _actor_matches(row, actor_ref)
    ]
    if len(actors) != 1:
        return {
            "status": "ACTOR_UNRESOLVED" if not actors else "ACTOR_AMBIGUOUS",
            "required_identity_coordinates": {},
            "source": "ACTOR_AUTHORIZATION_MODEL",
            "candidate_authorization_contract_refs": [],
        }

    operation_clause = as_dict(contract.get("operation_clause"))
    operation_ref = text(operation_clause.get("operation_ref"))
    object_refs = unique_text(as_list(operation_clause.get("object_refs")))
    candidates = [
        row
        for row in as_list(actors[0].get("authorization_contracts"))
        if isinstance(row, dict)
        and _coordinate_matches_contract(
            row,
            operation_ref=operation_ref,
            object_refs=object_refs,
            decision=decision,
        )
    ]
    if not candidates:
        return {
            "status": "AUTHORIZATION_CONTRACT_UNRESOLVED",
            "required_identity_coordinates": {},
            "source": "ACTOR_AUTHORIZATION_MODEL",
            "candidate_authorization_contract_refs": [],
        }

    by_scope: dict[str, dict[str, Any]] = {}
    refs_by_scope: dict[str, list[str]] = {}
    for row in candidates:
        scope = normalize_identity_coordinates(row.get("scope"))
        fingerprint = identity_coordinate_fingerprint(scope)
        by_scope[fingerprint] = scope
        refs_by_scope.setdefault(fingerprint, []).append(
            text(row.get("authorization_contract_id"))
        )
    if len(by_scope) > 1:
        return {
            "status": "AUTHORIZATION_SCOPE_AMBIGUOUS",
            "required_identity_coordinates": {},
            "source": "ACTOR_AUTHORIZATION_MODEL",
            "candidate_authorization_contract_refs": unique_text(
                ref for refs in refs_by_scope.values() for ref in refs
            ),
            "candidate_identity_scopes": list(by_scope.values()),
        }
    fingerprint, scope = next(iter(by_scope.items()))
    return {
        "status": "RESOLVED" if scope else "ROLE_ONLY_RESOLVED",
        "required_identity_coordinates": scope,
        "required_identity_coordinate_fingerprint": fingerprint,
        "source": "ACTOR_AUTHORIZATION_MODEL",
        "candidate_authorization_contract_refs": unique_text(
            refs_by_scope.get(fingerprint, [])
        ),
    }


def _credential_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    containers = [
        asset,
        as_dict(asset.get("environment_contract")),
        as_dict(asset.get("runtime_environment")),
        as_dict(asset.get("project_configuration")),
    ]
    for container in containers:
        for key in ("credential_refs", "test_account_refs"):
            value = container.get(key)
            if isinstance(value, dict):
                if text(value.get("credential_ref") or value.get("secret_ref")):
                    rows.append(dict(value))
                else:
                    for account_ref, raw in value.items():
                        if isinstance(raw, dict):
                            rows.append(
                                {
                                    **raw,
                                    "account_ref": raw.get("account_ref") or account_ref,
                                    "credential_ref": raw.get("credential_ref")
                                    or raw.get("secret_ref")
                                    or f"secret_ref:test_accounts:{account_ref}",
                                }
                            )
            rows.extend(_dicts(value))
    normalized = [credential_identity_record(row) for row in rows]
    return list(
        {
            text(row.get("credential_ref")): row
            for row in normalized
            if text(row.get("credential_ref"))
        }.values()
    )


def _role_matches(actor_ref: str, row: dict[str, Any]) -> bool:
    actor = _norm(actor_ref)
    return bool(
        actor
        and actor
        in {_norm(value) for value in as_list(row.get("roles")) if _norm(value)}
    )


def _plan_unknown(
    plan_id: str,
    contract_id: str,
    actor_ref: str,
    reason: str,
    **details: Any,
) -> dict[str, Any]:
    return {
        "unknown_id": stable_id(
            "runtime_plan_unknown",
            plan_id,
            reason,
            actor_ref,
            details.get("required_identity_coordinate_fingerprint"),
        ),
        "kind": reason,
        "reason_code": reason,
        "runtime_plan_ref": plan_id,
        "contract_ref": contract_id,
        "actor_ref": actor_ref,
        "blocks_runtime_plan": True,
        "execution_allowed": False,
        **details,
    }


def govern_runtime_plan_credentials(
    asset: dict[str, Any], model: dict[str, Any]
) -> None:
    """Replace role-only credential selection with full authorization-coordinate matching."""
    contracts = {
        text(row.get("contract_id")): row
        for row in [
            *_dicts(asset.get("scenario_execution_contracts")),
            *_dicts(model.get("scenario_execution_contracts")),
        ]
        if text(row.get("contract_id"))
    }
    catalog = _credential_rows(asset)
    plans = _dicts(asset.get("runtime_plans"))
    existing_unknowns = [
        row
        for row in _dicts(asset.get("runtime_plan_unknowns"))
        if text(row.get("reason_code"))
        not in _REPLACED_RUNTIME_PLAN_CREDENTIAL_REASONS
    ]
    governed: list[dict[str, Any]] = []
    new_unknowns: list[dict[str, Any]] = []

    for raw_plan in plans:
        plan = dict(raw_plan)
        plan_id = text(plan.get("plan_id"))
        contract_id = text(plan.get("execution_contract_ref"))
        contract = contracts.get(contract_id) or {}
        requirements = _dicts(contract.get("credential_requirements"))
        template = dict(as_dict(plan.get("credential_template")))
        slots: list[dict[str, Any]] = []

        for raw_slot in _dicts(template.get("credential_slots")):
            slot = dict(raw_slot)
            actor_ref = text(slot.get("actor_ref"))
            matching_requirements = [
                row
                for row in requirements
                if _norm(row.get("actor_ref")) == _norm(actor_ref)
            ]
            if len(matching_requirements) != 1:
                reason = "RUNTIME_PLAN_CREDENTIAL_REQUIREMENT_UNRESOLVED"
                new_unknowns.append(
                    _plan_unknown(
                        plan_id,
                        contract_id,
                        actor_ref,
                        reason,
                        requirement_count=len(matching_requirements),
                    )
                )
                slot.update(
                    {
                        "credential_ref": None,
                        "account_ref": None,
                        "required_identity_coordinates": {},
                        "credential_identity_coordinates": {},
                        "identity_match_status": "UNRESOLVED",
                        "resolution_status": reason,
                    }
                )
                slots.append(slot)
                continue

            authority = authorization_identity_requirement(
                contract,
                model,
                matching_requirements[0],
            )
            authority_status = text(authority.get("status"))
            required_coordinates = normalize_identity_coordinates(
                authority.get("required_identity_coordinates")
            )
            blocking_status_reason = {
                "ACTOR_UNRESOLVED": "RUNTIME_PLAN_AUTHORIZATION_CONTRACT_UNRESOLVED",
                "ACTOR_AMBIGUOUS": "RUNTIME_PLAN_AUTHORIZATION_CONTRACT_UNRESOLVED",
                "AUTHORIZATION_CONTRACT_UNRESOLVED": "RUNTIME_PLAN_AUTHORIZATION_CONTRACT_UNRESOLVED",
                "AUTHORIZATION_SCOPE_AMBIGUOUS": "RUNTIME_PLAN_AUTHORIZATION_IDENTITY_SCOPE_AMBIGUOUS",
            }.get(authority_status)
            if blocking_status_reason:
                new_unknowns.append(
                    _plan_unknown(
                        plan_id,
                        contract_id,
                        actor_ref,
                        blocking_status_reason,
                        authorization_resolution_status=authority_status,
                        candidate_authorization_contract_refs=authority.get(
                            "candidate_authorization_contract_refs"
                        ),
                        candidate_identity_scopes=authority.get(
                            "candidate_identity_scopes"
                        ),
                    )
                )
                slot.update(
                    {
                        "credential_ref": None,
                        "account_ref": None,
                        "required_identity_coordinates": required_coordinates,
                        "credential_identity_coordinates": {},
                        "identity_match_status": "UNRESOLVED",
                        "resolution_status": blocking_status_reason,
                    }
                )
                slots.append(slot)
                continue

            role_matches = [
                row
                for row in catalog
                if row.get("account_active") is True and _role_matches(actor_ref, row)
            ]
            exact_matches = [
                row
                for row in role_matches
                if not required_coordinates
                or identity_coordinates_match(
                    required_coordinates,
                    as_dict(row.get("identity_coordinates")),
                )
            ]
            if not exact_matches:
                reason = (
                    "RUNTIME_PLAN_CREDENTIAL_IDENTITY_COORDINATE_MISMATCH"
                    if role_matches and required_coordinates
                    else "RUNTIME_PLAN_CREDENTIAL_REF_UNRESOLVED"
                )
                new_unknowns.append(
                    _plan_unknown(
                        plan_id,
                        contract_id,
                        actor_ref,
                        reason,
                        required_identity_coordinates=required_coordinates,
                        required_identity_coordinate_fingerprint=identity_coordinate_fingerprint(
                            required_coordinates
                        ),
                        role_candidate_credential_refs=unique_text(
                            row.get("credential_ref") for row in role_matches
                        ),
                    )
                )
                selected: dict[str, Any] = {}
            elif len(exact_matches) > 1:
                reason = "RUNTIME_PLAN_CREDENTIAL_REF_AMBIGUOUS"
                new_unknowns.append(
                    _plan_unknown(
                        plan_id,
                        contract_id,
                        actor_ref,
                        reason,
                        required_identity_coordinates=required_coordinates,
                        required_identity_coordinate_fingerprint=identity_coordinate_fingerprint(
                            required_coordinates
                        ),
                        candidate_credential_refs=sorted(
                            text(row.get("credential_ref")) for row in exact_matches
                        ),
                        candidate_account_refs=sorted(
                            text(row.get("account_ref")) for row in exact_matches
                        ),
                    )
                )
                selected = {}
            else:
                selected = exact_matches[0]

            selected_coordinates = normalize_identity_coordinates(
                selected.get("identity_coordinates")
            )
            match_status = (
                "EXACT"
                if selected and required_coordinates
                else "ROLE_ONLY_RESOLVED"
                if selected
                else "UNRESOLVED"
            )
            slot.update(
                {
                    "credential_ref": selected.get("credential_ref"),
                    "account_ref": selected.get("account_ref"),
                    "environment_ref": selected.get("environment_ref")
                    or slot.get("environment_ref"),
                    "required_identity_coordinates": required_coordinates,
                    "required_identity_coordinate_fingerprint": identity_coordinate_fingerprint(
                        required_coordinates
                    ),
                    "credential_identity_coordinates": selected_coordinates,
                    "credential_identity_coordinate_fingerprint": selected.get(
                        "identity_coordinate_fingerprint"
                    ),
                    "authorization_identity_source": authority.get("source"),
                    "authorization_contract_refs": authority.get(
                        "candidate_authorization_contract_refs"
                    ),
                    "identity_match_status": match_status,
                    "resolution_status": (
                        "CREDENTIAL_IDENTITY_RESOLVED"
                        if selected
                        else "RUNTIME_CREDENTIAL_IDENTITY_REQUIRED"
                    ),
                    "credential_value_loaded": False,
                    "automatic_role_substitution_allowed": False,
                }
            )
            slots.append(slot)

        template["credential_slots"] = slots
        template["credentials_selected"] = bool(slots) and all(
            text(row.get("credential_ref"))
            and text(row.get("identity_match_status"))
            in {"EXACT", "ROLE_ONLY_RESOLVED"}
            for row in slots
        )
        template["credential_identity_coordinates_required"] = any(
            bool(as_dict(row.get("required_identity_coordinates"))) for row in slots
        )
        template["credential_identity_coordinates_resolved"] = bool(slots) and all(
            text(row.get("identity_match_status"))
            in {"EXACT", "ROLE_ONLY_RESOLVED"}
            for row in slots
        )
        template["credential_selection_by_role_order_allowed"] = False
        plan["credential_template"] = template
        governed.append(plan)

    all_unknowns = list(
        {
            text(row.get("unknown_id")): row
            for row in [*existing_unknowns, *new_unknowns]
            if text(row.get("unknown_id"))
        }.values()
    )
    blocking_by_plan = {
        text(row.get("runtime_plan_ref"))
        for row in all_unknowns
        if bool(row.get("blocks_runtime_plan"))
    }
    rebuilt: list[dict[str, Any]] = []
    for raw_plan in governed:
        plan = dict(raw_plan)
        plan_id = text(plan.get("plan_id"))
        reasons = unique_text(
            row.get("reason_code")
            for row in all_unknowns
            if text(row.get("runtime_plan_ref")) == plan_id
        )
        prior_reasons = [
            value
            for value in as_list(plan.get("unresolved_runtime_plan_semantics"))
            if text(value) not in _REPLACED_RUNTIME_PLAN_CREDENTIAL_REASONS
        ]
        plan["unresolved_runtime_plan_semantics"] = unique_text(
            [*prior_reasons, *reasons]
        )
        if plan_id in blocking_by_plan:
            plan["status"] = "INCOMPLETE"
            plan["formal_runtime_plan"] = False
        plan["credential_identity_governed"] = True
        plan["execution_allowed"] = False
        rebuilt.append(plan)

    asset["runtime_plans"] = rebuilt
    model["runtime_plans"] = [dict(row) for row in rebuilt]
    asset["runtime_plan_unknowns"] = all_unknowns
    model["runtime_plan_unknowns"] = [dict(row) for row in all_unknowns]


def _materialization_unknown(
    materialization_id: str,
    plan_id: str,
    actor_ref: str,
    reason: str,
    **details: Any,
) -> dict[str, Any]:
    return {
        "unknown_id": stable_id(
            "runtime_materialization_unknown",
            materialization_id,
            reason,
            actor_ref,
        ),
        "kind": reason,
        "reason_code": reason,
        "runtime_materialization_ref": materialization_id,
        "runtime_plan_ref": plan_id,
        "actor_ref": actor_ref,
        "blocks_runtime_materialization": True,
        "execution_allowed": False,
        **details,
    }


def govern_runtime_materialization_credentials(
    asset: dict[str, Any], model: dict[str, Any]
) -> None:
    """Preserve and revalidate governed account coordinates during materialization."""
    plans = {
        text(row.get("plan_id")): row
        for row in _dicts(asset.get("runtime_plans"))
        if text(row.get("plan_id"))
    }
    existing_unknowns = [
        row
        for row in _dicts(asset.get("runtime_materialization_unknowns"))
        if text(row.get("reason_code")) not in _MATERIALIZATION_IDENTITY_REASONS
    ]
    new_unknowns: list[dict[str, Any]] = []
    materializations: list[dict[str, Any]] = []

    for raw_materialization in _dicts(asset.get("runtime_materializations")):
        materialization = dict(raw_materialization)
        materialization_id = text(materialization.get("materialization_id"))
        plan_id = text(materialization.get("runtime_plan_ref"))
        plan = plans.get(plan_id) or {}
        plan_slots = {
            text(row.get("slot_id")): row
            for row in _dicts(
                as_dict(plan.get("credential_template")).get("credential_slots")
            )
            if text(row.get("slot_id"))
        }
        credentials = dict(as_dict(materialization.get("credential_binding")))
        slots: list[dict[str, Any]] = []
        for raw_slot in _dicts(credentials.get("credential_slots")):
            slot = dict(raw_slot)
            plan_slot = plan_slots.get(text(slot.get("slot_id")))
            if plan_slot is None:
                actor = text(slot.get("actor_ref"))
                candidates = [
                    row
                    for row in plan_slots.values()
                    if _norm(row.get("actor_ref")) == _norm(actor)
                ]
                plan_slot = candidates[0] if len(candidates) == 1 else None
            actor_ref = text(slot.get("actor_ref") or as_dict(plan_slot).get("actor_ref"))
            if not plan_slot or text(plan_slot.get("identity_match_status")) not in {
                "EXACT",
                "ROLE_ONLY_RESOLVED",
            }:
                reason = "RUNTIME_MATERIALIZATION_CREDENTIAL_IDENTITY_UNRESOLVED"
                new_unknowns.append(
                    _materialization_unknown(
                        materialization_id,
                        plan_id,
                        actor_ref,
                        reason,
                    )
                )
                slot.update(
                    {
                        "credential_ref": None,
                        "identity_match_status": "UNRESOLVED",
                        "resolution_status": reason,
                    }
                )
                slots.append(slot)
                continue
            if (
                text(slot.get("credential_ref"))
                and text(slot.get("credential_ref"))
                != text(plan_slot.get("credential_ref"))
            ):
                reason = "RUNTIME_MATERIALIZATION_CREDENTIAL_IDENTITY_SUBSTITUTED"
                new_unknowns.append(
                    _materialization_unknown(
                        materialization_id,
                        plan_id,
                        actor_ref,
                        reason,
                        planned_credential_ref=plan_slot.get("credential_ref"),
                        materialized_credential_ref=slot.get("credential_ref"),
                    )
                )
                slot.update(
                    {
                        "credential_ref": None,
                        "identity_match_status": "UNRESOLVED",
                        "resolution_status": reason,
                    }
                )
                slots.append(slot)
                continue
            slot.update(
                {
                    "actor_ref": plan_slot.get("actor_ref"),
                    "credential_ref": plan_slot.get("credential_ref"),
                    "account_ref": plan_slot.get("account_ref"),
                    "environment_ref": plan_slot.get("environment_ref"),
                    "required_identity_coordinates": plan_slot.get(
                        "required_identity_coordinates"
                    ),
                    "required_identity_coordinate_fingerprint": plan_slot.get(
                        "required_identity_coordinate_fingerprint"
                    ),
                    "credential_identity_coordinates": plan_slot.get(
                        "credential_identity_coordinates"
                    ),
                    "credential_identity_coordinate_fingerprint": plan_slot.get(
                        "credential_identity_coordinate_fingerprint"
                    ),
                    "authorization_contract_refs": plan_slot.get(
                        "authorization_contract_refs"
                    ),
                    "identity_match_status": plan_slot.get("identity_match_status"),
                    "binding_kind": "CREDENTIAL_REFERENCE",
                    "secret_loader_required": True,
                    "secret_value_loaded": False,
                    "secret_value_retained": False,
                    "automatic_role_substitution_allowed": False,
                }
            )
            slots.append(slot)
        credentials["credential_slots"] = slots
        credentials["credential_refs_resolved"] = bool(slots) and all(
            text(row.get("credential_ref"))
            and text(row.get("identity_match_status"))
            in {"EXACT", "ROLE_ONLY_RESOLVED"}
            for row in slots
        )
        credentials["credential_identity_coordinates_resolved"] = bool(slots) and all(
            text(row.get("identity_match_status"))
            in {"EXACT", "ROLE_ONLY_RESOLVED"}
            for row in slots
        )
        credentials["credential_selection_by_role_order_allowed"] = False
        materialization["credential_binding"] = credentials
        materialization["credential_identity_governed"] = True
        materializations.append(materialization)

    all_unknowns = list(
        {
            text(row.get("unknown_id")): row
            for row in [*existing_unknowns, *new_unknowns]
            if text(row.get("unknown_id"))
        }.values()
    )
    asset["runtime_materializations"] = materializations
    model["runtime_materializations"] = [dict(row) for row in materializations]
    asset["runtime_materialization_unknowns"] = all_unknowns
    model["runtime_materialization_unknowns"] = [
        dict(row) for row in all_unknowns
    ]


__all__ = [
    "authorization_identity_requirement",
    "credential_identity_record",
    "govern_runtime_materialization_credentials",
    "govern_runtime_plan_credentials",
    "identity_coordinate_fingerprint",
    "identity_coordinates_match",
    "normalize_identity_coordinates",
]
