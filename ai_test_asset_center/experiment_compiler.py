"""Scope source conflicts and preserve single-actor privacy field checks."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import experiment_compiler_conflict_base as _base
from .experiment_compiler_conflict_base import *  # noqa: F401,F403


_original_compile_experiment = _base._original_compile_experiment


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _add_refs(target: set[str], value: Any) -> None:
    if isinstance(value, (str, int)) and _text(value):
        target.add(_text(value))
    elif isinstance(value, list):
        for item in value:
            _add_refs(target, item)


def _refs_from_mapping(value: dict[str, Any]) -> set[str]:
    refs: set[str] = set()
    explicit_keys = {
        "subject_ref", "subject_refs", "operation_ref", "operation_refs",
        "operation_id", "operation_ids", "actor_ref", "actor_refs",
        "invariant_ref", "invariant_refs", "rule_ref", "rule_refs",
        "entity_ref", "entity_refs", "state_ref", "state_refs",
        "from_ref", "to_ref", "node_ref", "node_refs",
        "relation_ref", "relation_refs",
    }
    for key, child in value.items():
        normalized = _text(key).lower()
        if normalized in {"source_ref", "source_refs", "secret_ref", "credential_secret_ref"}:
            continue
        if (
            normalized in explicit_keys
            or normalized.endswith("_ref")
            or normalized.endswith("_refs")
        ):
            _add_refs(refs, child)
        elif isinstance(child, dict):
            refs.update(_refs_from_mapping(child))
    return refs


def _obligation_scope_refs(obligation: dict[str, Any]) -> set[str]:
    row = _dict(obligation)
    refs = _refs_from_mapping(row)
    _add_refs(refs, row.get("subject_refs"))
    _add_refs(refs, row.get("required_operations"))
    _add_refs(refs, row.get("required_actors"))
    _add_refs(refs, row.get("relation_refs"))
    return refs


def _conflict_is_relevant(
    conflict: dict[str, Any],
    *,
    obligation_refs: set[str],
    obligation_actor_roles: set[str] | None,
) -> bool:
    if _text(conflict.get("status")) != "conflicting":
        return True
    if _text(conflict.get("conflict_type")) == "permission_decision_conflict":
        conflict_role = _text(conflict.get("role_key")).lower()
        if (
            conflict_role
            and obligation_actor_roles is not None
            and conflict_role not in obligation_actor_roles
        ):
            return False
    conflict_refs = _refs_from_mapping(_dict(conflict))
    # An unscoped conflict is global and remains fail-closed. Only conflicts
    # that explicitly identify other IR nodes are safe to remove here.
    if not conflict_refs or not obligation_refs:
        return True
    return bool(conflict_refs.intersection(obligation_refs))


def _obligation_actor_roles(
    behavior_ir: dict[str, Any],
    obligation: dict[str, Any],
) -> set[str] | None:
    actors = {
        _text(actor.get("id")): _text(
            actor.get("role_key") or actor.get("role")
        ).lower()
        for actor in _list(_dict(behavior_ir).get("actors"))
        if isinstance(actor, dict) and _text(actor.get("id"))
    }
    actor_refs = _refs_from_mapping(_dict(obligation)).intersection(actors)
    if not actor_refs:
        return None
    roles = {actors[actor_ref] for actor_ref in actor_refs if actors[actor_ref]}
    return roles or None


def _scoped_behavior_ir(
    behavior_ir: dict[str, Any],
    obligation: dict[str, Any],
) -> dict[str, Any]:
    ir = deepcopy(_dict(behavior_ir))
    obligation_refs = _obligation_scope_refs(obligation)
    obligation_actor_roles = _obligation_actor_roles(behavior_ir, obligation)
    ir["conflicts"] = [
        dict(conflict)
        for conflict in _list(ir.get("conflicts"))
        if isinstance(conflict, dict)
        and _conflict_is_relevant(
            conflict,
            obligation_refs=obligation_refs,
            obligation_actor_roles=obligation_actor_roles,
        )
    ]
    return ir


def _privacy_field_mode(obligation: dict[str, Any]) -> bool:
    row = _dict(obligation)
    prop = _dict(row.get("property"))
    return bool(
        _text(row.get("risk_family")) == "privacy"
        and _text(prop.get("privacy_test_mode")) == "field_policy"
        and _text(prop.get("privacy_policy")) in {"absent", "masked"}
    )


def compile_experiment_for_obligation(
    obligation: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    environment_type: str = "",
    policy_version: str = "",
    available_adapters: set[str] | None = None,
) -> dict[str, Any]:
    if not _privacy_field_mode(obligation):
        problem = _base._runtime_pair_problem(obligation, behavior_ir)
        if problem:
            obligation_id = (
                _text(_dict(obligation).get("obligation_id"))
                or "unknown_obligation"
            )
            return _base._base.blocked_experiment(
                obligation_id,
                "BLOCKED_MISSING_ACTOR",
                f"runtime_actor_pair_not_distinct:{problem}",
            )

    return _original_compile_experiment(
        obligation,
        behavior_ir=_scoped_behavior_ir(behavior_ir, obligation),
        environment_type=environment_type,
        policy_version=policy_version,
        available_adapters=available_adapters,
    )


# Batch compilation is implemented in the stable base module and resolves this
# global at runtime. Patch both facade and base so direct and batch calls share
# the same scoped-conflict and privacy-field semantics.
_base.compile_experiment_for_obligation = compile_experiment_for_obligation
_base._base.compile_experiment_for_obligation = compile_experiment_for_obligation
