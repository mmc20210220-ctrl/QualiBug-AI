"""Graph-aware entry point for experiment plan execution.

The mature sequential transport/governance implementation lives in
``experiment_plan_step_executor`` and remains byte-for-byte the execution
kernel for ordinary plans.  This module is the existing mainline authority: it
adds source-backed dependency scheduling, exact approved-target dispatch, and
namespaced cross-node bindings before invoking that kernel one node at a time.

It does not infer targets, credentials, ordering, joins, or binding fields.
Asynchronous waits remain blocked until a receipt-backed observer scheduler is
available.  Secondary-system writes remain blocked until cleanup can dispatch
to the same approved target.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .contract_oracles import build_contract_evidence_receipt
from .experiment_plan_step_executor import (
    execute_non_barrier_plans as _execute_sequential_plans,
)
from .experiment_runtime_support import _dict, _list, _text
from .process_graph_runtime import (
    GRAPH_PREDECESSOR_NOT_SUCCEEDED,
    GRAPH_RUNTIME_INVALID,
    GRAPH_TARGET_ACTOR_CREDENTIAL_UNRESOLVED,
    extract_execution_graph,
    graph_step_context,
    prepare_graph_runtime,
    record_graph_step_outcome,
)
from .process_step_execution import (
    ProcessStepLedger,
    attach_ledger_refs_to_observations,
)


def _required_step_ids(
    activation_requirements: dict[str, Any],
    graph_order: list[str],
) -> list[str]:
    values: list[str] = []
    for phase in ("control", "treatment"):
        for value in _list(activation_requirements.get(phase)):
            token = _text(value)
            if token and token not in values:
                values.append(token)
    for value in graph_order:
        token = _text(value)
        if token and token not in values:
            values.append(token)
    return values


def _new_master_ledger(
    *,
    observations: dict[str, Any],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
    campaign_id: str,
    required_step_ids: list[str],
) -> ProcessStepLedger:
    fixture_id = _text(
        _dict(observations.get("disposable_fixture_contract")).get("fixture_id")
    )
    protocol_id = _text(
        observations.get("protocol_id")
        or _dict(observations.get("protocol")).get("protocol_id")
    )
    return ProcessStepLedger(
        experiment_id=eid,
        fixture_id=fixture_id,
        campaign_id=_text(resolved_campaign_id or campaign_id),
        run_id=_text(resolved_execution_id),
        obligation_id=_text(oid),
        protocol_id=protocol_id,
        required_step_ids=required_step_ids,
    )


def _record_blocked_step(
    *,
    master: ProcessStepLedger,
    contract_evidence_receipts: list[dict[str, Any]],
    pre_transport_block_reasons: list[str],
    step: dict[str, Any],
    reason_code: str,
    detail: str,
    phase: str,
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
    graph_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    step_id = _text(step.get("step_id"))
    op_ref = _text(step.get("operation_ref"))
    actor_ref = _text(step.get("actor_ref"))
    pre_transport_block_reasons.append(f"{reason_code}:{detail}")
    contract_evidence_receipts.append(
        build_contract_evidence_receipt(
            kind=phase,
            experiment_id=eid,
            obligation_id=oid,
            campaign_id=resolved_campaign_id,
            execution_id=resolved_execution_id,
            subject_id=step_id,
            status="BLOCKED",
            evidence={
                "request_reached_transport": False,
                "reason_code": reason_code,
                "detail": detail,
                "execution_graph_id": _text(
                    _dict(graph_meta).get("execution_graph_id")
                ),
            },
        )
    )
    row = master.record_step_execution(
        step_id=step_id,
        phase=phase,
        operation_ref=op_ref,
        actor_ref=actor_ref,
        runtime_identity={},
        status_code=0,
        final_status="BLOCKED",
        target_reached=False,
    )
    row.update(
        {
            "reason_code": reason_code,
            "detail": detail,
            "system_ref": _text(step.get("system_ref")),
            "object_refs": _list(step.get("object_refs")),
            "wave_index": int(_dict(graph_meta).get("wave_index") or 0),
        }
    )
    master.record_timeline_event(
        step_id=step_id,
        phase=phase,
        event_type="STEP_FAILED",
        operation_ref=op_ref,
        actor_ref=actor_ref,
    )
    return {
        "phase": phase,
        "step_id": step_id,
        "status": "blocked_request",
        "reason": reason_code,
        "detail": detail,
        "method": _text(step.get("method")).upper(),
        "path": _text(step.get("path") or step.get("path_template")),
        "status_code": 0,
        "actor_ref": actor_ref,
        "operation_ref": op_ref,
        "system_ref": _text(step.get("system_ref")),
        "object_refs": _list(step.get("object_refs")),
    }


def _copy_subledger_rows(
    master: ProcessStepLedger,
    subledger: Any,
    *,
    graph_context_by_step: dict[str, dict[str, Any]] | None = None,
) -> set[str]:
    copied: set[str] = set()
    if subledger is None or not hasattr(subledger, "all_rows"):
        return copied
    contexts = graph_context_by_step or {}
    for source in subledger.all_rows():
        if not isinstance(source, dict):
            continue
        step_id = _text(source.get("step_id"))
        if not step_id:
            continue
        row = master.record_step_execution(
            step_id=step_id,
            phase=_text(source.get("phase")),
            operation_ref=_text(
                source.get("operation_ref") or source.get("operation_id")
            ),
            actor_ref=_text(source.get("actor_ref")),
            runtime_identity=_dict(source.get("runtime_identity")),
            request_receipt_id=_text(source.get("request_receipt_id")),
            response_receipt_id=_text(source.get("response_receipt_id")),
            transport_receipt_id=_text(source.get("transport_receipt_id")),
            before_state_receipt_id=_text(source.get("before_state_receipt_id")),
            after_state_receipt_id=_text(source.get("after_state_receipt_id")),
            observer_receipt_ids=list(
                source.get("observation_receipt_ids")
                or source.get("observer_receipt_ids")
                or []
            ),
            oracle_receipt_ids=list(source.get("oracle_receipt_ids") or []),
            cleanup_contract_id=_text(source.get("cleanup_contract_id")),
            cleanup_receipt_ids=list(source.get("cleanup_receipt_ids") or []),
            status_code=int(source.get("status_code") or 0),
            final_status=_text(
                source.get("final_step_status") or source.get("final_status")
            )
            or "BLOCKED",
            mutation_occurred=source.get("mutation_occurred"),
            target_reached=source.get("target_reached"),
        )
        context = _dict(contexts.get(step_id))
        row.update(
            {
                key: value
                for key, value in (
                    ("system_ref", _text(context.get("system_ref"))),
                    ("object_refs", _list(context.get("object_refs"))),
                    ("wave_index", context.get("wave_index")),
                    (
                        "target_policy_decision_id",
                        _text(
                            _dict(context.get("target_policy_decision")).get(
                                "decision_id"
                            )
                        ),
                    ),
                )
                if value not in ("", None, [])
            }
        )
        copied.add(step_id)
    if hasattr(subledger, "timeline"):
        for event in subledger.timeline():
            if not isinstance(event, dict):
                continue
            master.record_timeline_event(
                step_id=_text(event.get("step_id")),
                phase=_text(event.get("phase")),
                event_type=_text(event.get("event_type")) or "STEP_FAILED",
                operation_ref=_text(event.get("operation_ref")),
                actor_ref=_text(event.get("actor_ref")),
                receipt_id=_text(event.get("receipt_id")),
            )
    return copied


def _merge_result_bags(target: dict[str, Any], result: dict[str, Any]) -> None:
    target["steps"].extend(list(result.get("steps") or []))
    target["contract_evidence_receipts"].extend(
        list(result.get("contract_evidence_receipts") or [])
    )
    target["request_bodies_for_cleanup"].update(
        dict(result.get("request_bodies_for_cleanup") or {})
    )
    target["pre_transport_block_reasons"].extend(
        list(result.get("pre_transport_block_reasons") or [])
    )
    target["cleanup_failures"] = max(
        int(target.get("cleanup_failures") or 0),
        int(result.get("cleanup_failures") or 0),
    )


def _step_observation(result: dict[str, Any], step_id: str) -> dict[str, Any]:
    candidates = [
        row
        for row in list(result.get("steps") or [])
        if isinstance(row, dict)
        and _text(row.get("step_id") or row.get("subject_id")) == step_id
        and not _text(row.get("phase")).endswith(
            "_response_bound_effect_observation"
        )
    ]
    return dict(candidates[-1]) if candidates else {}


def _scoped_actor_context(
    *,
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    step: dict[str, Any],
    credential_token_key: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, str], str]:
    if not credential_token_key:
        return actors, tokens, ""
    token = _text(tokens.get(credential_token_key))
    if not token:
        return {}, {}, f"credential_token_missing:{credential_token_key}"
    actor_ref = _text(step.get("actor_ref"))
    actor = _dict(actors.get(actor_ref))
    if not actor:
        return {}, {}, f"actor_identity_missing:{actor_ref}"
    scoped_actors = dict(actors)
    scoped_actor = dict(actor)
    scoped_actor["credential_secret_ref"] = credential_token_key
    scoped_actors[actor_ref] = scoped_actor
    return scoped_actors, tokens, ""


def _public_binding_ledger(runtime: dict[str, Any]) -> dict[str, Any]:
    source = deepcopy(_dict(runtime.get("binding_ledger")))
    outputs = _dict(source.get("outputs_by_node"))
    for node_values in outputs.values():
        if not isinstance(node_values, dict):
            continue
        for row in node_values.values():
            if not isinstance(row, dict) or "value" not in row:
                continue
            value = row.pop("value")
            import hashlib
            import json

            row["value_fingerprint"] = hashlib.sha256(
                json.dumps(
                    value,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
    return source


def _runtime_projection(runtime: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": runtime.get("schema_version"),
        "status": runtime.get("status"),
        "execution_graph_id": runtime.get("execution_graph_id"),
        "process_id": runtime.get("process_id"),
        "topological_order": list(runtime.get("topological_order") or []),
        "predecessors": deepcopy(_dict(runtime.get("predecessors"))),
        "wave_by_node": deepcopy(_dict(runtime.get("wave_by_node"))),
        "node_status": deepcopy(_dict(runtime.get("node_status"))),
        "target_decisions": {
            node_id: {
                "system_ref": _text(_dict(context).get("system_ref")),
                "base_url": _text(_dict(context).get("base_url")),
                "decision_id": _text(
                    _dict(_dict(context).get("target_policy_decision")).get(
                        "decision_id"
                    )
                ),
                "primary": _dict(context).get("primary") is True,
            }
            for node_id, context in _dict(runtime.get("target_contexts")).items()
            if isinstance(context, dict)
        },
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
    """Execute a normal plan unchanged or a synchronous source-backed graph."""
    graph, graph_error = extract_execution_graph(treatment_plan)
    if not graph and not graph_error:
        return _execute_sequential_plans(
            control_plan=control_plan,
            treatment_plan=treatment_plan,
            consumed_barrier_steps=consumed_barrier_steps,
            actors=actors,
            ops=ops,
            tokens=tokens,
            runtime_bindings=runtime_bindings,
            activation_requirements=activation_requirements,
            observations=observations,
            eid=eid,
            oid=oid,
            resolved_campaign_id=resolved_campaign_id,
            resolved_execution_id=resolved_execution_id,
            campaign_id=campaign_id,
            root=root,
            project=project,
            base_url=base_url,
            runtime_contract=runtime_contract,
            cleanup_failures=cleanup_failures,
        )

    order = [
        _text(value)
        for value in _list(graph.get("topological_order"))
        if _text(value)
    ]
    master = _new_master_ledger(
        observations=observations,
        eid=eid,
        oid=oid,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
        campaign_id=campaign_id,
        required_step_ids=_required_step_ids(activation_requirements, order),
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
        filtered_treatment = [
            step
            for step in treatment_plan
            if isinstance(step, dict) and id(step) not in consumed_barrier_steps
        ]
        runtime = prepare_graph_runtime(
            graph=graph,
            treatment_plan=filtered_treatment,
            ops=ops,
            base_url=base_url,
            runtime_contract=runtime_contract,
        )

    if _text(runtime.get("status")) != "READY":
        reason = _text(runtime.get("reason_code")) or GRAPH_RUNTIME_INVALID
        detail = _text(runtime.get("detail")) or "graph_runtime_not_ready"
        for step in treatment_plan:
            if not isinstance(step, dict):
                continue
            bags["steps"].append(
                _record_blocked_step(
                    master=master,
                    contract_evidence_receipts=bags["contract_evidence_receipts"],
                    pre_transport_block_reasons=bags["pre_transport_block_reasons"],
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

    plan_by_id = {
        _text(step.get("step_id")): step
        for step in treatment_plan
        if isinstance(step, dict) and _text(step.get("step_id"))
    }
    graph_observations: list[dict[str, Any]] = []

    for node_id in order:
        step = _dict(plan_by_id.get(node_id))
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
            detail = _text(context.get("detail"))
            bags["steps"].append(
                _record_blocked_step(
                    master=master,
                    contract_evidence_receipts=bags["contract_evidence_receipts"],
                    pre_transport_block_reasons=bags["pre_transport_block_reasons"],
                    step=step,
                    reason_code=reason,
                    detail=detail,
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
            continue

        call_actors, call_tokens, credential_error = _scoped_actor_context(
            actors=actors,
            tokens=tokens,
            step=step,
            credential_token_key=_text(context.get("credential_token_key")),
        )
        if credential_error:
            reason = GRAPH_TARGET_ACTOR_CREDENTIAL_UNRESOLVED
            bags["steps"].append(
                _record_blocked_step(
                    master=master,
                    contract_evidence_receipts=bags["contract_evidence_receipts"],
                    pre_transport_block_reasons=bags["pre_transport_block_reasons"],
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
            continue

        sub_result = _execute_sequential_plans(
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
        _merge_result_bags(bags, sub_result)
        copied = _copy_subledger_rows(
            master,
            sub_result.get("process_step_ledger"),
            graph_context_by_step={
                node_id: {
                    **context,
                    "object_refs": _list(step.get("object_refs")),
                }
            },
        )
        observation = _step_observation(sub_result, node_id)
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
        if observation:
            graph_observations.append(
                {
                    **observation,
                    "execution_graph_id": runtime.get("execution_graph_id"),
                    "process_id": runtime.get("process_id"),
                    "system_ref": _text(step.get("system_ref")),
                    "object_refs": _list(step.get("object_refs")),
                    "wave_index": int(context.get("wave_index") or 0),
                    "graph_node_status": outcome.get("status"),
                    "target_policy_decision_id": _text(
                        _dict(context.get("target_policy_decision")).get(
                            "decision_id"
                        )
                    ),
                }
            )

    observations["graph_step_observations"] = graph_observations
    observations["process_graph_runtime"] = _runtime_projection(runtime)
    observations["process_graph_binding_ledger"] = _public_binding_ledger(runtime)
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
        "process_graph_runtime": observations["process_graph_runtime"],
        "process_graph_binding_ledger": observations[
            "process_graph_binding_ledger"
        ],
    }


__all__ = ["execute_non_barrier_plans"]
