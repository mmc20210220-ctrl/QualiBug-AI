from __future__ import annotations

"""Phase93T: commercial audit export adapters.

Phase93S creates a normalized audit event stream.  Phase93T converts that stream
and the closure acceptance ledger into customer-system import artifacts: Jira
issue import JSON, Linear issue import JSON, CSV audit ledger rows, reviewer
packet markdown, and stable external tracking keys per closure claim.
"""

import csv
import io
import re
from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe_slug(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "-", str(value or "").strip()).strip("-")
    return text.upper()[:64] or "UNKNOWN"


def _severity_to_priority(severity: str) -> str:
    sev = str(severity or "").lower()
    if sev == "critical":
        return "Highest"
    if sev == "warning":
        return "High"
    return "Medium"


def _severity_to_linear_priority(severity: str) -> int:
    sev = str(severity or "").lower()
    if sev == "critical":
        return 1
    if sev == "warning":
        return 2
    return 3


def _external_key(project_id: Any, item_id: Any, prefix: str = "QB-AUDIT") -> str:
    return f"{prefix}-{_safe_slug(project_id)}-{_safe_slug(item_id)}"


def _event_description(event: dict[str, Any], run_lineage_id: str) -> str:
    lines = [
        str(event.get("summary") or "QualiBug commercial audit event."),
        "",
        f"Event kind: {event.get('event_kind')}",
        f"Severity: {event.get('severity')}",
        f"Run lineage: {run_lineage_id}",
    ]
    for key in ("gate_status", "dashboard_status", "signoff_status", "comparison_status", "commercial_acceptance_status", "endpoint"):
        if event.get(key) not in (None, ""):
            lines.append(f"{key}: {event.get(key)}")
    blocker_ids = [str(x) for x in _as_list(event.get("audit_blocker_ids")) if str(x)]
    if blocker_ids:
        lines.append("audit_blocker_ids: " + ", ".join(blocker_ids))
    return "\n".join(lines)


def build_commercial_audit_export_adapters(report: dict[str, Any]) -> dict[str, Any]:
    stream = _as_dict(report.get("commercial_audit_event_stream"))
    ledger = _as_dict(report.get("commercial_closure_acceptance_ledger"))
    signoff = _as_dict(report.get("commercial_lineage_reviewer_signoff_packet"))
    project_id = report.get("project_id") or stream.get("project_id") or "qualibug_project"
    run_lineage_id = str(stream.get("run_lineage_id") or (_as_dict(report.get("immutable_run_receipt")).get("run_lineage_id")) or "")
    events = [e for e in _as_list(stream.get("events")) if isinstance(e, dict)]

    jira_issues: list[dict[str, Any]] = []
    linear_issues: list[dict[str, Any]] = []
    csv_rows: list[dict[str, Any]] = []

    for idx, event in enumerate(events, 1):
        event_id = str(event.get("event_id") or f"AUDIT-{idx:04d}")
        key = _external_key(project_id, event_id)
        labels = [
            "qualibug",
            "commercial-audit",
            str(event.get("event_kind") or "audit-event").replace("_", "-"),
            f"severity-{str(event.get('severity') or 'unknown').lower()}",
        ]
        description = _event_description(event, run_lineage_id)
        issue_summary = f"[{event_id}] {event.get('summary') or event.get('event_kind') or 'QualiBug audit event'}"
        jira_issues.append({
            "external_tracking_key": key,
            "projectKey": "<FILL:jira_project_key>",
            "issueType": "Task",
            "summary": issue_summary,
            "description": description,
            "priority": _severity_to_priority(str(event.get("severity") or "info")),
            "labels": labels,
            "qualibug_event_id": event_id,
            "qualibug_run_lineage_id": run_lineage_id,
        })
        linear_issues.append({
            "external_tracking_key": key,
            "teamId": "<FILL:linear_team_id>",
            "title": issue_summary,
            "description": description,
            "priority": _severity_to_linear_priority(str(event.get("severity") or "info")),
            "labels": labels,
            "metadata": {
                "qualibug_event_id": event_id,
                "qualibug_event_kind": event.get("event_kind"),
                "qualibug_run_lineage_id": run_lineage_id,
            },
        })
        csv_rows.append({
            "external_tracking_key": key,
            "event_id": event_id,
            "event_kind": event.get("event_kind") or "",
            "severity": event.get("severity") or "",
            "summary": event.get("summary") or "",
            "run_lineage_id": run_lineage_id,
            "commercial_acceptance_status": event.get("commercial_acceptance_status") or "",
            "gate_status": event.get("gate_status") or "",
            "requires_reviewer_signoff": str(bool(event.get("requires_reviewer_signoff"))).lower(),
            "blocked": str(bool(event.get("blocked"))).lower(),
            "audit_blocker_ids": ", ".join(str(x) for x in _as_list(event.get("audit_blocker_ids")) if str(x)),
        })

    closure_keys: list[dict[str, Any]] = []
    for idx, entry in enumerate(_as_list(ledger.get("ledger_entries")), 1):
        if not isinstance(entry, dict):
            continue
        entry_id = str(entry.get("ledger_entry_id") or f"CLAIM-{idx:04d}")
        key = _external_key(project_id, entry_id, prefix="QB-CLOSURE")
        closure_keys.append({
            "external_tracking_key": key,
            "ledger_entry_id": entry_id,
            "previous_finding_id": entry.get("previous_finding_id"),
            "candidate_id": entry.get("candidate_id"),
            "endpoint": entry.get("endpoint"),
            "commercial_acceptance_status": entry.get("commercial_acceptance_status"),
            "requires_reviewer_signoff": bool(entry.get("requires_reviewer_signoff")),
            "blocked": bool(entry.get("blocked")),
            "audit_blocker_ids": entry.get("audit_blocker_ids") or [],
            "audit_blocker_details": entry.get("audit_blocker_details") or [],
        })

    critical_or_warning_events = [e for e in events if str(e.get("severity") or "").lower() in {"critical", "warning"}]
    reviewer_packet_lines = [
        "# Commercial Reviewer Packet Export",
        "",
        f"- project: `{project_id}`",
        f"- run lineage: `{run_lineage_id}`",
        f"- signoff required: `{bool(signoff.get('signoff_required'))}`",
        f"- signoff item count: `{signoff.get('signoff_item_count', 0)}`",
        "",
        "## Review Items",
        "",
    ]
    if critical_or_warning_events:
        for event in critical_or_warning_events:
            reviewer_packet_lines.append(f"- `{event.get('event_id')}` `{event.get('severity')}` — {event.get('summary')}")
    else:
        reviewer_packet_lines.append("- No warning or critical audit events require reviewer action.")
    reviewer_packet_lines.extend(["", "## Closure Claim Tracking Keys", ""])
    for item in closure_keys:
        reviewer_packet_lines.append(f"- `{item.get('external_tracking_key')}` `{item.get('commercial_acceptance_status')}` — {item.get('previous_finding_id') or item.get('ledger_entry_id')}")
    reviewer_packet_markdown = "\n".join(reviewer_packet_lines) + "\n"

    if not events:
        status = "commercial_audit_exports_blocked_missing_event_stream"
        next_action = "Build the Phase93S commercial audit event stream before exporting customer-system imports."
    elif any(str(e.get("severity") or "").lower() == "critical" for e in events):
        status = "commercial_audit_exports_ready_with_blockers"
        next_action = "Import critical events as customer tracking tasks, but do not accept closure until blockers are resolved."
    elif any(str(e.get("severity") or "").lower() == "warning" for e in events):
        status = "commercial_audit_exports_ready_for_reviewer_import"
        next_action = "Import warning events and complete reviewer signoff before accepting closure claims."
    else:
        status = "commercial_audit_exports_ready"
        next_action = "Import audit events and closure tracking keys as accepted commercial handoff evidence."

    return {
        "engine": "runtime_commercial_audit_export_adapters_v1_phase93t",
        "status": status,
        "project_id": project_id,
        "run_lineage_id": run_lineage_id,
        "event_count": len(events),
        "jira_issue_count": len(jira_issues),
        "linear_issue_count": len(linear_issues),
        "csv_row_count": len(csv_rows),
        "closure_tracking_key_count": len(closure_keys),
        "jira_issue_import": jira_issues,
        "linear_issue_import": linear_issues,
        "csv_audit_ledger_rows": csv_rows,
        "closure_external_tracking_keys": closure_keys,
        "reviewer_packet_markdown": reviewer_packet_markdown,
        "customer_next_action": next_action,
    }


def render_commercial_audit_exports_markdown(exports: dict[str, Any]) -> str:
    lines = [
        "# Commercial Audit Export Adapters",
        "",
        f"- engine: `{exports.get('engine')}`",
        f"- status: `{exports.get('status')}`",
        f"- project: `{exports.get('project_id')}`",
        f"- run lineage: `{exports.get('run_lineage_id')}`",
        f"- Jira issues: `{exports.get('jira_issue_count', 0)}`",
        f"- Linear issues: `{exports.get('linear_issue_count', 0)}`",
        f"- CSV rows: `{exports.get('csv_row_count', 0)}`",
        f"- closure tracking keys: `{exports.get('closure_tracking_key_count', 0)}`",
        f"- next action: {exports.get('customer_next_action')}",
        "",
        "## Jira Import Preview",
        "",
    ]
    for issue in _as_list(exports.get("jira_issue_import"))[:10]:
        if isinstance(issue, dict):
            lines.append(f"- `{issue.get('external_tracking_key')}` `{issue.get('priority')}` — {issue.get('summary')}")
    if not _as_list(exports.get("jira_issue_import")):
        lines.append("- No Jira issues generated.")
    lines.extend(["", "## Closure Tracking Keys", ""])
    for item in _as_list(exports.get("closure_external_tracking_keys"))[:20]:
        if isinstance(item, dict):
            lines.append(f"- `{item.get('external_tracking_key')}` `{item.get('commercial_acceptance_status')}`")
    if not _as_list(exports.get("closure_external_tracking_keys")):
        lines.append("- No closure tracking keys generated.")
    lines.append("")
    return "\n".join(lines)


def render_csv_audit_ledger(exports: dict[str, Any]) -> str:
    rows = [r for r in _as_list(exports.get("csv_audit_ledger_rows")) if isinstance(r, dict)]
    fieldnames = [
        "external_tracking_key",
        "event_id",
        "event_kind",
        "severity",
        "summary",
        "run_lineage_id",
        "commercial_acceptance_status",
        "gate_status",
        "requires_reviewer_signoff",
        "blocked",
        "audit_blocker_ids",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()
