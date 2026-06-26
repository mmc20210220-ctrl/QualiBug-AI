from __future__ import annotations

"""Phase93S: commercial audit event stream.

Phase93R records closure acceptance decisions.  Phase93S exports the commercial
handoff/rerun/closure lifecycle as ordered, machine-readable audit events so a
customer can mirror them into Jira, Linear, GRC or internal audit systems.
"""

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _event(event_id: str, kind: str, severity: str, summary: str, **extra: Any) -> dict[str, Any]:
    out = {
        "event_id": event_id,
        "event_kind": kind,
        "severity": severity,
        "summary": summary,
    }
    out.update(extra)
    return out


def build_commercial_audit_event_stream(report: dict[str, Any]) -> dict[str, Any]:
    receipt = _as_dict(report.get("immutable_run_receipt"))
    comparison = _as_dict(report.get("handoff_receipt_comparison"))
    gate = _as_dict(report.get("handoff_rerun_audit_gate"))
    dashboard = _as_dict(report.get("commercial_evidence_lineage_dashboard"))
    signoff = _as_dict(report.get("commercial_lineage_reviewer_signoff_packet"))
    ledger = _as_dict(report.get("commercial_closure_acceptance_ledger"))

    events: list[dict[str, Any]] = []
    events.append(_event(
        "AUDIT-0001",
        "immutable_receipt_created",
        "info" if receipt else "critical",
        "Immutable run receipt was created." if receipt else "Immutable run receipt is missing.",
        run_lineage_id=receipt.get("run_lineage_id"),
        receipt_status=receipt.get("receipt_status"),
        probe_plan_hash=receipt.get("probe_plan_hash"),
        artifact_archive_hash=receipt.get("artifact_archive_hash"),
    ))
    events.append(_event(
        "AUDIT-0002",
        "receipt_comparison_completed",
        "info" if comparison.get("previous_receipt_present") else "warning",
        f"Receipt comparison status: {comparison.get('status') or 'missing'}.",
        comparison_status=comparison.get("status"),
        previous_receipt_present=bool(comparison.get("previous_receipt_present")),
        change_count=comparison.get("change_count", 0),
    ))
    events.append(_event(
        "AUDIT-0003",
        "rerun_audit_gate_evaluated",
        "critical" if gate.get("blocker_count") else ("warning" if gate.get("warning_count") else "info"),
        f"Rerun audit gate status: {gate.get('status') or 'missing'}.",
        gate_status=gate.get("status"),
        closure_verification_allowed=bool(gate.get("closure_verification_allowed")),
        blocker_count=gate.get("blocker_count", 0),
        warning_count=gate.get("warning_count", 0),
    ))
    events.append(_event(
        "AUDIT-0004",
        "lineage_dashboard_published",
        "critical" if dashboard.get("status") == "lineage_dashboard_closure_blocked" else ("warning" if dashboard.get("reviewer_signoff_required") else "info"),
        f"Lineage dashboard status: {dashboard.get('status') or 'missing'}.",
        dashboard_status=dashboard.get("status"),
        closure_claim_state=dashboard.get("closure_claim_state"),
        changed_or_missing_hash_count=dashboard.get("changed_or_missing_hash_count", 0),
    ))
    events.append(_event(
        "AUDIT-0005",
        "reviewer_signoff_packet_published",
        "critical" if signoff.get("signoff_blocked") else ("warning" if signoff.get("signoff_required") else "info"),
        f"Reviewer signoff packet status: {signoff.get('status') or 'missing'}.",
        signoff_status=signoff.get("status"),
        signoff_required=bool(signoff.get("signoff_required")),
        signoff_item_count=signoff.get("signoff_item_count", 0),
    ))

    for idx, entry in enumerate(_as_list(ledger.get("ledger_entries")), 1):
        if not isinstance(entry, dict):
            continue
        status = str(entry.get("commercial_acceptance_status") or "unknown")
        severity = "info" if status == "accepted_for_customer_closure" else ("warning" if status == "pending_reviewer_signoff" else "critical")
        events.append(_event(
            f"AUDIT-CLOSURE-{idx:04d}",
            "finding_closure_claim_recorded",
            severity,
            f"Finding closure claim {entry.get('ledger_entry_id')} is {status}.",
            ledger_entry_id=entry.get("ledger_entry_id"),
            previous_finding_id=entry.get("previous_finding_id"),
            endpoint=entry.get("endpoint"),
            commercial_acceptance_status=status,
            requires_reviewer_signoff=bool(entry.get("requires_reviewer_signoff")),
            blocked=bool(entry.get("blocked")),
            audit_blocker_ids=entry.get("audit_blocker_ids") or [],
            audit_blocker_details=entry.get("audit_blocker_details") or [],
        ))

    severity_counts: dict[str, int] = {}
    for e in events:
        severity_counts[e["severity"]] = severity_counts.get(e["severity"], 0) + 1

    if severity_counts.get("critical"):
        status = "commercial_audit_event_stream_contains_blockers"
        next_action = "Mirror events to the customer audit system, but do not accept closure until critical events are resolved."
    elif severity_counts.get("warning"):
        status = "commercial_audit_event_stream_requires_review"
        next_action = "Mirror events and complete reviewer signoff for warning-level closure evidence."
    else:
        status = "commercial_audit_event_stream_ready"
        next_action = "Mirror events with the customer handoff archive as accepted audit evidence."

    return {
        "engine": "runtime_commercial_audit_event_stream_v1_phase93s",
        "status": status,
        "project_id": report.get("project_id"),
        "created_at": report.get("created_at"),
        "run_lineage_id": receipt.get("run_lineage_id") or dashboard.get("current_run_lineage_id"),
        "event_count": len(events),
        "severity_counts": severity_counts,
        "events": events,
        "customer_next_action": next_action,
    }


def render_commercial_audit_event_stream_markdown(stream: dict[str, Any]) -> str:
    lines = [
        "# Commercial Audit Event Stream",
        "",
        f"- engine: `{stream.get('engine')}`",
        f"- status: `{stream.get('status')}`",
        f"- run lineage: `{stream.get('run_lineage_id')}`",
        f"- event count: `{stream.get('event_count')}`",
        f"- next action: {stream.get('customer_next_action')}",
        "",
    ]
    if stream.get("severity_counts"):
        lines.extend(["## Severity Counts", ""])
        for key, value in sorted((stream.get("severity_counts") or {}).items()):
            lines.append(f"- `{key}`: `{value}`")
        lines.append("")
    lines.extend(["## Events", ""])
    for event in stream.get("events") or []:
        if isinstance(event, dict):
            lines.append(f"- `{event.get('event_id')}` `{event.get('event_kind')}` `{event.get('severity')}` — {event.get('summary')}")
    lines.append("")
    return "\n".join(lines)
