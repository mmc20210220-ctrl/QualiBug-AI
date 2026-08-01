"""Attempt-receipt-only discovery funnel and pipeline health projection."""
from __future__ import annotations

import math
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


def _generated_obligation_count(result: dict[str, Any]) -> int | None:
    nested = _dict(result.get("v12"))
    for owner in (result, nested):
        obligations = _dict(owner.get("test_obligations")).get("obligations")
        if isinstance(obligations, list):
            return len([row for row in obligations if isinstance(row, dict)])
    return None


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
    compile_rows = [_stage(attempt, "compile") for attempt in attempts]
    execution_rows = [_stage(attempt, "execution") for attempt in attempts]
    gate_rows = [_stage(attempt, "gate") for attempt in attempts]
    compile_count = sum(bool(row) for row in compile_rows)
    execution_count = sum(bool(row) for row in execution_rows)
    gate_count = sum(bool(row) for row in gate_rows)
    pre_execution_count = sum(
        1
        for row in compile_rows
        if _text(row.get("status")).upper()
        in {"BLOCKED", "DEFERRED", "HARNESS_FAILED"}
    )
    execution_unresolved_count = sum(
        1
        for execution, gate in zip(execution_rows, gate_rows)
        if execution
        and not gate
        and _text(execution.get("status")).upper()
        in {"BLOCKED", "DEFERRED", "HARNESS_FAILED"}
    )
    gate_terminal_count = sum(
        1
        for row in gate_rows
        if _text(row.get("status")).upper() in {
            "DELIVERABLE", "REJECTED", "BLOCKED", "DEFERRED", "HARNESS_FAILED",
        }
    )
    deliverable_attempt_ids = {
        _text(attempt.get("finding_id"))
        for attempt in attempts
        if _text(attempt.get("terminal_status")).upper() == "DELIVERABLE"
        and _text(attempt.get("finding_id"))
    }
    formal_occurrence_ids = {
        _text(value)
        for value in _list(formal.get("delivery_occurrence_finding_ids"))
        if _text(value)
    }
    checks: list[dict[str, Any]] = []
    if generated is not None:
        checks.append({
            "name": "obligation_generation_not_less_than_selected",
            "status": "PASS" if generated >= selected else "INCOMPLETE",
            "expected": True,
            "observed": generated >= selected,
            "detail": (
                "generated obligations come from the test-obligation receipt"
                if generated >= selected
                else "selected runtime-expanded obligations are not all present in the base test-obligation receipt"
            ),
        })
    _conservation_check(
        checks,
        name="selected_terminal_conservation",
        expected=selected,
        observed=terminal,
    )
    _conservation_check(
        checks,
        name="compile_stage_conservation",
        expected=selected,
        observed=compile_count,
    )
    _conservation_check(
        checks,
        name="compile_to_execution_conservation",
        expected=selected,
        observed=pre_execution_count + execution_count,
    )
    _conservation_check(
        checks,
        name="execution_to_oracle_conservation",
        expected=execution_count,
        observed=gate_count + execution_unresolved_count,
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

    failures = [row for row in checks if row.get("status") == "FAIL"]
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
        "selected_count": selected,
        "terminal_count": terminal,
        "compile_count": compile_count,
        "pre_execution_blocked_count": pre_execution_count,
        "execution_count": execution_count,
        "execution_unresolved_count": execution_unresolved_count,
        "oracle_count": gate_count,
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
                "reason_detail": _text(attempt.get("reason_detail"))[:500],
            })
            if len(examples) >= 3:
                break
        family = _text(profile.get("reason_family")) or "UNREGISTERED"
        rows.append({
            "reason": reason,
            "count": count,
            "reason_family": family,
            "registry_status": _text(profile.get("registry_status")),
            "recoverability": _text(profile.get("recoverability")),
            "is_blocking": bool(profile.get("is_blocking")),
            "must_remain_blocked": bool(profile.get("must_remain_blocked")),
            "customer_materials_needed": list(_REASON_MATERIALS.get(family, [])),
            "examples": examples,
        })
    blocking = [row for row in rows if row.get("is_blocking")]
    return blocking[:limit], sorted(set(unregistered))


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
    conservation = _dict(current_funnel.get("conservation"))
    if not conservation:
        conservation = _dict(
            _dict(current_funnel.get("pipeline_health")).get("funnel_conservation")
        )
    if not conservation:
        conservation = build_funnel_conservation(result)
    blockers, unregistered = _reason_details(attempts)
    nested = _dict(result.get("v12"))
    mainline = _dict(result.get("mainline_run") or nested.get("mainline_run"))
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
        "report_status": "FAILED_SAFE" if unregistered else "READY",
        "mainline_identity": {
            key: _text(mainline.get(key))
            for key in (
                "run_id",
                "campaign_id",
                "target_id",
                "environment_id",
                "policy_version",
                "evaluation_mode",
                "contract_fingerprint",
            )
            if _text(mainline.get(key))
        },
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
            "selected_count": conservation.get("selected_count"),
            "terminal_count": conservation.get("terminal_count"),
            "compiled_count": _int(
                next(
                    (
                        _dict(stage).get("success")
                        for stage in _list(current_funnel.get("stages"))
                        if _text(_dict(stage).get("name")) == "experiment_compile"
                    ),
                    0,
                )
            ),
            "executed_count": conservation.get("execution_count"),
            "oracle_count": conservation.get("oracle_count"),
            "formal_delivery_count": current_funnel.get("validated_bug_count"),
            "delivery_occurrence_count": current_funnel.get("delivery_occurrence_count"),
        },
        "pipeline_health": _dict(current_funnel.get("pipeline_health")),
        "conservation": conservation,
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
        f"| Selected | {display_metric(metrics.get('selected_count'))} |",
        f"| Terminal | {display_metric(metrics.get('terminal_count'))} |",
        f"| Compiled | {display_metric(metrics.get('compiled_count'))} |",
        f"| Executed | {display_metric(metrics.get('executed_count'))} |",
        f"| Oracle evaluated | {display_metric(metrics.get('oracle_count'))} |",
        f"| Formal delivery | {display_metric(metrics.get('formal_delivery_count'))} |",
        f"| Delivery occurrences | {display_metric(metrics.get('delivery_occurrence_count'))} |",
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
    ]
    blockers = [_dict(row) for row in _list(value.get("top_blocking_reasons"))]
    if not blockers:
        lines.append("No blocking reason receipt was recorded.")
    for row in blockers:
        lines.extend([
            f"### `{_text(row.get('reason'))}` ({_int(row.get('count'))})",
            "",
            f"- Family: `{_text(row.get('reason_family'))}`",
            f"- Registry: `{_text(row.get('registry_status'))}`",
            f"- Recoverability: `{_text(row.get('recoverability'))}`",
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
    "selected_count",
    "terminal_count",
    "compiled_count",
    "executed_count",
    "oracle_count",
    "formal_delivery_count",
    "delivery_occurrence_count",
)


def _comparison_snapshot(report: dict[str, Any]) -> dict[str, Any]:
    metrics = _dict(report.get("metrics"))
    health = _dict(report.get("pipeline_health"))
    return {
        "report_status": _text(report.get("report_status")),
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

    report = {
        "schema_version": "qualibug.discovery-funnel-comparison.v1",
        "status": comparison_status,
        "receipt_authority": "qualibug.obligation-attempt-ledger.v1",
        "baseline": baseline_snapshot,
        "candidate": candidate_snapshot,
        "delta": delta,
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
    preflight_failed = preflight_present and (
        diagnostics.get("ready") is False
        or diagnostics.get("all_checks_passed") is False
        or _int(diagnostics.get("errors")) > 0
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
    }
    return health
