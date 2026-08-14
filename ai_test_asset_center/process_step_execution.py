"""Multi-step process execution authority ledger and lifecycle predicates.

This module is the single per-step fact authority for compile -> execute ->
observe -> oracle -> cleanup. Transport success is deliberately separated from
business effect and target-state proof. Receipt ids appended without an
explicit, matching step scope remain diagnostic only and cannot satisfy the
observation, oracle, or cleanup gates.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

STEP_EXECUTION_SCHEMA = "qualibug.process-step-execution.v1"
PROCESS_STEP_LEDGER_SCHEMA = "qualibug.process-step-ledger.v1"
PROCESS_STEP_RECEIPT_SCHEMA = "qualibug.process-step-receipt.v1"
PROCESS_STEP_FACT_MODEL_VERSION = "v1.1-execution-acceptance-effect-completion"
TIMELINE_SCHEMA = "qualibug.process-timeline.v1"
EVIDENCE_SCHEMA = "qualibug.per-step-evidence-completeness.v1"
COMPLETION_ORACLE_SCHEMA = "qualibug.process-completion.v1"
REVERSE_CLEANUP_LEDGER_SCHEMA = "qualibug.reverse-cleanup-ledger.v1"

EVENT_STEP_READY = "STEP_READY"
EVENT_TRANSPORT_STARTED = "TRANSPORT_STARTED"
EVENT_TRANSPORT_COMPLETED = "TRANSPORT_COMPLETED"
EVENT_AFTER_STATE_OBSERVED = "AFTER_STATE_OBSERVED"
EVENT_STEP_COMPLETED = "STEP_COMPLETED"
EVENT_STEP_FAILED = "STEP_FAILED"
EVENT_CLEANUP_STARTED = "CLEANUP_STARTED"
EVENT_CLEANUP_COMPLETED = "CLEANUP_COMPLETED"

PROCESS_COMPLETED = "PROCESS_COMPLETED"
PROCESS_PARTIALLY_EXECUTED = "PROCESS_PARTIALLY_EXECUTED"
PROCESS_FAILED = "PROCESS_FAILED"
PROCESS_EVIDENCE_INCOMPLETE = "PROCESS_EVIDENCE_INCOMPLETE"

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

SEMANTIC_PENDING_OBSERVATION = "PENDING_OBSERVATION"
SEMANTIC_TARGET_REACHED = "TARGET_REACHED"
SEMANTIC_TARGET_NOT_REACHED = "TARGET_NOT_REACHED"
SEMANTIC_TRANSPORT_FAILED = "TRANSPORT_FAILED"
SEMANTIC_OPERATION_REJECTED = "OPERATION_REJECTED"
SEMANTIC_BLOCKED = "BLOCKED"
SEMANTIC_FAILED = "FAILED"

_EVIDENCE_LIST_FIELDS = frozenset(
    {
        "observer_receipt_ids",
        "observation_receipt_ids",
        "oracle_receipt_ids",
        "cleanup_receipt_ids",
    }
)
_SCALAR_RECEIPT_FIELDS = frozenset(
    {
        "request_receipt_id",
        "response_receipt_id",
        "transport_receipt_id",
        "before_state_receipt_id",
        "after_state_receipt_id",
    }
)
_SCOPED_FIELD_BY_PUBLIC_FIELD = {
    "observer_receipt_ids": "scoped_observation_receipt_ids",
    "observation_receipt_ids": "scoped_observation_receipt_ids",
    "oracle_receipt_ids": "scoped_oracle_receipt_ids",
    "cleanup_receipt_ids": "scoped_cleanup_receipt_ids",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique_texts(values: list[Any] | None) -> list[str]:
    out: list[str] = []
    for value in list(values or []):
        text = _text(value)
        if text and text not in out:
            out.append(text)
    return out


def _stable_hash(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _optional_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _canonical_step_fact(row: dict[str, Any]) -> dict[str, Any]:
    """Return every lifecycle-relevant fact in a deterministic shape."""
    return {
        "step_id": _text(row.get("step_id")),
        "step_ordinal": _safe_int(row.get("step_ordinal")),
        "phase": _text(row.get("phase")),
        "operation_id": _text(row.get("operation_id") or row.get("operation_ref")),
        "actor_ref": _text(row.get("actor_ref")),
        "runtime_identity": dict(_dict(row.get("runtime_identity"))),
        "request_receipt_id": _text(row.get("request_receipt_id")),
        "transport_receipt_id": _text(row.get("transport_receipt_id")),
        "response_receipt_id": _text(row.get("response_receipt_id")),
        "before_state_receipt_id": _text(row.get("before_state_receipt_id")),
        "after_state_receipt_id": _text(row.get("after_state_receipt_id")),
        "scoped_observation_receipt_ids": sorted(
            _unique_texts(list(row.get("scoped_observation_receipt_ids") or []))
        ),
        "scoped_oracle_receipt_ids": sorted(
            _unique_texts(list(row.get("scoped_oracle_receipt_ids") or []))
        ),
        "cleanup_contract_id": _text(row.get("cleanup_contract_id")),
        "scoped_cleanup_receipt_ids": sorted(
            _unique_texts(list(row.get("scoped_cleanup_receipt_ids") or []))
        ),
        "transport_attempted": row.get("transport_attempted") is True,
        "transport_failed": row.get("transport_failed") is True,
        "response_received": row.get("response_received") is True,
        "status_code": _safe_int(row.get("status_code")),
        "final_step_status": _text(
            row.get("final_step_status") or row.get("final_status")
        ).upper(),
        "operation_accepted": _optional_bool(row.get("operation_accepted")),
        "mutation_occurred": _optional_bool(row.get("mutation_occurred")),
        "business_effect_observed": row.get("business_effect_observed") is True,
        "target_state_observed": row.get("target_state_observed") is True,
        "target_reached": _optional_bool(row.get("target_reached")),
        "semantic_verdict_receipt_id": _text(
            row.get("semantic_verdict_receipt_id")
        ),
        "semantic_verdict_source": _text(
            row.get("semantic_verdict_source")
        ).lower(),
        "semantic_step_status": _text(row.get("semantic_step_status")),
        "step_completed": row.get("step_completed") is True,
        "step_failed": row.get("step_failed") is True,
    }


def _operation_accepted(status_code: int, final_status: str) -> bool:
    return final_status == "EXECUTED" and 200 <= status_code < 400


def _semantic_step_status(
    *,
    final_status: str,
    response_received: bool,
    operation_accepted: bool,
    target_state_observed: bool,
    target_reached: bool | None,
) -> str:
    if final_status == "BLOCKED":
        return SEMANTIC_BLOCKED
    if not response_received:
        return SEMANTIC_TRANSPORT_FAILED
    if not operation_accepted:
        return SEMANTIC_OPERATION_REJECTED
    if final_status == "FAILED":
        return SEMANTIC_FAILED
    if not target_state_observed:
        return SEMANTIC_PENDING_OBSERVATION
    return (
        SEMANTIC_TARGET_REACHED
        if target_reached is True
        else SEMANTIC_TARGET_NOT_REACHED
    )


class ProcessStepLedger:
    """Append-only authority ledger keyed by formal ``step_id``.

    Evidence supplied while the step row is created is inherently scoped to
    that step. Evidence discovered later must use
    :meth:`append_scoped_receipt_ref` and repeat the receipt's declared step
    identity. The legacy ``append_receipt_ref`` API remains for scalar transport
    facts only; using it for observation/oracle/cleanup ids is rejected and
    recorded for diagnostics.
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
        self._required_step_ids = _unique_texts(required_step_ids)
        self._rows: dict[str, dict[str, Any]] = {}
        self._timeline_events: list[dict[str, Any]] = []
        self._receipt_scope_rejections: list[dict[str, str]] = []
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
        digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:24]
        self.ledger_id = f"psl_{digest}"

    def set_required_step_ids(self, step_ids: "list[str]") -> None:
        self._required_step_ids = _unique_texts(step_ids)

    @property
    def required_step_ids(self) -> list[str]:
        return list(self._required_step_ids)

    @property
    def receipt_scope_rejections(self) -> list[dict[str, str]]:
        return [dict(row) for row in self._receipt_scope_rejections]

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
        operation_accepted: bool | None = None,
        business_effect_observed: bool | None = None,
        target_reached: bool | None = None,
    ) -> dict[str, Any]:
        """Record one terminal attempt without inventing business-state facts.

        ``target_reached=None`` means unknown. A target state is considered
        observed only when an explicit verdict and independent observation
        evidence are both supplied.
        """
        normalized_step_id = _text(step_id)
        if not normalized_step_id:
            raise ValueError("step_id_required")

        self._ordinal_counter += 1
        now = time.time()
        observed_status = int(status_code or 0)
        normalized_final = _text(final_status).upper() or "EXECUTED"
        observation_ids = _unique_texts(observer_receipt_ids)
        oracle_ids = _unique_texts(oracle_receipt_ids)
        cleanup_ids = _unique_texts(cleanup_receipt_ids)
        response_id = _text(response_receipt_id)
        independent_state_receipts = [
            rid
            for rid in _unique_texts(
                [
                    before_state_receipt_id,
                    after_state_receipt_id,
                    *observation_ids,
                ]
            )
            if rid != response_id
        ]
        response_received = observed_status > 0 or bool(response_id)
        transport_attempted = bool(
            _text(request_receipt_id)
            or _text(transport_receipt_id)
            or response_received
        )
        accepted = (
            operation_accepted
            if isinstance(operation_accepted, bool)
            else _operation_accepted(observed_status, normalized_final)
        )
        target_state_observed = (
            target_reached is not None and bool(independent_state_receipts)
        )
        effect_observed = (
            bool(business_effect_observed)
            if isinstance(business_effect_observed, bool)
            and bool(independent_state_receipts)
            else target_state_observed
        )
        reached: bool | None = (
            bool(target_reached) if target_state_observed else None
        )
        semantic_status = _semantic_step_status(
            final_status=normalized_final,
            response_received=response_received,
            operation_accepted=accepted,
            target_state_observed=target_state_observed,
            target_reached=reached,
        )
        step_completed = semantic_status == SEMANTIC_TARGET_REACHED
        step_failed = semantic_status in {
            SEMANTIC_TARGET_NOT_REACHED,
            SEMANTIC_TRANSPORT_FAILED,
            SEMANTIC_OPERATION_REJECTED,
            SEMANTIC_BLOCKED,
            SEMANTIC_FAILED,
        }
        row = {
            "schema_version": STEP_EXECUTION_SCHEMA,
            "campaign_id": self.campaign_id,
            "run_id": self.run_id,
            "obligation_id": self.obligation_id,
            "experiment_id": self.experiment_id,
            "fixture_id": self.fixture_id,
            "protocol_id": self.protocol_id,
            "step_id": normalized_step_id,
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
            "observer_receipt_ids": list(observation_ids),
            "observation_receipt_ids": list(observation_ids),
            "oracle_receipt_ids": list(oracle_ids),
            "cleanup_receipt_ids": list(cleanup_ids),
            "scoped_observation_receipt_ids": list(observation_ids),
            "scoped_oracle_receipt_ids": list(oracle_ids),
            "scoped_cleanup_receipt_ids": list(cleanup_ids),
            "cleanup_contract_id": cleanup_contract_id,
            "transport_attempted": transport_attempted,
            "transport_started": now if transport_attempted else None,
            "transport_completed": now if response_received else None,
            "transport_failed": bool(transport_attempted and not response_received),
            "response_received": response_received,
            "operation_accepted": accepted,
            "business_effect_observed": effect_observed,
            "target_state_observed": target_state_observed,
            "target_reached": reached,
            "semantic_step_status": semantic_status,
            "step_completed": step_completed,
            "step_failed": step_failed,
            "mutation_occurred": mutation_occurred,
            "status_code": observed_status,
            "final_status": normalized_final,
            "final_step_status": normalized_final,
        }
        self._rows[normalized_step_id] = row
        return row

    def append_receipt_ref(
        self,
        step_id: str,
        field: str,
        receipt_id: str,
    ) -> bool:
        """Append scalar transport/state facts; reject unscoped evidence ids."""
        normalized_step_id = _text(step_id)
        rid = _text(receipt_id)
        row = self._rows.get(normalized_step_id)
        if not rid or row is None:
            return False
        if field in _EVIDENCE_LIST_FIELDS:
            self._receipt_scope_rejections.append(
                {
                    "step_id": normalized_step_id,
                    "field": field,
                    "receipt_id": rid,
                    "reason_code": PROCESS_STEP_RECEIPT_IDENTITY_MISMATCH,
                }
            )
            return False
        if field not in _SCALAR_RECEIPT_FIELDS:
            return False
        existing = _text(row.get(field))
        if existing and existing != rid:
            return False
        row[field] = rid
        return True

    def append_scoped_receipt_ref(
        self,
        *,
        step_id: str,
        field: str,
        receipt_id: str,
        receipt_step_id: str,
    ) -> bool:
        """Append a late evidence receipt only when its declared scope matches."""
        normalized_step_id = _text(step_id)
        declared_step_id = _text(receipt_step_id)
        rid = _text(receipt_id)
        row = self._rows.get(normalized_step_id)
        scoped_field = _SCOPED_FIELD_BY_PUBLIC_FIELD.get(field)
        if (
            row is None
            or not rid
            or not scoped_field
            or not declared_step_id
            or declared_step_id != normalized_step_id
        ):
            self._receipt_scope_rejections.append(
                {
                    "step_id": normalized_step_id,
                    "receipt_step_id": declared_step_id,
                    "field": field,
                    "receipt_id": rid,
                    "reason_code": PROCESS_STEP_RECEIPT_IDENTITY_MISMATCH,
                }
            )
            return False

        scoped = _unique_texts(list(row.get(scoped_field) or []) + [rid])
        row[scoped_field] = scoped
        public = _unique_texts(list(row.get(field) or []) + [rid])
        row[field] = public
        if field in {"observer_receipt_ids", "observation_receipt_ids"}:
            row["observer_receipt_ids"] = _unique_texts(
                list(row.get("observer_receipt_ids") or []) + [rid]
            )
            row["observation_receipt_ids"] = _unique_texts(
                list(row.get("observation_receipt_ids") or []) + [rid]
            )
        return True

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

    def timeline(self) -> list[dict[str, Any]]:
        """Return recorded timeline events in insertion order."""
        return list(self._timeline_events)

    def get_step_row(self, step_id: str) -> dict[str, Any] | None:
        return self._rows.get(step_id)

    def _ordered_raw_rows(self) -> list[dict[str, Any]]:
        return sorted(
            self._rows.values(),
            key=lambda row: (
                _safe_int(row.get("step_ordinal")),
                _text(row.get("step_id")),
            ),
        )

    def build_fact_snapshot(self) -> dict[str, Any]:
        rows = [_canonical_step_fact(row) for row in self._ordered_raw_rows()]
        rejections = sorted(
            self.receipt_scope_rejections,
            key=lambda row: (
                _text(row.get("step_id")),
                _text(row.get("field")),
                _text(row.get("receipt_id")),
                _text(row.get("reason_code")),
            ),
        )
        return {
            "schema_version": PROCESS_STEP_LEDGER_SCHEMA,
            "fact_model_version": PROCESS_STEP_FACT_MODEL_VERSION,
            "ledger_id": self.ledger_id,
            "campaign_id": self.campaign_id,
            "run_id": self.run_id,
            "obligation_id": self.obligation_id,
            "experiment_id": self.experiment_id,
            "fixture_id": self.fixture_id,
            "protocol_id": self.protocol_id,
            "required_step_ids": list(self._required_step_ids),
            "attempted_step_ids": list(self.attempted_step_ids()),
            "executed_step_ids": list(self.executed_step_ids()),
            "accepted_step_ids": list(self.accepted_step_ids()),
            "completed_step_ids": list(self.completed_step_ids()),
            "failed_step_ids": list(self.failed_step_ids()),
            "rows": rows,
            "receipt_scope_rejections": rejections,
        }

    def step_fact_hash(self, step_id: str) -> str:
        row = self._rows.get(_text(step_id))
        if row is None:
            return ""
        return _stable_hash(_canonical_step_fact(row))

    def compute_hash(self) -> str:
        return _stable_hash(self.build_fact_snapshot())

    @property
    def ledger_hash(self) -> str:
        return self.compute_hash()

    def all_rows(self) -> list[dict[str, Any]]:
        """Return immutable receipt-ready snapshots bound to the current ledger."""
        ledger_hash = self.compute_hash()
        required = set(self._required_step_ids)
        out: list[dict[str, Any]] = []
        for row in self._ordered_raw_rows():
            step_id = _text(row.get("step_id"))
            receipt_id = "psr_" + hashlib.sha256(
                f"{self.ledger_id}|{step_id}".encode("utf-8")
            ).hexdigest()[:24]
            out.append(
                {
                    **dict(row),
                    "receipt_schema_version": PROCESS_STEP_RECEIPT_SCHEMA,
                    "receipt_id": receipt_id,
                    "process_step_ledger_id": self.ledger_id,
                    "process_step_ledger_hash": ledger_hash,
                    "fact_model_version": PROCESS_STEP_FACT_MODEL_VERSION,
                    "step_fact_hash": self.step_fact_hash(step_id),
                    "required_step": step_id in required,
                }
            )
        return out

    def recorded_step_ids(self) -> list[str]:
        return list(self._rows.keys())

    def attempted_step_ids(self) -> list[str]:
        return [
            sid
            for sid, row in self._rows.items()
            if row.get("transport_attempted") is True
        ]

    def executed_step_ids(self) -> list[str]:
        """Steps that received a real target response.

        Transport-level by contract: a 4xx/5xx response is still a real
        execution attempt and must remain available to Oracle,
        evidence-completeness, compensation, and bug-discovery accounting.
        Acceptance, business-state proof, and semantic completion are separate
        facts (``accepted_step_ids`` / ``target_state_observed`` /
        ``completed_step_ids``); a failed prior write is listed under
        ``failed_step_ids`` and ``attempted_step_ids``, never silently dropped.
        Fixture/binding materialization requests are timeline events, never
        ledger rows, so they can never enter this set in the formal mainline.
        """
        return [
            sid
            for sid, row in self._rows.items()
            if row.get("response_received") is True
            and _text(row.get("final_status")).upper() == "EXECUTED"
        ]

    def completed_step_ids(self) -> list[str]:
        return [
            sid
            for sid, row in self._rows.items()
            if row.get("semantic_step_status") == SEMANTIC_TARGET_REACHED
        ]

    def accepted_step_ids(self) -> list[str]:
        return [
            sid
            for sid, row in self._rows.items()
            if row.get("operation_accepted") is True
        ]

    def successful_write_step_ids(self) -> list[str]:
        return [
            sid
            for sid, row in self._rows.items()
            if row.get("operation_accepted") is True
            and row.get("mutation_occurred") is not False
        ]

    def failed_step_ids(self) -> list[str]:
        return [
            sid
            for sid, row in self._rows.items()
            if bool(row.get("step_failed"))
        ]

    def to_authority_dict(self) -> dict[str, Any]:
        fact_snapshot = self.build_fact_snapshot()
        return {
            "schema_version": PROCESS_STEP_LEDGER_SCHEMA,
            "fact_model_version": PROCESS_STEP_FACT_MODEL_VERSION,
            "process_step_ledger_id": self.ledger_id,
            "ledger_id": self.ledger_id,
            "campaign_id": self.campaign_id,
            "run_id": self.run_id,
            "obligation_id": self.obligation_id,
            "experiment_id": self.experiment_id,
            "fixture_id": self.fixture_id,
            "protocol_id": self.protocol_id,
            "required_step_ids": list(self._required_step_ids),
            "attempted_step_ids": list(self.attempted_step_ids()),
            "executed_step_ids": list(self.executed_step_ids()),
            "accepted_step_ids": list(self.accepted_step_ids()),
            "completed_step_ids": list(self.completed_step_ids()),
            "failed_step_ids": list(self.failed_step_ids()),
            "rows": self.all_rows(),
            "receipt_scope_rejections": self.receipt_scope_rejections,
            "fact_snapshot": fact_snapshot,
            "ledger_hash": _stable_hash(fact_snapshot),
        }

    def build_timeline_receipt(self) -> dict[str, Any]:
        return {
            "schema_version": TIMELINE_SCHEMA,
            "experiment_id": self.experiment_id,
            "events": list(self._timeline_events),
            "event_count": len(self._timeline_events),
        }


def _independent_observation_receipt_ids(row: dict[str, Any]) -> list[str]:
    response_id = _text(row.get("response_receipt_id"))
    out = _unique_texts(
        [
            row.get("before_state_receipt_id"),
            row.get("after_state_receipt_id"),
            *list(row.get("scoped_observation_receipt_ids") or []),
        ]
    )
    return [rid for rid in out if rid != response_id]


def step_ids_with_observation_evidence(
    ledger: ProcessStepLedger,
) -> list[str]:
    """Steps with INDEPENDENT observation evidence.

    The response body fingerprint is transport evidence, never business
    observation: a step cannot observe itself (a response receipt or an
    observer receipt that merely repeats the response id is self-authorization
    and is excluded). The formal mainline supplies independent evidence through
    the governance before/after state receipts and typed observer receipts
    attached via the exact-scoped sync.
    """
    return [
        _text(row.get("step_id"))
        for row in ledger.all_rows()
        if _text(row.get("step_id"))
        and _independent_observation_receipt_ids(row)
    ]


def step_ids_with_oracle_evidence(ledger: ProcessStepLedger) -> list[str]:
    return [
        _text(row.get("step_id"))
        for row in ledger.all_rows()
        if _text(row.get("step_id"))
        and _unique_texts(list(row.get("scoped_oracle_receipt_ids") or []))
    ]


def step_ids_with_cleanup_evidence(ledger: ProcessStepLedger) -> list[str]:
    return [
        _text(row.get("step_id"))
        for row in ledger.all_rows()
        if _text(row.get("step_id"))
        and _unique_texts(list(row.get("scoped_cleanup_receipt_ids") or []))
    ]


def attach_ledger_refs_to_observations(
    observations: dict[str, Any],
    ledger: ProcessStepLedger,
) -> dict[str, Any]:
    target = observations if isinstance(observations, dict) else {}
    target["process_step_ledger"] = ledger
    target["process_step_ledger_id"] = ledger.ledger_id
    target["process_step_ledger_hash"] = ledger.compute_hash()
    required = list(ledger.required_step_ids)
    target["required_step_ids"] = required
    target["planned_step_ids"] = list(required)
    target["attempted_step_ids"] = list(ledger.attempted_step_ids())
    target["executed_step_ids"] = list(ledger.executed_step_ids())
    target["accepted_step_ids"] = list(ledger.accepted_step_ids())
    target["completed_step_ids"] = list(ledger.completed_step_ids())
    target["recorded_step_ids"] = list(ledger.recorded_step_ids())
    target["process_step_receipts"] = list(ledger.all_rows())
    target["process_step_fact_model_version"] = PROCESS_STEP_FACT_MODEL_VERSION
    target["process_timeline"] = ledger.build_timeline_receipt()
    target["process_step_receipt_scope_rejections"] = (
        ledger.receipt_scope_rejections
    )

    transport_ids: list[str] = []
    observation_ids: list[str] = []
    oracle_ids: list[str] = []
    cleanup_ids: list[str] = []
    for row in ledger.all_rows():
        transport_ids = _unique_texts(
            transport_ids
            + [
                row.get("transport_receipt_id"),
                row.get("request_receipt_id"),
            ]
        )
        observation_ids = _unique_texts(
            observation_ids + _independent_observation_receipt_ids(row)
        )
        oracle_ids = _unique_texts(
            oracle_ids + list(row.get("scoped_oracle_receipt_ids") or [])
        )
        cleanup_ids = _unique_texts(
            cleanup_ids + list(row.get("scoped_cleanup_receipt_ids") or [])
        )

    for key, values in (
        ("transport_receipt_ids", transport_ids),
        ("observation_receipt_ids", observation_ids),
        ("oracle_invocation_receipt_ids", oracle_ids),
        ("cleanup_execution_receipt_ids", cleanup_ids),
    ):
        if values:
            target[key] = values
        else:
            target.pop(key, None)
    return target


def validate_required_actual_step_balance(
    *,
    required_step_ids: "list[str]",
    executed_step_ids: "list[str]",
    observed_step_ids: "list[str] | None" = None,
    oracle_step_ids: "list[str] | None" = None,
    cleanup_step_ids: "list[str] | None" = None,
) -> dict[str, Any]:
    required = _unique_texts(required_step_ids)
    raw_executed = [
        _text(step_id)
        for step_id in list(executed_step_ids or [])
        if _text(step_id)
    ]
    executed = _unique_texts(executed_step_ids)
    if not required:
        return {
            "balanced": False,
            "reason_code": PROCESS_STEP_REQUIRED_SET_MISMATCH,
            "detail": "required_step_ids_empty",
        }
    if len(raw_executed) != len(executed):
        return {
            "balanced": False,
            "reason_code": PROCESS_STEP_REQUIRED_SET_MISMATCH,
            "detail": "duplicate_executed_step_id",
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

    required_set = set(required)
    executed_set = set(executed)
    if required_set != executed_set:
        return {
            "balanced": False,
            "reason_code": PROCESS_STEP_REQUIRED_SET_MISMATCH,
            "missing_executed": sorted(required_set - executed_set),
            "unexpected_executed": sorted(executed_set - required_set),
        }

    observed = set(_unique_texts(observed_step_ids))
    oracle = set(_unique_texts(oracle_step_ids))
    if required_set - observed:
        return {
            "balanced": False,
            "reason_code": PROCESS_STEP_OBSERVATION_SET_INCOMPLETE,
            "missing_observed": sorted(required_set - observed),
        }
    if required_set - oracle:
        return {
            "balanced": False,
            "reason_code": PROCESS_STEP_ORACLE_SET_INCOMPLETE,
            "missing_oracle": sorted(required_set - oracle),
        }
    # An explicitly provided cleanup set is asserted: None means the gate was
    # not requested, an empty list means cleanup was checked and nothing
    # satisfies it (fail closed).
    if cleanup_step_ids is not None:
        cleanup = set(_unique_texts(cleanup_step_ids))
        if executed_set - cleanup:
            return {
                "balanced": False,
                "reason_code": PROCESS_STEP_CLEANUP_SET_INCOMPLETE,
                "missing_cleanup": sorted(executed_set - cleanup),
            }
    return {
        "balanced": True,
        "reason_code": "",
        "required_step_ids": required,
        "executed_step_ids": executed,
    }


def evaluate_per_step_evidence_completeness(
    *,
    planned_step_ids: "list[str]",
    ledger: ProcessStepLedger,
    observed_step_ids: "list[str] | None" = None,
    cleanup_covered_step_ids: "list[str] | None" = None,
) -> dict[str, Any]:
    planned = set(_unique_texts(planned_step_ids))
    executed = set(ledger.executed_step_ids())
    authoritative_observed = set(step_ids_with_observation_evidence(ledger))
    observed = (
        authoritative_observed
        if observed_step_ids is None
        else authoritative_observed & set(_unique_texts(observed_step_ids))
    )
    authoritative_cleanup = set(step_ids_with_cleanup_evidence(ledger))
    cleanup = (
        authoritative_cleanup
        if cleanup_covered_step_ids is None
        else authoritative_cleanup & set(_unique_texts(cleanup_covered_step_ids))
    )
    missing_execution = sorted(planned - executed)
    missing_observation = sorted(executed - observed)
    missing_cleanup = (
        sorted(executed - cleanup)
        if cleanup_covered_step_ids is not None
        else []
    )
    complete = not missing_execution and not missing_observation and not missing_cleanup
    return {
        "schema_version": EVIDENCE_SCHEMA,
        "experiment_id": ledger.experiment_id,
        "planned_step_ids": sorted(planned),
        "executed_step_ids": sorted(executed),
        "observed_step_ids": sorted(observed),
        "cleanup_covered_step_ids": sorted(cleanup),
        "missing_execution": missing_execution,
        "missing_observation": missing_observation,
        "missing_cleanup": missing_cleanup,
        "complete": complete,
        "reason_code": "" if complete else PROCESS_EVIDENCE_INCOMPLETE_CODE,
    }


def evaluate_process_completion(
    *,
    expected_step_ids: "list[str]",
    ledger: ProcessStepLedger,
    evidence_complete: bool = False,
    experiment_id: str = "",
) -> dict[str, Any]:
    expected = set(_unique_texts(expected_step_ids))
    executed = set(ledger.executed_step_ids())
    completed = set(ledger.completed_step_ids())
    failed = set(ledger.failed_step_ids())
    skipped = expected - executed
    if failed:
        result = PROCESS_FAILED
    elif expected and completed == expected and evidence_complete:
        result = PROCESS_COMPLETED
    elif executed and not evidence_complete:
        result = PROCESS_EVIDENCE_INCOMPLETE
    elif executed and (skipped or completed != expected):
        result = PROCESS_PARTIALLY_EXECUTED
    else:
        result = PROCESS_FAILED
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


def build_reverse_cleanup_ledger(
    *,
    experiment_id: str,
    successful_write_step_ids: "list[str]",
    cleanup_results: "list[dict[str, Any]]",
    environment_restoration_receipt_id: str = "",
    final_status: str = "CLEANED",
) -> dict[str, Any]:
    cleanup_covered = {
        _text(result.get("source_step_id"))
        for result in cleanup_results
        if isinstance(result, dict) and _text(result.get("source_step_id"))
    }
    expected = set(_unique_texts(successful_write_step_ids))
    uncovered = sorted(expected - cleanup_covered)
    all_verified = (
        not uncovered
        and all(
            _dict(result).get("verified", False)
            for result in cleanup_results
            if isinstance(result, dict)
        )
    )
    return {
        "schema_version": REVERSE_CLEANUP_LEDGER_SCHEMA,
        "experiment_id": experiment_id,
        "successful_write_step_ids": _unique_texts(successful_write_step_ids),
        "cleanup_order": [
            _text(result.get("cleanup_contract_id"))
            for result in cleanup_results
            if isinstance(result, dict)
        ],
        "cleanup_results": list(cleanup_results),
        "uncovered_steps": uncovered,
        "environment_restoration_receipt_id": environment_restoration_receipt_id,
        "final_status": final_status if all_verified else "CLEANUP_INCOMPLETE",
        "all_writes_covered": not uncovered,
    }


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
    inputs = {
        "fixture_materialized": fixture_materialized,
        "state_precondition_established": state_precondition_established,
        "all_required_steps_executed": all_required_steps_executed,
        "per_step_evidence_complete": per_step_evidence_complete,
        "minimal_oracle_evaluated": minimal_oracle_evaluated,
        "cleanup_executed": cleanup_executed,
        "cleanup_verified": cleanup_verified,
        "environment_restored": environment_restored,
    }
    completed = all(inputs.values())
    if completed:
        terminal = TRUE_COMPLETED
    elif not fixture_materialized:
        terminal = FIXTURE_BLOCKED
    elif not state_precondition_established:
        terminal = PRECONDITION_BLOCKED
    elif not all_required_steps_executed:
        terminal = PROCESS_PARTIAL
    elif not per_step_evidence_complete:
        terminal = EVIDENCE_INCOMPLETE
    elif not minimal_oracle_evaluated:
        terminal = INDETERMINATE
    elif not cleanup_executed or not cleanup_verified:
        terminal = CLEANUP_FAILED_STATE
    elif not environment_restored:
        terminal = ENVIRONMENT_DIRTY
    else:
        terminal = INDETERMINATE
    return {
        "true_completed": completed,
        "terminal_state": terminal,
        "formula_inputs": inputs,
    }
