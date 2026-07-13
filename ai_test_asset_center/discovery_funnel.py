"""Attempt-receipt-only discovery funnel and pipeline health projection."""
from __future__ import annotations

import math
from collections import Counter
from typing import Any

from .obligation_attempt_ledger import (
    ObligationAttemptLedgerError,
    validate_obligation_attempt_ledger,
)
from .discovery_mainline_contract import validate_mainline_run_contract
from .discovery_quality_projection import (
    SCHEMA_VERSION as QUALITY_PROJECTION_SCHEMA,
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
        return "not_executed"
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
    if execution_status != "EXECUTED" and stage_name not in {"formal_projection"}:
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


def build_pipeline_health(v12_result: dict[str, Any]) -> dict[str, Any]:
    result = _dict(v12_result)
    ledger = _attempt_ledger(result)
    formal = _formal_projection(result)
    attempts = [_dict(item) for item in _list(ledger.get("attempts"))]
    attempt_deliverable_ids = sorted(
        _text(item.get("finding_id"))
        for item in attempts
        if _text(item.get("terminal_status")).upper() == "DELIVERABLE"
        and _text(item.get("finding_id"))
    )
    raw_contract = result.get("mainline_run") or _dict(result.get("v12")).get("mainline_run")
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
        1 for attempt in attempts if _text(_stage(attempt, "execution").get("status")).upper() == "EXECUTED"
    )
    observation_missing = sum(
        1
        for attempt in attempts
        if _text(_stage(attempt, "execution").get("status")).upper() == "EXECUTED"
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
    cleanup_failures = sum(
        1
        for attempt in attempts
        if "CLEANUP" in _text(attempt.get("reason_code")).upper()
        and _text(attempt.get("terminal_status")).upper() == "HARNESS_FAILED"
    )
    cost_unknown = any(
        _text(attempt.get("cost_coverage_status")).upper() == "UNKNOWN"
        for attempt in attempts
    )
    status = "OK"
    if error_present or stage_failures or secret_scan_failed or observation_missing:
        status = "FAILED_SAFE"
    elif selected == 0:
        status = "BLOCKED"
    elif selected and blocked == selected:
        status = "BLOCKED"
    elif (
        harness_failures
        or blocked
        or execution_status != "completed"
        or reasoner_failure_count
        or reasoner_status in {"degraded", "failed", "provider_unavailable"}
        or formal_mismatch
        or observability_gaps
        or cost_unknown
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
    return {
        "schema_version": "qualibug.discovery-funnel.v2",
        "stages": stages,
        "top_blocking_reasons": top_reasons,
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
