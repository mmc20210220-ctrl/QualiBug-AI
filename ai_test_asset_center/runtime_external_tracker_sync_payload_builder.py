from __future__ import annotations

"""Phase93X: external tracker sync payload builder.

Phase93W decides whether a closure claim is safe to sync.  Phase93X turns the
safe policy entries into offline customer-system update payloads (Jira
transition/comment, Linear state/comment, CSV status update) without calling any
external API.  Blocked or pending entries produce hold/comment guidance instead
of resolution updates.
"""

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _refs_for_system(policy: dict[str, Any], system: str) -> list[dict[str, Any]]:
    out = []
    for ref in _as_list(policy.get("external_tracker_references")):
        if isinstance(ref, dict) and str(ref.get("system") or "").lower() == system:
            out.append(ref)
    return out


def _comment(policy: dict[str, Any], report: dict[str, Any]) -> str:
    receipt = _as_dict(report.get("immutable_run_receipt"))
    lines = [
        "QualiBug commercial closure sync recommendation",
        f"sync_policy_id: {policy.get('sync_policy_id')}",
        f"ledger_entry_id: {policy.get('ledger_entry_id')}",
        f"previous_finding_id: {policy.get('previous_finding_id')}",
        f"candidate_id: {policy.get('candidate_id')}",
        f"endpoint: {policy.get('endpoint')}",
        f"sync_status: {policy.get('sync_status')}",
        f"run_lineage_id: {receipt.get('run_lineage_id') or report.get('run_lineage_id') or ''}",
        f"receipt_hash: {receipt.get('receipt_hash') or receipt.get('run_receipt_hash') or ''}",
        "evidence_links:",
    ]
    blockers = [b for b in _as_list(policy.get("audit_blocker_details")) if isinstance(b, dict)]
    if blockers:
        lines.append("audit_blockers:")
        for blocker in blockers[:10]:
            lines.append(
                f"- {blocker.get('gate_id')}: {blocker.get('reason')}"
            )
    import_violations = [v for v in _as_list(policy.get("import_gate_violations")) if isinstance(v, dict)]
    if import_violations:
        lines.append("import_gate_violations:")
        for violation in import_violations[:10]:
            lines.append(
                f"- {violation.get('kind')}: {violation.get('message') or violation.get('ledger_entry_id') or violation.get('external_tracking_key') or ''}"
            )
    for link in _as_list(policy.get("required_tracker_comment_attachments")):
        if isinstance(link, dict):
            lines.append(f"- {link.get('kind')}: {link.get('path') or link.get('hash')}")
    return "\n".join(lines)


def build_external_tracker_sync_payloads(report: dict[str, Any]) -> dict[str, Any]:
    policy_doc = _as_dict(report.get("external_tracker_closure_sync_policy"))
    policies = [p for p in _as_list(policy_doc.get("policies")) if isinstance(p, dict)]

    jira_payloads: list[dict[str, Any]] = []
    linear_payloads: list[dict[str, Any]] = []
    csv_updates: list[dict[str, Any]] = []
    hold_items: list[dict[str, Any]] = []

    for policy in policies:
        status = str(policy.get("sync_status") or "")
        comment = _comment(policy, report)
        ready = status == "sync_ready_to_mark_resolved"
        refs = _as_list(policy.get("external_tracker_references"))
        if ready:
            jira_refs = _refs_for_system(policy, "jira") or [r for r in refs if isinstance(r, dict) and str(r.get("external_id") or "").upper().startswith("JIRA-")]
            linear_refs = _refs_for_system(policy, "linear") or [r for r in refs if isinstance(r, dict) and str(r.get("external_id") or "").upper().startswith("LIN")]
            csv_refs = _refs_for_system(policy, "csv")
            for ref in jira_refs:
                jira_payloads.append({
                    "sync_policy_id": policy.get("sync_policy_id"),
                    "external_tracking_key": ref.get("external_tracking_key") or policy.get("external_closure_tracking_key"),
                    "issue_id_or_key": ref.get("external_id") or ref.get("external_url"),
                    "transition": {"target_status": "Resolved", "resolution": "Fixed"},
                    "comment": comment,
                    "attachments_required": policy.get("required_tracker_comment_attachments") or [],
                    "dry_run_only": True,
                })
            for ref in linear_refs:
                linear_payloads.append({
                    "sync_policy_id": policy.get("sync_policy_id"),
                    "external_tracking_key": ref.get("external_tracking_key") or policy.get("external_closure_tracking_key"),
                    "issue_id": ref.get("external_id") or ref.get("external_url"),
                    "state": "Done",
                    "comment": comment,
                    "attachments_required": policy.get("required_tracker_comment_attachments") or [],
                    "dry_run_only": True,
                })
            if not jira_refs and not linear_refs:
                # Closure-only references still deserve a CSV audit update; they
                # are not enough to call a Jira/Linear API directly.
                csv_refs = csv_refs or [r for r in refs if isinstance(r, dict)] or [{"external_tracking_key": policy.get("external_closure_tracking_key")}]
            for ref in csv_refs:
                csv_updates.append({
                    "sync_policy_id": policy.get("sync_policy_id"),
                    "external_tracking_key": ref.get("external_tracking_key") or policy.get("external_closure_tracking_key"),
                    "commercial_closure_status": "resolved_sync_ready",
                    "sync_status": status,
                    "receipt_hash": (_as_dict(report.get("immutable_run_receipt"))).get("receipt_hash") or "",
                    "comment": comment,
                })
        else:
            hold_items.append({
                "sync_policy_id": policy.get("sync_policy_id"),
                "ledger_entry_id": policy.get("ledger_entry_id"),
                "external_closure_tracking_key": policy.get("external_closure_tracking_key"),
                "sync_status": status,
                "hold_reason": policy.get("sync_rationale") or policy.get("sync_action"),
                "audit_blocker_ids": policy.get("audit_blocker_ids") or [],
                "audit_blocker_details": policy.get("audit_blocker_details") or [],
                "import_gate_violation_kinds": policy.get("import_gate_violation_kinds") or [],
                "import_gate_violations": policy.get("import_gate_violations") or [],
                "recommended_external_state": "Open / Do not mark resolved",
                "comment": comment,
            })

    if policy_doc.get("status") in {"external_tracker_closure_sync_blocked", "external_tracker_closure_sync_no_claims"}:
        status = "external_tracker_sync_payloads_blocked_or_empty"
        next_action = "Do not import resolution updates; resolve closure sync policy blockers first."
    elif jira_payloads or linear_payloads or csv_updates:
        status = "external_tracker_sync_payloads_ready_dry_run"
        next_action = "Review dry-run payloads, then customer tracker owner may apply them manually or through an approved integration."
    elif hold_items:
        status = "external_tracker_sync_payloads_hold_only"
        next_action = "Keep tracker issues open and resolve pending signoff/reconciliation before generating resolved payloads."
    else:
        status = "external_tracker_sync_payloads_empty"
        next_action = "No external tracker sync payloads were generated."

    return {
        "engine": "runtime_external_tracker_sync_payload_builder_v1_phase93x",
        "status": status,
        "project_id": report.get("project_id") or policy_doc.get("project_id"),
        "run_lineage_id": policy_doc.get("run_lineage_id"),
        "source_policy_status": policy_doc.get("status"),
        "jira_transition_payload_count": len(jira_payloads),
        "linear_update_payload_count": len(linear_payloads),
        "csv_status_update_count": len(csv_updates),
        "hold_item_count": len(hold_items),
        "jira_transition_payloads": jira_payloads,
        "linear_update_payloads": linear_payloads,
        "csv_status_updates": csv_updates,
        "hold_items": hold_items,
        "dry_run_only": True,
        "customer_next_action": next_action,
    }


def render_external_tracker_sync_payloads_markdown(payloads: dict[str, Any]) -> str:
    lines = [
        "# External Tracker Sync Payloads",
        "",
        f"- engine: `{payloads.get('engine')}`",
        f"- status: `{payloads.get('status')}`",
        f"- dry run only: `{payloads.get('dry_run_only')}`",
        f"- Jira transitions: `{payloads.get('jira_transition_payload_count', 0)}`",
        f"- Linear updates: `{payloads.get('linear_update_payload_count', 0)}`",
        f"- CSV updates: `{payloads.get('csv_status_update_count', 0)}`",
        f"- hold items: `{payloads.get('hold_item_count', 0)}`",
        f"- next action: {payloads.get('customer_next_action')}",
        "",
        "## Ready Payloads",
        "",
    ]
    for item in _as_list(payloads.get("jira_transition_payloads"))[:25]:
        if isinstance(item, dict):
            lines.append(f"- Jira `{item.get('issue_id_or_key')}` -> `{(item.get('transition') or {}).get('target_status')}` via `{item.get('sync_policy_id')}`")
    for item in _as_list(payloads.get("linear_update_payloads"))[:25]:
        if isinstance(item, dict):
            lines.append(f"- Linear `{item.get('issue_id')}` -> `{item.get('state')}` via `{item.get('sync_policy_id')}`")
    for item in _as_list(payloads.get("csv_status_updates"))[:25]:
        if isinstance(item, dict):
            lines.append(f"- CSV `{item.get('external_tracking_key')}` -> `{item.get('commercial_closure_status')}`")
    if not (_as_list(payloads.get("jira_transition_payloads")) or _as_list(payloads.get("linear_update_payloads")) or _as_list(payloads.get("csv_status_updates"))):
        lines.append("- No ready resolution payloads generated.")
    if payloads.get("hold_items"):
        lines.extend(["", "## Hold Items", ""])
        for item in _as_list(payloads.get("hold_items"))[:50]:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('sync_policy_id')}` `{item.get('sync_status')}` — {item.get('recommended_external_state')}")
    lines.append("")
    return "\n".join(lines)
