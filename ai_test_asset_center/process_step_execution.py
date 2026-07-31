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
V1.6.2-R1 authority ledger export: qualibug.process-step-ledger.v1
"""
from __future__ import annotations

import hashlib
import json
import time
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Schema versions
STEP_EXECUTION_SCHEMA = "qualibug.process-step-execution.v1"
PROCESS_STEP_LEDGER_SCHEMA = "qualibug.process-step-ledger.v1"
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

# Breakpoint codes (SPEC §35 + V1.6.2-R1 §25)
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
FORMAL_MAINLINE_PROCESS_STEP_LEDGER_NOT_PROPAGATED = (
    "FORMAL_MAINLINE_PROCESS_STEP_LEDGER_NOT_PROPAGATED"
)
FINALIZER_PROCESS_STEP_LEDGER_MISSING = "FINALIZER_PROCESS_STEP_LEDGER_MISSING"
FINALIZER_RECEIPT_BUNDLE_NOT_ACTIVATED = "FINALIZER_RECEIPT_BUNDLE_NOT_ACTIVATED"
PROCESS_STEP_LEDGER_IDENTITY_MISMATCH = "PROCESS_STEP_LEDGER_IDENTITY_MISMATCH"
PROCESS_STEP_LEDGER_HASH_MISMATCH = "PROCESS_STEP_LEDGER_HASH_MISMATCH"
PROCESS_STEP_REQUIRED_SET_MISMATCH = "PROCESS_STEP_REQUIRED_SET_MISMATCH"
PROCESS_STEP_RECEIPT_IDENTITY_MISMATCH = "PROCESS_STEP_RECEIPT_IDENTITY_MISMATCH"
PROCESS_STEP_OBSERVATION_SET_INCOMPLETE = "PROCESS_STEP_OBSERVATION_SET_INCOMPLETE"
PROCESS_STEP_ORACLE_SET_INCOMPLETE = "PROCESS_STEP_ORACLE_SET_INCOMPLETE"
PROCESS_STEP_CLEANUP_SET_INCOMPLETE = "PROCESS_STEP_CLEANUP_SET_INCOMPLETE"


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

    V1.6.2-R1: exposes stable ``ledger_id`` / ``ledger_hash`` and append-only
    receipt reference fields so Finalizer can activate Execution Receipt Bundle
    without copying a second mutable ledger.
    """

    def __init__(
        self,
        experiment_id: str,
        fixture_id: str = "",
        *,
        campaign_id: str = "",
        run_id: str = "",
        obligation_id: str = "",
        protocol_id: str = "",
        required_step_ids: "list[str] | None" = None,
    ):
        self.experiment_id = experiment_id
        self.fixture_id = fixture_id
        self.campaign_id = _text(campaign_id)
        self.run_id = _text(run_id)
        self.obligation_id = _text(obligation_id)
        self.protocol_id = _text(protocol_id)
        self._required_step_ids: list[str] = [
            _text(sid) for sid in list(required_step_ids or []) if _text(sid)
        ]
        self._rows: dict[str, dict[str, Any]] = {}
        self._timeline_events: list[dict[str, Any]] = []
        self._ordinal_counter = 0
        seed = "|".join(
            [
                _text(experiment_id),
                _text(fixture_id),
                self.campaign_id,
                self.run_id,
                self.obligation_id,
                self.protocol_id,
            ]
        )
        self.ledger_id = f"psl_{hashlib.sha256(seed.encode('utf-8')).hexdigest()[:24]}"

    def set_required_step_ids(self, step_ids: "list[str]") -> None:
        """Record compile/plan-required step identities (not forged from responses)."""
        seen: set[str] = set()
        ordered: list[str] = []
        for sid in list(step_ids or []):
            text = _text(sid)
            if text and text not in seen:
                seen.add(text)
                ordered.append(text)
        self._required_step_ids = ordered

    @property
    def required_step_ids(self) -> list[str]:
        return list(self._required_step_ids)

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
        transport_receipt_id: str = "",
        before_state_receipt_id: str = "",
        after_state_receipt_id: str = "",
        observer_receipt_ids: "list[str] | None" = None,
        oracle_receipt_ids: "list[str] | None" = None,
        cleanup_contract_id: str = "",
        cleanup_receipt_ids: "list[str] | None" = None,
        status_code: int = 0,
        final_status: str = "EXECUTED",
        mutation_occurred: bool | None = None,
        target_reached: bool | None = None,
    ) -> dict[str, Any]:
        """Record one authoritative execution row for a step."""
        self._ordinal_counter += 1
        transport_started = time.time()
        observed_status = int(status_code or 0)
        reached = bool(target_reached) if target_reached is not None else observed_status > 0
        row = {
            "schema_version": STEP_EXECUTION_SCHEMA,
            "campaign_id": self.campaign_id,
            "run_id": self.run_id,
            "obligation_id": self.obligation_id,
            "experiment_id": self.experiment_id,
            "fixture_id": self.fixture_id,
            "protocol_id": self.protocol_id,
            "step_id": step_id,
            "step_ordinal": self._ordinal_counter,
            "phase": phase,
            "operation_ref": operation_ref,
            "operation_id": operation_ref,
            "actor_ref": actor_ref,
            "runtime_identity": _dict(runtime_identity),
            "request_receipt_id": request_receipt_id,
            "response_receipt_id": response_receipt_id,
            "transport_receipt_id": transport_receipt_id or request_receipt_id,
            "before_state_receipt_id": before_state_receipt_id,
            "after_state_receipt_id": after_state_receipt_id,
            "observer_receipt_ids": list(observer_receipt_ids or []),
            "observation_receipt_ids": list(observer_receipt_ids or []),
            "oracle_receipt_ids": list(oracle_receipt_ids or []),
            "cleanup_contract_id": cleanup_contract_id,
            "cleanup_receipt_ids": list(cleanup_receipt_ids or []),
            "transport_started": transport_started,
            "target_reached": reached,
            "transport_completed": time.time(),
            "step_completed": final_status == "EXECUTED" and observed_status > 0,
            "step_failed": final_status in {"FAILED", "BLOCKED"} or observed_status >= 400,
            "mutation_occurred": mutation_occurred,
            "status_code": observed_status,
            "final_status": final_status,
            "final_step_status": final_status,
        }
        self._rows[step_id] = row
        return row

    def append_receipt_ref(
        self,
        step_id: str,
        field: str,
        receipt_id: str,
    ) -> bool:
        """Append-only receipt reference on an existing step row (SPEC §10)."""
        rid = _text(receipt_id)
        row = self._rows.get(step_id)
        if not rid or row is None:
            return False
        list_fields = {
            "observer_receipt_ids",
            "observation_receipt_ids",
            "oracle_receipt_ids",
            "cleanup_receipt_ids",
        }
        scalar_fields = {
            "request_receipt_id",
            "response_receipt_id",
            "transport_receipt_id",
            "before_state_receipt_id",
            "after_state_receipt_id",
        }
        if field in list_fields:
            bucket = list(row.get(field) or [])
            if rid not in bucket:
                bucket.append(rid)
                row[field] = bucket
            return True
        if field in scalar_fields:
            existing = _text(row.get(field))
            if existing and existing != rid:
                # Never overwrite an established receipt id; keep first fact.
                return False
            row[field] = rid
            return True
        return False

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

    def recorded_step_ids(self) -> list[str]:
        """All steps with an authoritative terminal/attempt row, including blocks."""
        return list(self._rows.keys())

    def executed_step_ids(self) -> list[str]:
        """Steps that actually reached the target and completed execution.

        A BLOCKED/FAILED row is still an important lifecycle fact, but it is not
        an executed business step and must never satisfy required==executed.
        """
        return [
            step_id
            for step_id, row in self._rows.items()
            if bool(row.get("target_reached"))
            and _text(row.get("final_status")) == "EXECUTED"
            and int(row.get("status_code") or 0) > 0
        ]

    def successful_write_step_ids(self) -> list[str]:
        """Step IDs where a write was successfully executed."""
        return [
            step_id
            for step_id, row in self._rows.items()
            if row.get("final_status") == "EXECUTED"
            and int(row.get("status_code") or 0) < 400
            and bool(row.get("target_reached"))
        ]

    def failed_step_ids(self) -> list[str]:
        return [
            step_id
            for step_id, row in self._rows.items()
            if row.get("final_status") in {"FAILED", "BLOCKED"}
            or int(row.get("status_code") or 0) >= 400
        ]

    def compute_hash(self) -> str:
        """Stable hash over identity + required set + executed rows (no timestamps)."""
        payload = {
            "schema_version": PROCESS_STEP_LEDGER_SCHEMA,
            "ledger_id": self.ledger_id,
            "campaign_id": self.campaign_id,
            "run_id": self.run_id,
            "obligation_id": self.obligation_id,
            "experiment_id": self.experiment_id,
            "fixture_id": self.fixture_id,
            "protocol_id": self.protocol_id,
            "required_step_ids": list(self._required_step_ids),
            "rows": [
                {
                    "step_id": row.get("step_id"),
                    "step_ordinal": row.get("step_ordinal"),
                    "phase": row.get("phase"),
                    "operation_id": row.get("operation_id") or row.get("operation_ref"),
                    "request_receipt_id": row.get("request_receipt_id"),
                    "transport_receipt_id": row.get("transport_receipt_id"),
                    "response_receipt_id": row.get("response_receipt_id"),
                    "observation_receipt_ids": list(row.get("observation_receipt_ids") or []),
                    "oracle_receipt_ids": list(row.get("oracle_receipt_ids") or []),
                    "cleanup_receipt_ids": list(row.get("cleanup_receipt_ids") or []),
                    "final_step_status": row.get("final_step_status") or row.get("final_status"),
                    "target_reached": bool(row.get("target_reached")),
                }
                for row in self.all_rows()
            ],
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @property
    def ledger_hash(self) -> str:
        return self.compute_hash()

    def to_authority_dict(self) -> dict[str, Any]:
        """Export immutable authority snapshot (SPEC §8.1)."""
        return {
            "schema_version": PROCESS_STEP_LEDGER_SCHEMA,
            "process_step_ledger_id": self.ledger_id,
            "ledger_id": self.ledger_id,
            "campaign_id": self.campaign_id,
            "run_id": self.run_id,
            "obligation_id": self.obligation_id,
            "experiment_id": self.experiment_id,
            "fixture_id": self.fixture_id,
            "protocol_id": self.protocol_id,
            "required_step_ids": list(self._required_step_ids),
            "rows": [dict(row) for row in self.all_rows()],
            "ledger_hash": self.compute_hash(),
        }

    def build_timeline_receipt(self) -> dict[str, Any]:
        """Build the formal process timeline receipt."""
        return {
            "schema_version": TIMELINE_SCHEMA,
            "experiment_id": self.experiment_id,
            "events": list(self._timeline_events),
            "event_count": len(self._timeline_events),
        }


def _independent_observation_receipt_ids(row: dict[str, Any]) -> list[str]:
    """Return only receipts that independently observe business state.

    A transport response proves that the target answered. It does not prove that
    the business effect was observed. Older executor code copied the response
    body hash into ``observer_receipt_ids``; exclude that alias at the authority
    boundary so callers cannot turn response==observation into a self-proof.
    """
    response_id = _text(row.get("response_receipt_id"))
    out: list[str] = []
    for rid in (
        _text(row.get("before_state_receipt_id")),
        _text(row.get("after_state_receipt_id")),
    ):
        if rid and rid != response_id and rid not in out:
            out.append(rid)
    for rid in list(row.get("observation_receipt_ids") or []) + list(
        row.get("observer_receipt_ids") or []
    ):
        text = _text(rid)
        if text and text != response_id and text not in out:
            out.append(text)
    return out


def step_ids_with_observation_evidence(ledger: ProcessStepLedger) -> list[str]:
    """Step ids carrying real, independent observation evidence."""
    out: list[str] = []
    for row in ledger.all_rows():
        sid = _text(row.get("step_id"))
        if sid and _independent_observation_receipt_ids(row):
            out.append(sid)
    return out


def step_ids_with_oracle_evidence(ledger: ProcessStepLedger) -> list[str]:
    """Step ids whose ledger row carries at least one real oracle receipt id."""
    out: list[str] = []
    for row in ledger.all_rows():
        sid = _text(row.get("step_id"))
        if not sid:
            continue
        if any(_text(rid) for rid in list(row.get("oracle_receipt_ids") or [])):
            out.append(sid)
    return out


def step_ids_with_cleanup_evidence(ledger: ProcessStepLedger) -> list[str]:
    """Step ids whose ledger row carries at least one real cleanup receipt id."""
    out: list[str] = []
    for row in ledger.all_rows():
        sid = _text(row.get("step_id"))
        if not sid:
            continue
        if any(_text(rid) for rid in list(row.get("cleanup_receipt_ids") or [])):
            out.append(sid)
    return out


def attach_ledger_refs_to_observations(
    observations: dict[str, Any],
    ledger: ProcessStepLedger,
) -> dict[str, Any]:
    """Propagate ledger id/hash + step id sets without copying mutable ledger twice.

    The live ``ProcessStepLedger`` object remains the single source of truth on
    ``observations['process_step_ledger']``. Callers pass id/hash + id lists to
    Finalizer/batch/campaign layers.
    """
    target = observations if isinstance(observations, dict) else {}
    target["process_step_ledger"] = ledger
    target["process_step_ledger_id"] = ledger.ledger_id
    target["process_step_ledger_hash"] = ledger.compute_hash()
    # Required ids are compile/plan authority ONLY. Never forge them from the
    # executed set — that would make required==executed tautologically true
    # and hide missing-step defects from the balance validator.
    required = list(ledger.required_step_ids)
    executed = list(ledger.executed_step_ids())
    target["required_step_ids"] = required
    target["planned_step_ids"] = list(required)
    target["executed_step_ids"] = executed
    target["recorded_step_ids"] = list(ledger.recorded_step_ids())
    target["process_timeline"] = ledger.build_timeline_receipt()
    # Collect append-only receipt id sets from real step rows only.
    transport_ids: list[str] = []
    observation_ids: list[str] = []
    oracle_ids: list[str] = []
    cleanup_ids: list[str] = []
    for row in ledger.all_rows():
        for rid in (
            _text(row.get("transport_receipt_id")),
            _text(row.get("request_receipt_id")),
        ):
            if rid and rid not in transport_ids:
                transport_ids.append(rid)
        for rid in _independent_observation_receipt_ids(row):
            if rid not in observation_ids:
                observation_ids.append(rid)
        for rid in list(row.get("oracle_receipt_ids") or []):
            text = _text(rid)
            if text and text not in oracle_ids:
                oracle_ids.append(text)
        for rid in list(row.get("cleanup_receipt_ids") or []):
            text = _text(rid)
            if text and text not in cleanup_ids:
                cleanup_ids.append(text)
    if transport_ids:
        target["transport_receipt_ids"] = transport_ids
    if observation_ids:
        target["observation_receipt_ids"] = observation_ids
    else:
        target.pop("observation_receipt_ids", None)
    if oracle_ids:
        target["oracle_invocation_receipt_ids"] = oracle_ids
    if cleanup_ids:
        target["cleanup_execution_receipt_ids"] = cleanup_ids
    return target


def validate_required_actual_step_balance(
    *,
    required_step_ids: "list[str]",
    executed_step_ids: "list[str]",
    observed_step_ids: "list[str] | None" = None,
    oracle_step_ids: "list[str] | None" = None,
    cleanup_step_ids: "list[str] | None" = None,
) -> dict[str, Any]:
    """Required/actual step set balance for Finalizer bundle activation (SPEC §13).

    ``observed_step_ids`` and ``oracle_step_ids`` are mandatory evidence sets.
    A caller passing ``None`` means "I do not know the observation/oracle
    evidence" — that must fail closed with an incomplete-evidence reason
    code, never silently default to the executed set (which would make the
    balance tautologically true).

    ``cleanup_step_ids=None`` means cleanup is not part of this balance call.
    Passing an explicit empty list means cleanup was required/checked and no
    step has evidence; it must fail rather than silently bypass the gate.
    """
    required = [_text(s) for s in list(required_step_ids or []) if _text(s)]
    executed = [_text(s) for s in list(executed_step_ids or []) if _text(s)]
    if not required:
        return {
            "balanced": False,
            "reason_code": PROCESS_STEP_REQUIRED_SET_MISMATCH,
            "detail": "required_step_ids_empty",
        }
    if observed_step_ids is None:
        return {
            "balanced": False,
            "reason_code": PROCESS_STEP_OBSERVATION_SET_INCOMPLETE,
            "detail": "observed_step_ids_not_provided",
        }
    if oracle_step_ids is None:
        return {
            "balanced": False,
            "reason_code": PROCESS_STEP_ORACLE_SET_INCOMPLETE,
            "detail": "oracle_step_ids_not_provided",
        }
    observed = [_text(s) for s in list(observed_step_ids) if _text(s)]
    oracle = [_text(s) for s in list(oracle_step_ids) if _text(s)]
    cleanup_provided = cleanup_step_ids is not None
    cleanup = [_text(s) for s in list(cleanup_step_ids or []) if _text(s)]
    req_set = set(required)
    exe_set = set(executed)
    if len(executed) != len(exe_set):
        return {
            "balanced": False,
            "reason_code": PROCESS_STEP_REQUIRED_SET_MISMATCH,
            "detail": "duplicate_executed_step_id",
        }
    if req_set != exe_set:
        return {
            "balanced": False,
            "reason_code": PROCESS_STEP_REQUIRED_SET_MISMATCH,
            "missing_executed": sorted(req_set - exe_set),
            "unexpected_executed": sorted(exe_set - req_set),
        }
    if req_set - set(observed):
        return {
            "balanced": False,
            "reason_code": PROCESS_STEP_OBSERVATION_SET_INCOMPLETE,
            "missing_observed": sorted(req_set - set(observed)),
        }
    if req_set - set(oracle):
        return {
            "balanced": False,
            "reason_code": PROCESS_STEP_ORACLE_SET_INCOMPLETE,
            "missing_oracle": sorted(req_set - set(oracle)),
        }
    if cleanup_provided and (exe_set - set(cleanup)):
        return {
            "balanced": False,
            "reason_code": PROCESS_STEP_CLEANUP_SET_INCOMPLETE,
            "missing_cleanup": sorted(exe_set - set(cleanup)),
        }
    return {
        "balanced": True,
        "reason_code": "",
        "required_step_ids": required,
        "executed_step_ids": executed,
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
    """Verify planned = executed = independently observed for measured steps.

    Caller-provided step ids may narrow the scope but can never widen the
    evidence authority beyond receipt ids already attached to the ledger.
    This closes the historical self-proof where ``executed`` was passed back as
    ``observed`` and accepted without an observation receipt.
    """
    executed = set(ledger.executed_step_ids())
    authoritative_observed = set(step_ids_with_observation_evidence(ledger))
    declared_observed = (
        {_text(s) for s in list(observed_step_ids or []) if _text(s)}
        if observed_step_ids is not None
        else None
    )
    observed = (
        authoritative_observed
        if declared_observed is None
        else authoritative_observed & declared_observed
    )
    authoritative_cleanup = set(step_ids_with_cleanup_evidence(ledger))
    declared_cleanup = (
        {_text(s) for s in list(cleanup_covered_step_ids or []) if _text(s)}
        if cleanup_covered_step_ids is not None
        else None
    )
    cleanup_covered = (
        authoritative_cleanup
        if declared_cleanup is None
        else authoritative_cleanup & declared_cleanup
    )
    planned = {_text(s) for s in list(planned_step_ids or []) if _text(s)}

    missing_execution = sorted(planned - executed)
    missing_observation = sorted(executed - observed)
    missing_cleanup = (
        sorted(executed - cleanup_covered)
        if cleanup_covered_step_ids is not None
        else []
    )

    complete = (
        not missing_execution
        and not missing_observation
        and not missing_cleanup
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
