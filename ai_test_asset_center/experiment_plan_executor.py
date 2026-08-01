"""Graph-aware entry point for experiment plan execution.

The existing sequential transport/governance implementation remains the only
step kernel. This module adds source-backed dependency scheduling, exact
approved-target dispatch, namespaced cross-node bindings, and durable graph
resume checkpoints before invoking that kernel one node at a time. Ordinary
plans delegate unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from . import experiment_plan_step_executor as _step_kernel
from .experiment_plan_step_executor import (
    _http_request,
    _run_http_step,
    execute_governed_control_write,
    sandbox_write_allowed,
)
from .experiment_runtime_support import _dict, _list, _text
from .process_graph_executor_support import (
    copy_subledger_rows,
    merge_result_bags,
    new_master_ledger,
    public_binding_ledger,
    record_blocked_step,
    required_step_ids,
    runtime_projection,
    scoped_actor_context,
    step_observation,
)
from .process_graph_resume import (
    GRAPH_RESUME_STATE_INVALID,
    build_process_graph_resume_checkpoint,
    recover_process_graph_runtime,
)
from .process_graph_runtime import (
    GRAPH_PREDECESSOR_NOT_SUCCEEDED,
    GRAPH_RUNTIME_INVALID,
    GRAPH_TARGET_ACTOR_CREDENTIAL_UNRESOLVED,
    extract_execution_graph,
    graph_step_context,
    prepare_graph_runtime,
    record_graph_step_outcome,
)
from .process_step_execution import attach_ledger_refs_to_observations


def _sync_step_kernel_hooks() -> None:
    """Preserve the established test/runtime injection surface on this authority."""
    _step_kernel._http_request = _http_request
    _step_kernel._run_http_step = _run_http_step
    _step_kernel.execute_governed_control_write = execute_governed_control_write
    _step_kernel.sandbox_write_allowed = sandbox_write_allowed


def _delegate_sequential(**kwargs: Any) -> dict[str, Any]:
    _sync_step_kernel_hooks()
    return _step_kernel.execute_non_barrier_plans(**kwargs)


def _blocked_graph_result(
    *,
    runtime: dict[str, Any],
    treatment_plan: list[Any],
    master: Any,
    bags: dict[str, Any],
    observations: dict[str, Any],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
) -> dict[str, Any]:
    reason = _text(runtime.get("reason_code")) or GRAPH_RUNTIME_INVALID
    detail = _text(runtime.get("detail")) or "graph_runtime_not_ready"
    for step in treatment_plan:
        if not isinstance(step, dict):
            continue
        bags["steps"].append(
            record_blocked_step(
                master=master,
                contract_evidence_receipts=bags["contract_evidence_receipts"],
                pre_transport_block_reasons=bags[
                    "pre_transport_block_reasons"
                ],
                step=step,
                reason_code=reason,
                detail=detail,
                phase="treatment",
                eid=eid,
                oid=oid,
                resolved_campaign_id=resolved_campaign_id,
                resolved_execution_id=resolved_execution_id,
                graph_meta={},
            )
        )
    observations["process_graph_runtime"] = dict(runtime)
    attach_ledger_refs_to_observations(observations, master)
    return {
        **bags,
        "process_step_ledger": master,
        "process_step_ledger_id": master.ledger_id,
        "process_step_ledger_hash": master.compute_hash(),
        "process_timeline": master.build_timeline_receipt(),
        "required_step_ids": list(master.required_step_ids),
        "planned_step_ids": list(master.required_step_ids),
        "executed_step_ids": master.executed_step_ids(),
        "process_graph_runtime": dict(runtime),
        "process_graph_binding_ledger": {},
    }


def _apply_graph_outcome_to_master(
    *, master: Any, node_id: str, outcome: dict[str, Any]
) -> None:
    status = _text(outcome.get("status"))
    if status == "SUCCEEDED":
        return
    row = master.get_step_row(node_id) if hasattr(master, "get_step_row") else None
    if not isinstance(row, dict):
        return
    final_status = status or "BLOCKED"
    row["final_status"] = final_status
    row["final_step_status"] = final_status
    row["step_completed"] = False
    row["step_failed"] = True
    reason_code = _text(outcome.get("reason_code"))
    if reason_code:
        row["reason_code"] = reason_code
    unresolved = _list(outcome.get("output_binding_unresolved"))
    if unresolved:
        row["detail"] = ",".join(_text(value) for value in unresolved if _text(value))


def _publish_graph_progress(
    *,
    graph: dict[str, Any],
    runtime: dict[str, Any],
    master: Any,
    bags: dict[str, Any],
    observations: dict[str, Any],
    graph_observations: list[dict[str, Any]],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
) -> dict[str, Any]:
    """Publish one hash-bound checkpoint after every terminal node outcome."""
    observations["graph_step_observations"] = deepcopy(graph_observations)
    observations["process_graph_runtime"] = runtime_projection(runtime)
    observations["process_graph_binding_ledger"] = public_binding_ledger(runtime)
    observations["process_graph_request_bodies_for_cleanup"] = deepcopy(
        _dict(bags.get("request_bodies_for_cleanup"))
    )
    attach_ledger_refs_to_observations(observations, master)
    checkpoint = build_process_graph_resume_checkpoint(
        graph=graph,
        runtime=runtime,
        observations=observations,
        experiment_id=eid,
        obligation_id=oid,
        campaign_id=resolved_campaign_id,
        execution_id=resolved_execution_id,
    )
    observations["process_graph_resume_checkpoint"] = checkpoint
    return checkpoint


def _execute_graph_node(
    *,
    node_id: str,
    step: dict[str, Any],
    graph: dict[str, Any],
    runtime: dict[str, Any],
    master: Any,
    bags: dict[str, Any],
    observations: dict[str, Any],
    actors: dict[str, dict[str, Any]],
    ops: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    runtime_bindings: dict[str, Any],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
    campaign_id: str,
    root: Path,
    project: str,
) -> dict[str, Any]:
    context = graph_step_context(
        runtime=runtime,
        graph=graph,
        step=step,
        initial_bindings=runtime_bindings,
    )
    if _text(context.get("status")) != "READY":
        reason = _text(context.get("reason_code")) or (
            GRAPH_PREDECESSOR_NOT_SUCCEEDED
        )
        bags["steps"].append(
            record_blocked_step(
                master=master,
                contract_evidence_receipts=bags["contract_evidence_receipts"],
                pre_transport_block_reasons=bags[
                    "pre_transport_block_reasons"
                ],
                step=step,
                reason_code=reason,
                detail=_text(context.get("detail")),
                phase="treatment",
                eid=eid,
                oid=oid,
                resolved_campaign_id=resolved_campaign_id,
                resolved_execution_id=resolved_execution_id,
                graph_meta=context,
            )
        )
        record_graph_step_outcome(
            runtime=runtime,
            graph=graph,
            step=step,
            blocked_reason=reason,
        )
        return {}

    call_actors, call_tokens, credential_error = scoped_actor_context(
        actors=actors,
        tokens=tokens,
        step=step,
        credential_token_key=_text(context.get("credential_token_key")),
    )
    if credential_error:
        reason = GRAPH_TARGET_ACTOR_CREDENTIAL_UNRESOLVED
        bags["steps"].append(
            record_blocked_step(
                master=master,
                contract_evidence_receipts=bags["contract_evidence_receipts"],
                pre_transport_block_reasons=bags[
                    "pre_transport_block_reasons"
                ],
                step=step,
                reason_code=reason,
                detail=credential_error,
                phase="treatment",
                eid=eid,
                oid=oid,
                resolved_campaign_id=resolved_campaign_id,
                resolved_execution_id=resolved_execution_id,
                graph_meta=context,
            )
        )
        record_graph_step_outcome(
            runtime=runtime,
            graph=graph,
            step=step,
            blocked_reason=reason,
        )
        return {}

    sub_result = _delegate_sequential(
        control_plan=[],
        treatment_plan=[step],
        consumed_barrier_steps=set(),
        actors=call_actors,
        ops=ops,
        tokens=call_tokens,
        runtime_bindings=dict(context.get("bindings") or {}),
        activation_requirements={"control": [], "treatment": [node_id]},
        observations=observations,
        eid=eid,
        oid=oid,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
        campaign_id=campaign_id,
        root=root,
        project=project,
        base_url=_text(context.get("base_url")),
        runtime_contract=_dict(context.get("runtime_contract")),
        cleanup_failures=int(bags.get("cleanup_failures") or 0),
    )
    merge_result_bags(bags, sub_result)
    copied = copy_subledger_rows(
        master,
        sub_result.get("process_step_ledger"),
        graph_context_by_step={
            node_id: {
                **context,
                "object_refs": _list(step.get("object_refs")),
            }
        },
    )
    observation = step_observation(sub_result, node_id)
    if node_id not in copied:
        status_code = int(
            observation.get("status_code") or observation.get("status") or 0
        )
        row = master.record_step_execution(
            step_id=node_id,
            phase="treatment",
            operation_ref=_text(step.get("operation_ref")),
            actor_ref=_text(step.get("actor_ref")),
            runtime_identity=dict(context.get("bindings") or {}),
            status_code=status_code,
            final_status="EXECUTED" if status_code > 0 else "BLOCKED",
            target_reached=status_code > 0,
        )
        row.update(
            {
                "system_ref": _text(step.get("system_ref")),
                "object_refs": _list(step.get("object_refs")),
                "wave_index": int(context.get("wave_index") or 0),
            }
        )
    outcome = record_graph_step_outcome(
        runtime=runtime,
        graph=graph,
        step=step,
        observation=observation,
    )
    _apply_graph_outcome_to_master(master=master, node_id=node_id, outcome=outcome)
    if not observation:
        return {}
    return {
        **observation,
        "experiment_id": eid,
        "obligation_id": oid,
        "campaign_id": resolved_campaign_id,
        "execution_id": resolved_execution_id,
        "execution_graph_id": runtime.get("execution_graph_id"),
        "process_id": runtime.get("process_id"),
        "system_ref": _text(step.get("system_ref")),
        "object_refs": _list(step.get("object_refs")),
        "wave_index": int(context.get("wave_index") or 0),
        "graph_node_status": outcome.get("status"),
        "target_policy_decision_id": _text(
            _dict(context.get("target_policy_decision")).get("decision_id")
        ),
    }


def execute_non_barrier_plans(
    *,
    control_plan: list[Any],
    treatment_plan: list[Any],
    consumed_barrier_steps: set[int],
    actors: dict[str, dict[str, Any]],
    ops: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    runtime_bindings: dict[str, Any],
    activation_requirements: dict[str, Any],
    observations: dict[str, Any],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
    campaign_id: str,
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    cleanup_failures: int = 0,
) -> dict[str, Any]:
    """Execute a normal plan unchanged or resume one source-backed graph."""
    call_args = {
        "control_plan": control_plan,
        "treatment_plan": treatment_plan,
        "consumed_barrier_steps": consumed_barrier_steps,
        "actors": actors,
        "ops": ops,
        "tokens": tokens,
        "runtime_bindings": runtime_bindings,
        "activation_requirements": activation_requirements,
        "observations": observations,
        "eid": eid,
        "oid": oid,
        "resolved_campaign_id": resolved_campaign_id,
        "resolved_execution_id": resolved_execution_id,
        "campaign_id": campaign_id,
        "root": root,
        "project": project,
        "base_url": base_url,
        "runtime_contract": runtime_contract,
        "cleanup_failures": cleanup_failures,
    }
    graph, graph_error = extract_execution_graph(treatment_plan)
    if not graph and not graph_error:
        return _delegate_sequential(**call_args)

    order = [
        _text(value)
        for value in _list(graph.get("topological_order"))
        if _text(value)
    ]
    master = new_master_ledger(
        observations=observations,
        eid=eid,
        oid=oid,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
        campaign_id=campaign_id,
        required_ids=required_step_ids(activation_requirements, order),
    )
    bags: dict[str, Any] = {
        "steps": [],
        "contract_evidence_receipts": [],
        "request_bodies_for_cleanup": {},
        "pre_transport_block_reasons": [],
        "cleanup_failures": cleanup_failures,
    }
    if graph_error:
        runtime = {
            "status": "BLOCKED",
            "reason_code": GRAPH_RUNTIME_INVALID,
            "detail": graph_error,
        }
    elif control_plan:
        runtime = {
            "status": "BLOCKED",
            "reason_code": GRAPH_RUNTIME_INVALID,
            "detail": "graph_backed_protocol_must_not_mix_control_plan",
        }
    else:
        runtime = prepare_graph_runtime(
            graph=graph,
            treatment_plan=[
                step
                for step in treatment_plan
                if isinstance(step, dict)
                and id(step) not in consumed_barrier_steps
            ],
            ops=ops,
            base_url=base_url,
            runtime_contract=runtime_contract,
        )
    if _text(runtime.get("status")) != "READY":
        return _blocked_graph_result(
            runtime=runtime,
            treatment_plan=treatment_plan,
            master=master,
            bags=bags,
            observations=observations,
            eid=eid,
            oid=oid,
            resolved_campaign_id=resolved_campaign_id,
            resolved_execution_id=resolved_execution_id,
        )

    plan_by_id = {
        _text(step.get("step_id")): step
        for step in treatment_plan
        if isinstance(step, dict) and _text(step.get("step_id"))
    }
    resume = recover_process_graph_runtime(
        graph=graph,
        treatment_plan=treatment_plan,
        runtime=runtime,
        observations=observations,
        experiment_id=eid,
        obligation_id=oid,
        campaign_id=resolved_campaign_id,
        execution_id=resolved_execution_id,
    )
    if _text(resume.get("status")) == "BLOCKED":
        runtime.update(
            {
                "status": "BLOCKED",
                "reason_code": _text(resume.get("reason_code"))
                or GRAPH_RESUME_STATE_INVALID,
                "detail": _text(resume.get("detail"))
                or "process_graph_resume_state_invalid",
            }
        )
        return _blocked_graph_result(
            runtime=runtime,
            treatment_plan=treatment_plan,
            master=master,
            bags=bags,
            observations=observations,
            eid=eid,
            oid=oid,
            resolved_campaign_id=resolved_campaign_id,
            resolved_execution_id=resolved_execution_id,
        )

    recovered_node_ids = {
        _text(value)
        for value in _list(resume.get("recovered_node_ids"))
        if _text(value)
    }
    graph_observations = [
        dict(row)
        for row in _list(resume.get("recovered_graph_observations"))
        if isinstance(row, dict)
    ]
    if recovered_node_ids:
        contexts = {
            node_id: {
                **_dict(_dict(runtime.get("target_contexts")).get(node_id)),
                "wave_index": int(
                    _dict(runtime.get("wave_by_node")).get(node_id) or 0
                ),
                "object_refs": _list(
                    _dict(plan_by_id.get(node_id)).get("object_refs")
                ),
            }
            for node_id in recovered_node_ids
        }
        copy_subledger_rows(
            master,
            resume.get("subledger"),
            graph_context_by_step=contexts,
        )
        bags["steps"].extend(deepcopy(graph_observations))
        bags["request_bodies_for_cleanup"].update(
            deepcopy(_dict(resume.get("request_bodies_for_cleanup")))
        )
        _publish_graph_progress(
            graph=graph,
            runtime=runtime,
            master=master,
            bags=bags,
            observations=observations,
            graph_observations=graph_observations,
            eid=eid,
            oid=oid,
            resolved_campaign_id=resolved_campaign_id,
            resolved_execution_id=resolved_execution_id,
        )

    for node_id in order:
        if node_id in recovered_node_ids:
            continue
        projected = _execute_graph_node(
            node_id=node_id,
            step=_dict(plan_by_id.get(node_id)),
            graph=graph,
            runtime=runtime,
            master=master,
            bags=bags,
            observations=observations,
            actors=actors,
            ops=ops,
            tokens=tokens,
            runtime_bindings=runtime_bindings,
            eid=eid,
            oid=oid,
            resolved_campaign_id=resolved_campaign_id,
            resolved_execution_id=resolved_execution_id,
            campaign_id=campaign_id,
            root=root,
            project=project,
        )
        if projected:
            graph_observations.append(projected)
        _publish_graph_progress(
            graph=graph,
            runtime=runtime,
            master=master,
            bags=bags,
            observations=observations,
            graph_observations=graph_observations,
            eid=eid,
            oid=oid,
            resolved_campaign_id=resolved_campaign_id,
            resolved_execution_id=resolved_execution_id,
        )

    checkpoint = _publish_graph_progress(
        graph=graph,
        runtime=runtime,
        master=master,
        bags=bags,
        observations=observations,
        graph_observations=graph_observations,
        eid=eid,
        oid=oid,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
    )
    return {
        **bags,
        "process_step_ledger": master,
        "process_step_ledger_id": master.ledger_id,
        "process_step_ledger_hash": master.compute_hash(),
        "process_timeline": master.build_timeline_receipt(),
        "required_step_ids": list(master.required_step_ids),
        "planned_step_ids": list(master.required_step_ids),
        "executed_step_ids": master.executed_step_ids(),
        "process_graph_runtime": observations["process_graph_runtime"],
        "process_graph_binding_ledger": observations[
            "process_graph_binding_ledger"
        ],
        "process_graph_resume_checkpoint": checkpoint,
        "process_graph_resume_recovery": deepcopy(
            _dict(observations.get("process_graph_resume_recovery"))
        ),
    }


__all__ = ["execute_non_barrier_plans"]
