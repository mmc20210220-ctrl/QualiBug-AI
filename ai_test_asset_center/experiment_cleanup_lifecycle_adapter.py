"""Lifecycle adapter for the existing experiment cleanup authority.

The cleanup executor predates compiled state-precondition execution and therefore
recognises governed writes only in the measured ``control``/``treatment`` phases.
This adapter does not implement another cleanup engine.  It creates temporary
``treatment`` projections for real precondition write receipts, invokes the
existing cleanup executor unchanged, and removes those projections from the
returned runtime timeline.

The original precondition rows and the real cleanup rows remain authoritative.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .experiment_cleanup_executor_core import (
    execute_experiment_cleanup_compensation as _execute_cleanup,
)
from .experiment_runtime_support import _dict, _list, _request_example, _text
from .runtime_binding_materializer import materialize_body_template


PROJECTION_SCHEMA_VERSION = "qualibug.precondition-cleanup-projection.v1"
_SHADOW_MARKER = "_precondition_cleanup_shadow"


def _precondition_plan_by_step_id(exp: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("step_id") or row.get("id")): row
        for row in _list(_dict(exp).get("precondition_plan"))
        if isinstance(row, dict) and _text(row.get("step_id") or row.get("id"))
    }


def _materialized_precondition_request(
    *,
    plan_step: dict[str, Any],
    operations: dict[str, dict[str, Any]],
    runtime_bindings: dict[str, Any],
) -> Any:
    operation = _dict(operations.get(_text(plan_step.get("operation_ref"))))
    template = (
        plan_step.get("body")
        if "body" in plan_step
        else _request_example(operation)
    )
    return materialize_body_template(template, runtime_bindings)


def _project_precondition_steps(
    *,
    exp: dict[str, Any],
    steps_out: list[dict[str, Any]],
    operations: dict[str, dict[str, Any]],
    runtime_bindings: dict[str, Any],
    request_bodies_for_cleanup: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """Return cleanup input with temporary measured-phase projections.

    Projection is deliberately structural: it changes only the phase consumed by
    the legacy cleanup filters.  The governance receipt, actor, operation, path,
    response and semantic receipt are copied without reinterpretation.
    """
    plan_by_step = _precondition_plan_by_step_id(exp)
    projected_steps = list(steps_out)
    cleanup_bodies = dict(request_bodies_for_cleanup)
    projected_ids: list[str] = []

    for raw_step in steps_out:
        step = _dict(raw_step)
        if _text(step.get("phase")) != "precondition":
            continue
        if not isinstance(step.get("governance_receipt"), dict):
            continue
        step_id = _text(step.get("step_id"))
        operation_ref = _text(step.get("operation_ref"))
        if not step_id or not operation_ref:
            continue

        shadow = deepcopy(step)
        shadow["phase"] = "treatment"
        shadow[_SHADOW_MARKER] = True
        shadow["original_phase"] = "precondition"
        governed = _dict(shadow.get("governance_receipt"))
        write = _dict(governed.get("write"))
        if "body" not in shadow:
            shadow["body"] = write.get("body")
        if not _text(shadow.get("observation_path")):
            shadow["observation_path"] = _text(governed.get("observation_path"))
        projected_steps.append(shadow)
        projected_ids.append(step_id)

        plan_step = _dict(plan_by_step.get(step_id))
        if plan_step and step_id not in cleanup_bodies:
            cleanup_bodies[step_id] = _materialized_precondition_request(
                plan_step=plan_step,
                operations=operations,
                runtime_bindings=runtime_bindings,
            )

    return projected_steps, cleanup_bodies, projected_ids


def execute_experiment_cleanup_compensation(**kwargs: Any) -> dict[str, Any]:
    """Invoke the existing cleanup executor with precondition-write visibility."""
    original_steps = [
        row for row in _list(kwargs.get("steps_out")) if isinstance(row, dict)
    ]
    operations = {
        _text(key): value
        for key, value in _dict(kwargs.get("ops")).items()
        if _text(key) and isinstance(value, dict)
    }
    projected_steps, cleanup_bodies, projected_ids = _project_precondition_steps(
        exp=_dict(kwargs.get("exp")),
        steps_out=original_steps,
        operations=operations,
        runtime_bindings=_dict(kwargs.get("runtime_bindings")),
        request_bodies_for_cleanup=_dict(
            kwargs.get("request_bodies_for_cleanup")
        ),
    )

    result = _dict(
        _execute_cleanup(
            **{
                **kwargs,
                "steps_out": projected_steps,
                "request_bodies_for_cleanup": cleanup_bodies,
            }
        )
    )
    returned_steps = [
        row
        for row in _list(result.get("steps_out") or projected_steps)
        if isinstance(row, dict) and row.get(_SHADOW_MARKER) is not True
    ]
    result["steps_out"] = returned_steps

    observations = _dict(result.get("observations") or kwargs.get("observations"))
    observations["precondition_cleanup_projection_receipt"] = {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "projected_step_ids": list(dict.fromkeys(projected_ids)),
        "projected_step_count": len(set(projected_ids)),
        "shadow_rows_persisted": False,
        "cleanup_authority": "experiment_cleanup_executor_core",
    }
    result["observations"] = observations
    return result


__all__ = [
    "PROJECTION_SCHEMA_VERSION",
    "execute_experiment_cleanup_compensation",
]
