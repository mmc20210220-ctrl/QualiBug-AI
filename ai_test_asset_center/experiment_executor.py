"""Public experiment executor with sealed actor and operation authority.

The established execution, exploration, Oracle and delivery mechanics live in
``_experiment_executor_mainline_mechanics``. Current compilation already
promotes assertion-local actor exploration metadata into one top-level hashed
actor execution contract, so runtime accepts only that sealed contract.

Actor exploration additionally requires one primary operation identity. A
multi-operation experiment cannot use ``required_operations[0]`` as permission
context merely because it appears first; the operation must be unique or be
explicitly selected by the semantic property from within the required set.
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


def _text(value: Any) -> str:
    return str(value or "").strip()


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


def _semantic_property(experiment: dict[str, Any]) -> dict[str, Any]:
    direct = _dict(experiment.get("property"))
    if direct:
        return direct
    properties = [
        _dict(_dict(row).get("property"))
        for row in _list(experiment.get("assertions"))
        if isinstance(row, dict) and _dict(_dict(row).get("property"))
    ]
    if len(properties) == 1:
        return properties[0]
    return {}


def _unique_primary_operation_ref(
    experiment: dict[str, Any],
    semantic_property: dict[str, Any] | None = None,
) -> str:
    """Resolve one operation identity without source-order selection."""

    exp = _dict(experiment)
    prop = _dict(semantic_property) or _semantic_property(exp)
    required = list(
        dict.fromkeys(
            _text(value)
            for value in _list(exp.get("required_operations"))
            if _text(value)
        )
    )
    property_ref = _text(prop.get("operation_ref"))
    if property_ref:
        if not required or property_ref in required:
            return property_ref
        return ""
    if len(required) == 1:
        return required[0]
    if len(required) > 1:
        return ""

    step_refs = list(
        dict.fromkeys(
            _text(step.get("operation_ref"))
            for step in [
                *_list(exp.get("treatment_plan")),
                *_list(exp.get("control_plan")),
            ]
            if isinstance(step, dict) and _text(step.get("operation_ref"))
        )
    )
    return step_refs[0] if len(step_refs) == 1 else ""


def _actor_execution_plan(
    experiment: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    """Read one sealed plan and prove its permission-context operation."""

    exp = _dict(experiment)
    if _dict(exp.get("actor_execution_plan")):
        plan, problem = _original_actor_execution_plan(exp)
        if problem or not plan:
            return plan, problem
        mode = _text(plan.get("mode"))
        if mode in {"permission_exploration", "observed_permission"}:
            if not _unique_primary_operation_ref(exp):
                return {}, "actor_exploration_primary_operation_ambiguous"
        return plan, ""
    if _legacy_actor_plan_present(exp):
        return {}, "legacy_actor_execution_plan_not_authoritative"
    return {}, ""


def _primary_operation_ref(
    experiment: dict[str, Any],
    semantic_property: dict[str, Any],
) -> str:
    return _unique_primary_operation_ref(experiment, semantic_property)


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
_core._primary_operation_ref = _primary_operation_ref

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__") and not name.startswith("_original_")
        ],
        "_actor_execution_plan",
        "_primary_operation_ref",
        "_unique_primary_operation_ref",
        "execute_one_experiment",
    }
)
