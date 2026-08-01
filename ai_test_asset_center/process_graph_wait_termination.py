"""Fail-closed terminal authority for process-graph async waits.

A compensation receipt terminates only the exact graph branch, execution and
rollback contract that produced it. This module validates live cleanup receipts
or recovers their exact ledger-scoped persisted chain after a process restart,
then binds the terminal epoch to the compile-frozen wait/event contract before
any observer or business transport can run.
"""
from __future__ import annotations

from typing import Any

from . import process_graph_event_transition as _event_authority
from . import process_graph_wait_contract as _wait_authority
from .contract_oracles import validate_contract_evidence_receipt
from .process_graph_cleanup_executor_core import GRAPH_CLEANUP_SCHEMA
from .process_graph_wait_contract import STATUS_BLOCKED as WAIT_BLOCKED
from .process_graph_wait_termination_recovery import (
    PERSISTED_TERMINATION_AUTHORITY,
    recover_persisted_cleanup_receipts,
)

WAIT_TERMINATION_EPOCH_ACTIVE = (
    "PROCESS_GRAPH_WAIT_TERMINATION_EPOCH_ACTIVE"
)
WAIT_TERMINATION_RECEIPT_INVALID = (
    "PROCESS_GRAPH_WAIT_TERMINATION_RECEIPT_INVALID"
)
TERMINATION_EPOCH_SCHEMA = (
    "qualibug.process-graph-wait-termination-epoch.v1"
)
_LIVE_TERMINATION_AUTHORITY = "process_graph_cleanup_receipts"
_TERMINAL_CLEANUP_STATUSES = frozenset(
    {"BLOCKED", "COMPLETED", "FAILED", "NOT_REQUIRED"}
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _wait_contract(step: dict[str, Any]) -> dict[str, Any]:
    return _dict(step.get("wait_contract"))


def _source_node_id(step: dict[str, Any]) -> str:
    return _text(_wait_contract(step).get("source_node_id"))


def _graph_scope(
    *,
    step: dict[str, Any],
    observations: dict[str, Any],
) -> tuple[str, str, str]:
    graph = _dict(step.get("_execution_graph"))
    graph_id = _text(graph.get("execution_graph_id") or graph.get("process_id"))
    rollback = _dict(
        graph.get("rollback_contract")
        or graph.get("process_graph_rollback_contract")
    )
    rollback_id = _text(rollback.get("contract_fingerprint"))
    observed_runtime = _dict(observations.get("process_graph_runtime"))
    observed_graph_id = _text(
        observed_runtime.get("execution_graph_id")
        or observed_runtime.get("process_id")
    )
    return graph_id, rollback_id, observed_graph_id


def _cleanup_receipt_source(
    *,
    step: dict[str, Any],
    observations: dict[str, Any],
    experiment_id: str,
    obligation_id: str,
    campaign_id: str,
    execution_id: str,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], str]:
    live = [
        dict(row)
        for row in _list(observations.get("process_graph_cleanup_receipts"))
        if isinstance(row, dict)
    ]
    if live:
        return live, _LIVE_TERMINATION_AUTHORITY, {}, ""

    recovered, metadata, error = recover_persisted_cleanup_receipts(
        observations=observations,
        source_step_id=_source_node_id(step),
        experiment_id=experiment_id,
        obligation_id=obligation_id,
        campaign_id=campaign_id,
        execution_id=execution_id,
    )
    authority = _text(metadata.get("authority"))
    if recovered and not authority:
        authority = PERSISTED_TERMINATION_AUTHORITY
    return recovered, authority, metadata, error


def _matching_cleanup_receipts(
    *,
    step: dict[str, Any],
    observations: dict[str, Any],
    experiment_id: str,
    obligation_id: str,
    campaign_id: str,
    execution_id: str,
) -> tuple[list[dict[str, Any]], str, dict[str, Any], str]:
    source_node_id = _source_node_id(step)
    if not all(
        (
            source_node_id,
            experiment_id,
            obligation_id,
            campaign_id,
            execution_id,
        )
    ):
        return [], "", {}, ""

    graph_id, rollback_id, observed_graph_id = _graph_scope(
        step=step,
        observations=observations,
    )
    if graph_id and observed_graph_id and graph_id != observed_graph_id:
        return [], "", {}, ""
    observed_rollback_id = _text(
        observations.get("process_graph_rollback_contract_id")
    )

    raw_receipts, authority, recovery, source_error = _cleanup_receipt_source(
        step=step,
        observations=observations,
        experiment_id=experiment_id,
        obligation_id=obligation_id,
        campaign_id=campaign_id,
        execution_id=execution_id,
    )
    if source_error:
        return [], authority, recovery, source_error

    matches: list[dict[str, Any]] = []
    seen: set[str] = set()
    for receipt in raw_receipts:
        evidence = _dict(receipt.get("evidence"))
        if not (
            _text(receipt.get("kind")).lower() == "cleanup"
            and _text(receipt.get("experiment_id")) == experiment_id
            and _text(receipt.get("obligation_id")) == obligation_id
            and _text(receipt.get("campaign_id")) == campaign_id
            and _text(receipt.get("execution_id")) == execution_id
            and _text(evidence.get("source_step_id")) == source_node_id
        ):
            continue
        try:
            validated = validate_contract_evidence_receipt(receipt)
        except ValueError as exc:
            return (
                [],
                authority,
                recovery,
                f"cleanup_receipt_invalid:{exc}",
            )
        status = _text(validated.get("status")).upper()
        validated_evidence = _dict(validated.get("evidence"))
        if (
            status not in _TERMINAL_CLEANUP_STATUSES
            or _text(validated_evidence.get("schema_version"))
            != GRAPH_CLEANUP_SCHEMA
        ):
            return (
                [],
                authority,
                recovery,
                "cleanup_receipt_terminal_scope_invalid",
            )
        receipt_id = _text(validated.get("receipt_id"))
        if receipt_id and receipt_id not in seen:
            seen.add(receipt_id)
            matches.append(validated)

    matches.sort(key=lambda row: _text(row.get("receipt_id")))
    if matches and rollback_id:
        if not observed_rollback_id:
            return (
                [],
                authority,
                recovery,
                "cleanup_termination_rollback_contract_missing",
            )
        if rollback_id != observed_rollback_id:
            return (
                [],
                authority,
                recovery,
                "cleanup_termination_rollback_contract_mismatch",
            )
    return matches, authority, recovery, ""


def _termination_epoch(
    *,
    step: dict[str, Any],
    observations: dict[str, Any],
    execution_id: str,
    cleanup_receipts: list[dict[str, Any]],
    authority: str,
    recovery: dict[str, Any],
) -> dict[str, Any]:
    wait = _wait_contract(step)
    event = _dict(wait.get("event_transition_contract"))
    graph_id, rollback_id, _observed_graph_id = _graph_scope(
        step=step,
        observations=observations,
    )
    epoch = {
        "schema_version": TERMINATION_EPOCH_SCHEMA,
        "execution_id": execution_id,
        "execution_graph_id": graph_id,
        "source_node_id": _source_node_id(step),
        "target_node_id": _text(step.get("step_id") or step.get("node_id")),
        "wait_id": _text(wait.get("wait_id")),
        "wait_contract_fingerprint": _text(wait.get("contract_fingerprint")),
        "event_contract_fingerprint": _text(event.get("contract_fingerprint")),
        "rollback_contract_fingerprint": rollback_id,
        "cleanup_receipt_authority": authority,
        "recovery_schema_version": _text(recovery.get("schema_version")),
        "recovery_ledger_id": _text(recovery.get("ledger_id")),
        "recovery_ledger_hash": _text(recovery.get("ledger_hash")),
        "recovery_source_step_fact_hash": _text(
            recovery.get("source_step_fact_hash")
        ),
        "cleanup_receipt_ids": [
            _text(row.get("receipt_id")) for row in cleanup_receipts
        ],
        "cleanup_outcomes": [
            {
                "receipt_id": _text(row.get("receipt_id")),
                "status": _text(row.get("status")).upper(),
            }
            for row in cleanup_receipts
        ],
    }
    epoch["contract_fingerprint"] = _wait_authority._fingerprint(epoch)
    return epoch


def _blocked_receipt(
    *,
    step: dict[str, Any],
    observations: dict[str, Any],
    execution_id: str,
    cleanup_receipts: list[dict[str, Any]],
    authority: str,
    recovery: dict[str, Any],
    reason_code: str,
    detail: str,
) -> dict[str, Any]:
    wait = _wait_contract(step)
    event = _dict(wait.get("event_transition_contract"))
    epoch = _termination_epoch(
        step=step,
        observations=observations,
        execution_id=execution_id,
        cleanup_receipts=cleanup_receipts,
        authority=authority,
        recovery=recovery,
    )
    if event:
        receipt = _event_authority._blocked_receipt(
            event,
            reason_code,
            detail=detail,
        )
        receipt["step_id"] = _text(step.get("step_id") or step.get("node_id"))
    else:
        receipt = {
            "schema_version": "qualibug.process-graph-wait-receipt.v1",
            "status": WAIT_BLOCKED,
            "reason_code": reason_code,
            "detail": detail,
            "step_id": _text(step.get("step_id") or step.get("node_id")),
            "wait_id": _text(wait.get("wait_id")),
            "contract_fingerprint": _text(wait.get("contract_fingerprint")),
            "attempt_count": 0,
            "converged": False,
            "timed_out": False,
        }
    receipt.update(
        {
            "request_reached_transport": False,
            "observer_request_reached_transport": False,
            "termination_epoch_authority": authority,
            "termination_epoch_contract_fingerprint": _text(
                epoch.get("contract_fingerprint")
            ),
            "termination_epoch_contract": epoch,
            "termination_cleanup_receipt_ids": list(
                epoch.get("cleanup_receipt_ids") or []
            ),
            "termination_cleanup_outcomes": list(
                epoch.get("cleanup_outcomes") or []
            ),
            "termination_recovery_schema_version": _text(
                recovery.get("schema_version")
            ),
            "termination_recovery_ledger_id": _text(
                recovery.get("ledger_id")
            ),
            "termination_recovery_ledger_hash": _text(
                recovery.get("ledger_hash")
            ),
            "termination_recovery_source_step_fact_hash": _text(
                recovery.get("source_step_fact_hash")
            ),
        }
    )
    prefix = "event_wait_" if event else "wait_"
    receipt["receipt_id"] = prefix + _wait_authority._fingerprint(receipt)[:24]
    return receipt


def resolve_wait_termination_receipt(
    *,
    step: dict[str, Any],
    observations: dict[str, Any],
    experiment_id: str,
    obligation_id: str,
    campaign_id: str,
    execution_id: str,
) -> dict[str, Any]:
    """Return a terminal wait receipt or an empty dict when the branch is live."""
    receipts, authority, recovery, error = _matching_cleanup_receipts(
        step=step,
        observations=observations,
        experiment_id=_text(experiment_id),
        obligation_id=_text(obligation_id),
        campaign_id=_text(campaign_id),
        execution_id=_text(execution_id),
    )
    if error:
        return _blocked_receipt(
            step=step,
            observations=observations,
            execution_id=_text(execution_id),
            cleanup_receipts=[],
            authority=authority or _LIVE_TERMINATION_AUTHORITY,
            recovery=recovery,
            reason_code=WAIT_TERMINATION_RECEIPT_INVALID,
            detail=error,
        )
    if receipts:
        return _blocked_receipt(
            step=step,
            observations=observations,
            execution_id=_text(execution_id),
            cleanup_receipts=receipts,
            authority=authority or _LIVE_TERMINATION_AUTHORITY,
            recovery=recovery,
            reason_code=WAIT_TERMINATION_EPOCH_ACTIVE,
            detail="late_event_reactivation_forbidden",
        )
    return {}


__all__ = [
    "TERMINATION_EPOCH_SCHEMA",
    "WAIT_TERMINATION_EPOCH_ACTIVE",
    "WAIT_TERMINATION_RECEIPT_INVALID",
    "resolve_wait_termination_receipt",
]
