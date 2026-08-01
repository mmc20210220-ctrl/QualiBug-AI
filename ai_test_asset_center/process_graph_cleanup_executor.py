"""Dependency-aware facade for process-graph cleanup.

The governed compensation implementation remains unchanged in
``process_graph_cleanup_executor_core``. This facade invokes that core one
already-declared cleanup step at a time. The frozen rollback dependency contract
prevents unsafe ancestor compensation after a descendant restoration failure.

No compensator, binding, target or cleanup order is created at runtime.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import process_graph_cleanup_executor_core as _core
from .process_graph_rollback_contract import (
    ROLLBACK_CONTRACT_DRIFT,
    SAFE_ROLLBACK_OUTCOMES,
    validate_process_graph_rollback_contract,
)


GRAPH_CLEANUP_DEPENDENCY_NOT_RESTORED = (
    "PROCESS_GRAPH_CLEANUP_DEPENDENCY_NOT_RESTORED"
)
GRAPH_CLEANUP_SOURCE_WRITE_NOT_REACHED = (
    "PROCESS_GRAPH_SOURCE_WRITE_NOT_REACHED_TRANSPORT"
)
GRAPH_CLEANUP_RECEIPT_CARDINALITY_INVALID = (
    "PROCESS_GRAPH_CLEANUP_RECEIPT_CARDINALITY_INVALID"
)
GRAPH_CLEANUP_SOURCE_WRITE_EFFECT_UNPROVEN = (
    "PROCESS_GRAPH_SOURCE_WRITE_EFFECT_UNPROVEN"
)
GRAPH_CLEANUP_SOURCE_EXECUTION_AMBIGUOUS = (
    "PROCESS_GRAPH_SOURCE_EXECUTION_AMBIGUOUS"
)
GRAPH_CLEANUP_SOURCE_EXECUTION_EVIDENCE_MISSING = (
    "PROCESS_GRAPH_SOURCE_EXECUTION_EVIDENCE_MISSING"
)

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


def _source_rows(
    steps_out: list[dict[str, Any]], source_step_id: str
) -> list[dict[str, Any]]:
    return [
        row
        for row in steps_out
        if isinstance(row, dict)
        and _core._text(row.get("step_id") or row.get("subject_id"))
        == source_step_id
        and _core._text(row.get("phase")) == "treatment"
    ]


def _source_execution_state(
    *,
    steps_out: list[dict[str, Any]],
    observations: dict[str, Any],
    source_step_id: str,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Classify source execution from transport facts, not graph status alone.

    A graph node may be marked BLOCKED after a request reached transport (for
    example when a write response cannot satisfy a declared output binding).
    Such a node is not equivalent to a pre-transport block.  Cleanup may be
    waived only when the step rows prove zero transport.
    """
    rows = _source_rows(steps_out, source_step_id)
    governed = [
        row
        for row in rows
        if isinstance(row.get("governance_receipt"), dict)
    ]
    node_status = _core._text(
        _core._dict(
            _core._dict(observations.get("process_graph_runtime")).get(
                "node_status"
            )
        ).get(source_step_id)
    ).upper()
    status_codes = [int(row.get("status_code") or 0) for row in rows]
    transport_rows = [
        row
        for row in rows
        if int(row.get("status_code") or 0) > 0
        or row.get("request_reached_transport") is True
        or row.get("target_reached") is True
        or bool(_core._text(row.get("transport_receipt_id")))
    ]
    explicit_blocks = [
        row
        for row in rows
        if _core._text(row.get("status")).upper()
        in {"BLOCKED", "BLOCKED_REQUEST", "BLOCKED_WRITE"}
        or _core._text(row.get("reason")).upper().startswith("BLOCKED_")
        or _core._text(row.get("reason_code")).upper().startswith(
            "PROCESS_GRAPH_"
        )
        or _core._text(row.get("skipped_reason")).upper().startswith(
            "BLOCKED_"
        )
    ]
    evidence = {
        "source_execution_state": "",
        "source_node_status": node_status,
        "source_row_count": len(rows),
        "governed_receipt_count": len(governed),
        "source_status_codes": status_codes,
        "request_reached_transport": (
            True
            if transport_rows
            else False
            if rows and explicit_blocks
            else None
        ),
    }
    if len(governed) == 1:
        governance = _core._dict(governed[0].get("governance_receipt"))
        governance_write = _core._dict(governance.get("write"))
        evidence["source_execution_state"] = "GOVERNED"
        evidence["request_reached_transport"] = bool(
            transport_rows
            or int(
                governance_write.get("status")
                or governance_write.get("status_code")
                or 0
            )
            > 0
        )
        return "GOVERNED", dict(governed[0]), evidence
    if len(governed) > 1:
        evidence["source_execution_state"] = "AMBIGUOUS"
        return "AMBIGUOUS", {}, evidence
    if transport_rows:
        evidence["source_execution_state"] = "EFFECT_UNPROVEN"
        return "EFFECT_UNPROVEN", dict(transport_rows[0]), evidence

    zero_transport_proven = bool(rows) and bool(explicit_blocks) and all(
        int(row.get("status_code") or 0) == 0
        and row.get("request_reached_transport") is not True
        and row.get("target_reached") is not True
        and not _core._text(row.get("transport_receipt_id"))
        for row in rows
    )
    if zero_transport_proven:
        evidence["source_execution_state"] = "NOT_REACHED"
        evidence["request_reached_transport"] = False
        return "NOT_REACHED", dict(explicit_blocks[0]), evidence

    evidence["source_execution_state"] = "MISSING"
    return "MISSING", {}, evidence


def _append_scoped_receipt(
    observations: dict[str, Any],
    *,
    source_step_id: str,
    receipt: dict[str, Any],
) -> None:
    receipt_id = _core._text(receipt.get("receipt_id"))
    ledger = observations.get("process_step_ledger")
    if (
        receipt_id
        and ledger is not None
        and hasattr(ledger, "append_scoped_receipt_ref")
    ):
        ledger.append_scoped_receipt_ref(
            step_id=source_step_id,
            field="cleanup_receipt_ids",
            receipt_id=receipt_id,
            receipt_step_id=source_step_id,
        )


def _record_nontransport_timeline(
    observations: dict[str, Any],
    *,
    source_step_id: str,
    receipt: dict[str, Any],
) -> None:
    ledger = observations.get("process_step_ledger")
    if ledger is None or not hasattr(ledger, "record_timeline_event"):
        return
    status = _core._text(receipt.get("status")).upper()
    event_type = {
        "NOT_REQUIRED": "CLEANUP_NOT_REQUIRED",
        "BLOCKED": "CLEANUP_BLOCKED",
        "FAILED": "CLEANUP_FAILED",
    }.get(status)
    if not event_type:
        return
    evidence = _core._dict(receipt.get("evidence"))
    ledger.record_timeline_event(
        step_id=source_step_id,
        phase="cleanup",
        event_type=event_type,
        operation_ref=_core._text(
            evidence.get("cleanup_operation_ref")
        ),
        actor_ref="",
        receipt_id=_core._text(receipt.get("receipt_id")),
    )


def _manual_receipt(
    *,
    cleanup: dict[str, Any],
    source_step_id: str,
    status: str,
    reason_code: str,
    evidence: dict[str, Any],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    return _core._receipt(
        cleanup=cleanup,
        source_step_id=source_step_id,
        status=status,
        reason_code=reason_code,
        evidence=evidence,
        eid=_core._text(kwargs.get("eid")),
        oid=_core._text(kwargs.get("oid")),
        resolved_campaign_id=_core._text(
            kwargs.get("resolved_campaign_id")
        ),
        resolved_execution_id=_core._text(
            kwargs.get("resolved_execution_id")
        ),
    )


def _record_manual_receipt(
    receipt: dict[str, Any],
    *,
    source_step_id: str,
    observations: dict[str, Any],
    receipts: list[dict[str, Any]],
    contract_evidence_receipts: list[dict[str, Any]],
) -> None:
    receipts.append(receipt)
    contract_evidence_receipts.append(receipt)
    _append_scoped_receipt(
        observations,
        source_step_id=source_step_id,
        receipt=receipt,
    )
    _record_nontransport_timeline(
        observations,
        source_step_id=source_step_id,
        receipt=receipt,
    )


def _record_rollback_decision(
    decisions: dict[str, dict[str, Any]],
    *,
    source_step_id: str,
    cleanup: dict[str, Any],
    action: str,
    execution_evidence: dict[str, Any],
    outcome: str = "",
    required_downstream_step_ids: list[str] | None = None,
    unsafe_downstream_outcomes: dict[str, str] | None = None,
) -> None:
    decisions[source_step_id] = {
        "source_step_id": source_step_id,
        "cleanup_step_id": _core._text(cleanup.get("step_id")),
        "cleanup_operation_ref": _core._text(cleanup.get("operation_ref")),
        "system_ref": _core._text(cleanup.get("system_ref")),
        "action": action,
        "outcome": _core._text(outcome).upper(),
        **dict(execution_evidence),
        "required_downstream_step_ids": list(
            required_downstream_step_ids or []
        ),
        "unsafe_downstream_outcomes": dict(
            unsafe_downstream_outcomes or {}
        ),
    }


def _contract_drift_result(
    *,
    kwargs: dict[str, Any],
    cleanup_steps: list[dict[str, Any]],
    detail: str,
) -> dict[str, Any]:
    observations = kwargs["observations"]
    contract_evidence_receipts = kwargs["contract_evidence_receipts"]
    receipts: list[dict[str, Any]] = []
    cleanup_failures = int(kwargs.get("cleanup_failures") or 0)
    outcomes: dict[str, str] = {}
    for cleanup in cleanup_steps:
        source_step_id = _core._text(cleanup.get("source_step_id"))
        receipt = _manual_receipt(
            cleanup=cleanup,
            source_step_id=source_step_id,
            status="FAILED",
            reason_code=ROLLBACK_CONTRACT_DRIFT,
            evidence={
                "effectful_write_count": 0,
                "cleanup_write_count": 0,
                "request_reached_transport": False,
                "detail": detail,
            },
            kwargs=kwargs,
        )
        _record_manual_receipt(
            receipt,
            source_step_id=source_step_id,
            observations=observations,
            receipts=receipts,
            contract_evidence_receipts=contract_evidence_receipts,
        )
        outcomes[source_step_id] = "FAILED"
        cleanup_failures += 1
    observations["process_graph_cleanup_receipts"] = receipts
    observations["process_graph_cleanup_steps"] = []
    observations["cleanup_status"] = "failed"
    observations["process_graph_rollback_outcomes"] = outcomes
    return {
        "steps_out": kwargs["steps_out"],
        "observations": observations,
        "contract_evidence_receipts": contract_evidence_receipts,
        "cleanup_failures": cleanup_failures,
        "process_graph_cleanup_receipts": receipts,
    }


def execute_process_graph_cleanup(**kwargs: Any) -> dict[str, Any]:
    """Execute declared cleanup steps with dependency-aware rollback gating."""
    exp = _core._dict(kwargs.get("exp"))
    write_contract = _core._dict(exp.get("process_graph_write_contract"))
    cleanup_steps = [
        dict(row)
        for row in _core._list(write_contract.get("cleanup_steps"))
        if isinstance(row, dict)
    ]
    if not cleanup_steps:
        return _core.execute_process_graph_cleanup(**kwargs)

    graph = _core._dict(exp.get("execution_graph"))
    rollback_contract = _core._dict(
        exp.get("process_graph_rollback_contract")
        or write_contract.get("rollback_contract")
        or graph.get("rollback_contract")
    )
    valid, validation_error = validate_process_graph_rollback_contract(
        graph,
        write_contract,
        rollback_contract,
    )
    if not valid:
        return _contract_drift_result(
            kwargs=kwargs,
            cleanup_steps=cleanup_steps,
            detail=validation_error or "rollback_contract_validation_failed",
        )

    observations = kwargs["observations"]
    steps_out = kwargs["steps_out"]
    contract_evidence_receipts = kwargs["contract_evidence_receipts"]
    cleanup_failures = int(kwargs.get("cleanup_failures") or 0)
    downstream_by_source = _core._dict(
        rollback_contract.get("downstream_write_step_ids_by_source")
    )
    safe_outcomes = {
        _core._text(value).upper()
        for value in _core._list(
            rollback_contract.get("safe_prerequisite_outcomes")
        )
        if _core._text(value)
    } or set(SAFE_ROLLBACK_OUTCOMES)

    outcomes: dict[str, str] = {}
    decisions: dict[str, dict[str, Any]] = {}
    receipts: list[dict[str, Any]] = []
    cleanup_rows: list[dict[str, Any]] = []

    for cleanup in cleanup_steps:
        source_step_id = _core._text(cleanup.get("source_step_id"))
        execution_state, source, execution_evidence = _source_execution_state(
            steps_out=steps_out,
            observations=observations,
            source_step_id=source_step_id,
        )
        if execution_state == "NOT_REACHED":
            receipt = _manual_receipt(
                cleanup=cleanup,
                source_step_id=source_step_id,
                status="NOT_REQUIRED",
                reason_code=GRAPH_CLEANUP_SOURCE_WRITE_NOT_REACHED,
                evidence={
                    "effectful_write_count": 0,
                    "cleanup_write_count": 0,
                    "request_reached_transport": False,
                    **execution_evidence,
                },
                kwargs=kwargs,
            )
            _record_manual_receipt(
                receipt,
                source_step_id=source_step_id,
                observations=observations,
                receipts=receipts,
                contract_evidence_receipts=contract_evidence_receipts,
            )
            outcomes[source_step_id] = "NOT_REQUIRED"
            _record_rollback_decision(
                decisions,
                source_step_id=source_step_id,
                cleanup=cleanup,
                action="SKIP_NOT_REACHED",
                execution_evidence=execution_evidence,
                outcome="NOT_REQUIRED",
            )
            continue

        if execution_state in {"AMBIGUOUS", "EFFECT_UNPROVEN", "MISSING"}:
            reason_code = {
                "AMBIGUOUS": GRAPH_CLEANUP_SOURCE_EXECUTION_AMBIGUOUS,
                "EFFECT_UNPROVEN": GRAPH_CLEANUP_SOURCE_WRITE_EFFECT_UNPROVEN,
                "MISSING": GRAPH_CLEANUP_SOURCE_EXECUTION_EVIDENCE_MISSING,
            }[execution_state]
            action = {
                "AMBIGUOUS": "FAIL_CLOSED_EXECUTION_AMBIGUOUS",
                "EFFECT_UNPROVEN": "FAIL_CLOSED_EFFECT_UNPROVEN",
                "MISSING": "FAIL_CLOSED_EXECUTION_EVIDENCE_MISSING",
            }[execution_state]
            receipt = _manual_receipt(
                cleanup=cleanup,
                source_step_id=source_step_id,
                status="FAILED",
                reason_code=reason_code,
                evidence={
                    "effectful_write_count": 0,
                    "effectful_write_count_proven": False,
                    "cleanup_write_count": 0,
                    **execution_evidence,
                },
                kwargs=kwargs,
            )
            _record_manual_receipt(
                receipt,
                source_step_id=source_step_id,
                observations=observations,
                receipts=receipts,
                contract_evidence_receipts=contract_evidence_receipts,
            )
            outcomes[source_step_id] = "FAILED"
            cleanup_failures += 1
            _record_rollback_decision(
                decisions,
                source_step_id=source_step_id,
                cleanup=cleanup,
                action=action,
                execution_evidence=execution_evidence,
                outcome="FAILED",
            )
            continue

        effectful_write = _core._cleanup_candidate(source)
        prerequisites: list[str] = []
        if execution_state == "GOVERNED" and effectful_write:
            prerequisites = [
                _core._text(value)
                for value in _core._list(
                    downstream_by_source.get(source_step_id)
                )
                if _core._text(value)
            ]
            unsafe = {
                prerequisite: outcomes.get(prerequisite, "MISSING")
                for prerequisite in prerequisites
                if outcomes.get(prerequisite, "MISSING") not in safe_outcomes
            }
            if unsafe:
                receipt = _manual_receipt(
                    cleanup=cleanup,
                    source_step_id=source_step_id,
                    status="BLOCKED",
                    reason_code=GRAPH_CLEANUP_DEPENDENCY_NOT_RESTORED,
                    evidence={
                        "effectful_write_count": 1,
                        "cleanup_write_count": 0,
                        "request_reached_transport": False,
                        "required_downstream_step_ids": prerequisites,
                        "unsafe_downstream_outcomes": unsafe,
                    },
                    kwargs=kwargs,
                )
                _record_manual_receipt(
                    receipt,
                    source_step_id=source_step_id,
                    observations=observations,
                    receipts=receipts,
                    contract_evidence_receipts=contract_evidence_receipts,
                )
                outcomes[source_step_id] = "BLOCKED"
                cleanup_failures += 1
                _record_rollback_decision(
                    decisions,
                    source_step_id=source_step_id,
                    cleanup=cleanup,
                    action="BLOCK_DEPENDENCY_NOT_RESTORED",
                    execution_evidence=execution_evidence,
                    outcome="BLOCKED",
                    required_downstream_step_ids=prerequisites,
                    unsafe_downstream_outcomes=unsafe,
                )
                continue

        intended_action = (
            "EXECUTE_COMPENSATION"
            if effectful_write
            else "VERIFY_NO_OBSERVED_EFFECT"
        )
        one_exp = deepcopy(exp)
        one_contract = deepcopy(write_contract)
        one_contract["cleanup_steps"] = [deepcopy(cleanup)]
        one_exp["process_graph_write_contract"] = one_contract
        one_exp["cleanup_plan"] = [deepcopy(cleanup)]
        before_step_count = len(steps_out)
        one_result = _core.execute_process_graph_cleanup(
            **{
                **kwargs,
                "exp": one_exp,
                "cleanup_failures": cleanup_failures,
            }
        )
        cleanup_failures = int(one_result.get("cleanup_failures") or 0)
        one_receipts = [
            dict(row)
            for row in _core._list(
                one_result.get("process_graph_cleanup_receipts")
            )
            if isinstance(row, dict)
        ]
        new_cleanup_rows = [
            row
            for row in steps_out[before_step_count:]
            if isinstance(row, dict)
            and _core._text(row.get("phase")) == "cleanup"
        ]
        cleanup_rows.extend(new_cleanup_rows)
        if len(one_receipts) != 1:
            receipts.extend(one_receipts)
            receipt = _manual_receipt(
                cleanup=cleanup,
                source_step_id=source_step_id,
                status="FAILED",
                reason_code=GRAPH_CLEANUP_RECEIPT_CARDINALITY_INVALID,
                evidence={
                    "effectful_write_count": 1
                    if execution_state == "GOVERNED"
                    else 0,
                    "cleanup_write_count": 0,
                    "request_reached_transport": bool(new_cleanup_rows),
                    "returned_receipt_count": len(one_receipts),
                },
                kwargs=kwargs,
            )
            _record_manual_receipt(
                receipt,
                source_step_id=source_step_id,
                observations=observations,
                receipts=receipts,
                contract_evidence_receipts=contract_evidence_receipts,
            )
            outcomes[source_step_id] = "FAILED"
            cleanup_failures += 1
            _record_rollback_decision(
                decisions,
                source_step_id=source_step_id,
                cleanup=cleanup,
                action="FAIL_CLOSED_RECEIPT_CARDINALITY",
                execution_evidence=execution_evidence,
                outcome="FAILED",
                required_downstream_step_ids=prerequisites,
            )
            continue

        receipt = one_receipts[0]
        receipts.append(receipt)
        _append_scoped_receipt(
            observations,
            source_step_id=source_step_id,
            receipt=receipt,
        )
        outcome = _core._text(receipt.get("status")).upper() or "FAILED"
        outcomes[source_step_id] = outcome
        _record_rollback_decision(
            decisions,
            source_step_id=source_step_id,
            cleanup=cleanup,
            action=(
                "SKIP_NO_OBSERVED_EFFECT"
                if outcome == "NOT_REQUIRED" and not effectful_write
                else intended_action
            ),
            execution_evidence=execution_evidence,
            outcome=outcome,
            required_downstream_step_ids=prerequisites,
        )

    observations["process_graph_cleanup_receipts"] = receipts
    observations["process_graph_cleanup_steps"] = cleanup_rows
    observations["process_graph_rollback_outcomes"] = outcomes
    observations["process_graph_rollback_decisions"] = decisions
    observations["process_graph_rollback_contract_id"] = _core._text(
        rollback_contract.get("contract_fingerprint")
    )
    if cleanup_failures:
        observations["cleanup_status"] = "failed"
    elif cleanup_rows:
        observations["cleanup_status"] = "completed"
    else:
        observations["cleanup_status"] = "not_required"
    return {
        "steps_out": steps_out,
        "observations": observations,
        "contract_evidence_receipts": contract_evidence_receipts,
        "cleanup_failures": cleanup_failures,
        "process_graph_cleanup_receipts": receipts,
    }


def finalize_process_graph_cleanup_result(**kwargs: Any) -> dict[str, Any]:
    return _core.finalize_process_graph_cleanup_result(**kwargs)


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__") and name not in {"_core", "_name"}
)
