from __future__ import annotations

"""Phase93Z: external tracker sync receipt ledger.

Phase93X/Y produce and validate dry-run update payloads.  Phase93Z records the
customer's post-application receipt: which Jira/Linear/CSV updates were applied,
which failed, and which remain pending.  This keeps external tracker resolution
state auditable without QualiBug directly mutating customer systems.
"""

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _results_by_key(results: dict[str, Any], system: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in _as_list(results.get(system)):
        if isinstance(item, dict):
            key = str(item.get("external_tracking_key") or item.get("issue_id") or item.get("issue_id_or_key") or item.get("external_id") or "")
            if key:
                out[key] = item
    return out


def _find_result(mapping: dict[str, dict[str, Any]], *keys: Any) -> dict[str, Any]:
    for key in keys:
        text = str(key or "")
        if text and text in mapping:
            return mapping[text]
    return {}


def _receipt_status(result: dict[str, Any], payload_ready: bool) -> tuple[str, str]:
    if not payload_ready:
        return "sync_not_authorized_by_payload_gate", "Payload gate did not authorize applying this update."
    if not result:
        return "pending_customer_apply", "Customer has not provided an application result for this payload."
    raw = str(result.get("status") or result.get("sync_status") or result.get("result") or "").lower()
    external_state = str(result.get("external_status") or result.get("state") or result.get("resolution") or "").lower()
    if raw in {"applied", "success", "succeeded", "synced"} or external_state in {"resolved", "done", "fixed", "closed"}:
        return "sync_applied_confirmed", "Customer tracker result confirms the update was applied."
    if raw in {"failed", "error", "rejected"}:
        return "sync_failed_reconcile_required", "Customer tracker result reports a failed update; reconcile and retry."
    return "sync_result_needs_review", "Customer tracker result is present but not recognized as confirmed or failed."


def build_external_tracker_sync_receipt_ledger(report: dict[str, Any]) -> dict[str, Any]:
    payloads = _as_dict(report.get("external_tracker_sync_payloads"))
    gate = _as_dict(report.get("external_tracker_sync_payload_gate"))
    results = _as_dict(report.get("external_tracker_sync_results") or report.get("customer_external_tracker_sync_results"))
    payload_ready = bool(gate.get("payload_import_ready"))

    jira_results = _results_by_key(results, "jira")
    linear_results = _results_by_key(results, "linear")
    csv_results = _results_by_key(results, "csv")

    entries: list[dict[str, Any]] = []

    def add_entry(system: str, payload: dict[str, Any], result: dict[str, Any]) -> None:
        status, rationale = _receipt_status(result, payload_ready)
        entries.append({
            "sync_receipt_entry_id": f"SYNC-RECEIPT-{len(entries)+1:04d}",
            "system": system,
            "sync_policy_id": payload.get("sync_policy_id"),
            "external_tracking_key": payload.get("external_tracking_key"),
            "external_id": payload.get("issue_id_or_key") or payload.get("issue_id") or payload.get("external_id") or result.get("external_id") or "",
            "requested_state": (_as_dict(payload.get("transition"))).get("target_status") or payload.get("state") or payload.get("commercial_closure_status"),
            "receipt_status": status,
            "receipt_rationale": rationale,
            "customer_result": result,
        })

    for payload in _as_list(payloads.get("jira_transition_payloads")):
        if isinstance(payload, dict):
            result = _find_result(jira_results, payload.get("external_tracking_key"), payload.get("issue_id_or_key"))
            add_entry("jira", payload, result)
    for payload in _as_list(payloads.get("linear_update_payloads")):
        if isinstance(payload, dict):
            result = _find_result(linear_results, payload.get("external_tracking_key"), payload.get("issue_id"))
            add_entry("linear", payload, result)
    for payload in _as_list(payloads.get("csv_status_updates")):
        if isinstance(payload, dict):
            result = _find_result(csv_results, payload.get("external_tracking_key"))
            add_entry("csv", payload, result)

    for hold in _as_list(payloads.get("hold_items")):
        if isinstance(hold, dict):
            entries.append({
                "sync_receipt_entry_id": f"SYNC-RECEIPT-{len(entries)+1:04d}",
                "system": "hold",
                "sync_policy_id": hold.get("sync_policy_id"),
                "external_tracking_key": hold.get("external_closure_tracking_key"),
                "external_id": "",
                "requested_state": "Open / Do not mark resolved",
                "receipt_status": "hold_not_synced",
                "receipt_rationale": hold.get("hold_reason") or "This item intentionally remains open.",
                "customer_result": {},
            })

    counts: dict[str, int] = {}
    for entry in entries:
        key = str(entry.get("receipt_status") or "unknown")
        counts[key] = counts.get(key, 0) + 1

    if gate.get("status") == "external_tracker_sync_payload_gate_blocked":
        status = "external_tracker_sync_receipt_blocked_by_payload_gate"
        next_action = "Fix payload gate blockers; do not accept external tracker sync receipts yet."
    elif counts.get("sync_failed_reconcile_required"):
        status = "external_tracker_sync_receipt_failures"
        next_action = "Repair failed tracker updates and rerun the sync receipt ledger."
    elif counts.get("pending_customer_apply"):
        status = "external_tracker_sync_receipt_pending_customer_apply"
        next_action = "Customer tracker owner should apply approved payloads and provide external sync results."
    elif counts.get("sync_result_needs_review"):
        status = "external_tracker_sync_receipt_needs_review"
        next_action = "Review unrecognized tracker results before accepting sync completion."
    elif entries and all(e.get("receipt_status") in {"sync_applied_confirmed", "hold_not_synced"} for e in entries):
        status = "external_tracker_sync_receipt_confirmed"
        next_action = "Store this receipt ledger with the QualiBug immutable run receipt and customer tracker records."
    else:
        status = "external_tracker_sync_receipt_empty"
        next_action = "No external tracker sync receipt entries were generated."

    return {
        "engine": "runtime_external_tracker_sync_receipt_ledger_v1_phase93z",
        "status": status,
        "project_id": report.get("project_id") or payloads.get("project_id"),
        "run_lineage_id": payloads.get("run_lineage_id"),
        "payload_gate_status": gate.get("status"),
        "sync_receipt_entry_count": len(entries),
        "receipt_status_counts": counts,
        "entries": entries,
        "customer_next_action": next_action,
    }


def render_external_tracker_sync_receipt_ledger_markdown(ledger: dict[str, Any]) -> str:
    lines = [
        "# External Tracker Sync Receipt Ledger",
        "",
        f"- engine: `{ledger.get('engine')}`",
        f"- status: `{ledger.get('status')}`",
        f"- run lineage: `{ledger.get('run_lineage_id')}`",
        f"- payload gate: `{ledger.get('payload_gate_status')}`",
        f"- entries: `{ledger.get('sync_receipt_entry_count', 0)}`",
        f"- next action: {ledger.get('customer_next_action')}",
        "",
    ]
    if ledger.get("receipt_status_counts"):
        lines.extend(["## Receipt Status Counts", ""])
        for key, value in sorted((ledger.get("receipt_status_counts") or {}).items()):
            lines.append(f"- `{key}`: `{value}`")
        lines.append("")
    lines.extend(["## Entries", ""])
    for item in _as_list(ledger.get("entries"))[:50]:
        if isinstance(item, dict):
            lines.append(f"- `{item.get('sync_receipt_entry_id')}` `{item.get('system')}` `{item.get('receipt_status')}` for `{item.get('external_id') or item.get('external_tracking_key')}`")
    if not _as_list(ledger.get("entries")):
        lines.append("- No sync receipt entries generated.")
    lines.append("")
    return "\n".join(lines)
