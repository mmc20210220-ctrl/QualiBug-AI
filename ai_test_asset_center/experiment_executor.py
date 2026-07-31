"""Public experiment execution facade.

The existing implementation remains in ``experiment_executor_core``. This module preserves
the established public/re-export and monkeypatch surface while adapting two execution
boundaries: graph-only credentials remain owned by the graph target authority, and an actor
with an account-qualified identity may never fall back to another token that merely shares
its role.

No credential value is invented for execution. Compatibility markers exist only in a private
copy passed to structural preflight; the core retains and transports the caller's original
token map. The graph runtime resolves every target-specific credential before transport.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from . import experiment_executor_core as _core
from .experiment_runtime_support import (
    _jwt_expired,
    _parse_test_accounts_md,
    _resolve_token as _original_resolve_token,
    load_actor_tokens as _original_load_actor_tokens,
    preflight_experiment_executable as _original_preflight,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _actor_refs(value: Any) -> set[str]:
    row = _dict(value)
    refs = {
        _text(row.get("actor_ref")),
        _text(row.get("owner_actor_ref")),
        _text(row.get("fixture_owner_actor_ref")),
        _text(row.get("resolver_actor_ref")),
        _text(row.get("source_actor_ref")),
    }
    refs.update(_text(item) for item in _list(row.get("actor_refs")))
    return {ref for ref in refs if ref}


def _graph_actor_refs(experiment: dict[str, Any]) -> set[str]:
    contract = _dict(experiment.get("process_graph_write_contract"))
    if _text(contract.get("status")) != "RESOLVED":
        return set()
    refs: set[str] = set()
    for step in _list(experiment.get("treatment_plan")):
        if not isinstance(step, dict) or not _dict(step.get("_execution_graph")):
            continue
        refs.update(_actor_refs(step))
    return refs


def _pregraph_actor_refs(experiment: dict[str, Any]) -> set[str]:
    """Actors that may be used before the graph target gate runs."""
    refs: set[str] = set()
    for key in ("control_plan", "precondition_plan"):
        for row in _list(experiment.get(key)):
            if isinstance(row, dict):
                refs.update(_actor_refs(row))

    for binding in _list(experiment.get("binding_plan")):
        if not isinstance(binding, dict):
            continue
        refs.update(_actor_refs(binding))
        fixture_setup = _dict(binding.get("fixture_setup"))
        refs.update(_actor_refs(fixture_setup))
        for resolver in _list(binding.get("resolver_operations")):
            if isinstance(resolver, dict):
                refs.update(_actor_refs(resolver))
        for body_binding in _list(fixture_setup.get("body_bindings")):
            if not isinstance(body_binding, dict):
                continue
            refs.update(_actor_refs(body_binding))
            for resolver in _list(body_binding.get("resolver_operations")):
                if isinstance(resolver, dict):
                    refs.update(_actor_refs(resolver))

    fixture_dag = _dict(experiment.get("fixture_dag"))
    for node in _list(fixture_dag.get("nodes")):
        if isinstance(node, dict):
            refs.update(_actor_refs(node))
    return refs


def _test_account_rows(root: Path, project: str) -> list[dict[str, Any]]:
    path = Path(root) / "platform_inputs" / str(project) / "test_accounts.json"
    rows: list[dict[str, Any]] = []
    if path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict):
            rows = list(
                payload.get("accounts")
                or payload.get("actors")
                or payload.get("users")
                or []
            )
            if not rows:
                rows = [
                    {
                        **(value if isinstance(value, dict) else {}),
                        "account_ref": key,
                    }
                    for key, value in payload.items()
                    if isinstance(value, dict)
                    and key not in {"schema", "schema_version", "meta"}
                ]
        elif isinstance(payload, list):
            rows = payload
    if rows:
        return [row for row in rows if isinstance(row, dict)]
    return [dict(row) for row in _parse_test_accounts_md(root, project)]


def _ambiguous_roles(root: Path, project: str) -> set[str]:
    counts: dict[str, int] = {}
    for row in _test_account_rows(root, project):
        role = _text(row.get("role") or row.get("name") or row.get("id"))
        status = _text(
            row.get("status")
            or row.get("account_status")
            or row.get("state")
            or "active"
        ).upper()
        if not role or status in {"DISABLED", "LOCKED", "INACTIVE", "REVOKED"}:
            continue
        counts[role] = counts.get(role, 0) + 1
    return {role for role, count in counts.items() if count > 1}


def _identity_safe_load_actor_tokens(
    root: Path, project: str, *, base_url: str = ""
) -> dict[str, str]:
    """Load existing tokens but remove aliases shared by multiple active accounts."""
    tokens = dict(
        _original_load_actor_tokens(root, project, base_url=base_url)
    )
    for role in _ambiguous_roles(root, project):
        for alias in (
            role,
            f"secret_ref:test_accounts:{role}",
            f"secret_ref:context:{role}",
            f"secret_ref:actor:{role}",
        ):
            tokens.pop(alias, None)
    return tokens


def _actor_secret(actor: dict[str, Any]) -> str:
    return _text(
        actor.get("credential_secret_ref")
        or actor.get("secret_ref")
        or actor.get("credential_ref")
    )


def _actor_requires_exact_secret(actor: dict[str, Any]) -> bool:
    role = _text(actor.get("role"))
    secret = _actor_secret(actor)
    role_aliases = {
        role,
        f"secret_ref:test_accounts:{role}",
        f"secret_ref:context:{role}",
        f"secret_ref:actor:{role}",
    }
    has_account_coordinate = bool(
        _text(
            actor.get("account_ref")
            or actor.get("account_id")
            or actor.get("principal_ref")
            or actor.get("principal_id")
        )
        or _dict(actor.get("identity_coordinates"))
        or _dict(actor.get("credential_identity_coordinates"))
        or _text(actor.get("identity_match_status")) == "EXACT"
    )
    return bool(has_account_coordinate or (secret and secret not in role_aliases))


def _strict_resolve_token(actor: dict[str, Any], tokens: dict[str, str]) -> str:
    """An account-qualified actor may use only its declared secret reference."""
    role = _text(actor.get("role"))
    if role.lower() in {"anonymous", "public"}:
        return ""
    secret = _actor_secret(actor)
    if _actor_requires_exact_secret(actor):
        return tokens.get(secret) or ""
    return _original_resolve_token(actor, tokens)


def _exact_secret_preflight(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    actor_tokens: dict[str, str],
    deferred_actor_refs: set[str],
) -> tuple[bool, str, str]:
    actors = {
        _text(row.get("id") or row.get("actor_id")): row
        for row in _list(_dict(behavior_ir).get("actors"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("actor_id"))
    }
    required_refs = _pregraph_actor_refs(experiment)
    for key in ("control_plan", "treatment_plan"):
        for step in _list(_dict(experiment).get(key)):
            if isinstance(step, dict):
                required_refs.update(_actor_refs(step))
    for actor_ref in sorted(required_refs - deferred_actor_refs):
        actor = _dict(actors.get(actor_ref))
        if not actor or not _actor_requires_exact_secret(actor):
            continue
        secret = _actor_secret(actor)
        if not secret or secret not in actor_tokens:
            return (
                False,
                "BLOCKED_MISSING_ACTOR",
                f"exact_credential_unresolved:{actor_ref}",
            )
    return True, "", ""


def _graph_aware_preflight(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    actor_tokens: dict[str, str],
) -> tuple[bool, str, str]:
    """Run structural preflight with graph deferral and exact-account enforcement."""
    exp = _dict(experiment)
    graph_refs = _graph_actor_refs(exp)
    deferrable = graph_refs - _pregraph_actor_refs(exp)
    exact_ok, exact_reason, exact_detail = _exact_secret_preflight(
        exp,
        behavior_ir=behavior_ir,
        actor_tokens=actor_tokens,
        deferred_actor_refs=deferrable,
    )
    if not exact_ok:
        return exact_ok, exact_reason, exact_detail
    if not deferrable:
        return _original_preflight(
            exp,
            behavior_ir=behavior_ir,
            actor_tokens=actor_tokens,
        )

    ir_copy = deepcopy(_dict(behavior_ir))
    copied_actors: list[dict[str, Any]] = []
    token_view = dict(actor_tokens)
    for raw_actor in _list(ir_copy.get("actors")):
        if not isinstance(raw_actor, dict):
            continue
        actor = dict(raw_actor)
        actor_ref = _text(actor.get("id") or actor.get("actor_id"))
        role = _text(actor.get("role"))
        if actor_ref in deferrable and role.lower() not in {"anonymous", "public"}:
            secret = _actor_secret(actor)
            if not secret:
                secret = f"graph_target_preflight:{actor_ref}"
                actor["credential_secret_ref"] = secret
            marker = f"credential_deferred_to_graph_target:{actor_ref}"
            token_view.setdefault(secret, marker)
            if role:
                token_view.setdefault(role, marker)
        copied_actors.append(actor)
    ir_copy["actors"] = copied_actors
    return _original_preflight(
        exp,
        behavior_ir=ir_copy,
        actor_tokens=token_view,
    )


_core.preflight_experiment_executable = _graph_aware_preflight
_core._resolve_token = _strict_resolve_token
_core.load_actor_tokens = _identity_safe_load_actor_tokens
_execute_one_core = _core.execute_one_experiment

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

# Preserve the established public structural preflight identity. The private core
# path remains graph-aware and account-strict.
preflight_experiment_executable = _original_preflight
load_actor_tokens = _identity_safe_load_actor_tokens
_resolve_token = _strict_resolve_token

_HOOK_NAMES = (
    "_http_request",
    "_run_http_step",
    "_resolve_token",
    "execute_governed_control_write",
    "sandbox_write_allowed",
    "materialize_experiment_fixtures",
    "execute_barrier_plans",
    "execute_non_barrier_plans",
    "execute_experiment_cleanup_compensation",
    "execute_database_observer_phase",
    "finalize_experiment_execution",
    "load_actor_tokens",
    "validate_cleanup_plan",
)


def _sync_core_hooks() -> None:
    """Propagate established public injection points to the execution core."""
    for name in _HOOK_NAMES:
        value = globals().get(name)
        if value is not None and hasattr(_core, name):
            setattr(_core, name, value)
    _core.preflight_experiment_executable = _graph_aware_preflight
    _core._resolve_token = _strict_resolve_token


def execute_one_experiment(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    campaign_id: str,
    execution_id: str,
    actor_tokens: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute through the unchanged core with governed credential routing."""
    _sync_core_hooks()
    return _execute_one_core(
        experiment,
        behavior_ir=behavior_ir,
        root=root,
        project=project,
        base_url=base_url,
        runtime_contract=runtime_contract,
        campaign_id=campaign_id,
        execution_id=execution_id,
        actor_tokens=actor_tokens,
    )


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name
    not in {
        "_core",
        "_name",
        "_execute_one_core",
        "_original_preflight",
        "_original_resolve_token",
        "_original_load_actor_tokens",
    }
)
