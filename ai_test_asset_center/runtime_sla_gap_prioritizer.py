from __future__ import annotations

"""Phase93G: SLA gap auto-prioritizer and onboarding delta patcher.

Phase93E/F tell whether the customer environment can satisfy commercial runtime
SLA.  Phase93G turns failures into a smallest-next-delta plan: which onboarding
change should be made first, how much it is expected to improve the SLA score,
which P0/P1 probes it unblocks, and what safe ``probe_config`` patch should be
sent back to the customer for the next rerun.

This module is deliberately heuristic and evidence-readiness-only.  It never
creates or suppresses bug findings; it only helps customer onboarding converge
faster toward strong runtime evidence.
"""

from copy import deepcopy
from typing import Any

P0_P1_RISK_TYPES = {
    "ownership_scope_probe",
    "audit_privacy_probe",
    "state_transition_probe",
    "workflow_bypass_probe",
    "approval_flow_probe",
    "conservation_probe",
    "idempotency_replay_probe",
}

ACTION_METADATA: dict[str, dict[str, Any]] = {
    "ONBOARD-BASE-URL": {
        "gap_key": "base_url",
        "title": "Configure reachable staging/QA base_url",
        "estimated_score_gain": 35,
        "estimated_p0_p1_coverage_gain_pct": 35.0,
        "why_first": "No runtime evidence can be collected until QualiBug has a concrete non-production target.",
        "impact_area": "target_reachability",
    },
    "ONBOARD-NON-PROD": {
        "gap_key": "non_production_target",
        "title": "Switch to a clearly non-production target",
        "estimated_score_gain": 30,
        "estimated_p0_p1_coverage_gain_pct": 30.0,
        "why_first": "Write-sandbox probes and SLA acceptance must stay blocked on production-like targets.",
        "impact_area": "safety_governance",
    },
    "ONBOARD-PLACEHOLDERS": {
        "gap_key": "config_placeholders_resolved",
        "title": "Replace non-executable template placeholders",
        "estimated_score_gain": 25,
        "estimated_p0_p1_coverage_gain_pct": 25.0,
        "why_first": "Placeholder values make otherwise-ready probes non-executable and can poison rerun evidence.",
        "impact_area": "config_executability",
    },
    "ONBOARD-CLEANUP": {
        "gap_key": "cleanup",
        "title": "Declare cleanup/reset strategy",
        "estimated_score_gain": 22,
        "estimated_p0_p1_coverage_gain_pct": 20.0,
        "why_first": "P0/P1 write probes need repeatable cleanup before they can count toward commercial SLA.",
        "impact_area": "write_sandbox_repeatability",
    },
    "ONBOARD-AUTO-FIXTURE": {
        "gap_key": "write_sandbox",
        "title": "Enable disposable auto fixture creation",
        "estimated_score_gain": 20,
        "estimated_p0_p1_coverage_gain_pct": 18.0,
        "why_first": "High-value write probes need QualiBug-created data instead of customer-supplied business IDs.",
        "impact_area": "write_sandbox_execution",
    },
    "ONBOARD-SNAPSHOT": {
        "gap_key": "snapshot_observer",
        "title": "Expose read-only before/after snapshot observers",
        "estimated_score_gain": 18,
        "estimated_p0_p1_coverage_gain_pct": 15.0,
        "why_first": "Strong P0/P1 evidence needs before/after observers for state, ledger, inventory, tenant and audit views.",
        "impact_area": "strong_evidence_observability",
    },
    "ONBOARD-AUTH": {
        "gap_key": "auth_session",
        "title": "Provide auth_flow and disposable test accounts",
        "estimated_score_gain": 14,
        "estimated_p0_p1_coverage_gain_pct": 12.0,
        "why_first": "Auth, ownership and privacy probes are degraded without real staging sessions.",
        "impact_area": "identity_runtime_session",
    },
    "ONBOARD-ROLES": {
        "gap_key": "auth_session",
        "title": "Add normal/admin/owner/cross-tenant role coverage",
        "estimated_score_gain": 12,
        "estimated_p0_p1_coverage_gain_pct": 10.0,
        "why_first": "Boundary and tenant isolation probes need multiple role/tenant identities to become high-confidence evidence.",
        "impact_area": "tenant_role_coverage",
    },
}

PRIORITY_WEIGHT = {"P0": 4, "P1": 3, "P2": 2, "P3": 1}
SENSITIVE_PLACEHOLDER = "<FILL:customer_staging_secret>"


def _endpoint(row: dict[str, Any]) -> str:
    return f"{row.get('method')} {row.get('path')}".strip()


def _rows_for_gap(rows: list[dict[str, Any]], gap_key: str) -> list[dict[str, Any]]:
    affected: list[dict[str, Any]] = []
    for row in rows:
        missing = list(row.get("missing_blocking_capabilities") or []) + list(row.get("missing_optional_capabilities") or [])
        if gap_key in {str(x) for x in missing}:
            affected.append(row)
    return affected


def _compact_probe(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": row.get("candidate_id"),
        "risk_type": row.get("risk_type"),
        "endpoint": _endpoint(row),
        "lane": row.get("preflight_lane"),
        "expected_evidence_quality": row.get("expected_evidence_quality"),
    }


def _merge_patch(base: dict[str, Any], fragment: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(base)
    for key, value in fragment.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge_patch(out[key], value)
        else:
            out[key] = deepcopy(value)
    return out


def _patch_fragment_for_action(action_id: str, recommended_patch: dict[str, Any]) -> dict[str, Any]:
    """Extract the smallest safe patch fragment for a remediation action."""

    if action_id in {"ONBOARD-BASE-URL", "ONBOARD-NON-PROD"}:
        return {k: deepcopy(v) for k, v in recommended_patch.items() if k in {"base_url", "environment_kind"}}
    if action_id == "ONBOARD-AUTH":
        return {k: deepcopy(v) for k, v in recommended_patch.items() if k in {"auth_flow", "accounts", "default_account"}}
    if action_id == "ONBOARD-ROLES":
        accounts = deepcopy(recommended_patch.get("accounts") or {})
        return {"accounts": accounts, "default_account": recommended_patch.get("default_account", "normal_user")}
    if action_id in {"ONBOARD-AUTO-FIXTURE", "ONBOARD-CLEANUP"}:
        return {k: deepcopy(v) for k, v in recommended_patch.items() if k in {"test_environment", "auto_fixture"}}
    if action_id == "ONBOARD-SNAPSHOT":
        return {"snapshots": deepcopy(recommended_patch.get("snapshots") or {"*": {"before": [], "after": []}})}
    if action_id == "ONBOARD-PLACEHOLDERS":
        return {"probe_config_placeholders": "replace every <FILL:...> value with disposable staging values before execution"}
    return {}


def _fallback_fragment(action_id: str) -> dict[str, Any]:
    if action_id in {"ONBOARD-BASE-URL", "ONBOARD-NON-PROD"}:
        return {"base_url": "https://<FILL:staging-host>", "environment_kind": "staging"}
    if action_id in {"ONBOARD-AUTH", "ONBOARD-ROLES"}:
        return {
            "auth_flow": {
                "login_path": "/<FILL:login-path>",
                "method": "POST",
                "username_field": "username",
                "password_field": "password",
                "token_json_path": "data.access_token",
                "token_header_name": "Authorization",
                "token_header_prefix": "Bearer",
            },
            "accounts": {
                "normal_user": {"role": "normal_user", "username": "<FILL:staging-normal-user>", "password": SENSITIVE_PLACEHOLDER, "tenant_id": "<FILL:tenant-A>"},
                "admin_user": {"role": "admin", "username": "<FILL:staging-admin-user>", "password": SENSITIVE_PLACEHOLDER, "tenant_id": "<FILL:tenant-A>"},
                "owner_user": {"role": "owner", "username": "<FILL:staging-owner-user>", "password": SENSITIVE_PLACEHOLDER, "tenant_id": "<FILL:tenant-A>"},
                "cross_tenant_user": {"role": "other_tenant_user", "username": "<FILL:staging-tenant-b-user>", "password": SENSITIVE_PLACEHOLDER, "tenant_id": "<FILL:tenant-B>"},
            },
            "default_account": "normal_user",
        }
    if action_id in {"ONBOARD-AUTO-FIXTURE", "ONBOARD-CLEANUP"}:
        return {"test_environment": {"enabled": True, "allow_write_probes": True, "kind": "staging", "cleanup_strategy": "fixture_reset"}, "auto_fixture": {"enabled": True}}
    if action_id == "ONBOARD-SNAPSHOT":
        return {"snapshots": {"*": {"before": [{"method": "GET", "path": "/<FILL:resource-detail-or-ledger-observer>", "observer_kind": "primary_resource_detail"}], "after": [{"method": "GET", "path": "/<FILL:resource-detail-or-ledger-observer>", "observer_kind": "primary_resource_detail"}]}}}
    return {}


def _action_score(action: dict[str, Any], affected_rows: list[dict[str, Any]], current_score: int) -> tuple[int, int, int, str]:
    action_id = str(action.get("action_id") or "")
    meta = ACTION_METADATA.get(action_id, {})
    priority = str(action.get("priority") or "P2")
    p0p1_count = sum(1 for r in affected_rows if str(r.get("risk_type") or "") in P0_P1_RISK_TYPES)
    high_value_count = sum(1 for r in affected_rows if r.get("high_value_runtime_risk"))
    base_gain = int(meta.get("estimated_score_gain") or 8)
    if current_score >= 70:
        base_gain = max(3, round(base_gain * 0.55))
    rank = PRIORITY_WEIGHT.get(priority, 1) * 1000 + p0p1_count * 120 + high_value_count * 80 + base_gain
    return rank, base_gain, p0p1_count, str(meta.get("why_first") or action.get("reason") or "Improve runtime evidence readiness.")


def build_runtime_sla_gap_prioritizer(report: dict[str, Any]) -> dict[str, Any]:
    """Prioritize SLA gaps and produce the smallest next onboarding patch."""

    gate = report.get("runtime_evidence_readiness_sla_gate") if isinstance(report.get("runtime_evidence_readiness_sla_gate"), dict) else {}
    policy = report.get("runtime_sla_execution_policy") if isinstance(report.get("runtime_sla_execution_policy"), dict) else {}
    remediation = report.get("onboarding_remediation_kit") if isinstance(report.get("onboarding_remediation_kit"), dict) else {}
    matrix = report.get("runtime_capability_matrix") if isinstance(report.get("runtime_capability_matrix"), dict) else {}
    rows = [r for r in (matrix.get("rows") or []) if isinstance(r, dict)]
    actions = [a for a in (remediation.get("actions") or []) if isinstance(a, dict)]
    recommended_patch = remediation.get("recommended_probe_config_patch") if isinstance(remediation.get("recommended_probe_config_patch"), dict) else {}
    current_score = int(gate.get("commercial_readiness_score") or 0)

    prioritized: list[dict[str, Any]] = []
    for action in actions:
        action_id = str(action.get("action_id") or "")
        meta = ACTION_METADATA.get(action_id, {})
        gap_key = str(meta.get("gap_key") or "")
        affected_rows = _rows_for_gap(rows, gap_key) if gap_key else []
        if not affected_rows and action_id in {"ONBOARD-BASE-URL", "ONBOARD-NON-PROD", "ONBOARD-PLACEHOLDERS"}:
            affected_rows = rows
        rank, gain, p0p1_count, why_first = _action_score(action, affected_rows, current_score)
        fragment = _patch_fragment_for_action(action_id, recommended_patch) or _fallback_fragment(action_id)
        prioritized.append({
            "action_id": action_id,
            "priority": action.get("priority") or "P2",
            "title": meta.get("title") or action.get("title"),
            "impact_area": meta.get("impact_area") or "runtime_evidence_readiness",
            "rank_score": rank,
            "estimated_score_gain": gain,
            "estimated_p0_p1_coverage_gain_pct": float(meta.get("estimated_p0_p1_coverage_gain_pct") or min(25.0, p0p1_count * 8.0)),
            "affected_probe_count": len(affected_rows),
            "affected_p0_p1_probe_count": p0p1_count,
            "affected_high_value_probe_count": sum(1 for r in affected_rows if r.get("high_value_runtime_risk")),
            "affected_probe_samples": [_compact_probe(r) for r in affected_rows[:6]],
            "blocking_policy_probe_samples": [x for x in (policy.get("blocked_before_sla") or []) if isinstance(x, dict)][:6] if action_id in {"ONBOARD-BASE-URL", "ONBOARD-NON-PROD", "ONBOARD-CLEANUP", "ONBOARD-AUTO-FIXTURE"} else [],
            "why_first": why_first,
            "customer_action": action.get("validation_after_change") or action.get("reason"),
            "patch_fragment": fragment,
        })

    prioritized.sort(key=lambda x: (-int(x.get("rank_score") or 0), str(x.get("action_id") or "")))
    top_actions = prioritized[:3]
    minimal_patch: dict[str, Any] = {}
    for action in top_actions:
        minimal_patch = _merge_patch(minimal_patch, action.get("patch_fragment") or {})
    cumulative_gain = sum(int(a.get("estimated_score_gain") or 0) for a in top_actions)
    all_gain = sum(int(a.get("estimated_score_gain") or 0) for a in prioritized)
    estimated_after_top = min(100, current_score + cumulative_gain)
    estimated_after_all = min(100, current_score + all_gain)

    if not prioritized and bool(gate.get("sla_gate_passed")):
        status = "commercial_ready_no_onboarding_delta_required"
        recommendation = "SLA gate already passed; run the Phase93F SLA policy and use Phase92Z remediation artifacts for any validated findings."
    elif prioritized:
        status = "needs_onboarding_delta_patch"
        recommendation = "Apply the top prioritized onboarding patch, rerun preflight/capability matrix/SLA gate, then run only the Phase93F mandatory SLA probes."
    else:
        status = "no_action_available_manual_review"
        recommendation = "SLA gate did not pass but no structured onboarding actions were available; review preflight blocking reasons and regenerate the remediation kit."

    return {
        "engine": "runtime_sla_gap_prioritizer_v1_phase93g",
        "status": status,
        "current_readiness_score": current_score,
        "estimated_readiness_score_after_top_actions": estimated_after_top,
        "estimated_readiness_score_after_all_actions": estimated_after_all,
        "current_sla_gate_passed": bool(gate.get("sla_gate_passed")),
        "commercial_readiness_level": gate.get("commercial_readiness_level"),
        "action_count": len(prioritized),
        "top_action_count": len(top_actions),
        "p0_action_count": sum(1 for a in prioritized if a.get("priority") == "P0"),
        "p1_action_count": sum(1 for a in prioritized if a.get("priority") == "P1"),
        "prioritized_gap_actions": prioritized,
        "top_priority_actions": top_actions,
        "minimal_next_onboarding_patch": minimal_patch,
        "next_rerun_sequence": _next_rerun_sequence(top_actions, estimated_after_top, policy),
        "customer_acceptance_delta_summary": _delta_summary(current_score, estimated_after_top, estimated_after_all, prioritized, gate),
        "customer_safe_note": "Patch templates use placeholders only. Customers should provide disposable staging accounts and non-production URLs; production secrets are not required.",
        "recommendation": recommendation,
    }


def _delta_summary(current: int, after_top: int, after_all: int, actions: list[dict[str, Any]], gate: dict[str, Any]) -> dict[str, Any]:
    return {
        "current_score": current,
        "score_gain_after_top_actions": max(after_top - current, 0),
        "score_gain_after_all_actions": max(after_all - current, 0),
        "first_action": actions[0].get("action_id") if actions else None,
        "first_action_reason": actions[0].get("why_first") if actions else None,
        "blocked_high_value_candidate_ids": list(gate.get("blocked_high_value_candidate_ids") or []),
        "degraded_candidate_ids": list(gate.get("degraded_candidate_ids") or []),
    }


def _next_rerun_sequence(top_actions: list[dict[str, Any]], estimated_score: int, policy: dict[str, Any]) -> list[dict[str, Any]]:
    steps = [
        {"step_id": "GAP-01", "title": "Apply minimal onboarding patch", "goal": "Customer fills the placeholders in minimal_next_onboarding_patch using disposable staging values.", "action_ids": [str(a.get("action_id")) for a in top_actions]},
        {"step_id": "GAP-02", "title": "Rerun onboarding preflight", "goal": "Confirm the patched checks pass before executing write-sandbox probes."},
        {"step_id": "GAP-03", "title": "Rebuild capability matrix and SLA gate", "goal": f"Expected readiness score after top actions is approximately {estimated_score}; verify actual score from rerun evidence."},
    ]
    must_run = [str(x.get("candidate_id")) for x in (policy.get("must_run_for_sla") or []) if isinstance(x, dict) and x.get("candidate_id")]
    if must_run:
        steps.append({"step_id": "GAP-04", "title": "Run mandatory SLA probes", "goal": "Execute only mandatory P0/P1 SLA probes after the gate is acceptable.", "candidate_ids": must_run})
    else:
        steps.append({"step_id": "GAP-04", "title": "Regenerate SLA policy", "goal": "No mandatory probes were ready in the current policy; rerun Phase93F after onboarding gaps are fixed."})
    return steps


def render_runtime_sla_gap_prioritizer_markdown(plan: dict[str, Any]) -> str:
    lines = [
        "# Runtime SLA Gap Prioritizer",
        "",
        f"- engine: `{plan.get('engine')}`",
        f"- status: `{plan.get('status')}`",
        f"- current readiness score: `{plan.get('current_readiness_score')}`",
        f"- estimated after top actions: `{plan.get('estimated_readiness_score_after_top_actions')}`",
        f"- estimated after all actions: `{plan.get('estimated_readiness_score_after_all_actions')}`",
        f"- action count: `{plan.get('action_count')}`",
        "",
        "## Top priority actions",
        "",
    ]
    actions = [a for a in (plan.get("top_priority_actions") or []) if isinstance(a, dict)]
    if not actions:
        lines.append("No onboarding delta action is required.")
        lines.append("")
    for action in actions:
        lines.extend([
            f"### {action.get('action_id')} — {action.get('title')}",
            "",
            f"- priority: `{action.get('priority')}`",
            f"- estimated score gain: `+{action.get('estimated_score_gain')}`",
            f"- affected P0/P1 probes: `{action.get('affected_p0_p1_probe_count')}`",
            f"- why first: {action.get('why_first')}",
            "",
        ])
    lines.extend([
        "## Minimal next onboarding patch",
        "",
        "```json",
        _json_dumps(plan.get("minimal_next_onboarding_patch") or {}),
        "```",
        "",
        "## Next rerun sequence",
        "",
    ])
    for step in plan.get("next_rerun_sequence") or []:
        if isinstance(step, dict):
            lines.append(f"- **{step.get('step_id')}** — {step.get('title')}: {step.get('goal')}")
    lines.extend(["", f"> {plan.get('customer_safe_note')}"])
    return "\n".join(lines)


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)
