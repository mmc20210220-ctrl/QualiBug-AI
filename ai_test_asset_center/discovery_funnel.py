"""Attempt-receipt-only discovery funnel and pipeline health projection."""
from __future__ import annotations

import math
import re as _re
from collections import Counter
from pathlib import Path
from typing import Any

from .obligation_attempt_ledger import (
    ObligationAttemptLedgerError,
    validate_obligation_attempt_ledger,
)
from .discovery_mainline_contract import validate_mainline_run_contract
from .discovery_quality_projection import (
    SCHEMA_VERSION as QUALITY_PROJECTION_SCHEMA,
)
from .operational_receipts import EXECUTION_OPERATIONAL_SUMMARY_SCHEMA
from .formal_delivery_scope import validated_delivery_gate_finding_ids
from .blocker_attribution import (
    REASON_CODE_REGISTRY_SCHEMA,
    profile_reason_code,
)
from .customer_delivery_gate_v2 import _oracle_harness_reason_detail


REQUIRED_STAGE_NAMES = (
    "obligation_generation",
    "experiment_compile",
    "binding_materialization",
    "fixture_setup",
    "governed_execution",
    "observation",
    "assertion",
    "oracle_resolution",
    "delivery_gate",
    "cleanup",
    "formal_projection",
)

_ORACLE_NON_VIOLATION_REASON_CODES = frozenset({
    "ORACLE_NOT_VIOLATED",
    "ORACLE_NO_VIOLATION",
    "ASSERTION_NOT_VIOLATED",
    "SURFACE_DISCOVERY_OBSERVATION_ONLY",
})

_ORACLE_INDETERMINATE_REASON_CODES = frozenset({
    "ASSERTION_INDETERMINATE",
    "BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE",
    "BLOCKED_CANONICAL_OUTCOME_IDENTITY_INCOMPLETE",
    "BLOCKED_AMBIGUOUS_OUTCOME_FINDING",
    "CONTRACT_ORACLE_BLOCKED",
    "CONTRACT_ORACLE_HARNESS_FAILED",
    "HARNESS_BLOCKER_ATTRIBUTION_FAILED",
    "HARNESS_COVERAGE_FUNNEL_FAILED",
    "ORACLE_EXCEPTION",
    "VALIDATION_GATE_EXCEPTION",
})


class DiscoveryFunnelError(ValueError):
    """Authoritative attempt or formal projection receipts are missing."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _formal_obligation_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    nested = _dict(result.get("v12"))
    for owner in (result, nested):
        obligations = _dict(owner.get("test_obligations")).get("obligations")
        if isinstance(obligations, list):
            return [
                row for row in obligations if isinstance(row, dict)
            ]
    return []


def _formal_obligation_identity(result: dict[str, Any]) -> dict[str, Any]:
    rows = _formal_obligation_rows(result)
    ids = [_text(row.get("obligation_id")) for row in rows]
    missing_count = sum(not value for value in ids)
    counts = Counter(value for value in ids if value)
    duplicate_ids = sorted(
        value for value, count in counts.items() if count > 1
    )
    unique_ids = sorted(counts)
    return {
        "status": (
            "INCOMPLETE"
            if not rows or missing_count
            else "FAILED_SAFE"
            if duplicate_ids
            else "PASS"
        ),
        "row_count": len(rows),
        "unique_count": len(unique_ids),
        "missing_id_count": missing_count,
        "duplicate_id_count": sum(counts[value] - 1 for value in duplicate_ids),
        "duplicate_ids": duplicate_ids,
        "ids": unique_ids,
    }


def _generated_obligation_count(result: dict[str, Any]) -> int | None:
    identity = _formal_obligation_identity(result)
    if not identity["row_count"]:
        return None
    # Runtime interface discovery is an explicitly separate, read-only
    # planning/execution receipt. It is projected under
    # ``business_discovery_separation`` and must not inflate the formal
    # business-obligation funnel or create a terminal attempt implicitly.
    return int(identity["unique_count"])


def _status_count(rows: list[dict[str, Any]], *statuses: str) -> int:
    accepted = {value.upper() for value in statuses}
    return sum(
        1 for row in rows if _text(row.get("status")).upper() in accepted
    )


def _oracle_bucket(
    gate_stage: dict[str, Any],
) -> str:
    """Classify a gate receipt from explicit status/reason values only."""

    status = _text(gate_stage.get("status")).upper()
    reason = _text(gate_stage.get("reason_code")).upper()
    if status == "DELIVERABLE":
        return "violation"
    if status == "REJECTED" and reason in _ORACLE_NON_VIOLATION_REASON_CODES:
        return "pass"
    if status in {"BLOCKED", "DEFERRED", "HARNESS_FAILED"}:
        return "indeterminate"
    if reason in _ORACLE_INDETERMINATE_REASON_CODES:
        return "indeterminate"
    if status == "REJECTED":
        # A rejected gate with a non-normal reason is the explicit delivery-gate
        # blocked branch. The reason registry still owns its detailed family;
        # no free-form substring classification is used here.
        return "violation"
    return "indeterminate"


def _conservation_check(
    checks: list[dict[str, Any]],
    *,
    name: str,
    expected: Any,
    observed: Any,
    detail: str = "",
) -> None:
    checks.append({
        "name": name,
        "status": "PASS" if expected == observed else "FAIL",
        "expected": expected,
        "observed": observed,
        "detail": detail,
    })


def _build_funnel_conservation(
    result: dict[str, Any],
    ledger: dict[str, Any],
    formal: dict[str, Any],
) -> dict[str, Any]:
    """Check stage conservation using only immutable attempt/formal receipts."""

    attempts = [_dict(item) for item in _list(ledger.get("attempts"))]
    selected = _int(ledger.get("selected_count"))
    terminal = _int(ledger.get("terminal_count"))
    generated = _generated_obligation_count(result)
    formal_identity = _formal_obligation_identity(result)
    selected_ids = [_text(attempt.get("obligation_id")) for attempt in attempts]
    selected_id_set = {value for value in selected_ids if value}
    selected_identity_complete = (
        len(selected_ids) == len(selected_id_set)
        and len(selected_id_set) == selected
        and all(selected_ids)
    )
    generated_id_set = set(formal_identity["ids"])
    not_selected: int | None = None
    selected_outside_generated: int | None = None
    if formal_identity["status"] == "PASS" and selected_identity_complete:
        selected_outside_generated = len(selected_id_set - generated_id_set)
        if selected_outside_generated == 0:
            not_selected = len(generated_id_set - selected_id_set)
    compile_rows = [_stage(attempt, "compile") for attempt in attempts]
    execution_rows = [_stage(attempt, "execution") for attempt in attempts]
    gate_rows = [_stage(attempt, "gate") for attempt in attempts]
    compile_success_count = _status_count(compile_rows, "COMPILED")
    compile_blocked_count = _status_count(compile_rows, "BLOCKED")
    compile_deferred_count = _status_count(compile_rows, "DEFERRED")
    compile_harness_failure_count = _status_count(
        compile_rows, "HARNESS_FAILED"
    )
    compile_missing_count = sum(not row for row in compile_rows)
    execution_count = _status_count(execution_rows, "EXECUTED", "DELIVERABLE")
    execution_blocked_count = _status_count(
        execution_rows, "BLOCKED", "DEFERRED"
    )
    execution_harness_failure_count = _status_count(
        execution_rows, "HARNESS_FAILED"
    )
    execution_missing_after_compile_count = sum(
        not execution
        for compile, execution in zip(compile_rows, execution_rows)
        if _text(compile.get("status")).upper() == "COMPILED"
    )
    gate_count = sum(bool(row) for row in gate_rows)
    pre_execution_count = (
        compile_blocked_count
        + compile_deferred_count
        + compile_harness_failure_count
    )
    execution_unresolved_count = sum(
        1
        for execution, gate in zip(execution_rows, gate_rows)
        if _text(execution.get("status")).upper()
        in {"EXECUTED", "DELIVERABLE"}
        and not gate
    )
    gate_terminal_count = sum(
        1
        for row in gate_rows
        if _text(row.get("status")).upper() in {
            "DELIVERABLE", "REJECTED", "BLOCKED", "DEFERRED", "HARNESS_FAILED",
        }
    )
    oracle_rows = [
        gate
        for execution, gate in zip(execution_rows, gate_rows)
        if _text(execution.get("status")).upper()
        in {"EXECUTED", "DELIVERABLE"}
        and gate
    ]
    oracle_buckets = Counter(_oracle_bucket(row) for row in oracle_rows)
    oracle_pass_count = int(oracle_buckets.get("pass", 0))
    oracle_violation_count = int(oracle_buckets.get("violation", 0))
    oracle_indeterminate_count = int(oracle_buckets.get("indeterminate", 0))
    customer_deliverable_count = _status_count(oracle_rows, "DELIVERABLE")
    delivery_gate_blocked_finding_count = sum(
        1
        for row in oracle_rows
        if _oracle_bucket(row) == "violation"
        and _text(row.get("status")).upper() != "DELIVERABLE"
    )
    # Historical authorization quarantine is a derived formal-scope decision.
    # The immutable ledger may still contain DELIVERABLE terminal rows, but
    # those rows are not customer-deliverable until the same validated scope
    # used by the formal projection accepts their occurrence identities.
    deliverable_attempt_ids = set(validated_delivery_gate_finding_ids(ledger))
    formal_occurrence_ids = {
        _text(value)
        for value in _list(formal.get("delivery_occurrence_finding_ids"))
        if _text(value)
    }
    checks: list[dict[str, Any]] = []
    if formal_identity["row_count"]:
        checks.append({
            "name": "formal_obligation_identity_unique",
            "status": (
                "PASS"
                if formal_identity["status"] == "PASS"
                else formal_identity["status"]
            ),
            "expected": "one unique obligation_id per formal obligation",
            "observed": {
                "row_count": formal_identity["row_count"],
                "unique_count": formal_identity["unique_count"],
                "missing_id_count": formal_identity["missing_id_count"],
                "duplicate_id_count": formal_identity["duplicate_id_count"],
            },
            "detail": (
                "formal test-obligation identity is unique"
                if formal_identity["status"] == "PASS"
                else "duplicate or missing formal obligation identity is visible; no row was merged in the report"
            ),
        })
    else:
        checks.append({
            "name": "formal_obligation_identity_unique",
            "status": "INCOMPLETE",
            "expected": "one unique obligation_id per formal obligation",
            "observed": "test_obligations.obligations missing",
            "detail": "No formal obligation receipt was available.",
        })
    if generated is not None and not_selected is not None:
        _conservation_check(
            checks,
            name="obligation_selection_conservation",
            expected=generated,
            observed=selected + not_selected,
            detail="generated IDs are partitioned into selected and not-selected receipts",
        )
    else:
        checks.append({
            "name": "obligation_selection_conservation",
            "status": "INCOMPLETE",
            "expected": generated,
            "observed": {
                "selected": selected,
                "not_selected": not_selected,
                "selected_outside_generated": selected_outside_generated,
            },
            "detail": "Formal and selected obligation identities do not prove a complete partition.",
        })
    _conservation_check(
        checks,
        name="selected_terminal_conservation",
        expected=selected,
        observed=terminal,
    )
    _conservation_check(
        checks,
        name="selected_compile_outcome_conservation",
        expected=selected,
        observed=(
            compile_success_count
            + compile_blocked_count
            + compile_deferred_count
            + compile_harness_failure_count
        ),
        detail="each selected attempt has exactly one compile outcome",
    )
    _conservation_check(
        checks,
        name="compiled_execution_outcome_conservation",
        expected=compile_success_count,
        observed=(
            execution_count
            + execution_blocked_count
            + execution_harness_failure_count
            + execution_missing_after_compile_count
        ),
        detail="each compiled attempt has an executed, blocked, harness-failed, or missing execution receipt",
    )
    _conservation_check(
        checks,
        name="executed_oracle_outcome_conservation",
        expected=execution_count,
        observed=(
            oracle_pass_count
            + oracle_violation_count
            + oracle_indeterminate_count
            + execution_unresolved_count
        ),
        detail="each executed attempt has an explicit oracle pass, violation, indeterminate, or missing gate outcome",
    )
    _conservation_check(
        checks,
        name="oracle_violation_delivery_conservation",
        expected=oracle_violation_count,
        observed=(
            customer_deliverable_count
            + delivery_gate_blocked_finding_count
        ),
        detail="oracle violations are partitioned into deliverable and delivery-gate-blocked findings",
    )
    _conservation_check(
        checks,
        name="oracle_terminal_conservation",
        expected=gate_count,
        observed=gate_terminal_count,
    )
    _conservation_check(
        checks,
        name="delivery_occurrence_conservation",
        expected=_int(formal.get("delivery_occurrence_count")),
        observed=len(formal_occurrence_ids),
    )
    _conservation_check(
        checks,
        name="delivery_identity_conservation",
        expected=sorted(formal_occurrence_ids),
        observed=sorted(deliverable_attempt_ids),
    )
    obligation_ids = [
        _text(attempt.get("obligation_id")) for attempt in attempts
    ]
    _conservation_check(
        checks,
        name="terminal_attempt_identity_unique",
        expected=len(obligation_ids),
        observed=len(set(obligation_ids)),
    )

    identity = _dict(ledger.get("identity"))
    identity_missing = [
        _text(value)
        for value in _list(identity.get("missing_fields"))
        if _text(value)
    ]
    identity_stage_gaps: list[dict[str, Any]] = []
    attempt_identity_gaps: list[str] = []
    for attempt in attempts:
        obligation_id = _text(attempt.get("obligation_id"))
        attempt_identity = _dict(attempt.get("identity"))
        if (
            _text(attempt_identity.get("status")).upper() != "COMPLETE"
            or _list(attempt_identity.get("missing_fields"))
        ):
            attempt_identity_gaps.append(obligation_id or "MISSING")
        for stage in _list(attempt.get("stages")):
            stage_value = _dict(stage)
            stage_identity = _dict(stage_value.get("identity"))
            missing_fields = [
                _text(value)
                for value in _list(stage_identity.get("missing_fields"))
                if _text(value)
            ]
            stage_status = _text(
                stage_identity.get("status") or stage_value.get("identity_status")
            ).upper()
            if (
                _text(stage_identity.get("obligation_id")) != obligation_id
                or stage_status != "COMPLETE"
                or missing_fields
            ):
                identity_stage_gaps.append({
                    "obligation_id": obligation_id or "MISSING",
                    "stage": _text(stage_value.get("stage")) or "MISSING",
                    "status": stage_status or "MISSING",
                    "missing_fields": missing_fields or ["stage_identity_receipt"],
                })
    identity_complete = (
        bool(identity)
        and not identity_missing
        and not attempt_identity_gaps
        and not identity_stage_gaps
    )
    identity_status = "PASS" if identity_complete else "INCOMPLETE"
    checks.append({
        "name": "identity_continuity",
        "status": "PASS" if identity_complete else "INCOMPLETE",
        "expected": "one immutable identity per attempt and stage receipt",
        "observed": (
            "one immutable identity per attempt and stage receipt"
            if identity_complete
            else {
                "root_missing_fields": identity_missing,
                "attempt_identity_gaps": attempt_identity_gaps,
                "stage_identity_gaps": identity_stage_gaps[:50],
                "stage_identity_gap_count": len(identity_stage_gaps),
            }
        ),
        "detail": (
            "run, campaign and stage lineage are receipt-bound"
            if identity_complete
            else "Missing stage identity is visible; no value was re-derived."
        ),
    })

    reason_codes = {
        _text(attempt.get("reason_code"))
        for attempt in attempts
        if _text(attempt.get("reason_code"))
    }
    unregistered = sorted(
        code
        for code in reason_codes
        if _text(profile_reason_code(code).get("registry_status"))
        == "UNREGISTERED"
    )
    reason_registry_status = "PASS" if not unregistered else "FAILED_SAFE"
    checks.append({
        "name": "reason_code_registry",
        "status": reason_registry_status,
        "expected": "all terminal reason codes registered",
        "observed": unregistered or "all terminal reason codes registered",
        "detail": "Classification uses the explicit code registry; detail text is not used.",
    })

    failures = [
        row for row in checks if row.get("status") in {"FAIL", "FAILED_SAFE"}
    ]
    incomplete = [row for row in checks if row.get("status") == "INCOMPLETE"]
    missing_evidence: list[str] = []
    if generated is None:
        missing_evidence.append("test_obligations.obligations")
    if not identity:
        missing_evidence.append("ledger.identity")
    status = "FAILED_SAFE" if failures or unregistered else "PASS"
    if status == "PASS" and (incomplete or missing_evidence):
        status = "INCOMPLETE"
    return {
        "schema_version": "qualibug.discovery-funnel-conservation.v1",
        "status": status,
        "complete": status == "PASS",
        "generated_count": generated,
        "generated_row_count": formal_identity["row_count"],
        "generated_duplicate_count": formal_identity["duplicate_id_count"],
        "generated_duplicate_ids": formal_identity["duplicate_ids"],
        "generated_missing_id_count": formal_identity["missing_id_count"],
        "not_selected_count": not_selected,
        "selected_outside_generated_count": selected_outside_generated,
        "selected_count": selected,
        "terminal_count": terminal,
        "compile_count": compile_success_count,
        "compile_success_count": compile_success_count,
        "compile_blocked_count": compile_blocked_count,
        "compile_deferred_count": compile_deferred_count,
        "compile_harness_failure_count": compile_harness_failure_count,
        "compile_missing_count": compile_missing_count,
        "pre_execution_blocked_count": pre_execution_count,
        "execution_count": execution_count,
        "execution_blocked_count": execution_blocked_count,
        "execution_harness_failure_count": execution_harness_failure_count,
        "execution_missing_after_compile_count": execution_missing_after_compile_count,
        "execution_unresolved_count": execution_unresolved_count,
        "oracle_count": gate_count,
        "oracle_resolved_count": oracle_pass_count + oracle_violation_count,
        "oracle_pass_count": oracle_pass_count,
        "oracle_violation_count": oracle_violation_count,
        "oracle_indeterminate_count": oracle_indeterminate_count + execution_unresolved_count,
        "customer_deliverable_finding_count": customer_deliverable_count,
        "delivery_gate_blocked_finding_count": delivery_gate_blocked_finding_count,
        "delivery_count": len(deliverable_attempt_ids),
        "formal_delivery_occurrence_count": len(formal_occurrence_ids),
        "identity_status": identity_status,
        "identity_missing_fields": identity_missing,
        "identity_stage_gaps": identity_stage_gaps,
        "attempt_identity_gaps": attempt_identity_gaps,
        "reason_registry_schema": REASON_CODE_REGISTRY_SCHEMA,
        "unregistered_reason_codes": unregistered,
        "missing_evidence": missing_evidence,
        "failures": failures,
        "checks": checks,
    }


def build_funnel_conservation(v12_result: dict[str, Any]) -> dict[str, Any]:
    """Public conservation receipt for diagnostics, API and UI consumers."""

    result = _dict(v12_result)
    ledger = _attempt_ledger(result)
    formal = _formal_projection(result)
    return _build_funnel_conservation(result, ledger, formal)


def _is_execution_approval_gate(runtime_contract: dict[str, Any]) -> bool:
    """True when the runtime-contract block is an execution-approval gate.

    An execution-approval gate (missing/stale approval, campaign mismatch) means
    the scan was stopped *before* execution. It is NOT an execution-phase block.
    """
    approval = _dict(runtime_contract.get("execution_approval"))
    if approval:
        code = str(approval.get("code") or "").strip().upper()
        if code.startswith("EXECUTION_APPROVAL"):
            return True
        if str(approval.get("approval_id") or "").strip():
            return True
    for item in _list(runtime_contract.get("missing_requirements")):
        if str(item).strip().upper().startswith("EXECUTION_APPROVAL"):
            return True
    reason = str(runtime_contract.get("reason") or "").strip().lower()
    return "execution_approval" in reason


def _attempt_ledger(result: dict[str, Any] | None) -> dict[str, Any]:
    value = _dict(result)
    nested = _dict(value.get("v12"))
    raw = value.get("obligation_attempt_ledger") or nested.get("obligation_attempt_ledger")
    if not isinstance(raw, dict):
        raise DiscoveryFunnelError("obligation_attempt_ledger_missing")
    try:
        return validate_obligation_attempt_ledger(raw)
    except ObligationAttemptLedgerError as exc:
        raise DiscoveryFunnelError(f"obligation_attempt_ledger_invalid:{exc}") from exc


def effective_execution_status(v12_result: dict[str, Any] | None) -> str:
    """Derive execution completeness from the attempt ledger and no other source."""

    ledger = _attempt_ledger(v12_result)
    selected = _int(ledger.get("selected_count"))
    terminal = _int(ledger.get("terminal_count"))
    if selected == 0:
        # No obligations reached the attempt ledger. The honest execution status
        # depends on WHY nothing executed:
        #  - An *execution-approval gate* block (missing/stale approval, campaign
        #    mismatch) means the scan was stopped *before* execution. Nothing was
        #    attempted, so the status is "not_executed", never "blocked".
        #  - Any other block (source provenance, discovery-evolution, runtime
        #    contract, obligation-plan) means execution was attempted and blocked
        #    at the execution phase; that surfaces as "blocked" via
        #    phases.execution.status. A genuinely empty (approved, nothing-to-run)
        #    plan with no block falls back to "not_executed".
        result = _dict(v12_result)
        runtime_contract = _dict(result.get("runtime_contract"))
        if str(runtime_contract.get("status") or "").strip().lower() == "blocked" and _is_execution_approval_gate(runtime_contract):
            return "not_executed"
        execution_phase = _dict(_dict(result.get("phases")).get("execution"))
        legacy = execution_phase.get("status")
        return str(legacy or "not_executed").strip().lower()
    if bool(ledger.get("complete")) and selected == terminal:
        attempts = [
            _dict(attempt) for attempt in _list(ledger.get("attempts"))
        ]
        observed_attempt_count = sum(
            1 for attempt in attempts if _list(attempt.get("observation_receipt_ids"))
        )
        if observed_attempt_count == 0:
            return "blocked"
        return "completed"
    if terminal > 0:
        return "partial"
    return "not_executed"


def _stage(attempt: dict[str, Any], name: str) -> dict[str, Any]:
    return next(
        (
            _dict(item)
            for item in _list(attempt.get("stages"))
            if _text(_dict(item).get("stage")) == name
        ),
        {},
    )


def _classify_status(status: str) -> str:
    normalized = _text(status).upper()
    if normalized in {
        "COMPILED",
        "EXECUTED",
        "OBSERVED",
        "ASSERTED",
        "RESOLVED",
        "DELIVERABLE",
        "REJECTED",
        "PROJECTED",
        "SUCCEEDED",
        "NOT_REQUIRED",
    }:
        return "success"
    if normalized in {"BLOCKED", "DEFERRED", "NOT_REACHED"}:
        return "blocked"
    return "failed"


def _observation(
    attempt: dict[str, Any],
    stage_name: str,
) -> dict[str, Any] | None:
    compile_stage = _stage(attempt, "compile")
    execution_stage = _stage(attempt, "execution")
    gate_stage = _stage(attempt, "gate")
    compile_status = _text(compile_stage.get("status")).upper()
    execution_status = _text(execution_stage.get("status")).upper()
    terminal_status = _text(attempt.get("terminal_status")).upper()
    terminal_reason = _text(attempt.get("reason_code"))
    if stage_name == "obligation_generation":
        return {"status": "SUCCEEDED", "reason": "", "elapsed_ms": None}
    if stage_name == "experiment_compile":
        return {
            "status": compile_status or "RECEIPT_MISSING",
            "reason": _text(compile_stage.get("reason_code")) or (
                "STAGE_RECEIPT_MISSING" if not compile_status else ""
            ),
            "elapsed_ms": compile_stage.get("elapsed_ms"),
        }
    if stage_name == "binding_materialization":
        if not compile_stage:
            return {"status": "RECEIPT_MISSING", "reason": "STAGE_RECEIPT_MISSING", "elapsed_ms": None}
        if compile_status == "COMPILED":
            return {"status": "SUCCEEDED", "reason": "", "elapsed_ms": compile_stage.get("elapsed_ms")}
        return {
            "status": compile_status,
            "reason": _text(compile_stage.get("reason_code")) or "BINDING_NOT_MATERIALIZED",
            "elapsed_ms": compile_stage.get("elapsed_ms"),
        }
    if compile_status != "COMPILED" and stage_name not in {"formal_projection"}:
        return None
    if stage_name == "fixture_setup":
        if not execution_stage:
            return {"status": "RECEIPT_MISSING", "reason": "STAGE_RECEIPT_MISSING", "elapsed_ms": None}
        reason = _text(execution_stage.get("reason_code"))
        if "FIXTURE" in reason.upper():
            return {"status": "BLOCKED", "reason": reason, "elapsed_ms": execution_stage.get("elapsed_ms")}
        return {"status": "SUCCEEDED", "reason": "", "elapsed_ms": None}
    if stage_name == "governed_execution":
        if not execution_stage:
            return {"status": "RECEIPT_MISSING", "reason": "STAGE_RECEIPT_MISSING", "elapsed_ms": None}
        return {
            "status": execution_status,
            "reason": _text(execution_stage.get("reason_code")),
            "elapsed_ms": execution_stage.get("elapsed_ms"),
        }
    if execution_status not in ("EXECUTED", "DELIVERABLE") and stage_name not in {"formal_projection"}:
        return None
    if stage_name == "observation":
        observed = bool(_list(attempt.get("observation_receipt_ids")))
        return {
            "status": "OBSERVED" if observed else "RECEIPT_MISSING",
            "reason": "" if observed else "OBSERVATION_RECEIPT_MISSING",
            "elapsed_ms": None,
        }
    if stage_name == "assertion":
        asserted = bool(_text(attempt.get("oracle_receipt_id")))
        return {
            "status": "ASSERTED" if asserted else "RECEIPT_MISSING",
            "reason": "" if asserted else "ASSERTION_RECEIPT_MISSING",
            "elapsed_ms": None,
        }
    if stage_name == "oracle_resolution":
        resolved = bool(_text(attempt.get("oracle_receipt_id")))
        return {
            "status": "RESOLVED" if resolved else "RECEIPT_MISSING",
            "reason": _text(attempt.get("oracle_reason_code")) or (
                "" if resolved else "ORACLE_RECEIPT_MISSING"
            ),
            "elapsed_ms": None,
        }
    if stage_name == "delivery_gate":
        if not gate_stage:
            return {"status": "RECEIPT_MISSING", "reason": "GATE_RECEIPT_MISSING", "elapsed_ms": None}
        return {
            "status": _text(gate_stage.get("status")).upper(),
            "reason": _text(gate_stage.get("reason_code")),
            "elapsed_ms": gate_stage.get("elapsed_ms"),
        }
    if stage_name == "cleanup":
        if terminal_status == "HARNESS_FAILED" and "CLEANUP" in terminal_reason.upper():
            return {"status": "HARNESS_FAILED", "reason": terminal_reason, "elapsed_ms": None}
        return {"status": "NOT_REQUIRED", "reason": "", "elapsed_ms": None}
    if stage_name == "formal_projection":
        return {
            "status": "PROJECTED" if terminal_status else "RECEIPT_MISSING",
            "reason": terminal_reason if not terminal_status else "",
            "elapsed_ms": None,
        }
    raise DiscoveryFunnelError(f"unknown_required_stage:{stage_name}")


def _percentile(values: list[int], percentile: float) -> int | None:
    if not values:
        return None
    ordered = sorted(max(0, int(value)) for value in values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _dimension_counts(rows: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    dimensions: dict[str, Counter[str]] = {
        "source": Counter(),
        "risk_family": Counter(),
        "operation": Counter(),
        "actor": Counter(),
        "adapter": Counter(),
        "round": Counter(),
        "terminal_status": Counter(),
        "reason_code": Counter(),
    }
    for attempt, observation in rows:
        for source_ref in _list(attempt.get("source_refs")):
            source = _text(
                _dict(source_ref).get("source_type")
                or _dict(source_ref).get("kind")
            )
            if source:
                dimensions["source"][source] += 1
        dimensions["risk_family"][_text(attempt.get("risk_family")) or "unclassified"] += 1
        for operation in _list(attempt.get("operation_refs")):
            if _text(operation):
                dimensions["operation"][_text(operation)] += 1
        for actor in _list(attempt.get("actor_refs")):
            if _text(actor):
                dimensions["actor"][_text(actor)] += 1
        dimensions["adapter"][_text(attempt.get("adapter")) or "unclassified"] += 1
        dimensions["round"][_text(attempt.get("round")) or "unclassified"] += 1
        dimensions["terminal_status"][
            _text(attempt.get("terminal_status")).upper() or "UNCLASSIFIED"
        ] += 1
        reason = _text(observation.get("reason"))
        if reason:
            dimensions["reason_code"][reason] += 1
    return {
        name: dict(sorted(counts.items()))
        for name, counts in dimensions.items()
    }


def _stage_projection(
    attempts: list[dict[str, Any]],
    stage_name: str,
) -> dict[str, Any]:
    rows = [
        (attempt, observation)
        for attempt in attempts
        for observation in [_observation(attempt, stage_name)]
        if observation is not None
    ]
    classifications = Counter(
        _classify_status(_text(observation.get("status")))
        for _, observation in rows
    )
    reasons = Counter(
        _text(observation.get("reason"))
        for _, observation in rows
        if _text(observation.get("reason"))
    )
    elapsed = [
        _int(observation.get("elapsed_ms"))
        for _, observation in rows
        if observation.get("elapsed_ms") is not None
    ]
    return {
        "name": stage_name,
        "input": len(rows),
        "success": int(classifications.get("success", 0)),
        "blocked": int(classifications.get("blocked", 0)),
        "failed": int(classifications.get("failed", 0)),
        "elapsed_ms": {
            "p50": _percentile(elapsed, 0.50),
            "p95": _percentile(elapsed, 0.95),
        },
        "reason_counts": dict(sorted(reasons.items())),
        "dimensions": _dimension_counts(rows),
    }


def _formal_projection(result: dict[str, Any]) -> dict[str, Any]:
    nested = _dict(result.get("v12"))
    formal = _dict(result.get("formal_count_projection") or nested.get("formal_count_projection"))
    if formal.get("schema_version") != QUALITY_PROJECTION_SCHEMA:
        raise DiscoveryFunnelError("formal_count_projection_missing")
    if not isinstance(formal.get("canonical_defect_ids"), list):
        raise DiscoveryFunnelError("canonical_defect_ids_missing")
    if not isinstance(formal.get("delivery_occurrence_finding_ids"), list):
        raise DiscoveryFunnelError("delivery_occurrence_finding_ids_missing")
    canonical_ids = sorted({
        _text(value)
        for value in formal["canonical_defect_ids"]
        if _text(value)
    })
    occurrence_ids = sorted({
        _text(value)
        for value in formal["delivery_occurrence_finding_ids"]
        if _text(value)
    })
    count = _int(
        formal.get("formal_customer_deliverable_count"),
        len(canonical_ids),
    )
    occurrence_count = _int(
        formal.get("delivery_occurrence_count"),
        len(occurrence_ids),
    )
    if count != len(canonical_ids):
        raise DiscoveryFunnelError("formal_count_projection_id_mismatch")
    if occurrence_count != len(occurrence_ids):
        raise DiscoveryFunnelError("delivery_occurrence_projection_id_mismatch")
    return {
        **formal,
        "formal_customer_deliverable_count": count,
        "canonical_defect_ids": canonical_ids,
        "delivery_occurrence_count": occurrence_count,
        "delivery_occurrence_finding_ids": occurrence_ids,
    }


def _operational_cleanup_failure_count(
    result: dict[str, Any],
) -> int | None:
    """Read the write-level cleanup SSOT and reject contradictory projections."""

    nested = _dict(result.get("v12"))
    raw_summaries = [
        value
        for value in (
            result.get("operational_receipt_summary"),
            nested.get("operational_receipt_summary"),
        )
        if value is not None
    ]
    if not raw_summaries:
        return None
    counts: list[int] = []
    for raw in raw_summaries:
        summary = _dict(raw)
        if summary.get("schema_version") != EXECUTION_OPERATIONAL_SUMMARY_SCHEMA:
            raise DiscoveryFunnelError("operational_summary_schema_invalid")
        value = summary.get("cleanup_failures")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise DiscoveryFunnelError(
                "operational_cleanup_failure_count_invalid"
            )
        counts.append(value)
    if len(set(counts)) != 1:
        raise DiscoveryFunnelError("operational_cleanup_failure_count_mismatch")
    return counts[0]


def build_pipeline_health(v12_result: dict[str, Any]) -> dict[str, Any]:
    result = _dict(v12_result)
    ledger = _attempt_ledger(result)
    formal = _formal_projection(result)
    conservation = _build_funnel_conservation(result, ledger, formal)
    attempts = [_dict(item) for item in _list(ledger.get("attempts"))]
    # Use the same validated+quarantine-filtered scope that the formal
    # projection uses. Raw DELIVERABLE attempts may include quarantined
    # historical authorization attempts that the projection excludes.
    attempt_deliverable_ids = validated_delivery_gate_finding_ids(ledger)
    raw_contract = result.get("mainline_run") or _dict(result.get("v12")).get("mainline_run")
    enforce_conservation_completeness = isinstance(raw_contract, dict)
    customer_outputs_published = True
    if raw_contract is not None:
        customer_outputs_published = bool(
            validate_mainline_run_contract(raw_contract)["customer_outputs_published"]
        )
    if attempt_deliverable_ids != formal["delivery_occurrence_finding_ids"]:
        raise DiscoveryFunnelError("formal_projection_attempt_id_mismatch")
    terminal_counts = Counter(_text(item.get("terminal_status")).upper() for item in attempts)
    selected = _int(ledger.get("selected_count"))
    terminal = _int(ledger.get("terminal_count"))
    execution_status = effective_execution_status(result)
    harness_failures = int(terminal_counts.get("HARNESS_FAILED", 0))
    blocked = int(terminal_counts.get("BLOCKED", 0) + terminal_counts.get("DEFERRED", 0))
    executed = sum(
        1 for attempt in attempts if _text(_stage(attempt, "execution").get("status")).upper() in ("EXECUTED", "DELIVERABLE")
    )
    observation_missing = sum(
        1
        for attempt in attempts
        if _text(_stage(attempt, "execution").get("status")).upper() in ("EXECUTED", "DELIVERABLE")
        and not _list(attempt.get("observation_receipt_ids"))
    )
    error_present = bool(_text(result.get("error")))
    stage_failures = [
        _text(value) for value in _list(result.get("stage_failures")) if _text(value)
    ]
    secret_scan = _dict(result.get("artifact_secret_scan") or result.get("secret_scan"))
    secret_scan_failed = bool(secret_scan and secret_scan.get("safe") is False)
    reasoner = _dict(_dict(result.get("mainline_unification")).get("llm_reasoner"))
    reasoner_status = _text(reasoner.get("status")).lower()
    reasoner_failure_count = _int(reasoner.get("failed_engine_count"))
    reasoner_error_class_counts = {
        _text(key): _int(value)
        for key, value in _dict(reasoner.get("engine_error_class_counts")).items()
        if _text(key) and _int(value) > 0
    }
    nested_result = _dict(result.get("v12"))
    formal_consistency = _dict(
        result.get("defect_identity_consistency")
        or nested_result.get("defect_identity_consistency")
    )
    formal_mismatch = bool(formal_consistency and formal_consistency.get("consistent") is False)
    execution_observability = [
        dict(row)
        for row in _list(
            result.get("execution_observability")
            or nested_result.get("execution_observability")
        )
        if isinstance(row, dict)
    ]
    observability_gaps = [
        row
        for row in execution_observability
        if _text(row.get("status")).lower()
        not in {"ok", "healthy", "completed", "success", "succeeded"}
    ]
    # Count attempt-level cleanup failures. Evidence-validation reason codes
    # (CLEANUP_EVIDENCE_INCOMPLETE, CLEANUP_WRITE_COVERAGE_MISMATCH) are gate
    # validation issues, not actual cleanup execution failures, and must not
    # inflate the operational cleanup failure count.
    _EVIDENCE_ONLY_CLEANUP_REASONS = {
        "CLEANUP_EVIDENCE_INCOMPLETE",
        "CLEANUP_WRITE_COVERAGE_MISMATCH",
    }
    attempt_cleanup_failures = sum(
        1
        for attempt in attempts
        if "CLEANUP" in _text(attempt.get("reason_code")).upper()
        and _text(attempt.get("reason_code")).upper() not in _EVIDENCE_ONLY_CLEANUP_REASONS
        and _text(attempt.get("terminal_status")).upper() == "HARNESS_FAILED"
    )
    operational_cleanup_failures = _operational_cleanup_failure_count(result)
    if (
        operational_cleanup_failures is not None
        and operational_cleanup_failures < attempt_cleanup_failures
    ):
        raise DiscoveryFunnelError(
            "operational_cleanup_failure_count_below_attempt_receipts"
        )
    cleanup_failures = (
        operational_cleanup_failures
        if operational_cleanup_failures is not None
        else attempt_cleanup_failures
    )
    cost_unknown = any(
        _text(attempt.get("cost_coverage_status")).upper() == "UNKNOWN"
        for attempt in attempts
    )
    status = "OK"
    if (
        error_present
        or stage_failures
        or secret_scan_failed
        or observation_missing
        or conservation.get("status") == "FAILED_SAFE"
    ):
        status = "FAILED_SAFE"
    elif selected == 0:
        status = "BLOCKED"
    elif selected and blocked == selected:
        status = "BLOCKED"
    elif (
        harness_failures
        or cleanup_failures
        or blocked
        or execution_status != "completed"
        or reasoner_failure_count
        or reasoner_status in {"degraded", "failed", "provider_unavailable"}
        or formal_mismatch
        or observability_gaps
        or cost_unknown
        or (
            enforce_conservation_completeness
            and conservation.get("status") == "INCOMPLETE"
        )
    ):
        status = "DEGRADED"
    empty_means_no_bugs = bool(
        status == "OK"
        and selected > 0
        and selected == terminal
        and all(
            _text(item.get("terminal_status")).upper() == "REJECTED"
            and _text(item.get("reason_code")).upper() == "ORACLE_NOT_VIOLATED"
            for item in attempts
        )
    )
    reasons = Counter(
        _text(item.get("reason_code")) for item in attempts if _text(item.get("reason_code"))
    )
    if status == "OK":
        operator_note = "All selected obligations reached terminal, receipt-backed outcomes."
    else:
        reason_summary = ", ".join(
            f"{reason.lower()}={count}"
            for reason, count in sorted(reasons.items())
        ) or "none"
        health_flags = [
            label
            for active, label in (
                (error_present, "result.error"),
                (bool(stage_failures), "stage_failures"),
                (secret_scan_failed, "secret_scan_failed"),
                (observation_missing > 0, "observation_receipts_missing"),
                (cost_unknown, "usage/cost_coverage_unknown"),
                (reasoner_failure_count > 0, "reasoner_failures"),
                (formal_mismatch, "formal_id_mismatch"),
                (bool(observability_gaps), "execution_observability_gaps"),
                (cleanup_failures > 0, "cleanup_failures"),
                (blocked > 0, "blocked_or_deferred_obligations"),
                (selected == 0, "no_obligations_selected"),
                (
                    conservation.get("status") == "FAILED_SAFE",
                    "funnel_conservation_failed",
                ),
                (
                    enforce_conservation_completeness
                    and conservation.get("status") == "INCOMPLETE",
                    "funnel_conservation_incomplete",
                ),
            )
            if active
        ]
        flag_summary = ", ".join(health_flags) or "terminal_outcome_degraded"
        operator_note = (
            "Attempt receipts expose incomplete, blocked, failed, or unmeasured discovery work; "
            "empty findings must not be interpreted as a defect-free target. "
            f"Health flags: {flag_summary}. Terminal reasons: {reason_summary}."
        )
    return {
        "status": status,
        "execution_status": execution_status,
        "selected_obligation_count": selected,
        "terminal_obligation_count": terminal,
        "executed_obligation_count": executed,
        "blocked_obligation_count": blocked,
        "harness_failure_count": harness_failures,
        "cleanup_failure_count": cleanup_failures,
        "observation_receipt_missing_count": observation_missing,
        "empty_findings_means_no_bugs": empty_means_no_bugs,
        "usage_cost_unknown": cost_unknown,
        "reasoner_status": reasoner_status,
        "reasoner_failure_count": reasoner_failure_count,
        "reasoner_error_class_counts": reasoner_error_class_counts,
        "secret_scan_failed": secret_scan_failed,
        "formal_id_mismatch": formal_mismatch,
        "observability_gap_count": len(observability_gaps),
        "observability_gaps": observability_gaps,
        "funnel_conservation_status": conservation.get("status"),
        "funnel_conservation_complete": bool(conservation.get("complete")),
        "funnel_conservation": conservation,
        "terminal_reason_counts": dict(sorted(reasons.items())),
        "formal_customer_deliverable_count": formal["formal_customer_deliverable_count"],
        "canonical_defect_count": formal["formal_customer_deliverable_count"],
        "canonical_defect_ids": list(formal["canonical_defect_ids"]),
        "delivery_occurrence_count": formal["delivery_occurrence_count"],
        "delivery_occurrence_finding_ids": list(
            formal["delivery_occurrence_finding_ids"]
        ),
        "shadow_attempt_deliverable_count": (
            0 if customer_outputs_published else len(attempt_deliverable_ids)
        ),
        "planning_gap_reason": "NO_OBLIGATIONS_SELECTED" if selected == 0 else "",
        "operator_note": operator_note,
    }


def _safe_source_refs(attempt: dict[str, Any]) -> list[dict[str, str]]:
    allowed = {
        "source_id",
        "source_hash",
        "source_snapshot_hash",
        "source_locator",
        "locator",
        "kind",
        "source_type",
        "page",
        "line",
        "section",
        "field",
    }
    refs: list[dict[str, str]] = []
    for raw in _list(attempt.get("source_refs"))[:5]:
        source = _dict(raw)
        refs.append({
            key: _text(source.get(key))[:240]
            for key in allowed
            if _text(source.get(key))
        })
    return refs


_REASON_MATERIALS: dict[str, list[str]] = {
    "SOURCE_GAP": ["source material that declares the missing actor or rule"],
    "BEHAVIOR_MODEL_GAP": ["source operation/interface definition"],
    "COMPILER_GAP": ["source-backed field/state contract with exact expected values"],
    "OBSERVER_CAPABILITY_GAP": ["source-declared read or observable effect contract"],
    "BINDING_GRAPH_GAP": ["source-declared path/body binding and resolver operation"],
    "FIXTURE_CAPABILITY_GAP": ["source-declared fixture setup and ownership scope"],
    "ADAPTER_CAPABILITY_GAP": ["configured adapter capability for the declared surface"],
    "ENVIRONMENT_GAP": ["declared non-production target and environment identity"],
    "POLICY_SAFETY_BLOCK": ["operator-approved target policy and execution authorization"],
    "TARGET_SYSTEM_RESPONSE": ["target health evidence and the original transport receipt"],
    "ORACLE_INPUT_GAP": ["source-backed assertion inputs and observer evidence"],
    "CLEANUP_CAPABILITY_GAP": ["source-declared compensating action or adapter cleanup authority"],
    "EXECUTION_BUDGET": ["approved run budget or slice scope"],
    "PLANNING_DEFERRED": ["planning receipt explaining why the obligation was deferred"],
    "DISCOVERY_DIAGNOSTIC": [],
    "NORMAL_OUTCOME": [],
    "UNREGISTERED": ["operator review of the emitting module and its reason-code contract"],
}

_CUSTOMER_INPUT_LOSS_FAMILIES = frozenset({
    "SOURCE_GAP",
})
_QUALIBUG_CAPABILITY_LOSS_FAMILIES = frozenset({
    "BINDING_GRAPH_GAP",
    "BEHAVIOR_MODEL_GAP",
    "COMPILER_GAP",
    "FIXTURE_CAPABILITY_GAP",
    "OBSERVER_CAPABILITY_GAP",
    "ADAPTER_CAPABILITY_GAP",
    "ORACLE_INPUT_GAP",
    "EXECUTION_BUDGET",
    "PLANNING_DEFERRED",
})
_RUNTIME_ENVIRONMENT_LOSS_FAMILIES = frozenset({
    "ENVIRONMENT_GAP",
    "POLICY_SAFETY_BLOCK",
    "TARGET_SYSTEM_RESPONSE",
})


def _loss_attribution(
    attempts: list[dict[str, Any]],
    *,
    family: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    """Project owner buckets from the registered family and captured refs.

    Source-ref presence is intentionally reported as evidence presence only;
    it is not promoted to a sufficiency claim without an explicit receipt.
    """

    source_present_count = sum(bool(_list(item.get("source_refs"))) for item in attempts)
    source_missing_count = len(attempts) - source_present_count
    explicit_sufficiency = {
        _text(item.get("source_evidence_sufficiency")).upper()
        for item in attempts
        if _text(item.get("source_evidence_sufficiency")).upper()
        in {"SUFFICIENT", "INSUFFICIENT"}
    }
    if len(explicit_sufficiency) == 1:
        source_sufficiency = next(iter(explicit_sufficiency))
    elif explicit_sufficiency:
        source_sufficiency = "MIXED"
    else:
        source_sufficiency = "NOT_MEASURED"

    if family in _CUSTOMER_INPUT_LOSS_FAMILIES or (
        family == "CLEANUP_CAPABILITY_GAP"
        and _text(profile.get("recoverability")).upper() == "SOURCE_DEPENDENT"
    ):
        primary_owner = "CUSTOMER_INPUT_GAP"
    elif family in _QUALIBUG_CAPABILITY_LOSS_FAMILIES or family == "CLEANUP_CAPABILITY_GAP":
        primary_owner = "QUALIBUG_CAPABILITY_GAP"
    elif family in _RUNTIME_ENVIRONMENT_LOSS_FAMILIES:
        primary_owner = "RUNTIME_ENVIRONMENT_GAP"
    else:
        primary_owner = "UNKNOWN"
    return {
        "primary_owner": primary_owner,
        "classification_basis": "registered_reason_family_and_attempt_source_refs",
        "customer_input_gap": primary_owner == "CUSTOMER_INPUT_GAP",
        "qualibug_capability_gap": primary_owner == "QUALIBUG_CAPABILITY_GAP",
        "runtime_environment_gap": primary_owner == "RUNTIME_ENVIRONMENT_GAP",
        "source_evidence_present_blocked_count": source_present_count,
        "source_evidence_missing_blocked_count": source_missing_count,
        "source_evidence_sufficiency": source_sufficiency,
    }


def _reason_details(
    attempts: list[dict[str, Any]],
    *,
    limit: int = 20,
) -> tuple[list[dict[str, Any]], list[str]]:
    counts = Counter(
        _text(item.get("reason_code"))
        for item in attempts
        if _text(item.get("reason_code"))
    )
    rows: list[dict[str, Any]] = []
    unregistered: list[str] = []
    for reason, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        profile = profile_reason_code(reason)
        if profile.get("registry_status") == "UNREGISTERED":
            unregistered.append(reason)
        examples: list[dict[str, Any]] = []
        for attempt in attempts:
            if _text(attempt.get("reason_code")) != reason:
                continue
            examples.append({
                "obligation_id": _text(attempt.get("obligation_id")),
                "risk_family": _text(attempt.get("risk_family")),
                "terminal_stage": _text(attempt.get("terminal_stage")),
                "terminal_status": _text(attempt.get("terminal_status")),
                "operation_refs": [
                    _text(value)[:240]
                    for value in _list(attempt.get("operation_refs"))
                    if _text(value)
                ][:8],
                "actor_refs": [
                    _text(value)[:240]
                    for value in _list(attempt.get("actor_refs"))
                    if _text(value)
                ][:8],
                "source_refs": _safe_source_refs(attempt),
                "reason_detail": _attempt_reason_detail(attempt)[:500],
            })
            if len(examples) >= 3:
                break
        family = _text(profile.get("reason_family")) or "UNREGISTERED"
        reason_attempts = [
            attempt
            for attempt in attempts
            if _text(attempt.get("reason_code")) == reason
        ]
        rows.append({
            "reason": reason,
            "count": count,
            "reason_family": family,
            "registry_status": _text(profile.get("registry_status")),
            "recoverability": _text(profile.get("recoverability")),
            "is_blocking": bool(profile.get("is_blocking")),
            "must_remain_blocked": bool(profile.get("must_remain_blocked")),
            "customer_materials_needed": list(_REASON_MATERIALS.get(family, [])),
            "loss_attribution": _loss_attribution(
                reason_attempts,
                family=family,
                profile=profile,
            ),
            "examples": examples,
        })
    blocking = [row for row in rows if row.get("is_blocking")]
    return blocking[:limit], sorted(set(unregistered))


def _attempt_reason_detail(attempt: dict[str, Any]) -> str:
    """Project captured Oracle failure detail when an older Gate omitted it.

    Gate ``reason_detail`` was added after some immutable attempt ledgers were
    emitted.  The evidence bundle still contains the validated Oracle and
    activation reason codes, so the report may expose those exact values as a
    diagnostic fallback.  No business meaning is inferred from the detail.
    """

    explicit = _text(attempt.get("reason_detail"))
    if explicit:
        return explicit
    terminal_reason = _text(attempt.get("reason_code"))
    for stage in _list(attempt.get("stages")):
        stage_value = _dict(stage)
        if (
            _text(stage_value.get("reason_code")) == terminal_reason
            and _text(stage_value.get("reason_detail"))
        ):
            return _text(stage_value.get("reason_detail"))
    if _text(attempt.get("reason_code")).upper() != "CONTRACT_ORACLE_HARNESS_FAILED":
        return ""
    bundle = _dict(attempt.get("delivery_evidence_bundle"))
    oracle = _dict(bundle.get("oracle_receipt"))
    return _oracle_harness_reason_detail(oracle)


def _source_flow_projection(result: dict[str, Any]) -> dict[str, Any]:
    """Return the source-to-obligation receipt, or expose its absence."""

    nested = _dict(result.get("v12"))
    receipt = result.get("knowledge_source_flow_receipt")
    if not isinstance(receipt, dict):
        receipt = nested.get("knowledge_source_flow_receipt")
    if isinstance(receipt, dict) and receipt:
        return dict(receipt)
    return {
        "schema_version": "qualibug.discovery-source-flow-receipt.v1",
        "authority": (
            "enterprise_business_knowledge_asset -> enterprise_understanding_model "
            "-> Behavior IR -> formal obligations"
        ),
        "status": "NOT_MEASURED",
        "reason": "knowledge_source_flow_receipt_missing",
        "missing_evidence": ["knowledge_source_flow_receipt"],
        "issues": [],
    }


def _conversion_rate(
    *,
    numerator: Any,
    denominator: Any,
    name: str,
    definition: str,
) -> dict[str, Any]:
    """Calculate a receipt-backed stage rate without inventing zeroes."""

    if not isinstance(numerator, int) or isinstance(numerator, bool):
        return {
            "name": name,
            "status": "NOT_MEASURED",
            "numerator_count": None,
            "denominator_count": None,
            "rate": None,
            "definition": definition,
            "reason": "count_missing",
        }
    if not isinstance(denominator, int) or isinstance(denominator, bool):
        return {
            "name": name,
            "status": "NOT_MEASURED",
            "numerator_count": numerator,
            "denominator_count": None,
            "rate": None,
            "definition": definition,
            "reason": "denominator_missing",
        }
    if numerator < 0 or denominator < 0 or numerator > denominator:
        return {
            "name": name,
            "status": "FAILED_SAFE",
            "numerator_count": numerator,
            "denominator_count": denominator,
            "rate": None,
            "definition": definition,
            "reason": "count_out_of_range",
        }
    if denominator == 0:
        return {
            "name": name,
            "status": "NOT_MEASURED",
            "numerator_count": numerator,
            "denominator_count": denominator,
            "rate": None,
            "definition": definition,
            "reason": "zero_denominator",
        }
    return {
        "name": name,
        "status": "MEASURED",
        "numerator_count": numerator,
        "denominator_count": denominator,
        "rate": round(numerator / denominator, 6),
        "definition": definition,
    }


def _build_conversion_rates(
    conservation: dict[str, Any],
) -> dict[str, Any]:
    """Build the stage-rate projection from conservation-owned counts."""

    generated = conservation.get("generated_count")
    selected = conservation.get("selected_count")
    compiled = conservation.get("compile_success_count")
    executed = conservation.get("execution_count")
    oracle = conservation.get("oracle_count")
    violations = conservation.get("oracle_violation_count")
    deliverable = conservation.get("customer_deliverable_finding_count")
    rates = [
        _conversion_rate(
            numerator=selected,
            denominator=generated,
            name="generated_to_selected",
            definition="selected obligations / generated formal obligations",
        ),
        _conversion_rate(
            numerator=compiled,
            denominator=selected,
            name="selected_to_compiled",
            definition="compiled obligations / selected obligations",
        ),
        _conversion_rate(
            numerator=executed,
            denominator=selected,
            name="selected_to_executed",
            definition="executed obligations / selected obligations",
        ),
        _conversion_rate(
            numerator=executed,
            denominator=compiled,
            name="compiled_to_executed",
            definition="executed obligations / compiled obligations",
        ),
        _conversion_rate(
            numerator=oracle,
            denominator=executed,
            name="executed_to_oracle",
            definition="oracle-receipted executions / executed obligations",
        ),
        _conversion_rate(
            numerator=deliverable,
            denominator=violations,
            name="oracle_violation_to_customer_deliverable",
            definition="customer-deliverable gate outcomes / oracle violations",
        ),
    ]
    status = "PASS"
    if any(_text(row.get("status")).upper() == "FAILED_SAFE" for row in rates):
        status = "FAILED_SAFE"
    elif any(_text(row.get("status")).upper() == "NOT_MEASURED" for row in rates):
        status = "NOT_MEASURED"
    return {
        "schema_version": "qualibug.discovery-funnel-conversion-rates.v1",
        "authority": "qualibug.obligation-attempt-ledger.v1",
        "status": status,
        "rates": rates,
    }


def build_funnel_report(
    v12_result: dict[str, Any],
    *,
    funnel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a redacted JSON/Markdown-ready funnel loss report.

    The report is a projection of attempt, formal-delivery and conservation
    receipts. It never reads evaluator-private ground truth and never turns
    internal counts into precision, recall or a defect-free claim.
    """

    result = _dict(v12_result)
    current_funnel = _dict(funnel) or _dict(result.get("discovery_funnel"))
    if not current_funnel:
        current_funnel = build_funnel(result)
    ledger = _attempt_ledger(result)
    attempts = [_dict(item) for item in _list(ledger.get("attempts"))]
    # Recompute from the immutable receipts even when a caller supplies a
    # prebuilt funnel.  Stage projections are useful for presentation, but the
    # report's counts must have one authority and must not inherit stale or
    # inferred values from a compatibility projection.
    formal = _formal_projection(result)
    conservation = _build_funnel_conservation(
        result,
        ledger,
        formal,
    )
    pipeline_health = build_pipeline_health(result)
    blockers, unregistered = _reason_details(attempts)
    source_flow = _source_flow_projection(result)
    conversion_rates = _build_conversion_rates(conservation)
    source_flow_status = _text(source_flow.get("status")).upper()
    nested = _dict(result.get("v12"))
    discovery_separation = _dict(
        result.get("business_discovery_separation")
        or nested.get("business_discovery_separation")
    )
    formal_identity_receipt = _dict(
        _dict(
            result.get("test_obligations")
            or nested.get("test_obligations")
        ).get("obligation_identity_receipt")
    )
    mainline = _dict(result.get("mainline_run") or nested.get("mainline_run"))
    run_conditions = _run_condition_receipt(result, mainline)
    external = _dict(
        result.get("external_evaluation") or nested.get("external_evaluation")
    )
    externally_measured = (
        external.get("receipt_verified") is True
        and _text(external.get("status")).upper() == "MEASURED"
    )
    quality_status = "MEASURED" if externally_measured else "NOT_MEASURED"
    report: dict[str, Any] = {
        "schema_version": "qualibug.discovery-funnel-report.v1",
        "report_status": (
            "FAILED_SAFE"
            if unregistered
            or _text(conservation.get("status")).upper() == "FAILED_SAFE"
            or source_flow_status == "FAILED_SAFE"
            or _text(conversion_rates.get("status")).upper() == "FAILED_SAFE"
            else "BLOCKED"
            if source_flow_status == "BLOCKED"
            else "INCOMPLETE"
            if _text(conservation.get("status")).upper() == "INCOMPLETE"
            or source_flow_status in {"INCOMPLETE", "NOT_MEASURED"}
            else "READY"
        ),
        "mainline_identity": {
            key: _text(mainline.get(key))
            for key in (
                "run_id",
                "campaign_id",
                "target_id",
                "environment_id",
                "source_snapshot_hash",
                "policy_version",
                "evaluation_mode",
                "contract_fingerprint",
            )
            if _text(mainline.get(key))
        },
        "run_conditions": run_conditions,
        "ledger_identity": {
            key: value
            for key, value in _dict(ledger.get("identity")).items()
            if key != "missing_fields"
        },
        "quality": {
            "status": quality_status,
            "recall": "NOT_MEASURED",
            "precision": "NOT_MEASURED",
            "basis": (
                "authenticated_external_evaluator_receipt"
                if externally_measured
                else "no_authenticated_external_evaluator_receipt"
            ),
        },
        "metrics": {
            "generated_count": conservation.get("generated_count"),
            "not_selected_count": conservation.get("not_selected_count"),
            "selected_count": conservation.get("selected_count"),
            "terminal_count": conservation.get("terminal_count"),
            "compiled_count": conservation.get("compile_success_count"),
            "compile_blocked_count": conservation.get("compile_blocked_count"),
            "compile_deferred_count": conservation.get("compile_deferred_count"),
            "pre_execution_blocked_count": conservation.get(
                "pre_execution_blocked_count"
            ),
            "executed_count": conservation.get("execution_count"),
            "execution_blocked_count": conservation.get("execution_blocked_count"),
            "execution_harness_failure_count": conservation.get(
                "execution_harness_failure_count"
            ),
            "oracle_count": conservation.get("oracle_count"),
            "oracle_resolved_count": conservation.get("oracle_resolved_count"),
            "oracle_pass_count": conservation.get("oracle_pass_count"),
            "oracle_violation_count": conservation.get("oracle_violation_count"),
            "oracle_indeterminate_count": conservation.get(
                "oracle_indeterminate_count"
            ),
            "delivery_gate_blocked_finding_count": conservation.get(
                "delivery_gate_blocked_finding_count"
            ),
            "formal_delivery_count": formal.get(
                "formal_customer_deliverable_count"
            ),
            "delivery_occurrence_count": formal.get(
                "delivery_occurrence_count"
            ),
        },
        "pipeline_health": pipeline_health,
        "conservation": conservation,
        "conversion_rates": conversion_rates,
        "source_flow": source_flow,
        "formal_obligation_identity_receipt": formal_identity_receipt,
        "discovery_task_summary": _dict(
            discovery_separation.get("discovery_task_summary")
        ),
        "reason_registry": {
            "schema_version": REASON_CODE_REGISTRY_SCHEMA,
            "status": "FAILED_SAFE" if unregistered else "PASS",
            "unregistered_reason_codes": unregistered,
        },
        "top_blocking_reasons": blockers,
        "unresolved_top_10": blockers[:10],
        "receipt_authority": "qualibug.obligation-attempt-ledger.v1",
    }
    from .artifact_redactor import redact_and_validate

    redacted, _ = redact_and_validate(report)
    return redacted


def render_funnel_report_markdown(report: dict[str, Any]) -> str:
    """Render the already-redacted funnel report for operators."""

    value = _dict(report)
    metrics = _dict(value.get("metrics"))
    quality = _dict(value.get("quality"))
    conservation = _dict(value.get("conservation"))
    conversion_rates = _dict(value.get("conversion_rates"))
    source_flow = _dict(value.get("source_flow"))

    def display_metric(raw: Any) -> str:
        return "NOT_MEASURED" if raw is None or raw == "" else str(raw)

    lines = [
        "# Discovery Funnel Report",
        "",
        f"- Report status: `{_text(value.get('report_status')) or 'UNKNOWN'}`",
        f"- Quality status: `{_text(quality.get('status')) or 'NOT_MEASURED'}`",
        f"- Receipt authority: `{_text(value.get('receipt_authority'))}`",
        "",
        "## Funnel counts",
        "",
        "| Stage | Count |",
        "| --- | ---: |",
        f"| Generated | {display_metric(metrics.get('generated_count'))} |",
        f"| Not selected | {display_metric(metrics.get('not_selected_count'))} |",
        f"| Selected | {display_metric(metrics.get('selected_count'))} |",
        f"| Terminal | {display_metric(metrics.get('terminal_count'))} |",
        f"| Compiled | {display_metric(metrics.get('compiled_count'))} |",
        f"| Compile blocked | {display_metric(metrics.get('compile_blocked_count'))} |",
        f"| Pre-execution blocked | {display_metric(metrics.get('pre_execution_blocked_count'))} |",
        f"| Executed | {display_metric(metrics.get('executed_count'))} |",
        f"| Execution blocked | {display_metric(metrics.get('execution_blocked_count'))} |",
        f"| Oracle resolved | {display_metric(metrics.get('oracle_resolved_count'))} |",
        f"| Oracle violations | {display_metric(metrics.get('oracle_violation_count'))} |",
        f"| Oracle indeterminate | {display_metric(metrics.get('oracle_indeterminate_count'))} |",
        f"| Delivery-gate blocked findings | {display_metric(metrics.get('delivery_gate_blocked_finding_count'))} |",
        f"| Formal delivery | {display_metric(metrics.get('formal_delivery_count'))} |",
        f"| Delivery occurrences | {display_metric(metrics.get('delivery_occurrence_count'))} |",
        "",
        "## Stage conversion rates",
        "",
        "| Conversion | Rate | Status |",
        "| --- | ---: | --- |",
    ]
    for rate in _list(conversion_rates.get("rates")):
        row = _dict(rate)
        raw_rate = row.get("rate")
        display_rate = (
            "NOT_MEASURED"
            if raw_rate is None
            else f"{float(raw_rate) * 100:.2f}%"
        )
        lines.append(
            f"| {_text(row.get('name'))} | {display_rate} | "
            f"{_text(row.get('status')) or 'UNKNOWN'} |"
        )
    runtime_node_counts = _dict(_dict(source_flow.get("runtime_behavior_ir")).get("node_counts"))
    lines.extend([
        "",
        "## Source flow",
        "",
        f"- Status: `{_text(source_flow.get('status')) or 'NOT_MEASURED'}`",
        f"- Source materials: `{display_metric(_dict(source_flow.get('source_materials')).get('canonical_source_count'))}`",
        f"- Business facts: `{display_metric(_dict(source_flow.get('business_facts')).get('observed_row_count'))}`",
        f"- Enterprise Behavior IR nodes: `{display_metric(_dict(source_flow.get('enterprise_behavior_ir')).get('behavior_node_count'))}`",
        f"- Runtime Behavior IR operations: `{display_metric(runtime_node_counts.get('operations'))}`",
        f"- Formal obligations: `{display_metric(_dict(source_flow.get('formal_obligations')).get('formal_obligation_count'))}`",
        f"- Missing evidence: `{', '.join(_text(v) for v in _list(source_flow.get('missing_evidence')) if _text(v)) or 'none'}`",
        "",
        "## Conservation",
        "",
        f"- Status: `{_text(conservation.get('status')) or 'UNKNOWN'}`",
        f"- Identity status: `{_text(conservation.get('identity_status')) or 'UNKNOWN'}`",
        f"- Identity stage gaps: `{len(_list(conservation.get('identity_stage_gaps')))}`",
        f"- Attempt identity gaps: `{len(_list(conservation.get('attempt_identity_gaps')))}`",
        f"- Missing evidence: `{', '.join(_text(v) for v in _list(conservation.get('missing_evidence')) if _text(v)) or 'none'}`",
        "",
        "## Top blocking reasons",
        "",
    ])
    blockers = [_dict(row) for row in _list(value.get("top_blocking_reasons"))]
    if not blockers:
        lines.append("No blocking reason receipt was recorded.")
    for row in blockers:
        attribution = _dict(row.get("loss_attribution"))
        lines.extend([
            f"### `{_text(row.get('reason'))}` ({_int(row.get('count'))})",
            "",
            f"- Family: `{_text(row.get('reason_family'))}`",
            f"- Registry: `{_text(row.get('registry_status'))}`",
            f"- Recoverability: `{_text(row.get('recoverability'))}`",
            f"- Primary loss owner: `{_text(attribution.get('primary_owner')) or 'UNKNOWN'}`",
            f"- Source evidence present on blocked attempts: `{display_metric(attribution.get('source_evidence_present_blocked_count'))}`",
            f"- Source evidence sufficiency: `{_text(attribution.get('source_evidence_sufficiency')) or 'NOT_MEASURED'}`",
            f"- Customer materials needed: {', '.join(_text(v) for v in _list(row.get('customer_materials_needed')) if _text(v)) or 'none recorded'}",
            "",
        ])
        for example in _list(row.get("examples"))[:3]:
            item = _dict(example)
            lines.append(
                "- Example "
                f"`{_text(item.get('obligation_id'))}`: "
                f"{_text(item.get('reason_detail')) or 'no detail recorded'}"
            )
        lines.append("")
    lines.extend([
        "## Quality boundary",
        "",
        "Internal funnel counts are diagnostic only. Recall and precision remain `NOT_MEASURED` until an authenticated external evaluator receipt is verified.",
        "",
    ])
    return "\n".join(lines)


def write_funnel_report_files(
    v12_result: dict[str, Any],
    output_dir: Path | str,
    *,
    funnel: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Persist redacted JSON and Markdown reports through the artifact boundary."""

    report = build_funnel_report(v12_result, funnel=funnel)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "discovery_funnel_report.json"
    markdown_path = target_dir / "discovery_funnel_report.md"
    from .artifact_redactor import redact_and_validate, write_json_redacted

    write_json_redacted(json_path, report)
    redacted, _ = redact_and_validate(report)
    markdown_path.write_text(
        render_funnel_report_markdown(redacted),
        encoding="utf-8",
    )
    return {
        "json": str(json_path),
        "markdown": str(markdown_path),
    }


_COMPARISON_METRICS = (
    "generated_count",
    "not_selected_count",
    "selected_count",
    "terminal_count",
    "compiled_count",
    "compile_blocked_count",
    "pre_execution_blocked_count",
    "executed_count",
    "execution_blocked_count",
    "oracle_count",
    "oracle_resolved_count",
    "oracle_violation_count",
    "oracle_indeterminate_count",
    "delivery_gate_blocked_finding_count",
    "formal_delivery_count",
    "delivery_occurrence_count",
)

_COMPARISON_CONDITION_FIELDS = (
    "target_id",
    "environment_id",
    "source_snapshot_hash",
    "policy_version",
    "evaluation_mode",
    "execution_mode",
    "budget_configured",
    "budget_effective",
    "model_provider",
    "model_id",
)


def _run_condition_receipt(
    result: dict[str, Any],
    mainline: dict[str, Any],
) -> dict[str, Any]:
    """Project explicit conditions needed for a valid replay comparison.

    Target/source/evaluation identity comes from the immutable mainline
    contract. Execution mode and planning budget come from their named runtime
    receipts. Model identity must be supplied by an explicit run-condition
    receipt; a Behavior IR content id is not a provider/model execution
    identity and is therefore never substituted here.
    """

    nested = _dict(result.get("v12"))
    values: dict[str, Any] = {}
    conflicts: list[dict[str, Any]] = []
    invalid_fields: list[str] = []

    def add_value(field: str, value: Any, source: str) -> None:
        if field in {
            "budget_configured",
            "budget_effective",
        }:
            if value is None:
                return
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                invalid_fields.append(field)
                return
            normalized: Any = value
        else:
            normalized = _text(value)
            if not normalized:
                return
        if field in values and values[field] != normalized:
            conflicts.append({
                "field": field,
                "existing": values[field],
                "incoming": normalized,
                "source": source,
            })
            return
        values[field] = normalized

    for field in _COMPARISON_CONDITION_FIELDS[:5]:
        add_value(field, mainline.get(field), "mainline_run")

    explicit_sources = [
        ("run_conditions", result.get("run_conditions")),
        ("v12.run_conditions", nested.get("run_conditions")),
    ]
    for source, raw in explicit_sources:
        conditions = _dict(raw)
        for field in _COMPARISON_CONDITION_FIELDS:
            add_value(field, conditions.get(field), source)
        budget = _dict(conditions.get("budget"))
        add_value(
            "budget_configured",
            budget.get("configured_budget"),
            f"{source}.budget",
        )
        add_value(
            "budget_effective",
            budget.get("effective_budget"),
            f"{source}.budget",
        )
        model = _dict(conditions.get("model"))
        add_value("model_provider", model.get("provider"), f"{source}.model")
        add_value("model_id", model.get("id"), f"{source}.model")

    for source, raw in (
        ("runtime_contract", result.get("runtime_contract")),
        ("v12.runtime_contract", nested.get("runtime_contract")),
    ):
        runtime_contract = _dict(raw)
        add_value(
            "execution_mode",
            runtime_contract.get("execution_mode"),
            source,
        )

    for source, raw in (
        ("planning_budget_receipt", result.get("planning_budget_receipt")),
        ("v12.planning_budget_receipt", nested.get("planning_budget_receipt")),
    ):
        budget_receipt = _dict(raw)
        add_value(
            "budget_configured",
            budget_receipt.get("configured_budget"),
            source,
        )
        add_value(
            "budget_effective",
            budget_receipt.get("effective_budget"),
            source,
        )

    missing_fields = [
        field for field in _COMPARISON_CONDITION_FIELDS if field not in values
    ]
    status = (
        "FAILED_SAFE"
        if conflicts or invalid_fields
        else "INCOMPLETE"
        if missing_fields
        else "PASS"
    )
    return {
        "schema_version": "qualibug.discovery-run-conditions.v1",
        "status": status,
        "values": values,
        "missing_fields": missing_fields,
        "invalid_fields": sorted(set(invalid_fields)),
        "conflicts": conflicts,
    }


def _comparison_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    metrics = _dict(report.get("metrics"))
    health = _dict(report.get("pipeline_health"))
    return {
        "report_status": _text(report.get("report_status")),
        "mainline_identity": _dict(report.get("mainline_identity")),
        "run_conditions": _dict(report.get("run_conditions")),
        "quality": _dict(report.get("quality")),
        "metrics": {
            name: metrics.get(name)
            for name in _COMPARISON_METRICS
        },
        "pipeline_health": {
            "status": _text(health.get("status")),
            "funnel_conservation_status": _text(
                health.get("funnel_conservation_status")
            ),
            "funnel_conservation_complete": health.get(
                "funnel_conservation_complete"
            ),
        },
        "reason_registry": _dict(report.get("reason_registry")),
        "top_blocking_reasons": [
            {
                key: row.get(key)
                for key in (
                    "reason",
                    "count",
                    "reason_family",
                    "registry_status",
                )
            }
            for row in _list(report.get("top_blocking_reasons"))
            if isinstance(row, dict)
        ],
        "unresolved_top_10": [
            _text(_dict(row).get("reason"))
            for row in _list(report.get("unresolved_top_10"))
            if _text(_dict(row).get("reason"))
        ],
    }


def _comparison_condition_check(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    baseline_receipt = _dict(baseline.get("run_conditions"))
    candidate_receipt = _dict(candidate.get("run_conditions"))
    baseline_values = _dict(baseline_receipt.get("values"))
    candidate_values = _dict(candidate_receipt.get("values"))
    missing_fields: list[str] = []
    mismatches: list[dict[str, Any]] = []
    for field in _COMPARISON_CONDITION_FIELDS:
        before = baseline_values.get(field)
        after = candidate_values.get(field)
        if before is None or before == "" or after is None or after == "":
            missing_fields.append(field)
        elif before != after:
            mismatches.append({"field": field, "baseline": before, "candidate": after})
    invalid_fields = sorted(set(
        _list(baseline_receipt.get("invalid_fields"))
        + _list(candidate_receipt.get("invalid_fields"))
    ))
    conflicts = [
        *_list(baseline_receipt.get("conflicts")),
        *_list(candidate_receipt.get("conflicts")),
    ]
    if invalid_fields or conflicts:
        status = "FAILED_SAFE"
    elif missing_fields:
        status = "NOT_MEASURED"
    elif mismatches:
        status = "MISMATCH"
    else:
        status = "MATCH"
    return {
        "status": status,
        "checked_fields": list(_COMPARISON_CONDITION_FIELDS),
        "missing_fields": missing_fields,
        "mismatches": mismatches,
        "invalid_fields": invalid_fields,
        "conflicts": conflicts,
    }


def build_funnel_comparison_report(
    baseline_result: dict[str, Any],
    candidate_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compare two receipt-backed funnel reports without inventing a candidate.

    A missing candidate is an explicit ``NOT_MEASURED`` state. Numeric deltas
    are emitted only when both sides contain integer receipt metrics; this
    report never converts funnel deltas into recall, precision, or promotion.
    """

    baseline = build_funnel_report(baseline_result)
    baseline_snapshot = _comparison_snapshot(baseline)
    if candidate_result is None:
        candidate_snapshot = {
            "report_status": "NOT_MEASURED",
            "quality": {
                "status": "NOT_MEASURED",
                "recall": "NOT_MEASURED",
                "precision": "NOT_MEASURED",
                "basis": "candidate_receipt_missing",
            },
            "metrics": None,
            "pipeline_health": None,
            "run_conditions": {
                "schema_version": "qualibug.discovery-run-conditions.v1",
                "status": "NOT_MEASURED",
                "values": {},
                "missing_fields": list(_COMPARISON_CONDITION_FIELDS),
                "invalid_fields": [],
                "conflicts": [],
            },
            "reason_registry": None,
            "top_blocking_reasons": [],
            "unresolved_top_10": [],
        }
        delta: dict[str, Any] = {
            "status": "NOT_MEASURED",
            "reason": "candidate_receipt_missing",
            "metrics": "NOT_MEASURED",
        }
        comparison_status = "NOT_MEASURED"
    else:
        candidate = build_funnel_report(candidate_result)
        candidate_snapshot = _comparison_snapshot(candidate)
        delta_metrics: dict[str, Any] = {}
        for name in _COMPARISON_METRICS:
            before = baseline_snapshot["metrics"].get(name)
            after = candidate_snapshot["metrics"].get(name)
            if (
                isinstance(before, int)
                and not isinstance(before, bool)
                and isinstance(after, int)
                and not isinstance(after, bool)
            ):
                delta_metrics[name] = after - before
            else:
                delta_metrics[name] = "NOT_MEASURED"
        delta = {
            "status": "RECEIPT_COMPARISON",
            "metrics": delta_metrics,
            "quality": "NOT_MEASURED",
            "reason": "external_quality_must_be_verified_separately",
        }
        comparison_status = "RECEIPT_COMPARISON"

    condition_check = (
        _comparison_condition_check(baseline_snapshot, candidate_snapshot)
        if candidate_result is not None
        else {
            "status": "NOT_MEASURED",
            "checked_fields": list(_COMPARISON_CONDITION_FIELDS),
            "missing_fields": list(_COMPARISON_CONDITION_FIELDS),
            "mismatches": [],
            "invalid_fields": [],
            "conflicts": [],
        }
    )

    report = {
        "schema_version": "qualibug.discovery-funnel-comparison.v1",
        "status": comparison_status,
        "receipt_authority": "qualibug.obligation-attempt-ledger.v1",
        "baseline": baseline_snapshot,
        "candidate": candidate_snapshot,
        "delta": delta,
        "condition_check": condition_check,
        "quality_boundary": {
            "recall": "NOT_MEASURED",
            "precision": "NOT_MEASURED",
            "promotion": "NOT_MEASURED",
            "reason": (
                "An authenticated evaluator comparison and matching run "
                "conditions are required before any commercial claim."
            ),
        },
    }
    from .artifact_redactor import redact_and_validate

    redacted, _ = redact_and_validate(report)
    return redacted


def render_funnel_comparison_report_markdown(report: dict[str, Any]) -> str:
    """Render a redacted baseline/candidate comparison for operators."""

    value = _dict(report)
    baseline = _dict(value.get("baseline"))
    candidate = _dict(value.get("candidate"))
    delta = _dict(value.get("delta"))
    condition_check = _dict(value.get("condition_check"))

    def display_metric(raw: Any) -> str:
        return "NOT_MEASURED" if raw is None or raw == "" else str(raw)

    lines = [
        "# Discovery Funnel Baseline / Candidate Comparison",
        "",
        f"- Status: `{_text(value.get('status')) or 'NOT_MEASURED'}`",
        f"- Receipt authority: `{_text(value.get('receipt_authority'))}`",
        "",
        "## Receipt metrics",
        "",
        "| Metric | Baseline | Candidate | Delta |",
        "| --- | ---: | ---: | ---: |",
    ]
    baseline_metrics = _dict(baseline.get("metrics"))
    candidate_metrics = _dict(candidate.get("metrics"))
    delta_metrics = _dict(delta.get("metrics"))
    if not candidate_metrics:
        candidate_metrics = {name: "NOT_MEASURED" for name in _COMPARISON_METRICS}
    if not delta_metrics:
        delta_metrics = {name: "NOT_MEASURED" for name in _COMPARISON_METRICS}
    for name in _COMPARISON_METRICS:
        lines.append(
            f"| {name} | {display_metric(baseline_metrics.get(name))} | "
            f"{display_metric(candidate_metrics.get(name))} | "
            f"{display_metric(delta_metrics.get(name))} |"
        )
    lines.extend([
        "",
        "## Run-condition check",
        "",
        f"- Status: `{_text(condition_check.get('status')) or 'NOT_MEASURED'}`",
        f"- Missing fields: {', '.join(_text(item) for item in _list(condition_check.get('missing_fields'))) or 'none'}",
        f"- Mismatches: {', '.join(_text(_dict(item).get('field')) for item in _list(condition_check.get('mismatches'))) or 'none'}",
        f"- Invalid fields: {', '.join(_text(item) for item in _list(condition_check.get('invalid_fields'))) or 'none'}",
        f"- Conflicting receipts: {len(_list(condition_check.get('conflicts')))}",
        "",
        "## Unresolved top 10",
        "",
        "- Baseline: "
        + (", ".join(_text(v) for v in _list(baseline.get("unresolved_top_10"))) or "none recorded"),
        "- Candidate: "
        + (", ".join(_text(v) for v in _list(candidate.get("unresolved_top_10"))) or "NOT_MEASURED"),
        "",
        "## Quality boundary",
        "",
        "Recall, precision, and promotion remain `NOT_MEASURED`; funnel deltas are diagnostic receipt counts only.",
        "",
    ])
    return "\n".join(lines)


def write_funnel_comparison_report_files(
    baseline_result: dict[str, Any],
    output_dir: Path | str,
    *,
    candidate_result: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Persist a redacted baseline/candidate comparison report."""

    report = build_funnel_comparison_report(
        baseline_result,
        candidate_result=candidate_result,
    )
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    json_path = target_dir / "discovery_funnel_comparison.json"
    markdown_path = target_dir / "discovery_funnel_comparison.md"
    from .artifact_redactor import redact_and_validate, write_json_redacted

    write_json_redacted(json_path, report)
    redacted, _ = redact_and_validate(report)
    markdown_path.write_text(
        render_funnel_comparison_report_markdown(redacted),
        encoding="utf-8",
    )
    return {"json": str(json_path), "markdown": str(markdown_path)}


def build_funnel(
    v12_result: dict[str, Any],
    gate_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Project required stage loss from attempt receipts; legacy gate rows are ignored."""

    del gate_results
    result = _dict(v12_result)
    ledger = _attempt_ledger(result)
    attempts = [_dict(item) for item in _list(ledger.get("attempts"))]
    formal = _formal_projection(result)
    stages = [
        _stage_projection(attempts, stage_name)
        for stage_name in REQUIRED_STAGE_NAMES
    ]
    reasons = Counter(
        _text(item.get("reason_code")) for item in attempts if _text(item.get("reason_code"))
    )
    top_reasons = [
        {"reason": reason, "count": count}
        for reason, count in sorted(reasons.items(), key=lambda item: (-item[1], item[0]))
    ]
    rejected_pending = sum(
        1
        for item in attempts
        if _text(item.get("terminal_status")).upper() == "REJECTED"
        and _text(item.get("reason_code")).upper() != "ORACLE_NOT_VIOLATED"
    )
    health = build_pipeline_health(result)
    conservation = _dict(health.get("funnel_conservation"))
    blocking_reason_details, unregistered_reason_codes = _reason_details(attempts)
    return {
        "schema_version": "qualibug.discovery-funnel.v2",
        "stages": stages,
        "top_blocking_reasons": top_reasons,
        "top_blocking_reason_details": blocking_reason_details,
        "reason_registry": {
            "schema_version": REASON_CODE_REGISTRY_SCHEMA,
            "status": "FAILED_SAFE" if unregistered_reason_codes else "PASS",
            "unregistered_reason_codes": unregistered_reason_codes,
        },
        "conservation": conservation,
        "validated_bug_count": formal["formal_customer_deliverable_count"],
        "canonical_defect_count": formal["formal_customer_deliverable_count"],
        "canonical_defect_ids": list(formal["canonical_defect_ids"]),
        "delivery_occurrence_count": formal["delivery_occurrence_count"],
        "delivery_occurrence_finding_ids": list(
            formal["delivery_occurrence_finding_ids"]
        ),
        "pending_finding_count": rejected_pending,
        "candidate_count": _int(ledger.get("selected_count")),
        "explanation": (
            f"{_int(ledger.get('selected_count'))} selected obligations, "
            f"{_int(ledger.get('terminal_count'))} terminal outcomes, "
            f"{formal['formal_customer_deliverable_count']} formal deliverables; "
            f"pipeline health {health['status']}; all counts come from immutable "
            "attempt and formal projection receipts."
        ),
        "pipeline_health": health,
        "receipt_authority": "obligation_attempt_ledger",
    }


def reconcile_pipeline_health_after_campaign_cleanup(
    pipeline_health: dict[str, Any] | None,
    *,
    findings: list[dict[str, Any]],
    scenario_cleanup_failures_recovered: int = 0,
    environment_restored: bool = False,
    original_cleanup_failures: int | None = None,
) -> dict[str, Any]:
    """Record a global reset without erasing original per-attempt cleanup failures."""

    del scenario_cleanup_failures_recovered
    health = dict(pipeline_health or {})
    residual = sum(
        1
        for finding in findings
        if isinstance(finding, dict)
        and _text(
            _dict(finding.get("cleanup") or _dict(finding.get("evidence")).get("cleanup")).get("status")
            or finding.get("cleanup_status")
        ).upper()
        in {
            "FAILED",
            "CLEANUP_INCOMPLETE",
            "CLEANUP_NOT_SUCCEEDED",
            "INCOMPLETE",
            "NOT_REVERSIBLE",
        }
    )
    preserved = (
        _int(original_cleanup_failures)
        if original_cleanup_failures is not None
        else max(_int(health.get("cleanup_failure_count")), residual)
    )
    health["cleanup_failure_count"] = max(preserved, residual)
    health["environment_restored"] = bool(environment_restored)
    health["scenario_cleanup_failures_recovered_by_campaign_reset"] = 0
    health["campaign_cleanup_recovered"] = False
    if environment_restored:
        health["operator_note"] = (
            f"{_text(health.get('operator_note'))} Campaign reset restored the environment; "
            f"original cleanup_failure_count={health['cleanup_failure_count']} remains visible."
        ).strip()
    return health

# Discovery execution identity and source-flow projections stay with the funnel
# authority so the mainline execution module remains a thin authority wrapper.
_VARIANT_RE = _re.compile(r"^(.+?)__v_[a-f0-9]+$")
def _compiled_round0_obligation_ids(all_experiments: Any) -> set[str]:
    """Compatibility diagnostic for the historical compiled-only projection.

    Variant experiments (``obl_x__v_<digest>``) collapse to their base
    identity. This helper is not used by the product expansion path: round 2
    receives every immutable round-0 obligation identity, including compile-
    blocked rows, so a retry cannot create a duplicate formal obligation.
    """

    compiled: set[str] = set()
    for row in _list(all_experiments):
        if not isinstance(row, dict):
            continue
        if _text(_dict(row.get("compile_receipt")).get("status")).upper() != "COMPILED":
            continue
        obligation_id = _text(row.get("obligation_id"))
        if not obligation_id:
            continue
        match = _VARIANT_RE.match(obligation_id)
        compiled.add(match.group(1) if match else obligation_id)
    return compiled


def _runtime_recompile_round0_obligation_ids(
    obligations: Any,
    experiments_by_obligation: Any,
) -> set[str]:
    """Select only compile-blocked body bindings that runtime discovery can resolve.

    A runtime interface observation may reopen a round-0 compile terminal only
    when no target request was possible.  Other blockers (cleanup authority,
    missing fixtures, observers, or source request bodies) need their own
    source evidence and must remain blocked instead of being retried blindly.
    """

    experiments = _dict(experiments_by_obligation)
    retry_ids: set[str] = set()
    for obligation in _list(obligations):
        if not isinstance(obligation, dict):
            continue
        obligation_id = _text(obligation.get("obligation_id"))
        if not obligation_id:
            continue
        experiment = _dict(experiments.get(obligation_id))
        compile_receipt = _dict(experiment.get("compile_receipt"))
        if _text(compile_receipt.get("status")).upper() != "BLOCKED":
            continue
        if _text(compile_receipt.get("reason_code")) != "BLOCKED_MISSING_BINDING":
            continue
        detail = _text(
            compile_receipt.get("detail")
            or compile_receipt.get("reason_detail")
        )
        if "BODY_PARAMETER_NOT_SOURCE_BOUND" in detail:
            retry_ids.add(obligation_id)
    return retry_ids


def _formal_obligation_rows_and_identity_receipt(
    plan: Any,
    expansion: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Validate the final formal identity before any round-2 transport."""

    base_rows = [
        dict(row)
        for row in _list(plan.obligations.get("obligations"))
        if isinstance(row, dict)
    ]
    expansion_rows = [
        dict(row)
        for row in _list(expansion.get("delta_obligations"))
        if isinstance(row, dict)
    ]
    formal_rows = [*base_rows, *expansion_rows]
    base_ids = {
        _text(row.get("obligation_id"))
        for row in base_rows
        if _text(row.get("obligation_id"))
    }
    expansion_ids = {
        _text(row.get("obligation_id"))
        for row in expansion_rows
        if _text(row.get("obligation_id"))
    }
    all_id_values = [
        _text(row.get("obligation_id")) for row in formal_rows
    ]
    missing_formal_ids = sum(not value for value in all_id_values)
    duplicate_formal_ids = sorted(
        obligation_id
        for obligation_id, count in Counter(
            value for value in all_id_values if value
        ).items()
        if count > 1
    )
    expansion_overlap_ids = sorted(base_ids & expansion_ids)
    if missing_formal_ids or duplicate_formal_ids or expansion_overlap_ids:
        raise ValueError(
            "formal_obligation_identity_invalid:"
            f"missing={missing_formal_ids};"
            f"duplicates={','.join(duplicate_formal_ids[:20])};"
            f"expansion_overlap={','.join(expansion_overlap_ids[:20])}"
        )
    planning_identity_receipt = _dict(
        plan.obligations.get("obligation_identity_receipt")
    )
    return formal_rows, {
        "schema_version": "qualibug.obligation-identity-receipt.v1",
        "authority": "discovery_runtime_execution.formal_obligation_rows",
        "status": "PASS",
        "input_row_count": len(formal_rows),
        "unique_count": len(formal_rows),
        "duplicate_count": 0,
        "duplicate_ids": [],
        "missing_id_count": 0,
        "expansion_added_count": len(expansion_rows),
        "expansion_overlap_ids": [],
        "planning_receipt": planning_identity_receipt,
    }


def _build_knowledge_source_flow_receipt(
    *,
    plan: Any,
    behavior_ir: dict[str, Any],
    formal_obligation_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Project source-bound funnel counts without persisting source prose.

    The planning bundle keeps the enterprise knowledge asset private because it
    may contain customer material.  The product result still needs a durable
    explanation of how that material reached the executable funnel.  This
    receipt therefore carries only exact structural counts, identifiers and
    explicit gate statuses from the asset and the runtime Behavior IR.
    """

    def row_count(value: Any) -> int | None:
        return len(value) if isinstance(value, list) else None

    def integer(value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        return None

    asset = _dict(plan.experiments.get("_knowledge_asset"))
    receipt: dict[str, Any] = {
        "schema_version": "qualibug.discovery-source-flow-receipt.v1",
        "authority": (
            "enterprise_business_knowledge_asset -> enterprise_understanding_model "
            "-> Behavior IR -> formal obligations"
        ),
        "status": "NOT_MEASURED",
        "knowledge_asset": {},
        "source_materials": {},
        "business_facts": {},
        "enterprise_behavior_ir": {},
        "runtime_behavior_ir": {},
        "formal_obligations": {},
        "links": {},
        "missing_evidence": [],
        "issues": [],
    }
    if not asset:
        receipt["missing_evidence"].append("planning._knowledge_asset")
        receipt["reason"] = "knowledge_asset_not_available_in_planning_bundle"
        return receipt

    asset_id = _text(asset.get("asset_id"))
    model = _dict(asset.get("enterprise_understanding_model"))
    model_id = _text(model.get("model_id"))
    source_summary = _dict(model.get("source_summary"))
    asset_summary = _dict(asset.get("summary"))
    receipt["knowledge_asset"] = {
        "asset_id": asset_id,
        "enterprise_understanding_model_id": model_id,
        "source_snapshot_hash": _text(
            _dict(plan.mainline_run).get("source_snapshot_hash")
        ),
        "asset_gate_status": _text(_dict(model.get("gate")).get("status")),
        "business_comprehension_status": _text(
            asset_summary.get("business_comprehension_status")
        ),
    }
    if not asset_id:
        receipt["missing_evidence"].append("knowledge_asset.asset_id")
    if not model_id:
        receipt["missing_evidence"].append(
            "enterprise_understanding_model.model_id"
        )

    source_rows = asset.get("canonical_source_inventory")
    source_count = row_count(source_rows)
    declared_source_count = integer(source_summary.get("canonical_source_count"))
    if declared_source_count is None:
        declared_source_count = integer(asset_summary.get("canonical_source_count"))
    receipt["source_materials"] = {
        "status": "MEASURED" if source_count is not None else "NOT_MEASURED",
        "source_material_count": source_count,
        "canonical_source_count": source_count,
        "declared_canonical_source_count": declared_source_count,
        "active_source_count": integer(source_summary.get("active_source_count"))
        if integer(source_summary.get("active_source_count")) is not None
        else integer(asset_summary.get("active_source_count")),
        "parse_succeeded_count": integer(asset_summary.get("source_parse_succeeded")),
        "evidence_path": "canonical_source_inventory",
    }
    if source_count is None:
        receipt["missing_evidence"].append("canonical_source_inventory")
    elif declared_source_count is not None and source_count != declared_source_count:
        receipt["issues"].append(
            "canonical_source_count_mismatch:"
            f"observed={source_count};declared={declared_source_count}"
        )

    fact_ledger = _dict(asset.get("business_fact_ledger"))
    fact_rows = fact_ledger.get("items")
    fact_count = row_count(fact_rows)
    fact_ids = [
        _text(_dict(row).get("fact_id"))
        for row in _list(fact_rows)
        if isinstance(row, dict)
    ]
    fact_id_count = sum(bool(value) for value in fact_ids)
    unique_fact_id_count = len({value for value in fact_ids if value})
    compilation = _dict(asset.get("structure_first_business_fact_compilation_receipt"))
    final_fact_count = integer(compilation.get("final_fact_count"))
    exact_evidence_fact_count = integer(compilation.get("exact_evidence_fact_count"))
    receipt["business_facts"] = {
        "status": _text(compilation.get("status")) or (
            "MEASURED" if fact_count is not None else "NOT_MEASURED"
        ),
        "ledger_schema": _text(fact_ledger.get("schema")),
        "business_fact_count": fact_count,
        "observed_row_count": fact_count,
        "unique_fact_id_count": unique_fact_id_count,
        "missing_fact_id_count": max(0, (fact_count or 0) - fact_id_count)
        if fact_count is not None
        else None,
        "final_fact_count": final_fact_count,
        "exact_evidence_fact_count": exact_evidence_fact_count,
        "accepted_fact_count": integer(compilation.get("accepted_fact_count")),
        "pending_fact_count": integer(compilation.get("pending_fact_count")),
        "evidence_path": "business_fact_ledger.items",
    }
    if fact_count is None:
        receipt["missing_evidence"].append("business_fact_ledger.items")
    for field, value in (
        ("final_fact_count", final_fact_count),
        ("exact_evidence_fact_count", exact_evidence_fact_count),
    ):
        if fact_count is not None and value is not None and value != fact_count:
            receipt["issues"].append(
                f"business_fact_{field}_mismatch:observed={fact_count};declared={value}"
            )
    if fact_count is not None and fact_id_count != unique_fact_id_count:
        receipt["issues"].append("business_fact_identity_duplicate_or_missing")
    if fact_count is not None and fact_id_count != fact_count:
        receipt["issues"].append(
            "business_fact_identity_missing:"
            f"observed={fact_id_count};rows={fact_count}"
        )

    enterprise_ir = _dict(model.get("business_behavior_ir"))
    behavior_rows = enterprise_ir.get("behaviors")
    behavior_count = row_count(behavior_rows)
    source_fact_refs = [
        _text(source_ref)
        for row in _list(behavior_rows)
        if isinstance(row, dict)
        for source_ref in _list(row.get("source_refs"))
        if _text(source_ref)
    ]
    source_fact_ref_set = set(source_fact_refs)
    fact_id_set = {value for value in fact_ids if value}
    accepted_behavior_fact_count = integer(
        source_summary.get("accepted_behavior_fact_count")
    )
    declared_behavior_count = integer(source_summary.get("business_behavior_count"))
    receipt["enterprise_behavior_ir"] = {
        "status": _text(_dict(enterprise_ir.get("behavior_gate")).get("status"))
        or ("MEASURED" if behavior_count is not None else "NOT_MEASURED"),
        "schema": _text(enterprise_ir.get("schema")),
        "behavior_node_count": behavior_count,
        "behavior_ir_fact_count": len(source_fact_ref_set),
        "declared_behavior_node_count": declared_behavior_count,
        "source_bound_fact_ref_count": len(source_fact_refs),
        "unique_source_bound_fact_ref_count": len(source_fact_ref_set),
        "accepted_behavior_fact_count": accepted_behavior_fact_count,
        "fact_refs_not_in_ledger_count": len(source_fact_ref_set - fact_id_set),
        "facts_without_behavior_ref_count": len(fact_id_set - source_fact_ref_set),
        "gate_status": _text(_dict(enterprise_ir.get("behavior_gate")).get("status")),
        "evidence_path": "enterprise_understanding_model.business_behavior_ir.behaviors",
    }
    if behavior_count is None:
        receipt["missing_evidence"].append(
            "enterprise_understanding_model.business_behavior_ir.behaviors"
        )
    elif declared_behavior_count is not None and behavior_count != declared_behavior_count:
        receipt["issues"].append(
            "business_behavior_count_mismatch:"
            f"observed={behavior_count};declared={declared_behavior_count}"
        )
    if (
        accepted_behavior_fact_count is not None
        and len(source_fact_ref_set) != accepted_behavior_fact_count
    ):
        receipt["issues"].append(
            "accepted_behavior_fact_count_mismatch:"
            f"observed={len(source_fact_ref_set)};declared={accepted_behavior_fact_count}"
        )

    runtime_ir = _dict(behavior_ir)
    runtime_counts = {
        field: row_count(runtime_ir.get(field))
        for field in (
            "sources",
            "entities",
            "operations",
            "actors",
            "invariants",
            "relations",
            "states",
            "observation_surfaces",
            "coverage_gaps",
        )
    }
    receipt["runtime_behavior_ir"] = {
        "status": "MEASURED" if runtime_ir else "NOT_MEASURED",
        "schema": _text(runtime_ir.get("schema_version")),
        "model_id": _text(runtime_ir.get("model_id")),
        "source_snapshot_hash": _text(runtime_ir.get("source_snapshot_hash")),
        "node_counts": runtime_counts,
        "evidence_path": "runtime_behavior_ir",
    }
    if not runtime_ir:
        receipt["missing_evidence"].append("runtime_behavior_ir")
    elif not _text(runtime_ir.get("model_id")):
        receipt["missing_evidence"].append("runtime_behavior_ir.model_id")

    obligation_ids = [
        _text(row.get("obligation_id"))
        for row in formal_obligation_rows
        if isinstance(row, dict)
    ]
    unique_obligation_ids = {value for value in obligation_ids if value}
    missing_obligation_id_count = sum(not value for value in obligation_ids)
    duplicate_obligation_id_count = len(obligation_ids) - len(unique_obligation_ids)
    obligation_status = (
        "FAILED_SAFE"
        if duplicate_obligation_id_count or missing_obligation_id_count
        else "MEASURED"
        if formal_obligation_rows
        else "NOT_MEASURED"
    )
    receipt["formal_obligations"] = {
        "status": obligation_status,
        "obligation_count": len(formal_obligation_rows),
        "formal_obligation_count": len(formal_obligation_rows),
        "unique_obligation_id_count": len(unique_obligation_ids),
        "missing_obligation_id_count": missing_obligation_id_count,
        "duplicate_obligation_id_count": duplicate_obligation_id_count,
        "evidence_path": "test_obligations.obligations",
    }
    if not formal_obligation_rows:
        receipt["missing_evidence"].append("test_obligations.obligations")

    links = receipt["links"]
    links.update({
        "facts_to_enterprise_behavior_ir": {
            "status": (
                "GAP"
                if receipt["enterprise_behavior_ir"].get(
                    "facts_without_behavior_ref_count"
                )
                else "PASS"
                if fact_count is not None and behavior_count is not None
                else "NOT_MEASURED"
            ),
            "source_fact_ref_count": len(source_fact_ref_set),
            "facts_without_behavior_ref_count": receipt["enterprise_behavior_ir"].get(
                "facts_without_behavior_ref_count"
            ),
        },
        "runtime_behavior_ir_to_formal_obligations": {
            "status": (
                "PASS"
                if runtime_ir and formal_obligation_rows
                and not missing_obligation_id_count
                and not duplicate_obligation_id_count
                else "NOT_MEASURED"
            ),
            "runtime_behavior_ir_model_id": _text(runtime_ir.get("model_id")),
            "formal_obligation_count": len(formal_obligation_rows),
        },
    })

    if receipt["issues"]:
        receipt["status"] = "FAILED_SAFE"
    elif receipt["missing_evidence"]:
        receipt["status"] = "INCOMPLETE"
    elif _text(receipt["business_facts"].get("status")).upper() == "BLOCKED":
        receipt["status"] = "BLOCKED"
    elif _text(receipt["enterprise_behavior_ir"].get("status")).upper().startswith(
        "BLOCKED"
    ) or receipt["enterprise_behavior_ir"].get("facts_without_behavior_ref_count"):
        receipt["status"] = "BLOCKED"
    else:
        receipt["status"] = "PASS"
    return receipt


def _execution_ir_with_discovered_operations(
    behavior_ir: dict[str, Any],
    discovered_operations: Any,
) -> dict[str, Any]:
    """Append governed runtime-discovered operations to an execution IR view.

    Round-1 experiments were compiled against the immutable documented IR, but
    governed runtime interface discovery may prove additional routes before
    round 1 executes (e.g. GET /api/users/addresses for an order fixture's
    addressId dependency). The discovered operations are appended without
    rebuilding the IR so compiled operation identities stay valid; the same
    observations are covered by the behavior-ir-expansion-round receipt.
    """
    ir = dict(_dict(behavior_ir))
    rows = [
        dict(row)
        for row in _list(discovered_operations)
        if isinstance(row, dict)
    ]
    if not rows:
        return ir
    existing_ops = [
        dict(row)
        for row in _list(ir.get("operations"))
        if isinstance(row, dict)
    ]
    existing_keys = {
        (
            _text(row.get("method")).upper(),
            _text(row.get("path") or row.get("raw_path")),
        )
        for row in existing_ops
    }
    added = 0
    for row in rows:
        normalized = dict(row)
        if not _text(normalized.get("id")):
            # Runtime-discovered operations carry operation_id, while runtime
            # operation indexes are keyed by id. Normalize so the execution IR
            # view exposes the discovered route to resolver lookups.
            normalized["id"] = _text(normalized.get("operation_id"))
        row = normalized
        key = (
            _text(row.get("method")).upper(),
            _text(row.get("path") or row.get("raw_path")),
        )
        if key and key not in existing_keys:
            existing_ops.append(dict(row))
            existing_keys.add(key)
            added += 1
    if added:
        ir = {**ir, "operations": existing_ops}
    return ir

# ── P0-2: Business / Discovery funnel separation ──
_DISCOVERY_REASON_CODES = frozenset({
    "SURFACE_DISCOVERY_OBSERVATION_ONLY",
})
_DISCOVERY_FAMILIES = frozenset({
    "interface_discovery",
})


def _is_discovery_task(attempt: dict[str, Any]) -> bool:
    """Classify an attempt as a discovery task vs business obligation."""
    reason = _text(attempt.get("reason_code")).upper()
    family = _text(attempt.get("risk_family")).lower()
    return reason in _DISCOVERY_REASON_CODES or family in _DISCOVERY_FAMILIES


def build_business_discovery_separation(
    ledger: dict[str, Any],
    findings: list[dict[str, Any]],
) -> dict[str, Any]:
    """Separate ledger attempts into business obligations and discovery tasks.

    Produces per-type funnel statistics for business obligations and a
    separate discovery task summary. Existing metrics remain compatible.
    """
    attempts = [_dict(a) for a in _list(ledger.get("attempts"))]
    business_attempts = [a for a in attempts if not _is_discovery_task(a)]
    discovery_attempts = [a for a in attempts if _is_discovery_task(a)]

    # ── Business obligation per-type funnel ──
    _STAGES = (
        "generated", "eligible", "planned", "prepared",
        "executed", "observed", "oracle_evaluated", "finding", "confirmed_tp",
    )
    per_type: dict[str, dict[str, int]] = {}
    for a in business_attempts:
        family = _text(a.get("risk_family")) or "unknown"
        if family not in per_type:
            per_type[family] = {s: 0 for s in _STAGES}
            per_type[family]["blocked"] = 0
        row = per_type[family]
        row["generated"] += 1
        terminal = _text(a.get("terminal_status")).upper()
        reason = _text(a.get("reason_code")).upper()
        # Eligible: not blocked at compile
        if terminal not in ("DEFERRED",) or reason != "OBLIGATION_NOT_IN_PLAN":
            row["eligible"] += 1
        # Planned: selected for execution
        if reason != "OBLIGATION_NOT_IN_PLAN" and terminal != "DEFERRED":
            row["planned"] += 1
        # Executed: reached transport
        if terminal in ("DELIVERABLE", "REJECTED", "HARNESS_FAILED"):
            row["executed"] += 1
        # Observed: has observation receipts
        if terminal in ("DELIVERABLE", "REJECTED"):
            row["observed"] += 1
        # Oracle evaluated
        if terminal in ("DELIVERABLE", "REJECTED") and reason not in (
            "CONTRACT_ORACLE_BLOCKED", "CONTRACT_ORACLE_HARNESS_FAILED",
        ):
            row["oracle_evaluated"] += 1
        # Finding produced
        if terminal == "DELIVERABLE":
            row["finding"] += 1
        # Blocked
        if terminal in ("BLOCKED", "HARNESS_FAILED") or reason.startswith("BLOCKED"):
            row["blocked"] += 1

    # ── Business funnel totals ──
    biz_total = {s: sum(per_type[t][s] for t in per_type) for s in _STAGES}
    biz_total["blocked"] = sum(per_type[t]["blocked"] for t in per_type)

    # ── Discovery task summary ──
    disc_executed = sum(
        1 for a in discovery_attempts
        if _text(a.get("terminal_status")).upper() in ("REJECTED", "EXECUTED", "DELIVERABLE")
    )
    discovery_summary = {
        "generated_discovery_tasks": len(discovery_attempts),
        "executed_discovery_tasks": disc_executed,
        "successful_discovery_tasks": disc_executed,
        "discovered_operations": 0,  # filled by runtime_interface_discovery
        "discovered_observers": 0,
    }

    return {
        "schema_version": "qualibug.business-discovery-separation.v1",
        "business_obligation_summary": {
            "total": len(business_attempts),
            **biz_total,
        },
        "business_per_type": per_type,
        "discovery_task_summary": discovery_summary,
        "separation_note": (
            f"{len(business_attempts)} business obligations separated from "
            f"{len(discovery_attempts)} discovery tasks. "
            "Discovery tasks do not consume business execution budget."
        ),
    }


def reconcile_product_pipeline_health(
    v12_health: dict[str, Any] | None,
    *,
    execution_status: str,
    preflight_diagnostics: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply product preflight truth without manufacturing execution success."""

    health = dict(v12_health or {})
    normalized = _text(execution_status or "not_executed").lower()
    diagnostics = _dict(preflight_diagnostics)
    preflight_present = any(
        key in diagnostics for key in ("ready", "all_checks_passed", "errors", "checks")
    )
    checks = diagnostics.get("checks")
    if not isinstance(checks, list):
        checks = []
    failed_error_check = any(
        isinstance(check, dict)
        and check.get("passed") is False
        and _text(check.get("severity")).lower() == "error"
        for check in checks
    )
    # ``all_checks_passed`` also includes advisory info/warning checks (for
    # example an optional database connection).  It is a completeness signal,
    # not a blocking health gate.  ``ready``/error counts and explicit error
    # checks are the preflight authority for execution health.
    preflight_failed = preflight_present and (
        diagnostics.get("ready") is False
        or _int(diagnostics.get("errors")) > 0
        or failed_error_check
    )
    if normalized == "partial":
        health.update({
            "status": "DEGRADED",
            "execution_status": normalized,
            "execution_reason": "partial_execution",
            "empty_findings_means_no_bugs": False,
        })
    elif normalized not in {"completed", "executed"}:
        health.update({
            "status": "FAILED_SAFE" if _text(health.get("status")).upper() == "FAILED_SAFE" else "BLOCKED",
            "execution_status": normalized,
            "execution_reason": "preflight_not_ready" if preflight_failed else "execution_not_completed",
            "empty_findings_means_no_bugs": False,
        })
    elif preflight_failed:
        health.update({
            "status": "DEGRADED",
            "execution_status": normalized,
            "execution_reason": "preflight_health_failed",
            "empty_findings_means_no_bugs": False,
        })
    health["preflight"] = {
        "present": preflight_present,
        "ready": diagnostics.get("ready"),
        "all_checks_passed": diagnostics.get("all_checks_passed"),
        "errors": _int(diagnostics.get("errors")),
        "warnings": _int(diagnostics.get("warnings")),
        "blocking_failed": preflight_failed,
        "warning_only": bool(
            preflight_present
            and not preflight_failed
            and (
                _int(diagnostics.get("warnings")) > 0
                or diagnostics.get("all_checks_passed") is False
            )
        ),
    }
    return health
