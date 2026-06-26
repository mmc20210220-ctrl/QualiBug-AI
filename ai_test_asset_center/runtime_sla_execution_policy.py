from __future__ import annotations

"""Phase93F: SLA-gated runtime execution policy.

Phase93E scores customer environment evidence readiness.  Phase93F converts the
score and capability matrix into a run policy the delivery team can follow: what
must run for SLA acceptance, what may run as degraded evidence, and what must be
blocked until onboarding gaps are fixed.
"""

from typing import Any

READY_LANES = {"read_only_runtime_ready", "write_sandbox_runtime_ready"}
STRONG_EVIDENCE = "strong_runtime_before_after"
MEDIUM_EVIDENCE = "medium_runtime_request_response"
P0_P1_RISK_TYPES = {
    "ownership_scope_probe",
    "audit_privacy_probe",
    "state_transition_probe",
    "workflow_bypass_probe",
    "approval_flow_probe",
    "conservation_probe",
    "idempotency_replay_probe",
}


def _endpoint(row: dict[str, Any]) -> str:
    return f"{row.get('method')} {row.get('path')}".strip()


def _compact_row(row: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "candidate_id": row.get("candidate_id"),
        "risk_type": row.get("risk_type"),
        "endpoint": _endpoint(row),
        "lane": row.get("preflight_lane"),
        "expected_evidence_quality": row.get("expected_evidence_quality"),
        "reason": reason,
        "customer_action": row.get("customer_action"),
    }


def build_runtime_sla_execution_policy(report: dict[str, Any]) -> dict[str, Any]:
    gate = report.get("runtime_evidence_readiness_sla_gate") if isinstance(report.get("runtime_evidence_readiness_sla_gate"), dict) else {}
    matrix = report.get("runtime_capability_matrix") if isinstance(report.get("runtime_capability_matrix"), dict) else {}
    runbook = report.get("runtime_execution_runbook") if isinstance(report.get("runtime_execution_runbook"), dict) else {}
    rows = [r for r in (matrix.get("rows") or []) if isinstance(r, dict)]

    must_run: list[dict[str, Any]] = []
    optional_degraded: list[dict[str, Any]] = []
    blocked_before_sla: list[dict[str, Any]] = []
    supplemental_read_only: list[dict[str, Any]] = []

    for row in rows:
        risk = str(row.get("risk_type") or "")
        lane = str(row.get("preflight_lane") or "")
        quality = str(row.get("expected_evidence_quality") or "")
        is_p0_p1 = risk in P0_P1_RISK_TYPES
        if lane in READY_LANES and is_p0_p1 and quality == STRONG_EVIDENCE:
            must_run.append(_compact_row(row, "P0/P1 candidate has strong before/after evidence lane; include in SLA acceptance run."))
        elif lane in READY_LANES and is_p0_p1:
            must_run.append(_compact_row(row, "P0/P1 candidate is runtime-ready but may need manual evidence review because expected evidence is not strong before/after."))
        elif lane in READY_LANES:
            supplemental_read_only.append(_compact_row(row, "Runtime-ready but not mandatory for P0/P1 SLA acceptance."))
        elif "degraded" in lane:
            optional_degraded.append(_compact_row(row, "Can run only as degraded evidence; do not count as full P0/P1 SLA acceptance until missing optional capabilities are fixed."))
        else:
            blocked_before_sla.append(_compact_row(row, "Blocked or plan-only; must be remediated before this probe can count toward commercial SLA."))

    level = str(gate.get("commercial_readiness_level") or "not_ready")
    gate_passed = bool(gate.get("sla_gate_passed"))
    if gate_passed and must_run:
        mode = "sla_acceptance_runtime"
    elif gate_passed:
        mode = "conditional_runtime_no_mandatory_p0p1"
    elif must_run and optional_degraded:
        mode = "partial_runtime_with_degraded_review"
    elif must_run:
        mode = "partial_runtime_sla_not_claimable"
    elif supplemental_read_only:
        mode = "read_only_smoke_only"
    else:
        mode = "onboarding_blocked"

    policy_flags = {
        "allow_read_only_smoke": bool(supplemental_read_only or must_run or optional_degraded),
        "allow_write_sandbox_for_must_run": bool(must_run and level != "not_ready"),
        "count_degraded_findings_toward_sla": False,
        "require_customer_approval_for_write_sandbox": True,
        "require_post_fix_rerun_for_closed_findings": True,
        "block_production_like_targets": True,
    }
    acceptance = {
        "can_claim_commercial_runtime_sla": bool(gate_passed and must_run and not blocked_before_sla),
        "can_claim_conditional_runtime_sla": bool(gate_passed and must_run),
        "must_not_claim_sla_reason": "" if gate_passed and must_run else "SLA gate has not passed or no mandatory P0/P1 runtime probes are ready.",
        "readiness_score": gate.get("commercial_readiness_score"),
        "readiness_level": level,
        "runbook_status": runbook.get("status"),
    }
    return {
        "engine": "runtime_sla_execution_policy_v1_phase93f",
        "policy_mode": mode,
        "policy_flags": policy_flags,
        "acceptance": acceptance,
        "must_run_for_sla_count": len(must_run),
        "optional_degraded_count": len(optional_degraded),
        "blocked_before_sla_count": len(blocked_before_sla),
        "supplemental_ready_count": len(supplemental_read_only),
        "must_run_for_sla": must_run,
        "optional_degraded_probes": optional_degraded,
        "blocked_before_sla": blocked_before_sla,
        "supplemental_ready_probes": supplemental_read_only,
        "recommended_sequence": _recommended_sequence(mode, must_run, optional_degraded, blocked_before_sla),
        "customer_safe_note": "This policy determines which probes can count toward commercial runtime SLA. It does not suppress validated findings; it only controls acceptance claims and execution grouping.",
    }


def _recommended_sequence(mode: str, must_run: list[dict[str, Any]], optional_degraded: list[dict[str, Any]], blocked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = [
        {"step_id": "SLA-POLICY-01", "title": "Review readiness gate", "goal": "Confirm non-production target, grounding, accounts, sandbox and snapshot readiness before claiming SLA."}
    ]
    if must_run:
        steps.append({
            "step_id": "SLA-POLICY-02",
            "title": "Run mandatory P0/P1 SLA probes",
            "goal": "Collect request/response and before/after evidence for mandatory high-value probes.",
            "candidate_ids": [str(x.get("candidate_id")) for x in must_run if x.get("candidate_id")],
        })
    if optional_degraded:
        steps.append({
            "step_id": "SLA-POLICY-03",
            "title": "Run degraded probes as supplemental only",
            "goal": "Collect partial evidence without counting these probes as full SLA coverage.",
            "candidate_ids": [str(x.get("candidate_id")) for x in optional_degraded if x.get("candidate_id")],
        })
    if blocked:
        steps.append({
            "step_id": "SLA-POLICY-04",
            "title": "Unblock before SLA acceptance",
            "goal": "Resolve environment gaps for blocked probes and rerun preflight/capability matrix.",
            "candidate_ids": [str(x.get("candidate_id")) for x in blocked if x.get("candidate_id")],
        })
    steps.append({"step_id": "SLA-POLICY-05", "title": "Post-fix rerun", "goal": "After customer fixes findings, rerun remediation verification and lifecycle matching before closing P0/P1 findings."})
    return steps


def render_runtime_sla_execution_policy_markdown(policy: dict[str, Any]) -> str:
    acceptance = policy.get("acceptance") if isinstance(policy.get("acceptance"), dict) else {}
    lines = [
        "# Runtime SLA Execution Policy",
        "",
        f"- engine: `{policy.get('engine')}`",
        f"- policy mode: `{policy.get('policy_mode')}`",
        f"- can claim commercial runtime SLA: `{acceptance.get('can_claim_commercial_runtime_sla')}`",
        f"- can claim conditional runtime SLA: `{acceptance.get('can_claim_conditional_runtime_sla')}`",
        f"- readiness score: `{acceptance.get('readiness_score')}`",
        f"- readiness level: `{acceptance.get('readiness_level')}`",
        "",
        "## Probe groups",
        "",
        f"- must run for SLA: `{policy.get('must_run_for_sla_count')}`",
        f"- optional degraded: `{policy.get('optional_degraded_count')}`",
        f"- blocked before SLA: `{policy.get('blocked_before_sla_count')}`",
        f"- supplemental ready: `{policy.get('supplemental_ready_count')}`",
        "",
        "## Recommended sequence",
        "",
    ]
    for step in policy.get("recommended_sequence") or []:
        if not isinstance(step, dict):
            continue
        lines.append(f"- **{step.get('step_id')}** — {step.get('title')}: {step.get('goal')}")
        if step.get("candidate_ids"):
            lines.append(f"  - candidate ids: `{', '.join(str(x) for x in step.get('candidate_ids'))}`")
    if policy.get("blocked_before_sla"):
        lines.extend(["", "## Blocked before SLA", ""])
        for item in policy.get("blocked_before_sla") or []:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('candidate_id')}` `{item.get('endpoint')}` — {item.get('customer_action')}")
    lines.extend(["", f"> {policy.get('customer_safe_note')}"])
    return "\n".join(lines)
