"""Public experiment execution facade.

The existing implementation remains in ``experiment_executor_core``.  This
module preserves the established public/re-export and monkeypatch surface while
adapting one preflight boundary for process graphs: credential existence for an
actor used only by graph nodes is validated by the graph target authority,
which has the exact per-system runtime contract.  Actors used before graph
scheduling (fixture, binding, control or state-precondition work) still pass the
original single-target credential preflight.

No credential value is invented for execution.  The compatibility marker is
used only in a private copy passed to the structural preflight; the core retains
and transports the caller's original token map.  The graph runtime then resolves
every target-specific credential before any graph request reaches transport.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from . import experiment_executor_core as _core
from .experiment_runtime_support import (
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


def _graph_aware_preflight(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    actor_tokens: dict[str, str],
) -> tuple[bool, str, str]:
    """Run original structural preflight, deferring graph-only token lookup.

    The sentinel exists only in copied preflight inputs.  It can never reach the
    transport path because ``experiment_executor_core.execute_one_experiment``
    retains the caller's original ``actor_tokens`` object for all later phases.
    """
    exp = _dict(experiment)
    graph_refs = _graph_actor_refs(exp)
    if not graph_refs:
        return _original_preflight(
            exp,
            behavior_ir=behavior_ir,
            actor_tokens=actor_tokens,
        )
    deferrable = graph_refs - _pregraph_actor_refs(exp)
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
            secret = _text(
                actor.get("credential_secret_ref") or actor.get("secret_ref")
            )
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


# The copied core resolves this module global at call time.  Only its private
# preflight lookup is replaced; the public symbol below remains the original
# runtime-support function for compatibility and architecture identity tests.
_core.preflight_experiment_executable = _graph_aware_preflight
_execute_one_core = _core.execute_one_experiment

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

# Preserve public identity required by the established extraction contract.
preflight_experiment_executable = _original_preflight

_HOOK_NAMES = (
    "_http_request",
    "_run_http_step",
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
    # Never replace the graph-aware private preflight with the public legacy
    # symbol during hook synchronization.
    _core.preflight_experiment_executable = _graph_aware_preflight


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
    """Execute through the unchanged core with graph-aware preflight routing."""
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
    }
)
