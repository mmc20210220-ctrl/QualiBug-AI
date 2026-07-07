from __future__ import annotations

"""P4 pilot success gate.

The gate converts the customer value scorecard into a sales/customer-success
motion: whether the pilot can move to executive readout, procurement follow-up,
or needs more evidence.
"""

from typing import Any


DEFAULT_POLICY = {
    "min_detection_rate_for_success": 0.8,
    "require_p0_or_p1_signal": True,
    "require_customer_safe_scorecard": True,
    "procurement_requires_persisted_evidence": True,
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _policy(value: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_POLICY)
    if isinstance(value, dict):
        merged.update(value)
    return merged


def _metric(metrics: dict[str, Any], name: str, default: float = 0.0) -> float:
    try:
        return float(metrics.get(name, default) or default)
    except (TypeError, ValueError):
        return default


def _hard_blockers(scorecard: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, str]]:
    blockers: list[dict[str, str]] = []
    if not scorecard:
        return [{"code": "P4_SCORECARD_MISSING", "detail": "Generate p4_customer_value_scorecard before evaluating pilot success."}]
    if policy.get("require_customer_safe_scorecard") and scorecard.get("customer_safe") is not True:
        blockers.append({"code": "CUSTOMER_SAFE_SCORECARD_REQUIRED", "detail": "Pilot decision requires a customer-safe scorecard."})
    metrics = _as_dict(scorecard.get("board_metrics"))
    total = _metric(metrics, "seed_defects_total")
    found = _metric(metrics, "seed_defects_found")
    detection_rate = _metric(metrics, "detection_rate")
    p0_found = _metric(metrics, "p0_found")
    p1_found = _metric(metrics, "p1_found")
    if total <= 0:
        blockers.append({"code": "SEED_BENCHMARK_REQUIRED", "detail": "Pilot decision requires at least one seed defect benchmark."})
    if found <= 0:
        blockers.append({"code": "NO_DEFECT_VALUE_PROVEN", "detail": "No seed defects were found in this pilot run."})
    if detection_rate < float(policy.get("min_detection_rate_for_success", 0.8)):
        blockers.append({"code": "DETECTION_RATE_BELOW_SUCCESS_THRESHOLD", "detail": "Seed-defect detection rate is below the pilot success threshold."})
    if policy.get("require_p0_or_p1_signal") and p0_found + p1_found <= 0:
        blockers.append({"code": "HIGH_SEVERITY_SIGNAL_REQUIRED", "detail": "Pilot success requires at least one P0/P1 finding."})
    context = _as_dict(scorecard.get("execution_context"))
    if str(context.get("runtime_status") or "") == "blocked":
        blockers.append({"code": "RUNTIME_BLOCKED", "detail": "Runtime execution was blocked."})
    if str(context.get("execution_status") or "") == "blocked":
        blockers.append({"code": "EXECUTION_BLOCKED", "detail": "Execution status is blocked."})
    return blockers


def _warnings(scorecard: dict[str, Any], policy: dict[str, Any]) -> list[dict[str, str]]:
    if not scorecard:
        return []
    warnings: list[dict[str, str]] = []
    metrics = _as_dict(scorecard.get("board_metrics"))
    missed = _metric(metrics, "seed_defects_missed")
    detection_rate = _metric(metrics, "detection_rate")
    if missed > 0:
        warnings.append({"code": "SEED_DEFECTS_MISSED", "detail": "Some seed defects were missed and should be triaged before scaling the pilot."})
    if detection_rate < 1.0:
        warnings.append({"code": "DETECTION_RATE_NOT_100_PERCENT", "detail": "Detection rate is not yet 100%."})
    context = _as_dict(scorecard.get("execution_context"))
    evidence_status = str(context.get("evidence_bundle_status") or "")
    if evidence_status and evidence_status not in {"persisted", "verified"}:
        warnings.append({"code": "EVIDENCE_BUNDLE_NOT_PERSISTED", "detail": "Evidence bundle is not persisted/verified; procurement motion should wait for stronger evidence packaging."})
    verdict = str(context.get("release_gate_verdict") or "")
    if verdict and verdict not in {"approved", "review_required", "pass", "passed"}:
        warnings.append({"code": "RELEASE_GATE_NOT_READY", "detail": "Release gate is not ready for customer decision."})
    return warnings


def _decision(scorecard: dict[str, Any], blockers: list[dict[str, str]], warnings: list[dict[str, str]], policy: dict[str, Any]) -> str:
    if blockers:
        return "not_ready"
    level = str(scorecard.get("value_level") or "")
    metrics = _as_dict(scorecard.get("board_metrics"))
    p0_found = _metric(metrics, "p0_found")
    p1_found = _metric(metrics, "p1_found")
    context = _as_dict(scorecard.get("execution_context"))
    evidence_status = str(context.get("evidence_bundle_status") or "")
    evidence_ready = evidence_status in {"persisted", "verified"}
    if level == "critical_value_proven" and p0_found > 0 and (evidence_ready or not policy.get("procurement_requires_persisted_evidence")):
        return "procurement_ready"
    if level in {"critical_value_proven", "value_proven"} and p0_found + p1_found > 0:
        return "executive_readout_ready"
    if level in {"critical_value_proven", "value_proven", "early_signal"}:
        return "needs_evidence_hardening"
    return "not_ready"


def _next_actions(decision: str, blockers: list[dict[str, str]], warnings: list[dict[str, str]]) -> list[str]:
    if decision == "procurement_ready":
        return [
            "Schedule customer executive readout with the P4 value scorecard.",
            "Prepare customer-safe evidence stories for P0/P1 findings.",
            "Start procurement and deployment-scope discussion.",
        ]
    if decision == "executive_readout_ready":
        actions = [
            "Schedule customer executive readout, but mark procurement as evidence-dependent.",
            "Persist/verify evidence bundle before commercial close.",
        ]
        if warnings:
            actions.append("Triage warnings before expanding the pilot scope.")
        return actions
    if decision == "needs_evidence_hardening":
        return [
            "Run another benchmark round with more P0/P1 seed defects.",
            "Strengthen evidence persistence and customer-safe finding narratives.",
        ]
    if blockers:
        return [
            "Do not present as pilot success yet.",
            "Resolve blockers: " + ", ".join(item["code"] for item in blockers[:4]),
            "Re-run P3 benchmark and regenerate P4 scorecard.",
        ]
    return ["Collect more benchmark evidence before customer decision."]


def build_p4_pilot_success_gate(scan_result: dict[str, Any], policy: dict[str, Any] | None = None) -> dict[str, Any]:
    result = _as_dict(scan_result)
    scorecard = _as_dict(result.get("p4_customer_value_scorecard"))
    gate_policy = _policy(policy or _as_dict(result.get("p4_pilot_success_policy")))
    blockers = _hard_blockers(scorecard, gate_policy)
    warnings = _warnings(scorecard, gate_policy)
    decision = _decision(scorecard, blockers, warnings, gate_policy)
    pilot_success = decision in {"procurement_ready", "executive_readout_ready"}
    return {
        "schema_version": "p4-pilot-success-gate-v1",
        "decision": decision,
        "pilot_success": pilot_success,
        "executive_readout_ready": decision in {"procurement_ready", "executive_readout_ready"},
        "procurement_motion_ready": decision == "procurement_ready",
        "customer_safe": True,
        "policy": gate_policy,
        "blockers": blockers,
        "warnings": warnings,
        "scorecard_value_level": str(scorecard.get("value_level") or ""),
        "board_metrics": _as_dict(scorecard.get("board_metrics")),
        "next_actions": _next_actions(decision, blockers, warnings),
    }
