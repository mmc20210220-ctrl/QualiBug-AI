"""Single-variable identity comparison authority for authorization experiments.

Authorization findings are causal only when control and treatment differ in the declared
identity dimension and remain equal everywhere else. This module attaches that contract to
compiled experiments, binds operator-declared test-account coordinates at runtime, and
validates the pair before the existing executor reaches transport.

It does not select secrets, execute requests, infer tenant membership, or replace the existing
family protocol/compiler/executor authorities.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Iterable

from .enterprise_knowledge_center.enterprise_understanding.credential_identity import (
    credential_identity_record,
    identity_coordinates_match,
    normalize_identity_coordinates,
)

AUTHORIZATION_COMPARISON_SCHEMA = "qualibug.authorization-comparison-contract.v1"
_AUTHORIZATION_FAMILIES = frozenset({"authorization", "isolation", "visibility"})
_SCOPE_KEYS = (
    "tenant_ref",
    "organization_ref",
    "department_ref",
    "warehouse_ref",
    "project_ref",
    "region_ref",
    "ownership_scope",
)
_INACTIVE_STATUSES = frozenset({"DISABLED", "LOCKED", "INACTIVE", "REVOKED"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _text(value).lower())


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _actor_index(behavior_ir: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for raw in _list(_dict(behavior_ir).get("actors")):
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        actor_id = _text(row.get("id") or row.get("actor_id"))
        if actor_id:
            result[actor_id] = row
    return result


def _actor_role(actor: dict[str, Any]) -> str:
    return _text(actor.get("role_key") or actor.get("role") or actor.get("name"))


def _actor_coordinates(actor: dict[str, Any]) -> dict[str, list[str]]:
    for key in (
        "runtime_identity_coordinates",
        "credential_identity_coordinates",
        "identity_coordinates",
    ):
        coordinates = normalize_identity_coordinates(actor.get(key))
        if coordinates:
            return coordinates
    return normalize_identity_coordinates(actor)


def _pair_refs(experiment: dict[str, Any]) -> tuple[str, str]:
    selection = _dict(experiment.get("actor_selection_contract"))
    control = _text(selection.get("control_actor_ref"))
    treatment = _text(selection.get("treatment_actor_ref"))
    if not control:
        control = _text(_dict((_list(experiment.get("control_plan")) or [{}])[0]).get("actor_ref"))
    if not treatment:
        treatment = _text(_dict((_list(experiment.get("treatment_plan")) or [{}])[0]).get("actor_ref"))
    return control, treatment


def _dimension(family: str, prop: dict[str, Any]) -> str:
    explicit = _text(prop.get("comparison_dimension")).upper()
    if explicit in {"ROLE_PERMISSION", "OWNERSHIP_RELATION", "TENANT_SCOPE"}:
        return explicit
    template = _text(prop.get("template")).lower()
    ownership_declared = bool(
        _text(prop.get("ownership_param"))
        or _text(prop.get("owner_actor_ref"))
        or _text(prop.get("viewer_actor_ref"))
        or re.search(r"owner|ownership|own_data|self_only|所有者|本人|自己的", template)
    )
    if family == "isolation":
        return "OWNERSHIP_RELATION" if ownership_declared else "TENANT_SCOPE"
    if family == "visibility" and ownership_declared:
        return "OWNERSHIP_RELATION"
    return "ROLE_PERMISSION"


def resolve_comparison_dimension(family: str, prop: dict[str, Any]) -> str:
    """Public accessor for the comparison identity dimension.

    Observers and gates use the dimension to decide how much resource
    evidence a comparison verdict requires: ROLE_PERMISSION gates the
    operation itself (status equality is decisive), while
    OWNERSHIP_RELATION/TENANT_SCOPE partition per-owner resources and
    require body evidence that the viewer saw the owner's data.
    """
    return _dimension(family, prop)


def _request_shape(step: dict[str, Any]) -> dict[str, Any]:
    shape = {
        "path": deepcopy(step.get("path")),
    }
    # The protocol compiler omits empty request containers from the control
    # step, while the ownership binder adds only the changed container to the
    # treatment step. Treat an omitted empty container as the same structural
    # value as ``{}`` so the diff reaches the declared leaf (for example
    # ``query.user.id``) instead of reporting the entire container as an
    # unrelated request mutation. Non-mapping values remain visible and fail
    # closed through the existing asymmetry gate.
    for key in ("path_params", "query", "headers", "body"):
        if key not in step or step.get(key) is None:
            shape[key] = {}
        else:
            shape[key] = deepcopy(step.get(key))
    return shape


def _diff_paths(left: Any, right: Any, prefix: str = "") -> list[str]:
    if isinstance(left, dict) and isinstance(right, dict):
        paths: list[str] = []
        for key in sorted(set(left) | set(right)):
            child = f"{prefix}.{key}" if prefix else str(key)
            if key not in left or key not in right:
                paths.append(child)
            else:
                paths.extend(_diff_paths(left[key], right[key], child))
        return paths
    if isinstance(left, list) and isinstance(right, list):
        paths: list[str] = []
        for index in range(max(len(left), len(right))):
            child = f"{prefix}[{index}]"
            if index >= len(left) or index >= len(right):
                paths.append(child)
            else:
                paths.extend(_diff_paths(left[index], right[index], child))
        return paths
    return [] if left == right else [prefix or "$request"]


def _allowed_request_mutations(prop: dict[str, Any]) -> list[str]:
    parameter = _text(prop.get("ownership_param"))
    if not parameter:
        return []
    location = _text(prop.get("ownership_param_location")).lower() or "body"
    root = {
        "query": "query",
        "path": "path_params",
        "header": "headers",
        "body": "body",
    }.get(location, "body")
    return [f"{root}.{parameter}"]


def _path_allowed(path: str, allowed: Iterable[str]) -> bool:
    return any(path == prefix or path.startswith(prefix + ".") for prefix in allowed)


def _resource_binding_targets(experiment: dict[str, Any]) -> list[str]:
    targets: list[str] = []
    for raw in _list(experiment.get("binding_plan")):
        if not isinstance(raw, dict):
            continue
        target = _text(raw.get("binding_target") or raw.get("target"))
        if not target or target.startswith("actor:"):
            continue
        if target not in targets:
            targets.append(target)
    return targets


def _static_pair_problem(
    *,
    dimension: str,
    control_ref: str,
    treatment_ref: str,
    control_actor: dict[str, Any],
    treatment_actor: dict[str, Any],
    invariant_keys: list[str],
) -> str:
    if not control_ref or not treatment_ref or control_ref == treatment_ref:
        return "comparison_actor_pair_not_distinct"
    control_role = _norm(_actor_role(control_actor))
    treatment_role = _norm(_actor_role(treatment_actor))
    if dimension in {"OWNERSHIP_RELATION", "TENANT_SCOPE"}:
        if control_role and treatment_role and control_role != treatment_role:
            return f"comparison_role_changed_with_{dimension.lower()}"
    control_coordinates = _actor_coordinates(control_actor)
    treatment_coordinates = _actor_coordinates(treatment_actor)
    for key in invariant_keys:
        left = control_coordinates.get(key, [])
        right = treatment_coordinates.get(key, [])
        if left and right and not identity_coordinates_match({key: left}, {key: right}):
            return f"comparison_invariant_coordinate_mismatch:{key}"
    if dimension == "TENANT_SCOPE":
        left = control_coordinates.get("tenant_ref", [])
        right = treatment_coordinates.get("tenant_ref", [])
        if left and right and identity_coordinates_match({"tenant_ref": left}, {"tenant_ref": right}):
            return "comparison_tenant_dimension_not_distinct"
    return ""


def attach_authorization_comparison_contract(
    experiment: dict[str, Any],
    obligation: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    """Attach one causal identity comparison contract or return a typed blocker detail."""
    updated = deepcopy(_dict(experiment))
    if _text(_dict(updated.get("compile_receipt")).get("status")).upper() != "COMPILED":
        return updated, "", ""
    family = _text(updated.get("risk_family") or _dict(obligation).get("risk_family")).lower()
    if family not in _AUTHORIZATION_FAMILIES:
        return updated, "", ""
    prop = _dict(_dict(obligation).get("property"))
    if _text(prop.get("template")) == "permitted_operation_invocation":
        return updated, "", ""
    # Credential-gated write guard: single-arm by construction — the anonymous
    # write's rejection (http_status_class 4xx) IS the property, and there is
    # no authorized baseline to compare. Same exemption shape as the permit-only
    # template above.
    if _text(prop.get("template")) == "credential_gated_write":
        return updated, "", ""

    control_plan = [dict(row) for row in _list(updated.get("control_plan")) if isinstance(row, dict)]
    treatment_plan = [dict(row) for row in _list(updated.get("treatment_plan")) if isinstance(row, dict)]
    if len(control_plan) != 1 or len(treatment_plan) != 1:
        return updated, "BLOCKED_MISSING_ACTOR", "authorization_comparison_requires_one_control_and_one_treatment"
    control_ref, treatment_ref = _pair_refs(updated)
    actors = _actor_index(behavior_ir)
    control_actor = _dict(actors.get(control_ref))
    treatment_actor = _dict(actors.get(treatment_ref))
    if not control_actor or not treatment_actor:
        return updated, "BLOCKED_MISSING_ACTOR", "authorization_comparison_actor_identity_missing"

    control_operation = _text(control_plan[0].get("operation_ref"))
    treatment_operation = _text(treatment_plan[0].get("operation_ref"))
    if not control_operation or control_operation != treatment_operation:
        return updated, "BLOCKED_MISSING_OPERATION", "authorization_comparison_operation_mismatch"

    dimension = _dimension(family, prop)
    allowed_mutations = _allowed_request_mutations(prop)
    request_diffs = _diff_paths(_request_shape(control_plan[0]), _request_shape(treatment_plan[0]))
    illegal_diffs = [path for path in request_diffs if not _path_allowed(path, allowed_mutations)]
    if illegal_diffs:
        return (
            updated,
            "BLOCKED_MISSING_BINDING",
            "authorization_comparison_request_asymmetry:" + ",".join(illegal_diffs[:8]),
        )

    invariant_keys = [key for key in _SCOPE_KEYS if not (
        dimension == "TENANT_SCOPE" and key == "tenant_ref"
    ) and not (
        dimension == "OWNERSHIP_RELATION" and key == "ownership_scope"
    )]
    problem = _static_pair_problem(
        dimension=dimension,
        control_ref=control_ref,
        treatment_ref=treatment_ref,
        control_actor=control_actor,
        treatment_actor=treatment_actor,
        invariant_keys=invariant_keys,
    )
    if problem:
        return updated, "BLOCKED_MISSING_ACTOR", f"authorization_comparison_identity_invalid:{problem}"

    targets = _resource_binding_targets(updated)
    contract = {
        "schema_version": AUTHORIZATION_COMPARISON_SCHEMA,
        "status": "COMPILED_RUNTIME_VERIFICATION_REQUIRED",
        "risk_family": family,
        "comparison_dimension": dimension,
        "control_actor_ref": control_ref,
        "treatment_actor_ref": treatment_ref,
        "control_operation_ref": control_operation,
        "treatment_operation_ref": treatment_operation,
        "same_operation_required": True,
        "same_request_baseline_required": True,
        "allowed_request_mutation_paths": allowed_mutations,
        "observed_request_diff_paths": request_diffs,
        "invariant_identity_dimensions": invariant_keys,
        "allowed_varying_identity_dimensions": {
            "ROLE_PERMISSION": ["account_ref", "role", "permission_decision"],
            "OWNERSHIP_RELATION": ["account_ref", "ownership_scope"],
            "TENANT_SCOPE": ["account_ref", "tenant_ref"],
        }[dimension],
        "resource_identity_binding_targets": targets,
        # Every control/treatment authorization comparison must address the same
        # business resource. Placeholder targets determine materialization needs;
        # fixed-path and collection operations may prove identity from the sealed
        # comparison observer instead.
        "same_resource_identity_required": True,
        "shared_binding_graph_fingerprint": _fingerprint(
            {
                "targets": targets,
                "binding_plan": _list(updated.get("binding_plan")),
                "source_identity_fields": _list(updated.get("source_identity_fields")),
            }
        ),
        "control_actor_coordinates_at_compile": _actor_coordinates(control_actor),
        "treatment_actor_coordinates_at_compile": _actor_coordinates(treatment_actor),
        "runtime_identity_verification_required": True,
        "execution_allowed_without_runtime_verification": False,
        "automatic_identity_dimension_substitution_allowed": False,
        "source_refs": _list(_dict(obligation).get("source_refs"))[:5],
    }
    updated["authorization_comparison_contract"] = contract
    receipt = dict(_dict(updated.get("compile_receipt")))
    receipt["authorization_comparison_contract_status"] = contract["status"]
    receipt["authorization_comparison_dimension"] = dimension
    receipt["authorization_comparison_fingerprint"] = _fingerprint(contract)
    updated["compile_receipt"] = receipt
    return updated, "", ""


def _account_rows(account_rows: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in account_rows:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        status = _text(row.get("status") or row.get("account_status") or row.get("state") or "ACTIVE").upper()
        if status in _INACTIVE_STATUSES:
            continue
        account_ref = _text(row.get("account_ref") or row.get("account_id") or row.get("name") or row.get("id") or row.get("email"))
        if account_ref and not _text(row.get("credential_ref") or row.get("secret_ref")):
            row["credential_ref"] = f"secret_ref:test_accounts:{account_ref}"
        normalized = credential_identity_record(row)
        if _text(normalized.get("credential_ref")):
            result.append(normalized)
    return result


def bind_runtime_actor_identity_context(
    behavior_ir: dict[str, Any], account_rows: Iterable[Any]
) -> dict[str, Any]:
    """Bind declared account coordinates to actors without selecting or retaining secrets."""
    updated = deepcopy(_dict(behavior_ir))
    accounts = _account_rows(account_rows)
    actors: list[dict[str, Any]] = []
    for raw in _list(updated.get("actors")):
        if not isinstance(raw, dict):
            continue
        actor = dict(raw)
        actor_role = _norm(_actor_role(actor))
        actor_account = _norm(actor.get("account_ref") or actor.get("account_id"))
        actor_secret = _text(actor.get("credential_secret_ref") or actor.get("secret_ref") or actor.get("credential_ref"))
        exact = [
            row for row in accounts
            if (actor_secret and _text(row.get("credential_ref")) == actor_secret)
            or (actor_account and actor_account == _norm(row.get("account_ref")))
        ]
        if not exact and actor_role:
            exact = [
                row for row in accounts
                if actor_role in {_norm(value) for value in _list(row.get("roles"))}
            ]
        if len(exact) == 1:
            account = exact[0]
            actor["runtime_account_ref"] = account.get("account_ref")
            actor["runtime_credential_ref"] = account.get("credential_ref")
            actor["runtime_identity_coordinates"] = deepcopy(
                _dict(account.get("identity_coordinates"))
            )
            actor["runtime_identity_match_status"] = "EXACT"
            actor["runtime_identity_source"] = "TEST_ACCOUNT_DECLARATION"
        elif len(exact) > 1:
            actor["runtime_identity_match_status"] = "AMBIGUOUS"
            actor["runtime_identity_candidate_refs"] = [
                _text(row.get("credential_ref")) for row in exact
            ]
        else:
            actor["runtime_identity_match_status"] = "UNRESOLVED"
        actors.append(actor)
    updated["actors"] = actors
    return updated


def validate_authorization_comparison_contract(
    experiment: dict[str, Any], behavior_ir: dict[str, Any]
) -> tuple[bool, str, str]:
    """Validate the compiled single-variable comparison before transport."""
    contract = _dict(_dict(experiment).get("authorization_comparison_contract"))
    if not contract:
        return True, "", ""
    if _text(contract.get("status")) != "COMPILED_RUNTIME_VERIFICATION_REQUIRED":
        return False, "BLOCKED_MISSING_BINDING", "authorization_comparison_contract_invalid"
    actors = _actor_index(behavior_ir)
    control_ref = _text(contract.get("control_actor_ref"))
    treatment_ref = _text(contract.get("treatment_actor_ref"))
    control_actor = _dict(actors.get(control_ref))
    treatment_actor = _dict(actors.get(treatment_ref))
    if not control_actor or not treatment_actor:
        return False, "BLOCKED_MISSING_ACTOR", "authorization_comparison_runtime_actor_missing"
    for actor_ref, actor in ((control_ref, control_actor), (treatment_ref, treatment_actor)):
        status = _text(actor.get("runtime_identity_match_status"))
        if status == "AMBIGUOUS":
            return False, "BLOCKED_MISSING_ACTOR", f"authorization_comparison_account_ambiguous:{actor_ref}"

    control_coordinates = _actor_coordinates(control_actor)
    treatment_coordinates = _actor_coordinates(treatment_actor)
    for key in _list(contract.get("invariant_identity_dimensions")):
        coordinate = _text(key)
        left = control_coordinates.get(coordinate, [])
        right = treatment_coordinates.get(coordinate, [])
        if bool(left) != bool(right):
            return False, "BLOCKED_MISSING_ACTOR", f"authorization_comparison_coordinate_unpaired:{coordinate}"
        if left and right and not identity_coordinates_match({coordinate: left}, {coordinate: right}):
            return False, "BLOCKED_MISSING_ACTOR", f"authorization_comparison_coordinate_mismatch:{coordinate}"

    dimension = _text(contract.get("comparison_dimension"))
    control_role = _norm(_actor_role(control_actor))
    treatment_role = _norm(_actor_role(treatment_actor))
    if dimension in {"OWNERSHIP_RELATION", "TENANT_SCOPE"}:
        if control_role and treatment_role and control_role != treatment_role:
            return False, "BLOCKED_MISSING_ACTOR", f"authorization_comparison_role_not_symmetric:{dimension.lower()}"
    if dimension == "TENANT_SCOPE":
        left = control_coordinates.get("tenant_ref", [])
        right = treatment_coordinates.get("tenant_ref", [])
        if not left or not right:
            return False, "BLOCKED_MISSING_ACTOR", "authorization_comparison_tenant_coordinate_unresolved"
        if identity_coordinates_match({"tenant_ref": left}, {"tenant_ref": right}):
            return False, "BLOCKED_MISSING_ACTOR", "authorization_comparison_tenant_not_distinct"
    if dimension == "OWNERSHIP_RELATION":
        left = control_coordinates.get("ownership_scope", [])
        right = treatment_coordinates.get("ownership_scope", [])
        if left and right and identity_coordinates_match({"ownership_scope": left}, {"ownership_scope": right}):
            return False, "BLOCKED_MISSING_ACTOR", "authorization_comparison_ownership_not_distinct"
        if not _list(contract.get("resource_identity_binding_targets")):
            return False, "BLOCKED_MISSING_BINDING", "authorization_comparison_resource_identity_unresolved"

    control_plan = [row for row in _list(_dict(experiment).get("control_plan")) if isinstance(row, dict)]
    treatment_plan = [row for row in _list(_dict(experiment).get("treatment_plan")) if isinstance(row, dict)]
    if len(control_plan) != 1 or len(treatment_plan) != 1:
        return False, "BLOCKED_MISSING_BINDING", "authorization_comparison_plan_shape_drift"
    if _text(control_plan[0].get("operation_ref")) != _text(treatment_plan[0].get("operation_ref")):
        return False, "BLOCKED_MISSING_OPERATION", "authorization_comparison_operation_drift"
    diffs = _diff_paths(_request_shape(control_plan[0]), _request_shape(treatment_plan[0]))
    allowed = [_text(value) for value in _list(contract.get("allowed_request_mutation_paths")) if _text(value)]
    illegal = [path for path in diffs if not _path_allowed(path, allowed)]
    if illegal:
        return False, "BLOCKED_MISSING_BINDING", "authorization_comparison_request_drift:" + ",".join(illegal[:8])
    return True, "", ""


__all__ = [
    "AUTHORIZATION_COMPARISON_SCHEMA",
    "attach_authorization_comparison_contract",
    "bind_runtime_actor_identity_context",
    "validate_authorization_comparison_contract",
]
