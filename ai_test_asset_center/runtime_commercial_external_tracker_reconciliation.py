from __future__ import annotations

"""Phase93V: commercial external tracker reconciliation ledger.

Phase93T/U produce and validate import artifacts.  Phase93V turns those imports
into a reconciliation ledger so customers can verify that Jira/Linear/CSV items
were mirrored and can map each commercial closure claim back to a stable external
tracking key.
"""

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _result_by_key(results: list[Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in results:
        if isinstance(item, dict) and item.get("external_tracking_key"):
            out[str(item.get("external_tracking_key"))] = item
    return out


def build_commercial_external_tracker_reconciliation(report: dict[str, Any]) -> dict[str, Any]:
    exports = _as_dict(report.get("commercial_audit_export_adapters"))
    gate = _as_dict(report.get("commercial_audit_export_import_gate"))
    external_results = _as_dict(report.get("external_tracker_import_results") or report.get("external_tracker_results"))
    jira_results = _result_by_key(_as_list(external_results.get("jira")))
    linear_results = _result_by_key(_as_list(external_results.get("linear")))
    csv_results = _result_by_key(_as_list(external_results.get("csv")))

    gate_status = str(gate.get("status") or "not_built")
    gate_ready = bool(gate.get("import_ready"))
    placeholder_count = int(gate.get("placeholder_count") or 0)
    import_gate_violations = [item for item in _as_list(gate.get("violations")) if isinstance(item, dict)]
    import_gate_violation_kinds = [str(item.get("kind") or "unknown_violation") for item in import_gate_violations]

    entries: list[dict[str, Any]] = []

    def add_entry(system: str, item: dict[str, Any], results: dict[str, dict[str, Any]], source_kind: str) -> None:
        key = str(item.get("external_tracking_key") or "")
        result = results.get(key, {})
        external_id = result.get("external_id") or result.get("issue_id") or result.get("url")
        if not gate_ready:
            status = "blocked_by_import_gate"
        elif placeholder_count:
            status = "pending_customer_system_ids"
        elif external_id:
            status = "reconciled_import_confirmed"
        elif result.get("status") in {"failed", "error", "rejected"}:
            status = "import_failed_reconcile_required"
        else:
            status = "pending_customer_import"
        entries.append({
            "reconciliation_entry_id": f"RECON-{len(entries)+1:04d}",
            "system": system,
            "source_kind": source_kind,
            "external_tracking_key": key,
            "qualibug_event_id": item.get("qualibug_event_id") or item.get("event_id") or item.get("ledger_entry_id"),
            "summary": item.get("summary") or item.get("title") or item.get("commercial_acceptance_status"),
            "external_id": external_id or "",
            "external_url": result.get("url") or "",
            "reconciliation_status": status,
            "import_gate_violation_kinds": import_gate_violation_kinds if status == "blocked_by_import_gate" else [],
            "import_gate_violations": import_gate_violations if status == "blocked_by_import_gate" else [],
            "customer_action": _entry_action(status),
        })

    for issue in _as_list(exports.get("jira_issue_import")):
        if isinstance(issue, dict):
            add_entry("jira", issue, jira_results, "audit_issue")
    for issue in _as_list(exports.get("linear_issue_import")):
        if isinstance(issue, dict):
            add_entry("linear", issue, linear_results, "audit_issue")
    for row in _as_list(exports.get("csv_audit_ledger_rows")):
        if isinstance(row, dict):
            add_entry("csv", row, csv_results, "audit_ledger_row")
    for item in _as_list(exports.get("closure_external_tracking_keys")):
        if isinstance(item, dict):
            add_entry("closure_ledger", item, {}, "closure_claim_tracking_key")

    status_counts: dict[str, int] = {}
    for entry in entries:
        status_counts[entry["reconciliation_status"]] = status_counts.get(entry["reconciliation_status"], 0) + 1

    if status_counts.get("blocked_by_import_gate"):
        status = "external_tracker_reconciliation_blocked_by_import_gate"
        next_action = "Resolve the Phase93U import gate before attempting external tracker reconciliation."
    elif status_counts.get("pending_customer_system_ids"):
        status = "external_tracker_reconciliation_pending_customer_system_ids"
        next_action = "Fill Jira/Linear customer system identifiers, import artifacts, then attach external import results."
    elif status_counts.get("import_failed_reconcile_required"):
        status = "external_tracker_reconciliation_import_failures"
        next_action = "Fix failed customer tracker imports and rerun reconciliation."
    elif status_counts.get("pending_customer_import"):
        status = "external_tracker_reconciliation_pending_customer_import"
        next_action = "Import generated artifacts into customer systems and provide external ids/URLs for confirmation."
    elif entries:
        status = "external_tracker_reconciliation_confirmed"
        next_action = "Use reconciled external ids as customer audit tracking references."
    else:
        status = "external_tracker_reconciliation_empty"
        next_action = "Generate Phase93T audit exports before reconciling external trackers."

    return {
        "engine": "runtime_commercial_external_tracker_reconciliation_v1_phase93v",
        "status": status,
        "project_id": report.get("project_id") or exports.get("project_id"),
        "run_lineage_id": exports.get("run_lineage_id"),
        "import_gate_status": gate_status,
        "import_gate_violation_count": int(gate.get("violation_count") or len(import_gate_violations)),
        "import_gate_violation_kinds": import_gate_violation_kinds,
        "import_gate_violations": import_gate_violations,
        "entry_count": len(entries),
        "status_counts": status_counts,
        "entries": entries,
        "customer_next_action": next_action,
    }


def _entry_action(status: str) -> str:
    return {
        "blocked_by_import_gate": "Fix import gate violations first.",
        "pending_customer_system_ids": "Fill customer-owned system ids before import.",
        "pending_customer_import": "Import this artifact into the customer system and capture its external id.",
        "import_failed_reconcile_required": "Repair failed import and rerun reconciliation.",
        "reconciled_import_confirmed": "Keep this external id as the commercial audit reference.",
    }.get(status, "Review this reconciliation entry.")


def render_commercial_external_tracker_reconciliation_markdown(ledger: dict[str, Any]) -> str:
    lines = [
        "# Commercial External Tracker Reconciliation Ledger",
        "",
        f"- engine: `{ledger.get('engine')}`",
        f"- status: `{ledger.get('status')}`",
        f"- run lineage: `{ledger.get('run_lineage_id')}`",
        f"- entries: `{ledger.get('entry_count', 0)}`",
        f"- next action: {ledger.get('customer_next_action')}",
        "",
    ]
    if ledger.get("status_counts"):
        lines.extend(["## Status Counts", ""])
        for key, value in sorted((ledger.get("status_counts") or {}).items()):
            lines.append(f"- `{key}`: `{value}`")
        lines.append("")
    lines.extend(["## Entries", ""])
    for entry in _as_list(ledger.get("entries"))[:50]:
        if isinstance(entry, dict):
            lines.append(f"- `{entry.get('reconciliation_entry_id')}` `{entry.get('system')}` `{entry.get('reconciliation_status')}` — `{entry.get('external_tracking_key')}`")
    if not _as_list(ledger.get("entries")):
        lines.append("- No reconciliation entries generated.")
    lines.append("")
    return "\n".join(lines)
