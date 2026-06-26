from __future__ import annotations

"""Phase93R: commercial closure acceptance ledger.

Phase93P/Q surface lineage status and reviewer signoff needs.  Phase93R turns
those signals into an auditable ledger of finding closure claims: accepted,
pending signoff, blocked, baseline-only, or no-claim.  The ledger is deliberately
separate from bug validation; it only governs whether a disappearance/closure can
be accepted commercially.
"""

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _claims_from_dashboard(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for raw in _as_list(dashboard.get("finding_closure_claims")):
        if isinstance(raw, dict):
            claims.append(raw)
    return claims


def _claim_status(global_state: str, signoff_status: str, raw_claim_status: str | None) -> tuple[str, str]:
    if global_state == "closure_claim_allowed" and signoff_status in {"lineage_reviewer_signoff_not_required", ""}:
        return "accepted_for_customer_closure", "Same input/archive lineage; no reviewer signoff required."
    if global_state == "closure_claim_allowed" and signoff_status == "lineage_reviewer_signoff_required":
        return "pending_reviewer_signoff", "Closure is lineage-eligible but reviewer signoff packet is still pending."
    if global_state == "closure_claim_reviewer_approval_required":
        return "pending_reviewer_signoff", "Delivery evidence changed; reviewer approval is required before closure acceptance."
    if global_state == "closure_claim_baseline_only":
        return "baseline_only_not_closeable", "No previous receipt baseline was supplied; this run cannot close old findings."
    if global_state == "closure_claim_blocked" or signoff_status == "lineage_signoff_blocked_by_audit_gate":
        return "blocked_by_lineage_audit", "Rerun audit gate blocks closure claims for this lineage."
    if raw_claim_status:
        return str(raw_claim_status), "Using raw dashboard claim status because no recognized global state was available."
    return "no_commercial_closure_claim", "No commercial closure claim was made for this finding."


def build_commercial_closure_acceptance_ledger(report: dict[str, Any]) -> dict[str, Any]:
    dashboard = _as_dict(report.get("commercial_evidence_lineage_dashboard"))
    signoff = _as_dict(report.get("commercial_lineage_reviewer_signoff_packet"))
    gate = _as_dict(report.get("handoff_rerun_audit_gate"))
    global_state = str(dashboard.get("closure_claim_state") or "")
    signoff_status = str(signoff.get("status") or "")
    claims = _claims_from_dashboard(dashboard)

    entries: list[dict[str, Any]] = []
    for index, claim in enumerate(claims, 1):
        status, rationale = _claim_status(global_state, signoff_status, str(claim.get("claim_status") or ""))
        entries.append({
            "ledger_entry_id": f"CLAIM-{index:04d}",
            "previous_finding_id": claim.get("previous_finding_id"),
            "candidate_id": claim.get("candidate_id"),
            "endpoint": claim.get("endpoint"),
            "primary_lifecycle_signature": claim.get("primary_lifecycle_signature"),
            "close_basis": claim.get("close_basis"),
            "commercial_acceptance_status": status,
            "acceptance_rationale": rationale,
            "requires_reviewer_signoff": status == "pending_reviewer_signoff",
            "blocked": status == "blocked_by_lineage_audit",
        })

    counts: dict[str, int] = {}
    for entry in entries:
        key = str(entry.get("commercial_acceptance_status") or "unknown")
        counts[key] = counts.get(key, 0) + 1

    if any(e.get("blocked") for e in entries) or gate.get("blocker_count", 0):
        status = "closure_acceptance_ledger_blocked"
        next_action = "Resolve lineage audit blockers before accepting closure claims."
    elif any(e.get("requires_reviewer_signoff") for e in entries) or signoff.get("signoff_required"):
        status = "closure_acceptance_ledger_pending_reviewer_signoff"
        next_action = "Reviewer must complete the Phase93Q signoff packet before closure can be accepted."
    elif entries and all(e.get("commercial_acceptance_status") == "accepted_for_customer_closure" for e in entries):
        status = "closure_acceptance_ledger_ready"
        next_action = "Customer can accept the listed closure claims and store this ledger with the immutable receipt."
    elif global_state == "closure_claim_baseline_only":
        status = "closure_acceptance_ledger_baseline_only"
        next_action = "Store this ledger as a baseline; no old finding closure should be claimed."
    else:
        status = "closure_acceptance_ledger_no_claims"
        next_action = "No commercial closure claims are present in this run."

    return {
        "engine": "runtime_commercial_closure_acceptance_ledger_v1_phase93r",
        "status": status,
        "project_id": report.get("project_id"),
        "created_at": report.get("created_at"),
        "current_run_lineage_id": dashboard.get("current_run_lineage_id"),
        "previous_run_lineage_id": dashboard.get("previous_run_lineage_id"),
        "closure_claim_state": global_state,
        "signoff_packet_status": signoff_status,
        "ledger_entry_count": len(entries),
        "acceptance_status_counts": counts,
        "ledger_entries": entries,
        "customer_next_action": next_action,
    }


def render_commercial_closure_acceptance_ledger_markdown(ledger: dict[str, Any]) -> str:
    lines = [
        "# Commercial Closure Acceptance Ledger",
        "",
        f"- engine: `{ledger.get('engine')}`",
        f"- status: `{ledger.get('status')}`",
        f"- current lineage: `{ledger.get('current_run_lineage_id')}`",
        f"- previous lineage: `{ledger.get('previous_run_lineage_id')}`",
        f"- closure claim state: `{ledger.get('closure_claim_state')}`",
        f"- signoff packet status: `{ledger.get('signoff_packet_status')}`",
        f"- ledger entries: `{ledger.get('ledger_entry_count')}`",
        f"- next action: {ledger.get('customer_next_action')}",
        "",
    ]
    if ledger.get("acceptance_status_counts"):
        lines.extend(["## Status Counts", ""])
        for key, value in sorted((ledger.get("acceptance_status_counts") or {}).items()):
            lines.append(f"- `{key}`: `{value}`")
        lines.append("")
    if ledger.get("ledger_entries"):
        lines.extend(["## Ledger Entries", ""])
        for entry in ledger.get("ledger_entries") or []:
            if isinstance(entry, dict):
                lines.append(
                    f"- `{entry.get('ledger_entry_id')}` previous `{entry.get('previous_finding_id')}` "
                    f"endpoint `{entry.get('endpoint')}` status `{entry.get('commercial_acceptance_status')}`"
                )
        lines.append("")
    return "\n".join(lines)
