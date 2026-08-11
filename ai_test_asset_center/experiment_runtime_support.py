"""Runtime-support facade with structural identity, operation and observer authority.

The established transport/preflight/credential mechanics live in
``_experiment_runtime_support_mechanics``. Formal runtime execution may not
manufacture identity by convenience:

* resource selection is structural; only an explicitly compiled state predicate
  may filter rows;
* every transport step's HTTP method comes from its Behavior IR operation; and
* effect-observer derivation requires one exact source-declared write operation
  and one unambiguous materializable observer. Path-only or source-order
  selection never becomes observation authority.
"""
from __future__ import annotations

from typing import Any

from . import _experiment_runtime_support_mechanics as _core
from ._experiment_runtime_support_mechanics import *  # noqa: F401,F403
from .real_id_resolver import (
    _extract_entity_candidates as _structural_entity_candidates,
    bind_entity_fields as _structural_bind_entity_fields,
    normalize_path_placeholders,
)
from .runtime_binding_graph import (
    declared_effect_observers as _strict_declared_effect_observers,
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
    operations = {
        _text(row.get("id") or row.get("operation_id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
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


def _operation_for_observation_path(
    path: str,
    operations: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    normalized = normalize_path_placeholders(_text(path))
    if not normalized.startswith("/"):
        return {}
    candidates: list[dict[str, Any]] = []
    for raw in operations.values():
        operation = _dict(raw)
        if not operation:
            continue
        operation_ref = _text(operation.get("id") or operation.get("operation_id"))
        method = _text(operation.get("method")).upper()
        candidate_path = normalize_path_placeholders(
            _text(operation.get("path") or operation.get("raw_path"))
        )
        if (
            operation_ref
            and method in {"POST", "PUT", "PATCH", "DELETE"}
            and candidate_path == normalized
        ):
            candidates.append(operation)
    return dict(candidates[0]) if len(candidates) == 1 else {}


def _declared_observation_path(
    path: str,
    operations: dict[str, dict[str, Any]],
    *,
    runtime_bindings: dict[str, Any] | None = None,
    request_body: Any = None,
) -> str:
    operation = _operation_for_observation_path(path, operations)
    if not operation:
        return ""
    observers = _strict_declared_effect_observers(
        operation,
        behavior_ir={"operations": list(operations.values())},
        max_candidates=5,
    )
    binding_values = {
        **_core._scalar_body_bindings(_core._request_example(operation)),
        **_core._scalar_body_bindings(request_body),
        **(runtime_bindings or {}),
    }
    write_placeholders = set(
        _core.infer_path_params(normalize_path_placeholders(path))
    )
    entity_bound: list[str] = []
    collection_bound: list[str] = []
    for observer in observers:
        template = _text(observer.get("path"))
        materialized = template
        for name, value in binding_values.items():
            if value in (None, ""):
                continue
            materialized = materialized.replace(
                "{" + name + "}",
                _core.quote(str(value), safe=""),
            )
        if not (
            materialized.startswith("/")
            and not _core.path_has_placeholders(materialized)
        ):
            continue
        obs_placeholders = set(_core.infer_path_params(template))
        if obs_placeholders and (
            not write_placeholders or (obs_placeholders & write_placeholders)
        ):
            entity_bound.append(materialized)
        elif obs_placeholders:
            continue
        else:
            collection_bound.append(materialized)
    entity_bound = list(dict.fromkeys(entity_bound))
    collection_bound = list(dict.fromkeys(collection_bound))
    if len(entity_bound) == 1:
        return entity_bound[0]
    if not entity_bound and len(collection_bound) == 1:
        return collection_bound[0]
    return ""


def _response_bound_observation_path(
    operation: dict[str, Any],
    operations: dict[str, dict[str, Any]],
    write_body: Any,
) -> dict[str, str]:
    """Return one identity readback only when the source observer is unique."""

    if not isinstance(write_body, (dict, list)):
        return {}
    observers = _strict_declared_effect_observers(
        operation,
        behavior_ir={"operations": list(operations.values())},
        max_candidates=5,
    )
    candidates: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for observer in observers:
        operation_ref = _text(observer.get("operation_ref"))
        method = _text(observer.get("method")).upper()
        path = normalize_path_placeholders(_text(observer.get("path")))
        if (
            not operation_ref
            or method not in {"GET", "HEAD"}
            or not path.startswith("/")
            or not _core.path_has_placeholders(path)
        ):
            continue
        values: dict[str, Any] = {}
        for name in _core.infer_path_params(path):
            value = _core._runtime_setup_value_from_response(write_body, name)
            if value in (None, "", [], {}):
                values = {}
                break
            values[name] = value
        if not values:
            continue
        materialized = path
        for name, value in values.items():
            materialized = materialized.replace(
                "{" + name + "}",
                _core.quote(str(value), safe=""),
            )
        if not (
            materialized.startswith("/")
            and not _core.path_has_placeholders(materialized)
        ):
            continue
        key = (operation_ref, method, path, materialized)
        if key in seen:
            continue
        seen.add(key)
        candidates.append(
            {
                "operation_ref": operation_ref,
                "method": method,
                "path": materialized,
                "path_template": path,
            }
        )
    return candidates[0] if len(candidates) == 1 else {}


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


_core._runtime_entity_candidates = _runtime_entity_candidates
_core._select_runtime_binding = _select_runtime_binding
_core._operation_for_observation_path = _operation_for_observation_path
_core._declared_observation_path = _declared_observation_path
_core._response_bound_observation_path = _response_bound_observation_path
_core.declared_effect_observers = _strict_declared_effect_observers
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
        "_operation_for_observation_path",
        "_declared_observation_path",
        "_response_bound_observation_path",
        "preflight_experiment_executable",
    }
)
