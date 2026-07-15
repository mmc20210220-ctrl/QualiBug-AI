from __future__ import annotations

"""Phase93I: write-sandbox approval packet.

QualiBug already requires explicit write-sandbox flags and approval ids before
executing write probes.  Phase93I packages the customer-facing approval contract:
which mandatory SLA probes need approval, what safety checks must pass, and why
execution remains blocked if approval or environment safeguards are missing.
"""

from typing import Any

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _method_from_item(item: dict[str, Any]) -> str:
    endpoint = str(item.get("endpoint") or "").strip()
    if endpoint:
        return endpoint.split()[0].upper()
    return str(item.get("method") or "").upper()


def _is_write_item(item: dict[str, Any]) -> bool:
    return _method_from_item(item) in WRITE_METHODS


def _candidate_ids(items: list[dict[str, Any]]) -> list[str]:
    return [str(x.get("candidate_id")) for x in items if isinstance(x, dict) and x.get("candidate_id")]


def build_write_sandbox_approval_packet(report: dict[str, Any]) -> dict[str, Any]:
    policy = report.get("runtime_sla_execution_policy") if isinstance(report.get("runtime_sla_execution_policy"), dict) else {}
    safety = report.get("onboarding_patch_safety_validation") if isinstance(report.get("onboarding_patch_safety_validation"), dict) else {}
    preflight = report.get("onboarding_preflight") if isinstance(report.get("onboarding_preflight"), dict) else {}
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}

    must_run = [x for x in (policy.get("must_run_for_sla") or []) if isinstance(x, dict)]
    supplemental = [x for x in (policy.get("supplemental_ready_probes") or []) if isinstance(x, dict)]
    blocked = [x for x in (policy.get("blocked_before_sla") or []) if isinstance(x, dict)]
    degraded = [x for x in (policy.get("optional_degraded_probes") or []) if isinstance(x, dict)]
    write_must_run = [x for x in must_run if _is_write_item(x)]
    write_supplemental = [x for x in supplemental if _is_write_item(x)]
    write_blocked = [x for x in blocked if _is_write_item(x)]
    write_degraded = [x for x in degraded if _is_write_item(x)]

    allow_write = bool(report.get("allow_write_sandbox"))
    approval_present = bool(report.get("approval_id_present"))
    safety_ok = bool(safety.get("safe_to_send_to_customer", True)) and str(safety.get("status") or "safe_to_send") != "unsafe_blocked"
    nonprod_blocked = "non_production_target" in {str(x) for x in (preflight.get("blocking_reasons") or [])}
    cleanup_blocked = any("cleanup" in str(x.get("customer_action") or "").lower() or "cleanup" in str(x.get("reason") or "").lower() for x in blocked)

    blocking_reasons: list[str] = []
    if write_must_run or write_supplemental or write_degraded or write_blocked:
        if not allow_write:
            blocking_reasons.append("allow_write_sandbox flag is not enabled for this run")
        if not approval_present:
            blocking_reasons.append("write-sandbox approval_id is missing")
    if not safety_ok:
        blocking_reasons.append("onboarding patch safety validation is unsafe or blocked")
    if nonprod_blocked:
        blocking_reasons.append("non-production target check is not passing")
    if cleanup_blocked:
        blocking_reasons.append("cleanup/reset readiness is not sufficient for blocked write probes")

    write_required = bool(write_must_run or write_supplemental or write_degraded or write_blocked)
    if not write_required:
        status = "no_write_approval_required"
    elif blocking_reasons:
        status = "approval_blocked"
    elif write_must_run:
        status = "ready_for_customer_approval"
    else:
        status = "supplemental_write_approval_optional"

    checklist = [
        {"item_id": "APPROVAL-NON-PROD", "required": True, "passed": not nonprod_blocked, "text": "Customer confirms the target is staging/QA/UAT/sandbox and not production."},
        {"item_id": "APPROVAL-SAFETY", "required": True, "passed": safety_ok, "text": "Onboarding patch safety validation has no P0 blockers and contains no raw secrets."},
        {"item_id": "APPROVAL-CLEANUP", "required": True, "passed": not cleanup_blocked, "text": "Cleanup/reset strategy is declared for write-sandbox probes."},
        {"item_id": "APPROVAL-FLAG", "required": bool(write_required), "passed": bool(allow_write), "text": "Delivery operator enabled allow_write_sandbox only for disposable test data."},
        {"item_id": "APPROVAL-ID", "required": bool(write_required), "passed": bool(approval_present), "text": "Customer approval_id is present and recorded in the execution report."},
    ]

    return {
        "engine": "runtime_write_sandbox_approval_packet_v1_phase93i",
        "status": status,
        "write_approval_required": write_required,
        "ready_for_customer_approval": status in {"ready_for_customer_approval", "supplemental_write_approval_optional"},
        "blocking_reasons": blocking_reasons,
        "approval_required_candidate_ids": _candidate_ids(write_must_run + write_supplemental),
        "mandatory_write_sla_candidate_ids": _candidate_ids(write_must_run),
        "degraded_write_candidate_ids": _candidate_ids(write_degraded),
        "blocked_write_candidate_ids": _candidate_ids(write_blocked),
        "write_must_run_count": len(write_must_run),
        "write_supplemental_count": len(write_supplemental),
        "write_degraded_count": len(write_degraded),
        "write_blocked_count": len(write_blocked),
        "checklist": checklist,
        "approval_statement_template": "I approve QualiBug to run the listed write-sandbox probes only against the configured non-production environment using disposable test data and the declared cleanup strategy.",
        "execution_context": {
            "allow_write_sandbox": allow_write,
            "approval_id_present": approval_present,
            "runtime_ready_probe_count": summary.get("runtime_ready_probe_count"),
            "runtime_sla_must_run_count": summary.get("runtime_sla_must_run_count"),
        },
        "customer_safe_note": "This approval packet is a governance artifact. It does not execute write probes by itself and must remain tied to a non-production target.",
    }


def render_write_sandbox_approval_markdown(packet: dict[str, Any]) -> str:
    lines = [
        "# Write-Sandbox Approval Packet",
        "",
        f"- engine: `{packet.get('engine')}`",
        f"- status: `{packet.get('status')}`",
        f"- write approval required: `{packet.get('write_approval_required')}`",
        f"- ready for customer approval: `{packet.get('ready_for_customer_approval')}`",
        f"- mandatory write SLA probes: `{packet.get('write_must_run_count')}`",
        f"- blocked write probes: `{packet.get('write_blocked_count')}`",
        "",
    ]
    if packet.get("blocking_reasons"):
        lines.extend(["## Blocking reasons", ""])
        for reason in packet.get("blocking_reasons") or []:
            lines.append(f"- {reason}")
        lines.append("")
    lines.extend(["## Approval checklist", ""])
    for item in packet.get("checklist") or []:
        if isinstance(item, dict):
            lines.append(f"- `{item.get('item_id')}` required=`{item.get('required')}` passed=`{item.get('passed')}` — {item.get('text')}")
    lines.extend([
        "",
        "## Approval statement template",
        "",
        f"> {packet.get('approval_statement_template')}",
        "",
        f"> {packet.get('customer_safe_note')}",
    ])
    return "\n".join(lines)
