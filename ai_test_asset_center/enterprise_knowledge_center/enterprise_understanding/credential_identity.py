"""Canonical actor-account identity coordinates for runtime credential binding.

This module does not select secrets or infer enterprise identities.  It normalizes only
operator/source-declared account coordinates and joins them to the existing Actor
Authorization Contract.  Runtime planning must match the full declared coordinate before a
credential reference can become authoritative.
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable

from .schema import as_dict, as_list, text, unique_text

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


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", text(value).lower())


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
        if not ({_norm(value) for value in required_values} & {_norm(value) for value in candidate_values}):
            return False
    return True


def identity_coordinate_fingerprint(value: Any) -> str:
    coordinates = normalize_identity_coordinates(value)
    return json.dumps(coordinates, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
    contract: dict[str, Any], *, operation_ref: str, object_refs: Iterable[Any], decision: str
) -> bool:
    if text(contract.get("decision")).upper() != decision:
        return False
    if contract.get("coordinate_complete") is not True:
        return False
    actions = {_norm(value) for value in as_list(contract.get("actions")) if _norm(value)}
    resources = {
        _norm(value) for value in as_list(contract.get("resource_refs")) if _norm(value)
    }
    operation = _norm(operation_ref)
    objects = {_norm(value) for value in object_refs if _norm(value)}
    action_match = bool(operation and (operation in actions or _norm("*") in actions))
    resource_match = bool(objects and (objects & resources or _norm("*") in resources))
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


__all__ = [
    "authorization_identity_requirement",
    "credential_identity_record",
    "identity_coordinate_fingerprint",
    "identity_coordinates_match",
    "normalize_identity_coordinates",
]
