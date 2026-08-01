"""Hash-bound checkpoint authority for resumable process graphs."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import process_graph_read_runtime as _graph_core
from .experiment_runtime_support import _dict, _list, _text

GRAPH_RESUME_SCHEMA = "qualibug.process-graph-resume-checkpoint.v1"
GRAPH_RESUME_AUTHORITY = "process_step_ledger_graph_observations"


def stable_hash(value: Any) -> str:
    return _graph_core._stable_hash(value)


def graph_id(graph: dict[str, Any]) -> str:
    return _text(graph.get("execution_graph_id") or graph.get("process_id"))


def graph_fingerprint(graph: dict[str, Any]) -> str:
    return stable_hash(graph)


def identity_matches(
    row: dict[str, Any],
    *,
    experiment_id: str,
    obligation_id: str,
    campaign_id: str,
    execution_id: str,
) -> bool:
    return (
        _text(row.get("experiment_id")) == experiment_id
        and _text(row.get("obligation_id")) == obligation_id
        and _text(row.get("campaign_id")) == campaign_id
        and _text(row.get("execution_id") or row.get("run_id")) == execution_id
    )


def observation_node_id(row: dict[str, Any]) -> str:
    return _text(row.get("step_id") or row.get("node_id") or row.get("subject_id"))


def current_graph_observations(
    observations: dict[str, Any],
    *,
    experiment_id: str,
    obligation_id: str,
    campaign_id: str,
    execution_id: str,
    execution_graph_id: str,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(observations.get("graph_step_observations"))
        if isinstance(row, dict)
        and identity_matches(
            row,
            experiment_id=experiment_id,
            obligation_id=obligation_id,
            campaign_id=campaign_id,
            execution_id=execution_id,
        )
        and _text(row.get("execution_graph_id") or row.get("process_id"))
        == execution_graph_id
    ]


def observation_fingerprints(rows: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in rows:
        node_id = observation_node_id(row)
        if node_id:
            result[node_id] = stable_hash(row)
    return dict(sorted(result.items()))


def build_process_graph_resume_checkpoint(
    *,
    graph: dict[str, Any],
    runtime: dict[str, Any],
    observations: dict[str, Any],
    experiment_id: str,
    obligation_id: str,
    campaign_id: str,
    execution_id: str,
) -> dict[str, Any]:
    """Seal graph identity, ledger, node state and persisted evidence projections."""
    execution_graph_id = graph_id(graph)
    graph_rows = current_graph_observations(
        observations,
        experiment_id=_text(experiment_id),
        obligation_id=_text(obligation_id),
        campaign_id=_text(campaign_id),
        execution_id=_text(execution_id),
        execution_graph_id=execution_graph_id,
    )
    payload = {
        "schema_version": GRAPH_RESUME_SCHEMA,
        "authority": GRAPH_RESUME_AUTHORITY,
        "experiment_id": _text(experiment_id),
        "obligation_id": _text(obligation_id),
        "campaign_id": _text(campaign_id),
        "execution_id": _text(execution_id),
        "execution_graph_id": execution_graph_id,
        "process_id": _text(graph.get("process_id")),
        "graph_contract_fingerprint": graph_fingerprint(graph),
        "process_step_ledger_id": _text(observations.get("process_step_ledger_id")),
        "process_step_ledger_hash": _text(
            observations.get("process_step_ledger_hash")
        ),
        "node_status": {
            _text(node_id): _text(status).upper()
            for node_id, status in _dict(runtime.get("node_status")).items()
            if _text(node_id)
        },
        "runtime_projection_fingerprint": stable_hash(
            _dict(observations.get("process_graph_runtime"))
        ),
        "binding_ledger_fingerprint": stable_hash(
            _dict(observations.get("process_graph_binding_ledger"))
        ),
        "graph_observation_fingerprints": observation_fingerprints(graph_rows),
        "request_bodies_for_cleanup_fingerprint": stable_hash(
            _dict(observations.get("process_graph_request_bodies_for_cleanup"))
        ),
    }
    payload["checkpoint_fingerprint"] = stable_hash(payload)
    return payload


def current_ledger_rows(
    observations: dict[str, Any],
    *,
    graph_node_ids: set[str],
    experiment_id: str,
    obligation_id: str,
    campaign_id: str,
    execution_id: str,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(observations.get("process_step_receipts"))
        if isinstance(row, dict)
        and _text(row.get("step_id")) in graph_node_ids
        and identity_matches(
            row,
            experiment_id=experiment_id,
            obligation_id=obligation_id,
            campaign_id=campaign_id,
            execution_id=execution_id,
        )
    ]


def checkpoint_applies(
    checkpoint: dict[str, Any],
    *,
    graph: dict[str, Any],
    experiment_id: str,
    obligation_id: str,
    campaign_id: str,
    execution_id: str,
) -> bool:
    return bool(checkpoint) and all(
        (
            _text(checkpoint.get("experiment_id")) == experiment_id,
            _text(checkpoint.get("obligation_id")) == obligation_id,
            _text(checkpoint.get("campaign_id")) == campaign_id,
            _text(checkpoint.get("execution_id")) == execution_id,
            _text(checkpoint.get("execution_graph_id")) == graph_id(graph),
        )
    )


def validate_fresh_runtime_scope(
    *,
    runtime: dict[str, Any],
    persisted_runtime: dict[str, Any],
) -> str:
    for key in ("execution_graph_id", "process_id"):
        if _text(runtime.get(key)) != _text(persisted_runtime.get(key)):
            return f"resume_fresh_runtime_{key}_mismatch"
    for key in ("topological_order", "predecessors", "wave_by_node"):
        if runtime.get(key) != persisted_runtime.get(key):
            return f"resume_fresh_runtime_{key}_mismatch"
    persisted_targets = _dict(persisted_runtime.get("target_decisions"))
    current_targets = _dict(runtime.get("target_contexts"))
    if set(persisted_targets) != set(current_targets):
        return "resume_target_decision_set_mismatch"
    for node_id, raw in persisted_targets.items():
        expected = _dict(raw)
        current = _dict(current_targets.get(node_id))
        decision = _dict(current.get("target_policy_decision"))
        actual = {
            "system_ref": _text(current.get("system_ref")),
            "base_url": _text(current.get("base_url")),
            "decision_id": _text(decision.get("decision_id")),
            "primary": current.get("primary") is True,
        }
        normalized_expected = {
            "system_ref": _text(expected.get("system_ref")),
            "base_url": _text(expected.get("base_url")),
            "decision_id": _text(expected.get("decision_id")),
            "primary": expected.get("primary") is True,
        }
        if actual != normalized_expected:
            return f"resume_target_decision_drift:{node_id}"
    return ""


def validate_checkpoint(
    *,
    checkpoint: dict[str, Any],
    graph: dict[str, Any],
    observations: dict[str, Any],
    experiment_id: str,
    obligation_id: str,
    campaign_id: str,
    execution_id: str,
) -> str:
    if _text(checkpoint.get("schema_version")) != GRAPH_RESUME_SCHEMA:
        return "resume_checkpoint_schema_invalid"
    if _text(checkpoint.get("authority")) != GRAPH_RESUME_AUTHORITY:
        return "resume_checkpoint_authority_invalid"
    expected_scope = {
        "experiment_id": experiment_id,
        "obligation_id": obligation_id,
        "campaign_id": campaign_id,
        "execution_id": execution_id,
        "execution_graph_id": graph_id(graph),
    }
    for key, expected in expected_scope.items():
        if _text(checkpoint.get(key)) != expected:
            return f"resume_checkpoint_{key}_mismatch"
    if _text(checkpoint.get("graph_contract_fingerprint")) != graph_fingerprint(
        graph
    ):
        return "resume_checkpoint_graph_contract_drift"
    fingerprint = _text(checkpoint.get("checkpoint_fingerprint"))
    payload = {
        key: deepcopy(value)
        for key, value in checkpoint.items()
        if key != "checkpoint_fingerprint"
    }
    if not fingerprint or fingerprint != stable_hash(payload):
        return "resume_checkpoint_fingerprint_mismatch"
    projections = (
        ("process_step_ledger_id", "process_step_ledger_id", _text),
        ("process_step_ledger_hash", "process_step_ledger_hash", _text),
        ("runtime_projection_fingerprint", "process_graph_runtime", stable_hash),
        ("binding_ledger_fingerprint", "process_graph_binding_ledger", stable_hash),
        (
            "request_bodies_for_cleanup_fingerprint",
            "process_graph_request_bodies_for_cleanup",
            stable_hash,
        ),
    )
    for checkpoint_key, observation_key, normalizer in projections:
        observed = observations.get(observation_key)
        if normalizer is stable_hash:
            observed = _dict(observed)
        if _text(checkpoint.get(checkpoint_key)) != normalizer(observed):
            return f"resume_{checkpoint_key}_mismatch"
    return ""


__all__ = [
    "GRAPH_RESUME_SCHEMA",
    "GRAPH_RESUME_AUTHORITY",
    "build_process_graph_resume_checkpoint",
    "checkpoint_applies",
    "current_graph_observations",
    "current_ledger_rows",
    "graph_id",
    "observation_fingerprints",
    "observation_node_id",
    "stable_hash",
    "validate_checkpoint",
    "validate_fresh_runtime_scope",
]
