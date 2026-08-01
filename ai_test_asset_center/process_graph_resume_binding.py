"""Rebuild private graph bindings from persisted, hash-bound observations."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import process_graph_read_runtime as _graph_core
from .experiment_runtime_support import _dict, _list, _text
from .process_graph_resume_checkpoint import stable_hash

WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def row_proves_success(row: dict[str, Any]) -> bool:
    status_code = int(row.get("status_code") or 0)
    return (
        row.get("response_received") is True
        and row.get("operation_accepted") is True
        and 200 <= status_code < 300
        and _text(row.get("final_status") or row.get("final_step_status")).upper()
        == "EXECUTED"
        and row.get("step_failed") is not True
    )


def method_for_node(
    runtime: dict[str, Any], graph: dict[str, Any], node_id: str
) -> str:
    context = _dict(_dict(runtime.get("target_contexts")).get(node_id))
    if _text(context.get("method")):
        return _text(context.get("method")).upper()
    for raw in _list(graph.get("nodes")):
        node = _dict(raw)
        if _text(node.get("node_id") or node.get("step_id")) == node_id:
            return _text(node.get("method")).upper()
    return ""


def validate_public_outputs(
    *,
    public_ledger: dict[str, Any],
    private_ledger: dict[str, Any],
    recovered_node_ids: set[str],
) -> str:
    if _text(public_ledger.get("schema_version")) != _text(
        private_ledger.get("schema_version")
    ):
        return "resume_binding_ledger_schema_mismatch"
    public_outputs = _dict(public_ledger.get("outputs_by_node"))
    private_outputs = _dict(private_ledger.get("outputs_by_node"))
    for node_id in sorted(recovered_node_ids):
        expected_rows = _dict(public_outputs.get(node_id))
        actual_rows = _dict(private_outputs.get(node_id))
        if set(expected_rows) != set(actual_rows):
            return f"resume_binding_output_set_mismatch:{node_id}"
        for field, raw in actual_rows.items():
            actual = _dict(raw)
            expected = _dict(expected_rows.get(field))
            if _text(expected.get("value_fingerprint")) != stable_hash(
                actual.get("value")
            ):
                return f"resume_binding_value_fingerprint_mismatch:{node_id}:{field}"
            for key in (
                "producer_node_id",
                "canonical_field_id",
                "source_path",
                "system_ref",
                "response_fingerprint",
            ):
                if _text(expected.get(key)) != _text(actual.get(key)):
                    return f"resume_binding_metadata_mismatch:{node_id}:{field}:{key}"
            if stable_hash(expected.get("object_refs") or []) != stable_hash(
                actual.get("object_refs") or []
            ):
                return (
                    f"resume_binding_metadata_mismatch:{node_id}:{field}:"
                    "object_refs"
                )
    return ""


def replay_recovered_outputs(
    *,
    graph: dict[str, Any],
    runtime: dict[str, Any],
    plan_by_id: dict[str, dict[str, Any]],
    order: list[str],
    recovered_node_ids: set[str],
    rows_by_node: dict[str, list[dict[str, Any]]],
    observations_by_node: dict[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any], str]:
    replay_runtime = deepcopy(runtime)
    replay_runtime["node_status"] = {
        node_id: "PENDING" for node_id in plan_by_id
    }
    replay_runtime["binding_ledger"] = {
        "schema_version": _text(
            _dict(runtime.get("binding_ledger")).get("schema_version")
        ),
        "execution_graph_id": _text(runtime.get("execution_graph_id")),
        "outputs_by_node": {},
        "consumptions": [],
        "unresolved": [],
    }
    for node_id in order:
        if node_id not in recovered_node_ids:
            continue
        rows = rows_by_node.get(node_id, [])
        observations = observations_by_node.get(node_id, [])
        if len(rows) != 1 or not row_proves_success(rows[0]):
            return {}, f"resume_succeeded_step_ledger_unproven:{node_id}"
        if len(observations) != 1:
            return {}, f"resume_succeeded_observation_cardinality_invalid:{node_id}"
        outcome = _graph_core.record_graph_step_outcome(
            runtime=replay_runtime,
            graph=graph,
            step=plan_by_id[node_id],
            observation=observations[0],
        )
        if _text(outcome.get("status")) != "SUCCEEDED":
            return {}, f"resume_output_binding_replay_failed:{node_id}"
    return replay_runtime, ""


def restored_binding_ledger(
    *,
    public_ledger: dict[str, Any],
    replay_ledger: dict[str, Any],
    recovered_node_ids: set[str],
) -> dict[str, Any]:
    return {
        "schema_version": _text(replay_ledger.get("schema_version")),
        "execution_graph_id": _text(replay_ledger.get("execution_graph_id")),
        "outputs_by_node": {
            node_id: deepcopy(
                _dict(_dict(replay_ledger.get("outputs_by_node")).get(node_id))
            )
            for node_id in sorted(recovered_node_ids)
        },
        "consumptions": [
            dict(row)
            for row in _list(public_ledger.get("consumptions"))
            if isinstance(row, dict)
            and _text(row.get("consumer_node_id")) in recovered_node_ids
            and _text(row.get("producer_node_id")) in recovered_node_ids
        ],
        "unresolved": [],
    }


__all__ = [
    "WRITE_METHODS",
    "method_for_node",
    "replay_recovered_outputs",
    "restored_binding_ledger",
    "row_proves_success",
    "validate_public_outputs",
]
