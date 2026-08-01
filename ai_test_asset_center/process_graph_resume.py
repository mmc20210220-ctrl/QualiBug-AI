"""Recover a process graph without replaying completed or ambiguous writes."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from .experiment_runtime_support import _dict, _list, _text
from .process_graph_resume_binding import (
    WRITE_METHODS,
    method_for_node,
    replay_recovered_outputs,
    restored_binding_ledger,
    validate_public_outputs,
)
from .process_graph_resume_checkpoint import (
    GRAPH_RESUME_AUTHORITY,
    GRAPH_RESUME_SCHEMA,
    build_process_graph_resume_checkpoint,
    checkpoint_applies,
    current_graph_observations,
    current_ledger_rows,
    graph_id,
    observation_fingerprints,
    observation_node_id,
    validate_checkpoint,
    validate_fresh_runtime_scope,
)
from .process_graph_wait_termination_recovery import _validate_ledger

GRAPH_RESUME_STATE_INVALID = "PROCESS_GRAPH_RESUME_STATE_INVALID"
GRAPH_RESUME_EFFECT_AMBIGUOUS = "PROCESS_GRAPH_RESUME_EFFECT_AMBIGUOUS"
_ALLOWED_NODE_STATUSES = frozenset({"PENDING", "SUCCEEDED", "FAILED", "BLOCKED"})


class PersistedProcessGraphSubledger:
    """Adapter for continuing through the existing ledger-copy authority."""

    def __init__(self, rows: list[dict[str, Any]], events: list[dict[str, Any]]):
        self._rows = [dict(row) for row in rows]
        self._events = [dict(row) for row in events]

    def all_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._rows]

    def timeline(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._events]


def _blocked(detail: str, reason_code: str = GRAPH_RESUME_STATE_INVALID) -> dict:
    return {"status": "BLOCKED", "reason_code": reason_code, "detail": detail}


def _fresh() -> dict:
    return {
        "status": "FRESH",
        "recovered_node_ids": [],
        "recovered_rows": [],
        "recovered_graph_observations": [],
        "request_bodies_for_cleanup": {},
    }


def _node_maps(
    rows: list[dict[str, Any]], observations: list[dict[str, Any]]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    rows_by_node: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_node.setdefault(_text(row.get("step_id")), []).append(row)
    observations_by_node: dict[str, list[dict[str, Any]]] = {}
    for row in observations:
        observations_by_node.setdefault(observation_node_id(row), []).append(row)
    return rows_by_node, observations_by_node


def recover_process_graph_runtime(
    *,
    graph: dict[str, Any],
    treatment_plan: list[Any],
    runtime: dict[str, Any],
    observations: dict[str, Any],
    experiment_id: str,
    obligation_id: str,
    campaign_id: str,
    execution_id: str,
) -> dict[str, Any]:
    """Rehydrate proven nodes or fail closed before any new transport."""
    eid = _text(experiment_id)
    oid = _text(obligation_id)
    cid = _text(campaign_id)
    run_id = _text(execution_id)
    execution_graph_id = graph_id(graph)
    plan_by_id = {
        _text(step.get("step_id") or step.get("node_id")): dict(step)
        for step in treatment_plan
        if isinstance(step, dict)
        and _text(step.get("step_id") or step.get("node_id"))
    }
    graph_node_ids = set(plan_by_id)
    current_rows = current_ledger_rows(
        observations,
        graph_node_ids=graph_node_ids,
        experiment_id=eid,
        obligation_id=oid,
        campaign_id=cid,
        execution_id=run_id,
    )
    checkpoint = _dict(observations.get("process_graph_resume_checkpoint"))
    applies = checkpoint_applies(
        checkpoint,
        graph=graph,
        experiment_id=eid,
        obligation_id=oid,
        campaign_id=cid,
        execution_id=run_id,
    )
    if not current_rows and not applies:
        return _fresh()
    if not applies:
        return _blocked("resume_checkpoint_missing_for_persisted_execution")
    error = validate_checkpoint(
        checkpoint=checkpoint,
        graph=graph,
        observations=observations,
        experiment_id=eid,
        obligation_id=oid,
        campaign_id=cid,
        execution_id=run_id,
    )
    if error:
        return _blocked(error)

    persisted_rows = [
        dict(row)
        for row in _list(observations.get("process_step_receipts"))
        if isinstance(row, dict)
    ]
    ledger, error = _validate_ledger(observations=observations, rows=persisted_rows)
    if error:
        return _blocked(error)
    if (
        _text(ledger.get("ledger_id"))
        != _text(checkpoint.get("process_step_ledger_id"))
        or _text(ledger.get("ledger_hash"))
        != _text(checkpoint.get("process_step_ledger_hash"))
    ):
        return _blocked("resume_validated_ledger_scope_mismatch")

    persisted_runtime = _dict(observations.get("process_graph_runtime"))
    error = validate_fresh_runtime_scope(
        runtime=runtime, persisted_runtime=persisted_runtime
    )
    if error:
        return _blocked(error)
    persisted_status = {
        _text(node_id): _text(status).upper()
        for node_id, status in _dict(persisted_runtime.get("node_status")).items()
        if _text(node_id)
    }
    checkpoint_status = {
        _text(node_id): _text(status).upper()
        for node_id, status in _dict(checkpoint.get("node_status")).items()
        if _text(node_id)
    }
    if checkpoint_status != persisted_status:
        return _blocked("resume_node_status_projection_mismatch")
    if set(checkpoint_status) != graph_node_ids or any(
        status not in _ALLOWED_NODE_STATUSES for status in checkpoint_status.values()
    ):
        return _blocked("resume_node_status_set_invalid")

    graph_rows = current_graph_observations(
        observations,
        experiment_id=eid,
        obligation_id=oid,
        campaign_id=cid,
        execution_id=run_id,
        execution_graph_id=execution_graph_id,
    )
    expected_observations = {
        _text(key): _text(value)
        for key, value in _dict(
            checkpoint.get("graph_observation_fingerprints")
        ).items()
        if _text(key)
    }
    if observation_fingerprints(graph_rows) != expected_observations:
        return _blocked("resume_graph_observation_projection_drift")
    rows_by_node, observations_by_node = _node_maps(current_rows, graph_rows)

    recovered = {
        node_id
        for node_id, status in checkpoint_status.items()
        if status == "SUCCEEDED"
    }
    predecessors = _dict(runtime.get("predecessors"))
    for node_id in sorted(recovered):
        missing = [
            _text(value)
            for value in _list(predecessors.get(node_id))
            if _text(value) not in recovered
        ]
        if missing:
            return _blocked(
                f"resume_succeeded_node_predecessor_incomplete:{node_id}:"
                f"{','.join(missing)}"
            )

    order = [
        _text(value)
        for value in _list(runtime.get("topological_order"))
        if _text(value)
    ]
    replay_runtime, error = replay_recovered_outputs(
        graph=graph,
        runtime=runtime,
        plan_by_id=plan_by_id,
        order=order,
        recovered_node_ids=recovered,
        rows_by_node=rows_by_node,
        observations_by_node=observations_by_node,
    )
    if error:
        return _blocked(error)
    for node_id in order:
        if node_id in recovered:
            continue
        rows = rows_by_node.get(node_id, [])
        if len(rows) > 1:
            return _blocked(f"resume_step_ledger_cardinality_invalid:{node_id}")
        if rows and method_for_node(runtime, graph, node_id) in WRITE_METHODS:
            if rows[0].get("transport_attempted") is True:
                return _blocked(
                    f"resume_write_effect_not_terminal:{node_id}",
                    GRAPH_RESUME_EFFECT_AMBIGUOUS,
                )

    public_ledger = _dict(observations.get("process_graph_binding_ledger"))
    if _text(public_ledger.get("execution_graph_id")) != execution_graph_id:
        return _blocked("resume_binding_ledger_graph_scope_mismatch")
    error = validate_public_outputs(
        public_ledger=public_ledger,
        private_ledger=_dict(replay_runtime.get("binding_ledger")),
        recovered_node_ids=recovered,
    )
    if error:
        return _blocked(error)

    runtime["node_status"] = {
        node_id: "SUCCEEDED" if node_id in recovered else "PENDING"
        for node_id in order
    }
    runtime["binding_ledger"] = restored_binding_ledger(
        public_ledger=public_ledger,
        replay_ledger=_dict(replay_runtime.get("binding_ledger")),
        recovered_node_ids=recovered,
    )
    recovered_rows = [
        dict(rows_by_node[node_id][0]) for node_id in order if node_id in recovered
    ]
    recovered_observations = [
        dict(observations_by_node[node_id][0])
        for node_id in order
        if node_id in recovered
    ]
    recovered_events = [
        dict(row)
        for row in _list(_dict(observations.get("process_timeline")).get("events"))
        if isinstance(row, dict) and _text(row.get("step_id")) in recovered
    ]
    metadata = {
        "schema_version": GRAPH_RESUME_SCHEMA,
        "authority": GRAPH_RESUME_AUTHORITY,
        "checkpoint_fingerprint": _text(checkpoint.get("checkpoint_fingerprint")),
        "execution_graph_id": execution_graph_id,
        "execution_id": run_id,
        "recovered_node_ids": [node_id for node_id in order if node_id in recovered],
        "recovered_node_count": len(recovered),
        "process_step_ledger_id": _text(ledger.get("ledger_id")),
        "process_step_ledger_hash": _text(ledger.get("ledger_hash")),
    }
    observations["process_graph_resume_recovery"] = dict(metadata)
    return {
        "status": "RECOVERED" if recovered else "RETRYABLE",
        **metadata,
        "recovered_rows": recovered_rows,
        "recovered_timeline_events": recovered_events,
        "recovered_graph_observations": recovered_observations,
        "request_bodies_for_cleanup": deepcopy(
            _dict(observations.get("process_graph_request_bodies_for_cleanup"))
        ),
        "subledger": PersistedProcessGraphSubledger(
            recovered_rows, recovered_events
        ),
    }


__all__ = [
    "GRAPH_RESUME_SCHEMA",
    "GRAPH_RESUME_AUTHORITY",
    "GRAPH_RESUME_STATE_INVALID",
    "GRAPH_RESUME_EFFECT_AMBIGUOUS",
    "PersistedProcessGraphSubledger",
    "build_process_graph_resume_checkpoint",
    "recover_process_graph_runtime",
]
