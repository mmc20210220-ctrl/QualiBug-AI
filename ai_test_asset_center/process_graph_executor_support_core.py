"""Support functions for the existing graph-aware plan executor.

The public execution authority remains ``experiment_plan_executor``.  This
module contains only bookkeeping and projection helpers so the authority stays
small and the mature sequential transport kernel remains unchanged.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any

from .contract_oracles import build_contract_evidence_receipt
from .experiment_runtime_support import _dict, _list, _text
from .process_step_execution import ProcessStepLedger


def required_step_ids(
    activation_requirements: dict[str, Any], graph_order: list[str]
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


def new_master_ledger(
    *,
    observations: dict[str, Any],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
    campaign_id: str,
    required_ids: list[str],
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
        required_step_ids=required_ids,
    )


def record_blocked_step(
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


def blocked_graph_result(
    *,
    runtime: dict[str, Any],
    treatment_plan: list[Any],
    master: ProcessStepLedger,
    bags: dict[str, Any],
    observations: dict[str, Any],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
) -> dict[str, Any]:
    reason = _text(runtime.get("reason_code")) or "GRAPH_RUNTIME_INVALID"
    detail = _text(runtime.get("detail")) or "graph_runtime_not_ready"
    for step in treatment_plan:
        if not isinstance(step, dict):
            continue
        bags["steps"].append(
            record_blocked_step(
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
    from .process_step_execution import attach_ledger_refs_to_observations

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


def copy_subledger_rows(
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


def merge_result_bags(target: dict[str, Any], result: dict[str, Any]) -> None:
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


def step_observation(result: dict[str, Any], step_id: str) -> dict[str, Any]:
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


def scoped_actor_context(
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


def public_binding_ledger(runtime: dict[str, Any]) -> dict[str, Any]:
    source = deepcopy(_dict(runtime.get("binding_ledger")))
    outputs = _dict(source.get("outputs_by_node"))
    for node_values in outputs.values():
        if not isinstance(node_values, dict):
            continue
        for row in node_values.values():
            if not isinstance(row, dict) or "value" not in row:
                continue
            value = row.pop("value")
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


def runtime_projection(runtime: dict[str, Any]) -> dict[str, Any]:
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


__all__ = [
    "required_step_ids",
    "new_master_ledger",
    "record_blocked_step",
    "blocked_graph_result",
    "copy_subledger_rows",
    "merge_result_bags",
    "step_observation",
    "scoped_actor_context",
    "public_binding_ledger",
    "runtime_projection",
]
