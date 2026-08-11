"""Runtime-support facade with structural identity and operation authority.

The established transport/preflight/credential mechanics live in
``_experiment_runtime_support_mechanics``. Runtime execution must not select a
business resource because it has a larger balance/quantity or because its
current fields make a planned mutation easier to observe. It also must not
invent an HTTP method when Behavior IR omitted one.

This facade therefore enforces two boundaries:
* ordinary entity extraction uses the domain-neutral structural resolver; only
  an explicitly compiled ``@state=...@`` target may filter by business state;
* every HTTP control/treatment step must reference an IR operation with a
  declared method, and any method carried by the step must match that source
  method exactly. Missing method never defaults to GET.
"""
from __future__ import annotations

from typing import Any

from . import _experiment_runtime_support_mechanics as _core
from ._experiment_runtime_support_mechanics import *  # noqa: F401,F403
from .real_id_resolver import (
    _extract_entity_candidates as _structural_entity_candidates,
    bind_entity_fields as _structural_bind_entity_fields,
)

_original_preflight_experiment_executable = _core.preflight_experiment_executable


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


def _runtime_entity_candidates(value: Any) -> list[dict[str, Any]]:
    """Return structurally identified rows in target response order."""

    return [
        dict(row)
        for row in _structural_entity_candidates(value)
        if isinstance(row, dict)
    ]


def _select_runtime_binding(
    body: Any,
    target_path: str,
    *,
    preferred_body: Any = None,
) -> dict[str, str]:
    """Resolve one binding without mutation-convenience candidate ranking."""

    governed_body = body
    governed_path = _text(target_path)
    if governed_path.startswith("@state="):
        from .runtime_binding_materializer_base import (
            _STATE_TARGET_PATH_RE,
            _state_selected_entity,
        )

        match = _STATE_TARGET_PATH_RE.match(governed_path)
        if not match:
            return {}
        required_state = _text(match.group(1)).lower()
        governed_path = _text(match.group(2))
        selected = _state_selected_entity(
            _runtime_entity_candidates(governed_body),
            required_state,
        )
        if not selected:
            return {}
        governed_body = selected

    return _structural_bind_entity_fields(governed_body, governed_path)


def _operation_method_authority(
    experiment: dict[str, Any],
    behavior_ir: dict[str, Any],
) -> tuple[bool, str, str]:
    """Prove every transport step's method from the referenced IR operation."""

    actors_irrelevant = _dict(behavior_ir)
    operations = {
        _text(row.get("id") or row.get("operation_id")): row
        for row in _list(actors_irrelevant.get("operations"))
        if isinstance(row, dict)
        and _text(row.get("id") or row.get("operation_id"))
    }
    for phase in ("control", "treatment"):
        for raw in _list(_dict(experiment).get(f"{phase}_plan")):
            step = _dict(raw)
            if not step:
                continue
            if _text(step.get("protocol_step")) == "ui_open":
                continue
            op_ref = _text(step.get("operation_ref"))
            if not op_ref or op_ref not in operations:
                return False, "BLOCKED_MISSING_OPERATION", op_ref or "missing"
            operation = _dict(operations[op_ref])
            declared_method = _text(operation.get("method")).upper()
            if not declared_method:
                return (
                    False,
                    "BLOCKED_MISSING_OPERATION",
                    f"source_declared_method_missing:{op_ref}",
                )
            step_method = _text(step.get("method")).upper()
            if step_method and step_method != declared_method:
                return (
                    False,
                    "BLOCKED_OPERATION_CONTRACT_DRIFT",
                    f"method_mismatch:{op_ref}:step={step_method}:ir={declared_method}",
                )
    return True, "", ""


def preflight_experiment_executable(
    experiment: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    actor_tokens: dict[str, str],
) -> tuple[bool, str, str]:
    method_ok, method_reason, method_detail = _operation_method_authority(
        experiment,
        behavior_ir,
    )
    if not method_ok:
        return method_ok, method_reason, method_detail
    return _original_preflight_experiment_executable(
        experiment,
        behavior_ir=behavior_ir,
        actor_tokens=actor_tokens,
    )


# Functions extracted into the mechanics module resolve helpers from that
# module's globals. Mirror the governed authorities there so internal execution
# cannot retain either resource-richness ranking or the implicit-GET fallback.
_core._runtime_entity_candidates = _runtime_entity_candidates
_core._select_runtime_binding = _select_runtime_binding
_core.preflight_experiment_executable = preflight_experiment_executable

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "_runtime_entity_candidates",
        "_select_runtime_binding",
        "_operation_method_authority",
        "preflight_experiment_executable",
    }
)
