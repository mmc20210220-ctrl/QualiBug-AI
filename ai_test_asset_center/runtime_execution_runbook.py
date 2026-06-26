from __future__ import annotations

"""Phase93D: customer runtime execution runbook.

The runbook converts onboarding and capability data into an execution sequence
customers can follow: plan-only setup, read-only smoke/runtime validation,
approved write-sandbox validation, then remediation rerun/review.
"""

from typing import Any


def _rows_by_lane(matrix: dict[str, Any], lane: str) -> list[dict[str, Any]]:
    return [r for r in (matrix.get("rows") or []) if isinstance(r, dict) and r.get("preflight_lane") == lane]


def _ids(rows: list[dict[str, Any]]) -> list[str]:
    return [str(r.get("candidate_id")) for r in rows if r.get("candidate_id")]


def _endpoint(row: dict[str, Any]) -> str:
    return f"{row.get('method')} {row.get('path')}".strip()


def build_runtime_execution_runbook(report: dict[str, Any]) -> dict[str, Any]:
    preflight = report.get("onboarding_preflight") if isinstance(report.get("onboarding_preflight"), dict) else {}
    matrix = report.get("runtime_capability_matrix") if isinstance(report.get("runtime_capability_matrix"), dict) else {}
    remediation = report.get("onboarding_remediation_kit") if isinstance(report.get("onboarding_remediation_kit"), dict) else {}
    rows = [r for r in (matrix.get("rows") or []) if isinstance(r, dict)]
    read_ready = _rows_by_lane(matrix, "read_only_runtime_ready")
    write_ready = _rows_by_lane(matrix, "write_sandbox_runtime_ready")
    degraded = [r for r in rows if "degraded" in str(r.get("preflight_lane") or "")]
    blocked = [r for r in rows if "blocked" in str(r.get("preflight_lane") or "")]
    plan_only = [r for r in rows if r.get("preflight_lane") == "plan_only"]

    if blocked and not (read_ready or write_ready or degraded):
        status = "blocked"
    elif remediation.get("p0_action_count"):
        status = "blocked_until_p0_onboarding_actions_resolved"
    elif degraded:
        status = "degraded_runnable"
    elif read_ready or write_ready:
        status = "ready"
    else:
        status = "plan_only"

    steps: list[dict[str, Any]] = []
    steps.append({
        "step_id": "RUNBOOK-01-PREFLIGHT",
        "title": "Review onboarding preflight and capability matrix",
        "when": "always",
        "goal": "Confirm the target is non-production and identify probes that are ready, degraded or blocked before runtime execution.",
        "inputs": ["grounded_probe_onboarding_preflight.json", "grounded_probe_runtime_capability_matrix.json", "grounded_probe_onboarding_remediation_kit.md"],
        "success_condition": "No P0 onboarding remediation actions remain for the intended execution lane.",
    })
    if read_ready or degraded:
        steps.append({
            "step_id": "RUNBOOK-02-READONLY",
            "title": "Run read-only runtime probes first",
            "when": "read-only ready or degraded probes exist",
            "candidate_ids": _ids(read_ready + [r for r in degraded if str(r.get("method") or "").upper() in {"GET", "HEAD"}]),
            "command_template": "python -m ai_test_asset_center.grounded_probe_executor --execute-readonly --probe-config <FILL:staging_probe_config.json>",
            "success_condition": "Report contains request/response observations and no unexpected onboarding/network blockers.",
        })
    if write_ready:
        steps.append({
            "step_id": "RUNBOOK-03-WRITE-SANDBOX",
            "title": "Run approved disposable-sandbox write probes",
            "when": "write_sandbox_runtime_ready probes exist",
            "candidate_ids": _ids(write_ready),
            "command_template": "$env:QUALIBUG_ALLOW_GROUNDED_WRITE_PROBES='1'; python -m ai_test_asset_center.grounded_probe_executor --allow-write-sandbox --approval-id <FILL:approval-id> --probe-config <FILL:staging_probe_config.json>",
            "success_condition": "Before/after snapshots, cleanup receipts and remediation artifacts are generated for validated P0/P1 findings.",
        })
    if blocked or plan_only:
        steps.append({
            "step_id": "RUNBOOK-04-UNBLOCK",
            "title": "Resolve blocked or plan-only probes before expecting runtime evidence",
            "when": "blocked or plan_only probes exist",
            "candidate_ids": _ids(blocked + plan_only),
            "top_missing_capabilities": _top_missing(blocked + plan_only),
            "success_condition": "Rerun preflight; blocked probes move to ready/degraded lanes.",
        })
    steps.append({
        "step_id": "RUNBOOK-05-FIX-VERIFY",
        "title": "Use remediation verification artifacts for fix and rerun",
        "when": "validated findings exist or remediation work items are generated",
        "inputs": ["grounded_probe_remediation_verification.md", "grounded_probe_repro.ps1", "grounded_probe_regression_pytest.py"],
        "success_condition": "Finding lifecycle moves to closed_by_rerun or remains open with fresh before/after evidence.",
    })

    return {
        "engine": "runtime_execution_runbook_v1_phase93d",
        "status": status,
        "preflight_status": preflight.get("status"),
        "ready_for_p0_p1_runtime_validation": bool(preflight.get("ready_for_p0_p1_runtime_validation")),
        "lane_summary": matrix.get("by_preflight_lane") or {},
        "read_only_ready_candidate_ids": _ids(read_ready),
        "write_sandbox_ready_candidate_ids": _ids(write_ready),
        "degraded_candidate_ids": _ids(degraded),
        "blocked_candidate_ids": _ids(blocked),
        "plan_only_candidate_ids": _ids(plan_only),
        "high_value_runtime_ready_count": matrix.get("high_value_runtime_ready_count", 0),
        "steps": steps,
        "review_focus": _review_focus(rows),
        "recommended_usage": "Run the steps in order: preflight review, read-only first, approved write-sandbox second, then fix-verification rerun using generated remediation artifacts.",
    }


def _top_missing(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        for cap in list(row.get("missing_blocking_capabilities") or []) + list(row.get("missing_optional_capabilities") or []):
            key = str(cap)
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:12])


def _review_focus(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    interesting = sorted(rows, key=lambda r: (
        0 if r.get("preflight_lane") == "write_sandbox_runtime_ready" else 1 if r.get("preflight_lane") == "read_only_runtime_ready" else 2,
        str(r.get("candidate_id") or ""),
    ))[:20]
    return [
        {
            "candidate_id": r.get("candidate_id"),
            "lane": r.get("preflight_lane"),
            "endpoint": _endpoint(r),
            "expected_evidence_quality": r.get("expected_evidence_quality"),
            "customer_action": r.get("customer_action"),
        }
        for r in interesting
    ]


def render_runtime_execution_runbook_markdown(runbook: dict[str, Any]) -> str:
    lines = [
        "# QualiBug Runtime Execution Runbook",
        "",
        f"- engine: `{runbook.get('engine')}`",
        f"- status: `{runbook.get('status')}`",
        f"- preflight: `{runbook.get('preflight_status')}`",
        f"- P0/P1 ready: `{runbook.get('ready_for_p0_p1_runtime_validation')}`",
        f"- lane summary: `{runbook.get('lane_summary')}`",
        "",
        "## Execution sequence",
        "",
    ]
    for step in runbook.get("steps") or []:
        if not isinstance(step, dict):
            continue
        lines.extend([
            f"### {step.get('step_id')} — {step.get('title')}",
            "",
            f"- when: {step.get('when')}",
            f"- goal: {step.get('goal') or step.get('success_condition')}",
        ])
        if step.get("candidate_ids"):
            lines.append(f"- candidate ids: `{', '.join(str(x) for x in step.get('candidate_ids'))}`")
        if step.get("command_template"):
            lines.extend(["", "```bash", str(step.get("command_template")), "```"])
        lines.append("")
    if runbook.get("review_focus"):
        lines.extend(["## Review focus", ""])
        for item in runbook.get("review_focus") or []:
            lines.append(f"- `{item.get('candidate_id')}` `{item.get('lane')}` `{item.get('endpoint')}` — {item.get('customer_action')}")
    return "\n".join(lines)
