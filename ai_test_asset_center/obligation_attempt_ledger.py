"""Authoritative terminal accounting for selected discovery obligations."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Mapping


OBLIGATION_ATTEMPT_LEDGER_SCHEMA = "qualibug.obligation-attempt-ledger.v1"
TERMINAL_STATUSES = frozenset({
    "DELIVERABLE",
    "REJECTED",
    "BLOCKED",
    "DEFERRED",
    "HARNESS_FAILED",
})
_STAGE_STATUSES = {
    "compile": frozenset({"COMPILED", "BLOCKED", "DEFERRED", "HARNESS_FAILED"}),
    "execution": frozenset({"EXECUTED", "BLOCKED", "DEFERRED", "HARNESS_FAILED"}),
    "gate": frozenset(TERMINAL_STATUSES),
}
_COST_COVERAGE_STATUSES = frozenset({"MEASURED", "PARTIAL", "UNKNOWN"})


class ObligationAttemptLedgerError(ValueError):
    """Attempt receipts disagree or cannot prove terminal coverage."""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _object(value: Any, *, field: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ObligationAttemptLedgerError(f"{field}_not_object")
    return dict(value)


def _fingerprint(value: Any) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _receipt_map(value: Any, *, field: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise ObligationAttemptLedgerError(f"{field}_not_object")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_key, raw_receipt in value.items():
        obligation_id = _text(raw_key)
        if not obligation_id:
            raise ObligationAttemptLedgerError(f"{field}_obligation_id_missing")
        normalized[obligation_id] = _object(raw_receipt, field=f"{field}:{obligation_id}")
    return normalized


def _stage_record(stage: str, receipt: dict[str, Any]) -> dict[str, Any] | None:
    if not receipt:
        return None
    status = _text(receipt.get("status")).upper()
    if status not in _STAGE_STATUSES[stage]:
        raise ObligationAttemptLedgerError(
            f"{stage}_status_invalid:{status or 'MISSING'}"
        )
    receipt_id = _text(
        receipt.get(f"{stage}_receipt_id")
        or receipt.get("receipt_id")
        or (receipt.get("gate_receipt_id") if stage == "gate" else "")
    )
    elapsed = receipt.get("elapsed_ms")
    if elapsed is not None:
        try:
            elapsed = max(0, int(elapsed))
        except (TypeError, ValueError) as exc:
            raise ObligationAttemptLedgerError(
                f"{stage}_elapsed_ms_invalid"
            ) from exc
    return {
        "stage": stage,
        "status": status,
        "reason_code": _text(receipt.get("reason_code")),
        "receipt_id": receipt_id,
        "input_fingerprint": _text(receipt.get("input_fingerprint")),
        "output_fingerprint": _text(receipt.get("output_fingerprint")),
        "elapsed_ms": elapsed,
    }


def _selected_rows(selected: Any) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if not isinstance(selected, list):
        raise ObligationAttemptLedgerError("selected_obligations_not_list")
    ids: list[str] = []
    rows: dict[str, dict[str, Any]] = {}
    for value in selected:
        row = _object(value, field="selected_obligation")
        obligation_id = _text(row.get("obligation_id"))
        ids.append(obligation_id)
        if obligation_id:
            rows[obligation_id] = row
    if not all(ids) or len(ids) != len(set(ids)):
        raise ObligationAttemptLedgerError("selected_obligation_identity_invalid")
    return ids, rows


def build_obligation_attempt_ledger(
    *,
    mainline_run: dict[str, Any],
    selected: list[dict[str, Any]],
    compile_results: dict[str, Any],
    execution_results: dict[str, Any],
    gate_results: dict[str, Any],
) -> dict[str, Any]:
    """Join stage receipts without copying raw request or response payloads."""

    run = _object(mainline_run, field="mainline_run")
    run_id = _text(run.get("run_id"))
    campaign_id = _text(run.get("campaign_id"))
    if not run_id:
        raise ObligationAttemptLedgerError("run_id_missing")
    if not campaign_id:
        raise ObligationAttemptLedgerError("campaign_id_missing")

    selected_ids, selected_by_id = _selected_rows(selected)
    compile_by_id = _receipt_map(compile_results, field="compile_results")
    execution_by_id = _receipt_map(execution_results, field="execution_results")
    gate_by_id = _receipt_map(gate_results, field="gate_results")
    foreign_ids = sorted(
        (set(compile_by_id) | set(execution_by_id) | set(gate_by_id))
        - set(selected_ids)
    )
    if foreign_ids:
        raise ObligationAttemptLedgerError(
            f"foreign_obligation_receipt:{foreign_ids[0]}"
        )

    attempts: list[dict[str, Any]] = []
    for obligation_id in selected_ids:
        selected_row = selected_by_id[obligation_id]
        compile_receipt = compile_by_id.get(obligation_id, {})
        execution_receipt = execution_by_id.get(obligation_id, {})
        gate_receipt = gate_by_id.get(obligation_id, {})
        stage_records = [
            record
            for record in (
                _stage_record("compile", compile_receipt),
                _stage_record("execution", execution_receipt),
                _stage_record("gate", gate_receipt),
            )
            if record is not None
        ]
        terminals = [
            (stage, receipt)
            for stage, receipt in (
                ("compile", compile_receipt),
                ("execution", execution_receipt),
                ("gate", gate_receipt),
            )
            if _text(receipt.get("status")).upper() in TERMINAL_STATUSES
        ]
        if len(terminals) != 1:
            code = "terminal_receipt_missing" if not terminals else "duplicate_terminal_receipt"
            raise ObligationAttemptLedgerError(f"{code}:{obligation_id}")

        compile_status = _text(compile_receipt.get("status")).upper()
        execution_status = _text(execution_receipt.get("status")).upper()
        if execution_receipt and compile_status != "COMPILED":
            raise ObligationAttemptLedgerError(
                f"execution_without_compiled_obligation:{obligation_id}"
            )
        if gate_receipt and execution_status != "EXECUTED":
            raise ObligationAttemptLedgerError(
                f"gate_without_executed_obligation:{obligation_id}"
            )

        terminal_stage, terminal_receipt = terminals[0]
        terminal_status = _text(terminal_receipt.get("status")).upper()
        reason_code = _text(terminal_receipt.get("reason_code"))
        finding_id = _text(terminal_receipt.get("finding_id"))
        if terminal_status == "DELIVERABLE" and not finding_id:
            raise ObligationAttemptLedgerError(
                f"deliverable_finding_id_missing:{obligation_id}"
            )
        if terminal_status != "DELIVERABLE" and finding_id:
            raise ObligationAttemptLedgerError(
                f"nondeliverable_finding_id_present:{obligation_id}"
            )
        if terminal_status != "DELIVERABLE" and not reason_code:
            raise ObligationAttemptLedgerError(
                f"terminal_reason_code_missing:{obligation_id}"
            )

        cost_coverage_status = _text(
            gate_receipt.get("cost_coverage_status")
            or execution_receipt.get("cost_coverage_status")
            or compile_receipt.get("cost_coverage_status")
            or "UNKNOWN"
        ).upper()
        if cost_coverage_status not in _COST_COVERAGE_STATUSES:
            raise ObligationAttemptLedgerError(
                f"cost_coverage_status_invalid:{obligation_id}"
            )
        elapsed_values = [
            int(record["elapsed_ms"])
            for record in stage_records
            if record["elapsed_ms"] is not None
        ]
        observation_receipt_ids = [
            _text(value)
            for value in execution_receipt.get("observation_receipt_ids", []) or []
            if _text(value)
        ]
        attempt: dict[str, Any] = {
            "candidate_id": _text(
                selected_row.get("candidate_id")
                or compile_receipt.get("candidate_id")
                or execution_receipt.get("candidate_id")
            ),
            "source_refs": [
                dict(value)
                for value in selected_row.get("source_refs", []) or []
                if isinstance(value, dict)
            ],
            "risk_family": _text(selected_row.get("risk_family")),
            "operation_refs": [
                _text(value)
                for value in (
                    selected_row.get("operation_refs")
                    or selected_row.get("required_operations")
                    or []
                )
                if _text(value)
            ],
            "actor_refs": [
                _text(value)
                for value in (
                    selected_row.get("actor_refs")
                    or selected_row.get("required_actors")
                    or []
                )
                if _text(value)
            ],
            "adapter": _text(
                selected_row.get("adapter")
                or selected_row.get("execution_adapter")
            ),
            "round": _text(
                selected_row.get("planning_round")
                if selected_row.get("planning_round") is not None
                else selected_row.get("round")
            ),
            "behavior_slice_id": _text(
                selected_row.get("behavior_slice_id")
                or selected_row.get("slice_id")
            ),
            "behavior_ir_refs": [
                _text(value)
                for value in selected_row.get("behavior_ir_refs", []) or []
                if _text(value)
            ],
            "obligation_id": obligation_id,
            "experiment_id": _text(
                compile_receipt.get("experiment_id")
                or execution_receipt.get("experiment_id")
                or selected_row.get("experiment_id")
            ),
            "execution_id": _text(execution_receipt.get("execution_id")),
            "observation_receipt_ids": observation_receipt_ids,
            "oracle_receipt_id": _text(
                execution_receipt.get("oracle_receipt_id")
                or gate_receipt.get("oracle_receipt_id")
            ),
            "oracle_reason_code": _text(
                execution_receipt.get("oracle_reason_code")
                or gate_receipt.get("oracle_reason_code")
            ),
            "gate_receipt_id": _text(
                gate_receipt.get("gate_receipt_id")
                or gate_receipt.get("receipt_id")
            ),
            "finding_id": finding_id,
            "terminal_stage": terminal_stage,
            "terminal_status": terminal_status,
            "reason_code": reason_code,
            "input_fingerprint": _text(
                selected_row.get("input_fingerprint")
                or compile_receipt.get("input_fingerprint")
            ),
            "output_fingerprint": _text(
                gate_receipt.get("output_fingerprint")
                or execution_receipt.get("output_fingerprint")
            ),
            "receipt_refs": {
                record["stage"]: record["receipt_id"]
                for record in stage_records
                if record["receipt_id"]
            },
            "elapsed_ms": sum(elapsed_values) if elapsed_values else None,
            "cost_coverage_status": cost_coverage_status,
            "stages": stage_records,
        }
        attempt["attempt_fingerprint"] = _fingerprint(attempt)
        attempts.append(attempt)

    terminal_counts = dict(
        sorted(Counter(row["terminal_status"] for row in attempts).items())
    )
    ledger: dict[str, Any] = {
        "schema_version": OBLIGATION_ATTEMPT_LEDGER_SCHEMA,
        "run_id": run_id,
        "campaign_id": campaign_id,
        "mainline_contract_fingerprint": _text(run.get("contract_fingerprint")),
        "selected_count": len(selected_ids),
        "terminal_count": len(attempts),
        "complete": len(attempts) == len(selected_ids),
        "terminal_status_counts": terminal_counts,
        "attempts": attempts,
    }
    ledger["ledger_fingerprint"] = _fingerprint(ledger)
    return ledger


def validate_obligation_attempt_ledger(
    ledger: dict[str, Any],
) -> dict[str, Any]:
    """Validate schema plus immutable ledger and per-attempt fingerprints."""

    value = _object(ledger, field="obligation_attempt_ledger")
    if value.get("schema_version") != OBLIGATION_ATTEMPT_LEDGER_SCHEMA:
        raise ObligationAttemptLedgerError("obligation_attempt_ledger_schema_invalid")
    observed_fingerprint = _text(value.get("ledger_fingerprint"))
    if not observed_fingerprint:
        raise ObligationAttemptLedgerError(
            "obligation_attempt_ledger_fingerprint_missing"
        )
    fingerprint_payload = {
        key: item
        for key, item in value.items()
        if key != "ledger_fingerprint"
    }
    if observed_fingerprint != _fingerprint(fingerprint_payload):
        raise ObligationAttemptLedgerError(
            "obligation_attempt_ledger_fingerprint_mismatch"
        )
    attempts = value.get("attempts")
    if not isinstance(attempts, list):
        raise ObligationAttemptLedgerError("obligation_attempts_not_list")
    for row in attempts:
        attempt = _object(row, field="obligation_attempt")
        observed_attempt_fingerprint = _text(attempt.get("attempt_fingerprint"))
        expected_attempt_fingerprint = _fingerprint({
            key: item
            for key, item in attempt.items()
            if key != "attempt_fingerprint"
        })
        if observed_attempt_fingerprint != expected_attempt_fingerprint:
            obligation_id = _text(attempt.get("obligation_id")) or "MISSING"
            raise ObligationAttemptLedgerError(
                f"obligation_attempt_fingerprint_mismatch:{obligation_id}"
            )
    return value


def derive_campaign_terminal_status(ledger: dict[str, Any]) -> str:
    """Project campaign status only from obligation terminal coverage."""

    value = validate_obligation_attempt_ledger(ledger)
    try:
        selected_count = int(value.get("selected_count"))
        terminal_count = int(value.get("terminal_count"))
    except (TypeError, ValueError) as exc:
        raise ObligationAttemptLedgerError("obligation_attempt_counts_invalid") from exc
    attempts = value["attempts"]
    if (
        selected_count != terminal_count
        or terminal_count != len(attempts)
        or not bool(value.get("complete"))
    ):
        return "active"
    statuses = [_text(_object(row, field="obligation_attempt").get("terminal_status")).upper() for row in attempts]
    if any(status not in TERMINAL_STATUSES for status in statuses):
        raise ObligationAttemptLedgerError("obligation_terminal_status_invalid")
    if "HARNESS_FAILED" in statuses:
        return "degraded"
    return "completed"
