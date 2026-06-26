from __future__ import annotations

"""Phase93E: runtime evidence readiness score + customer SLA gate.

Phase93A-D make the customer environment observable and runnable.  Phase93E
turns those signals into a commercial-readiness gate: can this onboarding state
produce strong evidence for high-value P0/P1 runtime bugs, or should the run be
sold/delivered as degraded/plan-only until the customer fixes environment gaps?

This module is intentionally independent from bug validation.  It never marks a
system behavior as a bug; it only scores the evidence readiness of the runtime
setup and produces customer-facing SLA gate actions.
"""

from typing import Any

HIGH_VALUE_RUNTIME_RISKS = {
    "auth_boundary_probe",
    "ownership_scope_probe",
    "audit_privacy_probe",
    "state_transition_probe",
    "workflow_bypass_probe",
    "approval_flow_probe",
    "conservation_probe",
    "idempotency_replay_probe",
    "async_external_event_probe",
}
P0_P1_RISK_TYPES = {
    "ownership_scope_probe",
    "audit_privacy_probe",
    "state_transition_probe",
    "workflow_bypass_probe",
    "approval_flow_probe",
    "conservation_probe",
    "idempotency_replay_probe",
}
STRONG_EVIDENCE_QUALITY = {"strong_runtime_before_after"}
MEDIUM_OR_BETTER_EVIDENCE_QUALITY = {"strong_runtime_before_after", "medium_runtime_request_response"}
READY_LANES = {"read_only_runtime_ready", "write_sandbox_runtime_ready"}
WRITE_READY_LANE = "write_sandbox_runtime_ready"
READ_READY_LANE = "read_only_runtime_ready"
BLOCKED_LANES = {"blocked_by_preflight", "write_sandbox_blocked_by_capability", "unsupported_method_blocked"}


def _pct(num: int | float, den: int | float) -> float:
    if not den:
        return 100.0 if not num else 0.0
    return round((float(num) / float(den)) * 100.0, 2)


def _quality_score(quality: str) -> int:
    if quality == "strong_runtime_before_after":
        return 100
    if quality == "medium_runtime_request_response":
        return 70
    if quality == "weak_or_partial_runtime_evidence":
        return 40
    if quality == "no_runtime_evidence_plan_only":
        return 15
    return 0


def _check_map(preflight: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(c.get("name")): c for c in (preflight.get("checks") or []) if isinstance(c, dict) and c.get("name")}


def _risk_family_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in rows:
        risk = str(row.get("risk_type") or "unknown")
        out[risk] = out.get(risk, 0) + 1
    return dict(sorted(out.items()))


def _sla_level(score: int, blockers: list[str], p0_p1_coverage_pct: float, strong_coverage_pct: float, blocked_high_value_count: int) -> str:
    if blockers or score < 50 or p0_p1_coverage_pct < 50 or blocked_high_value_count > 0 and p0_p1_coverage_pct < 60:
        return "not_ready"
    if score >= 85 and p0_p1_coverage_pct >= 80 and strong_coverage_pct >= 60:
        return "commercial_ready"
    if score >= 70 and p0_p1_coverage_pct >= 65:
        return "conditionally_ready"
    return "degraded_ready"


def _recommended_action(level: str, blockers: list[str], gaps: list[dict[str, Any]]) -> str:
    if level == "commercial_ready":
        return "Proceed with commercial runtime validation: run ready read-only and approved write-sandbox probes, then package validated findings with evidence artifacts."
    if blockers:
        return "Resolve blocking onboarding checks before claiming commercial SLA readiness: " + ", ".join(blockers[:5]) + "."
    if gaps:
        return "Runtime can proceed with limitations; fix the highest-priority evidence gaps before P0/P1 customer acceptance: " + ", ".join(str(g.get("gap_id")) for g in gaps[:4]) + "."
    return "Runtime can proceed in degraded mode; review evidence coverage before committing P0/P1 validation SLA."


def _gap(gap_id: str, title: str, severity: str, reason: str, affected_count: int = 0, customer_action: str = "") -> dict[str, Any]:
    return {
        "gap_id": gap_id,
        "title": title,
        "severity": severity,
        "reason": reason,
        "affected_probe_count": int(affected_count or 0),
        "customer_action": customer_action,
    }


def build_runtime_evidence_readiness_sla_gate(report: dict[str, Any]) -> dict[str, Any]:
    """Build a commercial readiness score and SLA gate from an execution report.

    Expected report inputs are Phase93A-D sections.  Missing sections are treated
    conservatively so the gate never overstates runtime readiness.
    """

    preflight = report.get("onboarding_preflight") if isinstance(report.get("onboarding_preflight"), dict) else {}
    matrix = report.get("runtime_capability_matrix") if isinstance(report.get("runtime_capability_matrix"), dict) else {}
    remediation = report.get("onboarding_remediation_kit") if isinstance(report.get("onboarding_remediation_kit"), dict) else {}
    rows = [r for r in (matrix.get("rows") or []) if isinstance(r, dict)]
    checks = _check_map(preflight)

    total = len(rows)
    high_value = [r for r in rows if r.get("high_value_runtime_risk") or str(r.get("risk_type") or "") in HIGH_VALUE_RUNTIME_RISKS]
    p0_p1 = [r for r in rows if str(r.get("risk_type") or "") in P0_P1_RISK_TYPES]
    ready = [r for r in rows if str(r.get("preflight_lane") or "") in READY_LANES]
    high_value_ready = [r for r in high_value if str(r.get("preflight_lane") or "") in READY_LANES]
    p0_p1_ready = [r for r in p0_p1 if str(r.get("preflight_lane") or "") in READY_LANES]
    strong_expected = [r for r in rows if str(r.get("expected_evidence_quality") or "") in STRONG_EVIDENCE_QUALITY]
    p0_p1_strong = [r for r in p0_p1 if str(r.get("expected_evidence_quality") or "") in STRONG_EVIDENCE_QUALITY]
    medium_or_better = [r for r in rows if str(r.get("expected_evidence_quality") or "") in MEDIUM_OR_BETTER_EVIDENCE_QUALITY]
    blocked = [r for r in rows if str(r.get("preflight_lane") or "") in BLOCKED_LANES or "blocked" in str(r.get("preflight_lane") or "")]
    degraded = [r for r in rows if "degraded" in str(r.get("preflight_lane") or "")]
    write_ready = [r for r in rows if r.get("preflight_lane") == WRITE_READY_LANE]
    read_ready = [r for r in rows if r.get("preflight_lane") == READ_READY_LANE]

    coverage = {
        "probe_count": total,
        "high_value_probe_count": len(high_value),
        "p0_p1_candidate_probe_count": len(p0_p1),
        "runtime_ready_probe_count": len(ready),
        "read_only_ready_probe_count": len(read_ready),
        "write_sandbox_ready_probe_count": len(write_ready),
        "degraded_probe_count": len(degraded),
        "blocked_probe_count": len(blocked),
        "high_value_runtime_ready_count": len(high_value_ready),
        "p0_p1_runtime_ready_count": len(p0_p1_ready),
        "strong_evidence_expected_probe_count": len(strong_expected),
        "p0_p1_strong_evidence_expected_count": len(p0_p1_strong),
        "medium_or_better_evidence_expected_count": len(medium_or_better),
        "runtime_ready_coverage_pct": _pct(len(ready), total),
        "high_value_runtime_ready_coverage_pct": _pct(len(high_value_ready), len(high_value)),
        "p0_p1_runtime_ready_coverage_pct": _pct(len(p0_p1_ready), len(p0_p1)),
        "strong_evidence_expected_coverage_pct": _pct(len(strong_expected), total),
        "p0_p1_strong_evidence_expected_coverage_pct": _pct(len(p0_p1_strong), len(p0_p1)),
        "medium_or_better_evidence_expected_coverage_pct": _pct(len(medium_or_better), total),
        "risk_family_counts": _risk_family_counts(rows),
    }

    blocking_reasons = list(preflight.get("blocking_reasons") or [])
    warning_reasons = list(preflight.get("warning_reasons") or [])
    gaps: list[dict[str, Any]] = []
    if not preflight.get("ready_for_runtime"):
        gaps.append(_gap("SLA-PREFLIGHT-RUNTIME", "Runtime target is not ready", "P0", "Preflight does not consider the environment runtime-ready.", total, "Configure a reachable non-production base URL and rerun preflight."))
    if not preflight.get("ready_for_p0_p1_runtime_validation"):
        gaps.append(_gap("SLA-P0P1-READY", "P0/P1 validation is not fully ready", "P0", "Preflight does not meet the high-value runtime validation gate.", len(p0_p1), "Resolve blocking checks and enable accounts/sandbox/snapshot evidence for high-value probes."))
    if blocked:
        gaps.append(_gap("SLA-BLOCKED-PROBES", "Some probes are blocked by environment capabilities", "P0", "Blocked probes cannot produce runtime evidence.", len(blocked), "Use the onboarding remediation kit to unblock base URL, sandbox, cleanup, or grounding gaps."))
    if len(p0_p1_ready) < len(p0_p1):
        gaps.append(_gap("SLA-P0P1-COVERAGE", "P0/P1 runtime coverage is incomplete", "P1", f"{len(p0_p1_ready)}/{len(p0_p1)} P0/P1 candidate probes are runtime-ready.", max(len(p0_p1) - len(p0_p1_ready), 0), "Prioritize missing capabilities on P0/P1 risk rows in the capability matrix."))
    if len(p0_p1_strong) < len(p0_p1):
        gaps.append(_gap("SLA-STRONG-EVIDENCE", "Strong before/after evidence coverage is incomplete", "P1", f"{len(p0_p1_strong)}/{len(p0_p1)} P0/P1 probes are expected to produce strong before/after evidence.", max(len(p0_p1) - len(p0_p1_strong), 0), "Enable snapshot observers and write-sandbox execution where needed."))
    if warning_reasons:
        gaps.append(_gap("SLA-WARNINGS", "Preflight warnings reduce confidence", "P2", ", ".join(str(x) for x in warning_reasons[:6]), len(warning_reasons), "Fix warnings to improve evidence quality and reduce manual review."))

    avg_quality = round(sum(_quality_score(str(r.get("expected_evidence_quality") or "")) for r in rows) / max(total, 1), 2)
    # Weighted commercial readiness score.  It is intentionally conservative:
    # blocked onboarding and missing P0/P1 strong evidence dominate the result.
    score = round(
        0.30 * coverage["p0_p1_runtime_ready_coverage_pct"]
        + 0.25 * coverage["high_value_runtime_ready_coverage_pct"]
        + 0.20 * coverage["p0_p1_strong_evidence_expected_coverage_pct"]
        + 0.15 * coverage["medium_or_better_evidence_expected_coverage_pct"]
        + 0.10 * avg_quality,
        2,
    )
    if blocking_reasons:
        score = min(score, 49.0)
    if remediation.get("p0_action_count"):
        score = min(score, 59.0)
    if not rows:
        score = 0.0
    score_int = int(round(score))
    level = _sla_level(
        score_int,
        blocking_reasons,
        coverage["p0_p1_runtime_ready_coverage_pct"],
        coverage["p0_p1_strong_evidence_expected_coverage_pct"],
        len([r for r in high_value if r in blocked]),
    )
    minimum = {
        "non_production_target": bool((checks.get("non_production_target") or {}).get("ok")),
        "base_url_configured": bool((checks.get("base_url_configured") or {}).get("ok")),
        "strict_document_grounding": bool((checks.get("probe_plan_grounded") or {}).get("ok")),
        "p0_p1_runtime_ready_coverage_at_least_65_pct": coverage["p0_p1_runtime_ready_coverage_pct"] >= 65.0,
        "p0_p1_strong_evidence_coverage_at_least_50_pct": coverage["p0_p1_strong_evidence_expected_coverage_pct"] >= 50.0,
        "no_blocking_preflight_reasons": not blocking_reasons,
        "no_p0_onboarding_actions": not bool(remediation.get("p0_action_count")),
    }

    return {
        "engine": "runtime_evidence_readiness_sla_gate_v1_phase93e",
        "commercial_readiness_score": score_int,
        "commercial_readiness_level": level,
        "sla_gate_passed": level in {"commercial_ready", "conditionally_ready"},
        "customer_acceptance_recommendation": _recommended_action(level, blocking_reasons, gaps),
        "minimum_commercial_gate": minimum,
        "coverage": coverage,
        "evidence_quality_average_score": avg_quality,
        "blocking_reasons": blocking_reasons,
        "warning_reasons": warning_reasons,
        "p0_action_count": int(remediation.get("p0_action_count") or 0),
        "p1_action_count": int(remediation.get("p1_action_count") or 0),
        "evidence_gaps": gaps,
        "blocked_high_value_candidate_ids": [str(r.get("candidate_id")) for r in high_value if r in blocked and r.get("candidate_id")],
        "degraded_candidate_ids": [str(r.get("candidate_id")) for r in degraded if r.get("candidate_id")],
        "ready_candidate_ids": [str(r.get("candidate_id")) for r in ready if r.get("candidate_id")],
        "customer_safe_note": "This SLA gate scores runtime evidence readiness only. It does not assert that any behavior is or is not a bug without runtime evidence.",
    }


def render_runtime_evidence_readiness_markdown(gate: dict[str, Any]) -> str:
    coverage = gate.get("coverage") if isinstance(gate.get("coverage"), dict) else {}
    lines = [
        "# Runtime Evidence Readiness SLA Gate",
        "",
        f"- engine: `{gate.get('engine')}`",
        f"- commercial readiness score: `{gate.get('commercial_readiness_score')}`",
        f"- level: `{gate.get('commercial_readiness_level')}`",
        f"- SLA gate passed: `{gate.get('sla_gate_passed')}`",
        f"- recommendation: {gate.get('customer_acceptance_recommendation')}",
        "",
        "## Coverage",
        "",
        f"- runtime ready coverage: `{coverage.get('runtime_ready_coverage_pct')}%`",
        f"- high-value runtime ready coverage: `{coverage.get('high_value_runtime_ready_coverage_pct')}%`",
        f"- P0/P1 runtime ready coverage: `{coverage.get('p0_p1_runtime_ready_coverage_pct')}%`",
        f"- P0/P1 strong evidence expected coverage: `{coverage.get('p0_p1_strong_evidence_expected_coverage_pct')}%`",
        f"- blocked probes: `{coverage.get('blocked_probe_count')}`",
        f"- degraded probes: `{coverage.get('degraded_probe_count')}`",
        "",
        "## Minimum commercial gate",
        "",
    ]
    minimum = gate.get("minimum_commercial_gate") if isinstance(gate.get("minimum_commercial_gate"), dict) else {}
    for key, value in minimum.items():
        lines.append(f"- `{key}`: `{value}`")
    gaps = [g for g in (gate.get("evidence_gaps") or []) if isinstance(g, dict)]
    if gaps:
        lines.extend(["", "## Evidence gaps", ""])
        for gap in gaps:
            lines.append(f"- **{gap.get('gap_id')}** `{gap.get('severity')}` — {gap.get('title')}: {gap.get('customer_action')}")
    lines.extend(["", f"> {gate.get('customer_safe_note')}"])
    return "\n".join(lines)
