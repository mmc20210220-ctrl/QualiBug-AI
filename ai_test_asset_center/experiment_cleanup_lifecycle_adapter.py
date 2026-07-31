"""Lifecycle adapter for the existing experiment cleanup authority.

Ordinary experiments delegate to the existing cleanup core with the established
precondition-write projection. Process graphs execute system-aware compensation
first, then use the receipt finalizer matching their proof scope: ordinary WRP
for one write, per-source-step aggregation for multiple writes.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import experiment_cleanup_executor_core as _core
from .experiment_runtime_support import _dict, _list, _request_example, _text
from .process_graph_cleanup_executor import (
    execute_process_graph_cleanup,
    finalize_process_graph_cleanup_result,
)
from .process_graph_cleanup_equivalence import (
    finalize_process_graph_cleanup_equivalence_inputs,
)
from .process_graph_reversibility import (
    is_process_graph_reversibility_proof,
)
from .runtime_binding_materializer import materialize_body_template


PROJECTION_SCHEMA_VERSION = "qualibug.precondition-cleanup-projection.v1"
_SHADOW_MARKER = "_precondition_cleanup_shadow"
_CLEANUP_AUTHORITY = "experiment_cleanup_executor_core"


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
    """Return cleanup input with temporary measured-phase projections."""
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


def _ordinary_cleanup(kwargs: dict[str, Any]) -> dict[str, Any]:
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
        _core.execute_experiment_cleanup_compensation(
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
        "cleanup_authority": _CLEANUP_AUTHORITY,
    }
    result["observations"] = observations
    return result


def _graph_cleanup(kwargs: dict[str, Any]) -> dict[str, Any]:
    exp = _dict(kwargs.get("exp"))
    graph_result = execute_process_graph_cleanup(
        exp=exp,
        steps_out=[
            row for row in _list(kwargs.get("steps_out")) if isinstance(row, dict)
        ],
        observations=_dict(kwargs.get("observations")),
        contract_evidence_receipts=[
            row
            for row in _list(kwargs.get("contract_evidence_receipts"))
            if isinstance(row, dict)
        ],
        request_bodies_for_cleanup=_dict(
            kwargs.get("request_bodies_for_cleanup")
        ),
        runtime_bindings=_dict(kwargs.get("runtime_bindings")),
        cleanup_failures=int(kwargs.get("cleanup_failures") or 0),
        actors=_dict(kwargs.get("actors")),
        tokens={
            _text(key): _text(value)
            for key, value in _dict(kwargs.get("tokens")).items()
            if _text(key)
        },
        eid=_text(kwargs.get("eid")),
        oid=_text(kwargs.get("oid")),
        resolved_campaign_id=_text(kwargs.get("resolved_campaign_id")),
        resolved_execution_id=_text(kwargs.get("resolved_execution_id")),
        campaign_id=_text(kwargs.get("campaign_id")),
        root=kwargs.get("root"),
        project=_text(kwargs.get("project")),
        base_url=_text(kwargs.get("base_url")),
        runtime_contract=_dict(kwargs.get("runtime_contract")),
        execute_governed_control_write=_core.execute_governed_control_write,
        sandbox_write_allowed=_core.sandbox_write_allowed,
    )

    # The graph helper already compensates every business write. Reuse the
    # established core only for fixture cleanup and activation aggregation.
    legacy_exp = deepcopy(exp)
    legacy_exp["cleanup_plan"] = []
    legacy_exp["safety_contract"] = {
        **_dict(legacy_exp.get("safety_contract")),
        "governed_write": False,
        "graph_cleanup_already_executed": True,
    }
    core_result = _ordinary_cleanup(
        {
            **kwargs,
            "exp": legacy_exp,
            "steps_out": graph_result["steps_out"],
            "observations": graph_result["observations"],
            "contract_evidence_receipts": graph_result[
                "contract_evidence_receipts"
            ],
            "cleanup_failures": graph_result["cleanup_failures"],
        }
    )
    proof = _dict(exp.get("write_reversibility_proof"))
    if is_process_graph_reversibility_proof(proof):
        finalized = finalize_process_graph_cleanup_equivalence_inputs(
            exp=exp,
            result=core_result,
            resolved_campaign_id=_text(kwargs.get("resolved_campaign_id")),
            runtime_bindings=_dict(kwargs.get("runtime_bindings")),
        )
    else:
        finalized = finalize_process_graph_cleanup_result(
            exp=exp,
            result=core_result,
            resolved_campaign_id=_text(kwargs.get("resolved_campaign_id")),
        )
    observations = _dict(finalized.get("observations"))
    observations["cleanup_authority"] = "process_graph_write_contract"
    observations["process_graph_write_contract_id"] = _text(
        _dict(exp.get("process_graph_write_contract")).get("contract_id")
    )
    finalized["observations"] = observations
    return finalized


def execute_experiment_cleanup_compensation(**kwargs: Any) -> dict[str, Any]:
    """Dispatch one cleanup call through its compiled authority."""
    contract = _dict(_dict(kwargs.get("exp")).get("process_graph_write_contract"))
    if (
        _text(contract.get("status")) == "RESOLVED"
        and bool(_list(contract.get("write_step_ids")))
    ):
        return _graph_cleanup(dict(kwargs))
    return _ordinary_cleanup(dict(kwargs))


__all__ = [
    "PROJECTION_SCHEMA_VERSION",
    "execute_experiment_cleanup_compensation",
]
