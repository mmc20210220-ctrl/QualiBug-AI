"""Single source of truth for discovery quality / formal-count projection.

Commercial quality claims come only from the external evaluator. Runtime and
product surfaces may expose funnel diagnostics and formal customer-deliverable
counts, but MUST NOT invent recall/precision/quality scores when measurement
status is NOT_MEASURED.
"""
from __future__ import annotations

from typing import Any

from .customer_delivery_gate import (
    customer_delivery_rejection_reasons,
    is_customer_deliverable_defect,
    split_customer_delivery_tracks,
)


SCHEMA_VERSION = "qualibug.discovery-quality-projection.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def formal_customer_deliverable_findings(findings: Any) -> list[dict[str, Any]]:
    return [
        item
        for item in _list(findings)
        if isinstance(item, dict) and is_customer_deliverable_defect(item)
    ]


def build_formal_count_projection(
    *,
    findings: Any = None,
    candidate_findings: Any = None,
    discovery_funnel: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Canonical formal / clue / funnel counts for scan, submission, and API."""
    all_findings = [item for item in _list(findings) if isinstance(item, dict)]
    candidates = [item for item in _list(candidate_findings) if isinstance(item, dict)]
    defects, clues_from_findings = split_customer_delivery_tracks(all_findings)
    funnel = _dict(discovery_funnel)
    funnel_validated = int(funnel.get("validated_bug_count") or 0)
    formal_count = len(defects)
    return {
        "schema_version": SCHEMA_VERSION,
        "formal_customer_deliverable_count": formal_count,
        "executed_clue_count": len(clues_from_findings) + len(candidates),
        "confirmation_receipt_count": len(all_findings),
        "candidate_count": len(candidates),
        "funnel_validated_bug_count": funnel_validated,
        "count_consistency": {
            "formal_equals_funnel_validated": (
                formal_count == funnel_validated if "validated_bug_count" in funnel else None
            ),
            "note": (
                "formal_customer_deliverable_count is the only commercial defect count; "
                "confirmation_receipt_count and funnel diagnostics are internal only."
            ),
        },
    }


def build_finding_classification_projection(
    *,
    findings: Any = None,
    candidate_findings: Any = None,
) -> dict[str, Any]:
    """Partition product results without reinterpreting the delivery gate."""
    deliverable: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for item in _list(findings):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        if is_customer_deliverable_defect(row):
            row["finding_class"] = "deliverable"
            deliverable.append(row)
            continue
        reasons = customer_delivery_rejection_reasons(row)
        row["finding_class"] = "rejected"
        row["delivery_rejection_reasons"] = reasons
        rejected.append(row)
    candidates = []
    for item in _list(candidate_findings):
        if isinstance(item, dict):
            row = dict(item)
            row["finding_class"] = "candidate"
            candidates.append(row)
    return {
        "schema_version": SCHEMA_VERSION,
        "deliverable": deliverable,
        "candidate": candidates,
        "rejected": rejected,
        "counts": {
            "deliverable": len(deliverable),
            "candidate": len(candidates),
            "rejected": len(rejected),
        },
    }


def build_external_evaluation_projection(
    *,
    measurement_status: str = "NOT_MEASURED",
    reason: str = "external_evaluator_receipt_required",
    formal_customer_deliverable_count: int = 0,
    evaluator_report: dict[str, Any] | None = None,
    claim_status: str | None = None,
) -> dict[str, Any]:
    """Product-facing external evaluation projection.

    When NOT_MEASURED, quality_score / recall / precision / f1 are explicitly
    null and must not be rendered as 100 or 0.
    """
    status = str(measurement_status or "NOT_MEASURED").strip().upper() or "NOT_MEASURED"
    report = _dict(evaluator_report)
    claim = str(claim_status or report.get("claim_status") or status).strip().upper()
    measured = status == "MEASURED" and claim == "MEASURED"
    metrics = _dict(report.get("metrics") or report.get("quality_metrics"))
    projection: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "measurement_status": status,
        "claim_status": claim,
        "reason": str(reason or report.get("reason") or ""),
        "commercial_promotion_evidence": bool(report.get("commercial_promotion_evidence")) if measured else False,
        "formal_customer_deliverable_count": int(formal_customer_deliverable_count),
        "quality_score": None,
        "recall": None,
        "precision": None,
        "f1": None,
        "display": {
            "quality_label": (
                "外部质量评测已完成"
                if measured
                else "尚未完成外部质量评测"
            ),
            "suppress_quality_score": not measured,
            "suppress_recall_precision": not measured,
        },
    }
    if measured:
        for key in ("quality_score", "recall", "precision", "f1", "f1_score"):
            if key in metrics and metrics.get(key) is not None:
                out_key = "f1" if key == "f1_score" else key
                projection[out_key] = metrics.get(key)
        if projection["quality_score"] is None and report.get("quality_score") is not None:
            projection["quality_score"] = report.get("quality_score")
    return projection


def build_obligation_execution_projection(scan_result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Product-facing obligation / experiment / adapter health (Spec §10.3)."""
    result = _dict(scan_result)
    v12 = _dict(result.get("v12"))
    obligations = _dict(result.get("test_obligations") or v12.get("test_obligations"))
    experiments = _dict(result.get("experiment_compile") or v12.get("experiment_compile"))
    experiment_execution = _dict(result.get("experiment_execution") or v12.get("experiment_execution"))
    plan = _dict(result.get("obligation_plan") or v12.get("obligation_plan"))
    adapters = _dict(result.get("execution_adapters") or v12.get("execution_adapters"))
    phases = _dict(result.get("phases") or v12.get("phases"))
    ir_phase = _dict(phases.get("behavior_ir"))
    oracle_phase = _dict(phases.get("oracle"))
    execution_phase = _dict(phases.get("execution"))
    formal = build_formal_count_projection(
        findings=result.get("findings") or v12.get("findings"),
        candidate_findings=result.get("candidate_findings"),
        discovery_funnel=_dict(result.get("discovery_funnel")),
    )
    obligation_count = int(obligations.get("count") or obligations.get("obligation_count") or ir_phase.get("obligation_count") or 0)
    compiled = int(experiments.get("compiled_count") or ir_phase.get("compiled_experiments") or 0)
    blocked = int(experiments.get("blocked_count") or ir_phase.get("blocked_experiments") or 0)
    selected = int(
        experiment_execution.get("selected_count")
        or plan.get("selected_count")
        or len(_list(plan.get("selected")))
        or 0
    )
    executed = int(
        experiment_execution.get("executed_count")
        if "executed_count" in experiment_execution
        else (
            execution_phase.get("executed_count")
            or oracle_phase.get("traces_with_http")
            or oracle_phase.get("total_evaluated")
            or 0
        )
    )
    execution_blocked = int(experiment_execution.get("blocked_count") or 0)
    block_reasons = _dict(experiments.get("block_reason_counts"))
    fingerprints = {
        "policy_version": _text(_dict(result.get("policy") or result.get("active_policy")).get("policy_version")),
        "manifest_id": _text(_dict(result.get("evaluation_manifest") or result.get("manifest")).get("dataset_id")),
        "target_id": _text(result.get("target_id") or _dict(result.get("campaign")).get("environment_ref")),
        "source_hash": _text(
            _dict(result.get("campaign")).get("source_snapshot_hash")
            or _dict(result.get("behavior_slice_ledger")).get("source_snapshot_hash")
        ),
        "behavior_ir_model_id": _text(_dict(result.get("behavior_ir") or v12.get("behavior_ir")).get("model_id")),
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "obligation_total": obligation_count,
        "obligation_compiled": compiled,
        "obligation_blocked": blocked,
        "obligation_selected": selected,
        "obligation_executed": executed,
        "obligation_execution_blocked": execution_blocked,
        "formal_defect_count": formal["formal_customer_deliverable_count"],
        "executed_clue_count": formal["executed_clue_count"],
        "block_reason_counts": block_reasons,
        "adapter_health": {
            "supported_count": int(adapters.get("supported_count") or len(_list(adapters.get("supported"))) or 0),
            "blocked_count": int(adapters.get("blocked_count") or len(_list(adapters.get("blocked"))) or 0),
            "degraded_count": int(adapters.get("degraded_count") or len(_list(adapters.get("degraded"))) or 0),
            "matrix": adapters if adapters else {},
        },
        "fixture_cleanup_health": {
            "status": _text(
                _dict(result.get("post_run_cleanliness") or result.get("cleanup") or execution_phase).get("status")
                or _dict(result.get("pipeline_health")).get("cleanup_status")
            ),
            "pipeline_health_status": _text(_dict(result.get("pipeline_health")).get("status")),
        },
        "fingerprints": fingerprints,
        "display": {
            "obligation_label": f"义务 {obligation_count} · 已编译 {compiled} · 已阻断 {blocked}",
            "execution_label": f"已执行 {executed} · 正式缺陷 {formal['formal_customer_deliverable_count']} · 线索 {formal['executed_clue_count']}",
        },
    }


def _text(value: Any) -> str:
    return str(value or "").strip()


def attach_quality_projection_to_scan_result(scan_result: dict[str, Any]) -> dict[str, Any]:
    """Mutate-safe: return a copy of scan_result with SSOT quality projection."""
    result = dict(scan_result or {})
    initial_counts = build_formal_count_projection(
        findings=result.get("findings"),
        candidate_findings=result.get("candidate_findings"),
        discovery_funnel=_dict(result.get("discovery_funnel")),
    )
    # Align legacy funnel diagnostics to the formal customer-delivery SSOT before
    # building downstream projections. Otherwise nested projections can retain a
    # stale pre-gate `validated_bug_count` even after cleanup/readjudication
    # promotes additional customer-deliverable defects.
    funnel = _dict(result.get("discovery_funnel"))
    if funnel:
        funnel = dict(funnel)
        funnel["validated_bug_count"] = initial_counts["formal_customer_deliverable_count"]
        result["discovery_funnel"] = funnel
    counts = build_formal_count_projection(
        findings=result.get("findings"),
        candidate_findings=result.get("candidate_findings"),
        discovery_funnel=_dict(result.get("discovery_funnel")),
    )
    existing_external = _dict(result.get("external_evaluation"))
    external = build_external_evaluation_projection(
        measurement_status=str(existing_external.get("measurement_status") or "NOT_MEASURED"),
        reason=str(existing_external.get("reason") or "external_evaluator_receipt_required"),
        formal_customer_deliverable_count=counts["formal_customer_deliverable_count"],
        evaluator_report=_dict(existing_external.get("evaluator_report")),
        claim_status=existing_external.get("claim_status"),
    )
    # Preserve submission file pointer and other non-metric fields.
    for key, value in existing_external.items():
        if key not in external:
            external[key] = value
    obligation_proj = build_obligation_execution_projection(result)
    finding_classification = build_finding_classification_projection(
        findings=result.get("findings"),
        candidate_findings=result.get("candidate_findings"),
    )
    result["formal_count_projection"] = counts
    result["external_evaluation"] = external
    result["obligation_execution_projection"] = obligation_proj
    result["finding_classification"] = finding_classification
    result["scope_counts"] = {
        "current_run_formal_deliverable": counts["formal_customer_deliverable_count"],
        "current_campaign_formal_deliverable": counts["formal_customer_deliverable_count"],
        "project_open_formal_deliverable": None,
    }
    # Internal evidence-strength score must never be presented as quality when
    # external evaluation is incomplete.
    if external["display"]["suppress_quality_score"]:
        result["quality_claim_status"] = "NOT_MEASURED"
        result["commercial_quality_score"] = None
        # Keep legacy `score` for internal diagnostics but mark it non-commercial.
        result["score_semantics"] = {
            "score_field": "internal_evidence_strength_only",
            "commercial_quality_score": None,
            "note": "score is not recall/precision/commercial capability while external_evaluation is NOT_MEASURED",
        }
    else:
        result["quality_claim_status"] = "MEASURED"
        result["commercial_quality_score"] = external.get("quality_score")
        result["score_semantics"] = {
            "score_field": "external_evaluator",
            "commercial_quality_score": external.get("quality_score"),
        }
    # Align funnel validated count with formal SSOT when funnel is present.
    funnel = _dict(result.get("discovery_funnel"))
    if funnel:
        funnel = dict(funnel)
        funnel["validated_bug_count"] = counts["formal_customer_deliverable_count"]
        funnel["formal_count_projection"] = counts
        result["discovery_funnel"] = funnel
    return result


def suppress_benchmark_quality_when_not_measured(
    benchmark_metrics: dict[str, Any] | None,
    external_evaluation: dict[str, Any] | None,
) -> dict[str, Any]:
    """Strip product-facing quality metrics from benchmark_metrics when NOT_MEASURED."""
    metrics = dict(benchmark_metrics or {})
    external = _dict(external_evaluation)
    status = str(external.get("measurement_status") or "NOT_MEASURED").upper()
    if status != "MEASURED":
        metrics = dict(metrics)
        metrics["measurement_status"] = "NOT_MEASURED"
        metrics["commercial_quality_suppressed"] = True
        for key in ("recall", "precision", "f1_score", "f1", "quality_score", "score"):
            if key in metrics:
                metrics[key] = None
        metrics["display_note"] = "尚未完成外部质量评测；内部 benchmark_metrics 不得作为商业能力主张。"
    return metrics
