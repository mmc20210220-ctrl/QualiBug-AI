"""Public experiment executor with compiler-sealed actor-plan authority.

The established execution, exploration, Oracle and delivery mechanics live in
``_experiment_executor_mainline_mechanics``. Current compilation already
promotes assertion-local actor exploration metadata into one top-level
``qualibug.actor-execution-plan.v1`` contract and seals it with ``plan_hash``.
Runtime therefore has no reason to trust the historical unsealed assertion
fallback.

Formal execution accepts the compiler-sealed plan or no exploration plan. A
legacy ``_actor_exploration_plan`` found without the sealed top-level contract is
visible drift and blocks execution; ``candidate_ids[0]`` is never promoted to a
source actor merely because it appears first.
"""
from __future__ import annotations

from typing import Any

from . import _experiment_executor_mainline_mechanics as _core

for _name in dir(_core):
    if not _name.startswith("__") and not _name.startswith("_original_"):
        globals()[_name] = getattr(_core, _name)

_original_actor_execution_plan = _core._actor_execution_plan
_original_execute_one_experiment = _core.execute_one_experiment


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _legacy_actor_plan_present(experiment: dict[str, Any]) -> bool:
    exp = _dict(experiment)
    direct_property = _dict(exp.get("property"))
    if _dict(direct_property.get(_core._LEGACY_ACTOR_PLAN_KEY)):
        return True
    for raw in _list(exp.get("assertions")):
        assertion = _dict(raw)
        if _dict(_dict(assertion.get("property")).get(_core._LEGACY_ACTOR_PLAN_KEY)):
            return True
    return False


def _actor_execution_plan(
    experiment: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Read only a compiler-sealed top-level actor execution plan."""

    exp = _dict(experiment)
    if _dict(exp.get("actor_execution_plan")):
        return _original_actor_execution_plan(exp)
    if _legacy_actor_plan_present(exp):
        return {}, "legacy_actor_execution_plan_not_authoritative"
    return {}, ""


def _sync_public_executor_hooks() -> None:
    for name in tuple(getattr(_core, "_HOOK_NAMES", ())):
        value = globals().get(name)
        if value is not None and hasattr(_core, name):
            setattr(_core, name, value)
    public_loader = globals().get("load_actor_tokens")
    if public_loader is not None:
        _core.load_actor_tokens = public_loader


def execute_one_experiment(*args: Any, **kwargs: Any) -> dict[str, Any]:
    _sync_public_executor_hooks()
    return _original_execute_one_experiment(*args, **kwargs)


_core._actor_execution_plan = _actor_execution_plan

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__") and not name.startswith("_original_")
        ],
        "_actor_execution_plan",
        "execute_one_experiment",
    }
)
