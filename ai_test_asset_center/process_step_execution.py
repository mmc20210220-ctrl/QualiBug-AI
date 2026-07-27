"""V1.5.0 Multi-Step Process Execution: per-step ledger, timeline, and oracles.

Implements SPEC §21-§26:
- Per-Step Execution Ledger (process_step_execution rows)
- Process Timeline (real-time event recording)
- Per-Step Evidence Completeness Gate
- Sequence Oracle (source-declared order verification)
- Process Completion Oracle

This module is consumed by ``experiment_plan_executor`` during multi-step
execution and by ``experiment_outcome_finalizer`` for TRUE_COMPLETED evaluation.

Schema: qualibug.process-step-execution.v1
"""
from __future__ import annotations

import time
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Schema versions
STEP_EXECUTION_SCHEMA = "qualibug.process-step-execution.v1"
TIMELINE_SCHEMA = "qualibug.process-timeline.v1"
EVIDENCE_SCHEMA = "qualibug.per-step-evidence-completeness.v1"
COMPLETION_ORACLE_SCHEMA = "qualibug.process-completion.v1"
REVERSE_CLEANUP_LEDGER_SCHEMA = "qualibug.reverse-cleanup-ledger.v1"

# Timeline event types (SPEC §23)
EVENT_STEP_READY = "STEP_READY"
EVENT_TRANSPORT_STARTED = "TRANSPORT_STARTED"
EVENT_TRANSPORT_COMPLETED = "TRANSPORT_COMPLETED"
EVENT_AFTER_STATE_OBSERVED = "AFTER_STATE_OBSERVED"
EVENT_STEP_COMPLETED = "STEP_COMPLETED"
EVENT_STEP_FAILED = "STEP_FAILED"
EVENT_CLEANUP_STARTED = "CLEANUP_STARTED"
EVENT_CLEANUP_COMPLETED = "CLEANUP_COMPLETED"

# Sequence Oracle: use process_step_observer.evaluate_step_sequence_order
# (registered as assertion kind 'step_sequence_order' via install_process_step_surface).
# No duplicate implementation here.

# Process Completion results (SPEC §26)
PROCESS_COMPLETED = "PROCESS_COMPLETED"
PROCESS_PARTIALLY_EXECUTED = "PROCESS_PARTIALLY_EXECUTED"
PROCESS_FAILED = "PROCESS_FAILED"
PROCESS_EVIDENCE_INCOMPLETE = "PROCESS_EVIDENCE_INCOMPLETE"

# Breakpoint codes (SPEC §35)
PROCESS_EVIDENCE_INCOMPLETE_CODE = "PROCESS_EVIDENCE_INCOMPLETE"
PROCESS_STEP_NOT_EXECUTED = "PROCESS_STEP_NOT_EXECUTED"
PROCESS_STEP_NOT_OBSERVED = "PROCESS_STEP_NOT_OBSERVED"
PROCESS_TIMELINE_INCOMPLETE = "PROCESS_TIMELINE_INCOMPLETE"
STEP_ORDER_NOT_DECLARED = "STEP_ORDER_NOT_DECLARED"
DECLARED_STEP_NOT_OBSERVED = "DECLARED_STEP_NOT_OBSERVED"
REVERSE_CLEANUP_PLAN_INCOMPLETE = "REVERSE_CLEANUP_PLAN_INCOMPLETE"
REVERSE_CLEANUP_FAILED = "REVERSE_CLEANUP_FAILED"
MULTI_STEP_ENVIRONMENT_NOT_RESTORED = "MULTI_STEP_ENVIRONMENT_NOT_RESTORED"
FALSE_COMPLETED_BLOCKED = "FALSE_COMPLETED_BLOCKED"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


# ═══════════════════════════════════════════════════════════════════════════════
# §21: Per-Step Execution Ledger
# ═══════════════════════════════════════════════════════════════════════════════


class ProcessStepLedger:
    """Accumulates per-step execution rows for a multi-step experiment.

    Each real step produces exactly one authoritative execution row, keyed by
    step_id. The step_id is the identity that threads through compile → execute
    → observe → oracle → cleanup → timeline.
    """

    def __init__(self, experiment_id: str, fixture_id: str = ""):
        self.experiment_id = experiment_id
        self.fixture_id = fixture_id
        self._rows: dict[str, dict[str, Any]] = {}
        self._timeline_events: list[dict[str, Any]] = []
        self._ordinal_counter = 0

    def record_step_execution(
        self,
        *,
        step_id: str,
        phase: str,
        operation_ref: str,
        actor_ref: str,
        runtime_identity: dict[str, Any] | None = None,
        request_receipt_id: str = "",
        response_receipt_id: str = "",
        before_state_receipt_id: str = "",
        after_state_receipt_id: str = "",
        observer_receipt_ids: "list[str] | None" = None,
        cleanup_contract_id: str = "",
        status_code: int = 0,
        final_status: str = "EXECUTED",
    ) -> dict[str, Any]:
        """Record one authoritative execution row for a step."""
        self._ordinal_counter += 1
        row = {
            "schema_version": STEP_EXECUTION_SCHEMA,
            "experiment_id": self.experiment_id,
            "fixture_id": self.fixture_id,
            "step_id": step_id,
            "step_ordinal": self._ordinal_counter,
            "phase": phase,
            "operation_ref": operation_ref,
            "actor_ref": actor_ref,
            "runtime_identity": _dict(runtime_identity),
            "request_receipt_id": request_receipt_id,
            "response_receipt_id": response_receipt_id,
            "before_state_receipt_id": before_state_receipt_id,
            "after_state_receipt_id": after_state_receipt_id,
            "observer_receipt_ids": list(observer_receipt_ids or []),
            "cleanup_contract_id": cleanup_contract_id,
            "transport_started": time.time(),
            "transport_completed": time.time(),
            "status_code": status_code,
            "final_status": final_status,
        }
        self._rows[step_id] = row
        return row

    def record_timeline_event(
        self,
        *,
        step_id: str,
        phase: str,
        event_type: str,
        operation_ref: str = "",
        actor_ref: str = "",
        receipt_id: str = "",
    ) -> dict[str, Any]:
        """Record a process timeline event (SPEC §23)."""
        event = {
            "step_id": step_id,
            "step_ordinal": self._ordinal_counter,
            "phase": phase,
            "event_type": event_type,
            "occurred_at": time.time(),
            "operation_ref": operation_ref,
            "actor_ref": actor_ref,
            "receipt_id": receipt_id,
        }
        self._timeline_events.append(event)
        return event

    def get_step_row(self, step_id: str) -> dict[str, Any] | None:
        return self._rows.get(step_id)

    def all_rows(self) -> list[dict[str, Any]]:
        return list(self._rows.values())

    def timeline(self) -> list[dict[str, Any]]:
        return list(self._timeline_events)

    def executed_step_ids(self) -> list[str]:
        return list(self._rows.keys())

    def successful_write_step_ids(self) -> list[str]:
        """Step IDs where a write was successfully executed."""
        return [
            step_id
            for step_id, row in self._rows.items()
            if row.get("final_status") == "EXECUTED"
            and int(row.get("status_code") or 0) < 400
        ]

    def failed_step_ids(self) -> list[str]:
        return [
            step_id
            for step_id, row in self._rows.items()
            if row.get("final_status") in {"FAILED", "BLOCKED"}
            or int(row.get("status_code") or 0) >= 400
        ]

    def build_timeline_receipt(self) -> dict[str, Any]:
        """Build the formal process timeline receipt."""
        return {
            "schema_version": TIMELINE_SCHEMA,
            "experiment_id": self.experiment_id,
            "events": list(self._timeline_events),
            "event_count": len(self._timeline_events),
        }


# ═══════════════════════════════════════════════════════════════════════════════
# §22: Per-Step Evidence Completeness Gate
# ═══════════════════════════════════════════════════════════════════════════════


def evaluate_per_step_evidence_completeness(
    *,
    planned_step_ids: "list[str]",
    ledger: ProcessStepLedger,
    observed_step_ids: "list[str] | None" = None,
    cleanup_covered_step_ids: "list[str] | None" = None,
) -> dict[str, Any]:
    """Verify planned = executed = observed for all measured steps.

    SPEC §22: Any measured step missing → PROCESS_EVIDENCE_INCOMPLETE.
    """
    executed = set(ledger.executed_step_ids())
    observed = set(observed_step_ids or executed)
    cleanup_covered = set(cleanup_covered_step_ids or [])
    planned = set(planned_step_ids)

    missing_execution = sorted(planned - executed)
    missing_observation = sorted(executed - observed)
    missing_cleanup = sorted(executed - cleanup_covered) if cleanup_covered_step_ids is not None else []

    complete = (
        not missing_execution
        and not missing_observation
    )

    return {
        "schema_version": EVIDENCE_SCHEMA,
        "experiment_id": ledger.experiment_id,
        "planned_step_ids": sorted(planned),
        "executed_step_ids": sorted(executed),
        "observed_step_ids": sorted(observed),
        "cleanup_covered_step_ids": sorted(cleanup_covered),
        "missing_execution": missing_execution,
        "missing_observation": missing_observation,
        "missing_cleanup": missing_cleanup,
        "complete": complete,
        "reason_code": "" if complete else PROCESS_EVIDENCE_INCOMPLETE_CODE,
    }


# NOTE: Sequence Oracle is provided by process_step_observer.evaluate_step_sequence_order
# (assertion kind 'step_sequence_order'). Do NOT re-implement here.


# ═══════════════════════════════════════════════════════════════════════════════
# §26: Process Completion Oracle
# ═══════════════════════════════════════════════════════════════════════════════


def evaluate_process_completion(
    *,
    expected_step_ids: "list[str]",
    ledger: ProcessStepLedger,
    evidence_complete: bool = False,
    experiment_id: str = "",
) -> dict[str, Any]:
    """Judge whether the multi-step process completed.

    SPEC §26: Only judges process completeness, NOT field-level business correctness.

    Results:
    - PROCESS_COMPLETED: all steps executed and evidence complete
    - PROCESS_PARTIALLY_EXECUTED: some steps executed, some not
    - PROCESS_FAILED: a step failed causing downstream skip
    - PROCESS_EVIDENCE_INCOMPLETE: steps executed but evidence gaps
    """
    expected = set(expected_step_ids)
    executed = set(ledger.executed_step_ids())
    completed = {
        sid for sid, row in ledger._rows.items()
        if row.get("final_status") == "EXECUTED" and int(row.get("status_code") or 0) < 400
    }
    failed = set(ledger.failed_step_ids())
    skipped = expected - executed

    if not evidence_complete and executed:
        result = PROCESS_EVIDENCE_INCOMPLETE
    elif expected and executed == expected and not failed:
        result = PROCESS_COMPLETED
    elif failed:
        result = PROCESS_FAILED
    elif executed and skipped:
        result = PROCESS_PARTIALLY_EXECUTED
    elif not executed:
        result = PROCESS_FAILED
    else:
        result = PROCESS_COMPLETED

    return {
        "schema_version": COMPLETION_ORACLE_SCHEMA,
        "experiment_id": experiment_id or ledger.experiment_id,
        "result": result,
        "expected_step_ids": sorted(expected),
        "executed_step_ids": sorted(executed),
        "completed_step_ids": sorted(completed),
        "failed_step_ids": sorted(failed),
        "skipped_step_ids": sorted(skipped),
        "evidence_complete": evidence_complete,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# §29: Reverse Cleanup Runtime Ledger
# ═══════════════════════════════════════════════════════════════════════════════


def build_reverse_cleanup_ledger(
    *,
    experiment_id: str,
    successful_write_step_ids: "list[str]",
    cleanup_results: "list[dict[str, Any]]",
    environment_restoration_receipt_id: str = "",
    final_status: str = "CLEANED",
) -> dict[str, Any]:
    """Build qualibug.reverse-cleanup-ledger.v1 after cleanup execution.

    Verifies: successful_write_step_ids = cleanup_covered_source_step_ids.
    """
    cleanup_covered = {
        _text(r.get("source_step_id"))
        for r in cleanup_results
        if isinstance(r, dict) and _text(r.get("source_step_id"))
    }
    expected_covered = set(successful_write_step_ids)
    uncovered = sorted(expected_covered - cleanup_covered)

    all_verified = (
        not uncovered
        and all(
            _dict(r).get("verified", False)
            for r in cleanup_results
            if isinstance(r, dict)
        )
    )

    return {
        "schema_version": REVERSE_CLEANUP_LEDGER_SCHEMA,
        "experiment_id": experiment_id,
        "successful_write_step_ids": list(successful_write_step_ids),
        "cleanup_order": [
            _text(r.get("cleanup_contract_id"))
            for r in cleanup_results
            if isinstance(r, dict)
        ],
        "cleanup_results": list(cleanup_results),
        "uncovered_steps": uncovered,
        "environment_restoration_receipt_id": environment_restoration_receipt_id,
        "final_status": final_status if all_verified else "CLEANUP_INCOMPLETE",
        "all_writes_covered": not uncovered,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# §31: TRUE_COMPLETED Formula
# ═══════════════════════════════════════════════════════════════════════════════

# TRUE_COMPLETED terminal states (SPEC §31)
TRUE_COMPLETED = "TRUE_COMPLETED"
FIXTURE_BLOCKED = "FIXTURE_BLOCKED"
FIXTURE_PARTIAL = "FIXTURE_PARTIAL"
PRECONDITION_BLOCKED = "PRECONDITION_BLOCKED"
PROCESS_PARTIAL = "PROCESS_PARTIAL"
PROCESS_FAILED_STATE = "PROCESS_FAILED"
EVIDENCE_INCOMPLETE = "EVIDENCE_INCOMPLETE"
CLEANUP_FAILED_STATE = "CLEANUP_FAILED"
ENVIRONMENT_DIRTY = "ENVIRONMENT_DIRTY"
INDETERMINATE = "INDETERMINATE"


def evaluate_true_completed(
    *,
    fixture_materialized: bool,
    state_precondition_established: bool,
    all_required_steps_executed: bool,
    per_step_evidence_complete: bool,
    minimal_oracle_evaluated: bool,
    cleanup_executed: bool,
    cleanup_verified: bool,
    environment_restored: bool,
) -> dict[str, Any]:
    """Compute the TRUE_COMPLETED formula (SPEC §31).

    Only the Finalizer may call this. No caller may directly write COMPLETED
    bypassing this formula.

    TRUE_COMPLETED =
        fixture_materialized
        AND state_precondition_established
        AND all_required_business_steps_executed
        AND per_step_evidence_complete
        AND minimal_oracle_evaluated
        AND cleanup_executed
        AND cleanup_verified
        AND environment_restored
    """
    is_true_completed = (
        fixture_materialized
        and state_precondition_established
        and all_required_steps_executed
        and per_step_evidence_complete
        and minimal_oracle_evaluated
        and cleanup_executed
        and cleanup_verified
        and environment_restored
    )

    # Determine terminal state
    if is_true_completed:
        terminal_state = TRUE_COMPLETED
    elif not fixture_materialized:
        terminal_state = FIXTURE_BLOCKED
    elif not state_precondition_established:
        terminal_state = PRECONDITION_BLOCKED
    elif not all_required_steps_executed:
        terminal_state = PROCESS_PARTIAL
    elif not per_step_evidence_complete:
        terminal_state = EVIDENCE_INCOMPLETE
    elif not cleanup_executed or not cleanup_verified:
        terminal_state = CLEANUP_FAILED_STATE
    elif not environment_restored:
        terminal_state = ENVIRONMENT_DIRTY
    elif not minimal_oracle_evaluated:
        terminal_state = INDETERMINATE
    else:
        terminal_state = INDETERMINATE

    return {
        "true_completed": is_true_completed,
        "terminal_state": terminal_state,
        "formula_inputs": {
            "fixture_materialized": fixture_materialized,
            "state_precondition_established": state_precondition_established,
            "all_required_steps_executed": all_required_steps_executed,
            "per_step_evidence_complete": per_step_evidence_complete,
            "minimal_oracle_evaluated": minimal_oracle_evaluated,
            "cleanup_executed": cleanup_executed,
            "cleanup_verified": cleanup_verified,
            "environment_restored": environment_restored,
        },
    }
