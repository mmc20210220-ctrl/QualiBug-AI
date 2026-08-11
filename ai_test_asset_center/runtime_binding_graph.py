"""Runtime binding graph facade with actor-scoped credential authority.

The established source/fixture/read resolver mechanics live in
``_runtime_binding_graph_mechanics``.  A credential-valued body placeholder is
special: unlike an entity id, its value is principal-specific.  A shared
experiment binding plan therefore may not take the first actor that happens to
have a secret reference.

This facade permits compile-time credential binding only when the compiler's
required-actor set proves one principal, or every explicit actor coordinate on
the obligation converges to the same principal.  Multi-actor plans remain
blocked until a future per-step runtime credential receipt can bind the exact
step actor; they are never silently cross-wired.
"""
from __future__ import annotations

from typing import Any

from . import _runtime_binding_graph_mechanics as _core
from ._runtime_binding_graph_mechanics import *  # noqa: F401,F403

_original_build_binding_plan = _core.build_binding_plan


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _actor_ref(actor: dict[str, Any]) -> str:
    return _text(actor.get("id") or actor.get("actor_id"))


def _actor_secret(actor: dict[str, Any]) -> str:
    secret = _text(
        actor.get("credential_secret_ref")
        or actor.get("secret_ref")
        or actor.get("credential_ref")
    )
    if secret.lower().startswith("secret_ref:actor:"):
        return ""
    return secret


def _credential_actor_authority(
    obligation: dict[str, Any],
    actors: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    """Return one principal only when compile-time actor identity is unique."""

    candidates = {
        _actor_ref(actor): actor
        for actor in actors
        if isinstance(actor, dict)
        and _actor_ref(actor)
        and _actor_secret(actor)
    }
    if len(candidates) == 1:
        return next(iter(candidates.values())), "unique_required_actor"
    if not candidates:
        return None, "credential_actor_missing"

    obl = _dict(obligation)
    prop = _dict(obl.get("property"))
    explicit_refs = {
        _text(value)
        for value in (
            prop.get("actor_ref"),
            prop.get("control_actor_ref"),
            prop.get("treatment_actor_ref"),
            prop.get("owner_actor_ref"),
            prop.get("resource_owner_actor_id"),
            obl.get("actor_ref"),
        )
        if _text(value)
    }
    explicit_candidates = explicit_refs.intersection(candidates)
    # A shared binding plan is safe only when all explicit actor coordinates
    # converge.  control=A,treatment=B is intentionally ambiguous even if one
    # of them appears first in the obligation/property object.
    if len(explicit_refs) == 1 and len(explicit_candidates) == 1:
        actor_ref = next(iter(explicit_candidates))
        return candidates[actor_ref], "explicit_actor_consensus"
    return None, "credential_actor_ambiguous"


def _govern_credential_bindings(
    plan: list[dict[str, Any]],
    *,
    obligation: dict[str, Any],
    actors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    governed: list[dict[str, Any]] = []
    for raw in plan:
        row = dict(raw) if isinstance(raw, dict) else raw
        if not isinstance(row, dict):
            governed.append(row)
            continue
        if _text(row.get("source_priority")) != "actor_credential_secret":
            governed.append(row)
            continue

        actor, authority = _credential_actor_authority(obligation, actors)
        if actor is None:
            row.update(
                {
                    "status": "blocked",
                    "blocked_reason": "CREDENTIAL_BINDING_ACTOR_AMBIGUOUS",
                    "credential_actor_authority": authority,
                    "actor_ref": "",
                    "credential_secret_ref": "",
                    "value_fingerprint": "",
                }
            )
            setup = dict(_dict(row.get("fixture_setup")))
            setup.pop("actor_ref", None)
            setup.pop("credential_secret_ref", None)
            if setup:
                setup["credential_actor_authority"] = authority
                row["fixture_setup"] = setup
            governed.append(row)
            continue

        actor_ref = _actor_ref(actor)
        secret_ref = _actor_secret(actor)
        row.update(
            {
                "status": "runtime_resolvable",
                "actor_ref": actor_ref,
                "credential_secret_ref": secret_ref,
                "credential_actor_authority": authority,
                "value_fingerprint": "",
            }
        )
        setup = dict(_dict(row.get("fixture_setup")))
        if setup:
            setup.update(
                {
                    "actor_ref": actor_ref,
                    "credential_secret_ref": secret_ref,
                    "credential_actor_authority": authority,
                }
            )
            row["fixture_setup"] = setup
        governed.append(row)
    return governed


def build_binding_plan(
    *,
    operation: dict[str, Any],
    obligation: dict[str, Any],
    actors: list[dict[str, Any]] | None = None,
    available_values: dict[str, dict[str, Any]] | None = None,
    behavior_ir: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    plan = _original_build_binding_plan(
        operation=operation,
        obligation=obligation,
        actors=actors,
        available_values=available_values,
        behavior_ir=behavior_ir,
    )
    return _govern_credential_bindings(
        plan,
        obligation=obligation,
        actors=[
            dict(actor)
            for actor in (actors or [])
            if isinstance(actor, dict)
        ],
    )


__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "build_binding_plan",
        "_credential_actor_authority",
        "_govern_credential_bindings",
    }
)
