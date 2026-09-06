"""Behavior IR public facade with account-role and stable revision identity.

The immutable IR builder remains in :mod:`behavior_ir_core`. This facade preserves
runtime actor identity and annotates that same IR with deterministic cross-revision
identity. It never creates a parallel Behavior IR.
"""
from __future__ import annotations

from typing import Any

from . import behavior_ir_core as _core
from .runtime_actor_role_identity import resolve_runtime_actor_roles
from .stable_behavioral_identity import (
    StableBehaviorIdentityError,
    attach_stable_behavior_identity,
    attach_stable_behavioral_identity,
    build_ir_delta,
    build_minimum_ir_delta,
    derive_revision_identity,
    match_behavior_ir_revisions,
    validate_revision_identity,
)

for _name, _value in vars(_core).items():
    if _name.startswith("__") or _name == "build_behavior_ir_from_knowledge_asset":
        continue
    globals().setdefault(_name, _value)

_original_build_behavior_ir = _core.build_behavior_ir_from_knowledge_asset


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _runtime_actor_coordinates(
    runtime_actors: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    actors = [dict(row) for row in _list(runtime_actors) if isinstance(row, dict)]
    account_roles: dict[str, set[str]] = {}
    account_spellings: dict[str, str] = {}
    for actor in actors:
        role = _text(actor.get("role") or actor.get("name") or actor.get("id"))
        account = _text(
            actor.get("account_ref") or actor.get("email")
            or actor.get("username") or actor.get("id")
        )
        if role and account:
            key = account.casefold()
            account_spellings.setdefault(key, account)
            account_roles.setdefault(key, set()).add(role.casefold())

    projection: dict[str, dict[str, Any]] = {}
    prepared: list[dict[str, Any]] = []
    for actor in actors:
        role = _text(actor.get("role") or actor.get("name") or actor.get("id"))
        account = _text(
            actor.get("account_ref") or actor.get("email")
            or actor.get("username") or actor.get("id")
        )
        roles = account_roles.get(account.casefold(), set())
        if not account or not role or len(roles) <= 1:
            prepared.append(actor)
            continue
        synthetic = f"urn:qualibug:account-role:{account.casefold()}:{role.casefold()}"
        projected = dict(actor)
        projected["account_ref"] = synthetic
        prepared.append(projected)
        projection[synthetic] = {
            "account_ref": account_spellings.get(account.casefold(), account),
            "role_key": role.casefold(),
            "account_role_count": len(roles),
        }
    return prepared, projection


def build_behavior_ir_from_knowledge_asset(
    asset: dict[str, Any] | None,
    *,
    project_id: str = "",
    source_snapshot_hash: str = "",
    api_operations: list[dict[str, Any]] | None = None,
    runtime_actors: list[dict[str, Any]] | None = None,
    available_surfaces: dict[str, bool] | None = None,
    operation_path_scope: set[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    role_bound_actors, role_identity_receipt = resolve_runtime_actor_roles(
        asset, runtime_actors
    )
    prepared, projection = _runtime_actor_coordinates(role_bound_actors)
    model = _original_build_behavior_ir(
        asset,
        project_id=project_id,
        source_snapshot_hash=source_snapshot_hash,
        api_operations=api_operations,
        runtime_actors=prepared,
        available_surfaces=available_surfaces,
        operation_path_scope=operation_path_scope,
    )
    for actor in _list(model.get("actors")):
        if not isinstance(actor, dict):
            continue
        coordinate = projection.get(_text(actor.get("account_ref")))
        if not coordinate:
            continue
        account = coordinate["account_ref"]
        role_key = _text(actor.get("role_key") or coordinate.get("role_key"))
        actor["account_ref"] = account
        actor["credential_identity_ref"] = _core._stable_id(
            "credential_identity", account.casefold()
        )
        actor["role_assignment_ref"] = _core._stable_id(
            "role_assignment", account.casefold(), role_key
        )
        actor["account_role_count"] = int(coordinate["account_role_count"])
    # Runtime-observed identity ids survive the actor projection. The core
    # builder keeps role/account coordinates; bearer-token identity claims
    # (account_id) are runtime-observed material that read-side ownership
    # protocols bind "own identity" parameters from. Matched by account_ref —
    # structural, never guessed. Part of the runtime input, so the content
    # address legitimately reflects it.
    _runtime_account_ids = {
        _text(
            item.get("account_ref") or item.get("email") or item.get("username")
        ).casefold(): _text(item.get("account_id"))
        for item in _list(runtime_actors)
        if isinstance(item, dict) and _text(item.get("account_id"))
    }
    if _runtime_account_ids:
        for actor in _list(model.get("actors")):
            if not isinstance(actor, dict) or _text(actor.get("account_id")):
                continue
            _ref = _text(
                actor.get("account_ref") or actor.get("email") or actor.get("username")
            )
            _identity = _runtime_account_ids.get(_ref.casefold())
            if _identity:
                actor["account_id"] = _identity
    if role_identity_receipt.get("actor_count"):
        model["runtime_actor_role_identity_receipt"] = role_identity_receipt
    attach_stable_behavioral_identity(model)
    model["model_id"] = _core._content_addressed_id(model)
    return model


__all__ = sorted(
    name for name in globals()
    if not name.startswith("__") and name not in {"_core", "_name", "_value"}
)
