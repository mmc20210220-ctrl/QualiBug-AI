from __future__ import annotations

"""Phase93W: external tracker closure sync policy.

Phase93V reconciles QualiBug commercial audit artifacts with customer-owned
trackers such as Jira and Linear.  Phase93W converts the reconciliation and
closure acceptance ledger into a conservative sync policy: only closure claims
that are commercially accepted, reconciled to customer tracker ids, and backed by
safe lineage/secret gates can be marked resolved externally.  Everything else is
left pending or explicitly blocked so a disappeared finding is not accidentally
synced as fixed.
"""

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _by_key(entries: list[Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in entries:
        if isinstance(item, dict) and item.get("external_tracking_key"):
            out[str(item.get("external_tracking_key"))] = item
    return out


def _closure_key_by_ledger(exports: dict[str, Any]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in _as_list(exports.get("closure_external_tracking_keys")):
        if isinstance(item, dict):
            ledger_id = str(item.get("ledger_entry_id") or "")
            key = str(item.get("external_tracking_key") or "")
            if ledger_id and key:
                mapping[ledger_id] = key
    return mapping


def _reconciled_issue_entries(reconciliation: dict[str, Any], closure_key: str) -> list[dict[str, Any]]:
    entries = []
    for entry in _as_list(reconciliation.get("entries")):
        if not isinstance(entry, dict):
            continue
        if entry.get("external_tracking_key") == closure_key and entry.get("reconciliation_status") == "reconciled_import_confirmed":
            entries.append(entry)
    return entries


def _audit_issue_entries(reconciliation: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for entry in _as_list(reconciliation.get("entries")):
        if isinstance(entry, dict) and entry.get("source_kind") == "audit_issue" and entry.get("reconciliation_status") == "reconciled_import_confirmed":
            out.append(entry)
    return out


def _attachment_links(report: dict[str, Any], entry: dict[str, Any]) -> list[dict[str, str]]:
    outputs = _as_dict(report.get("outputs"))
    receipt = _as_dict(report.get("immutable_run_receipt"))
    links = [
        {"kind": "immutable_run_receipt", "path": str(outputs.get("immutable_run_receipt_json") or ""), "hash": str(receipt.get("receipt_hash") or receipt.get("run_receipt_hash") or "")},
        {"kind": "handoff_archive_manifest", "path": str(outputs.get("handoff_archive_manifest_json") or ""), "hash": str((_as_dict(report.get("handoff_archive_manifest"))).get("archive_manifest_hash") or "")},
        {"kind": "commercial_closure_acceptance_ledger", "path": str(outputs.get("commercial_closure_acceptance_ledger_json") or ""), "hash": ""},
        {"kind": "commercial_evidence_lineage_dashboard", "path": str(outputs.get("commercial_evidence_lineage_dashboard_json") or ""), "hash": ""},
        {"kind": "commercial_external_tracker_reconciliation", "path": str(outputs.get("commercial_external_tracker_reconciliation_json") or ""), "hash": ""},
    ]
    # Preserve per-finding reproduction/remediation links when present in the
    # ledger entry or report finding.  They are optional but useful for tracker
    # comments on the resolved issue.
    for link in _as_list(entry.get("reproduction_artifact_links")) + _as_list(entry.get("remediation_artifact_links")):
        if isinstance(link, dict):
            links.append({"kind": str(link.get("kind") or link.get("artifact") or "evidence"), "path": str(link.get("path") or link.get("file") or ""), "hash": str(link.get("hash") or "")})
    return [link for link in links if link.get("path") or link.get("hash")]


def _sync_status(
    entry: dict[str, Any],
    *,
    reconciliation: dict[str, Any],
    import_gate: dict[str, Any],
    secret_audit: dict[str, Any],
    rerun_gate: dict[str, Any],
    closure_reconciled: bool,
) -> tuple[str, str, bool]:
    acceptance = str(entry.get("commercial_acceptance_status") or "")
    recon_status = str(reconciliation.get("status") or "")
    if not bool(import_gate.get("import_ready")):
        return "sync_blocked_by_import_gate", "External tracker imports are not ready; do not update customer trackers.", True
    if not bool(secret_audit.get("safe_for_customer_handoff", True)):
        return "sync_blocked_by_secret_audit", "Commercial handoff secret audit is not safe; do not attach/sync artifacts.", True
    if not bool(rerun_gate.get("closure_verification_allowed", False)) and acceptance == "accepted_for_customer_closure":
        return "sync_blocked_by_lineage_audit", "Rerun audit gate does not allow closure verification for this lineage.", True
    if acceptance == "blocked_by_lineage_audit":
        return "sync_blocked_by_lineage_audit", "Closure ledger blocks this claim by lineage audit.", True
    if acceptance == "pending_reviewer_signoff":
        return "sync_pending_reviewer_signoff", "Reviewer signoff is required before external tracker closure sync. Keep the external tracker item open.", False
    if acceptance == "baseline_only_not_closeable":
        return "sync_baseline_only_not_closeable", "This run is only a baseline and must not close old tracker items.", True
    if acceptance != "accepted_for_customer_closure":
        return "sync_not_claimed", "No accepted commercial closure claim exists for this ledger entry.", False
    if recon_status.startswith("external_tracker_reconciliation_blocked"):
        return "sync_blocked_by_reconciliation", "External tracker reconciliation is blocked.", True
    if not closure_reconciled:
        return "sync_pending_external_tracker_reconciliation", "Attach customer tracker ids/URLs before marking external issues resolved.", False
    return "sync_ready_to_mark_resolved", "Closure is accepted, lineage-safe, and reconciled to external tracker references.", False


def build_external_tracker_closure_sync_policy(report: dict[str, Any]) -> dict[str, Any]:
    closure_ledger = _as_dict(report.get("commercial_closure_acceptance_ledger"))
    reconciliation = _as_dict(report.get("commercial_external_tracker_reconciliation"))
    exports = _as_dict(report.get("commercial_audit_export_adapters"))
    import_gate = _as_dict(report.get("commercial_audit_export_import_gate"))
    secret_audit = _as_dict(report.get("commercial_handoff_secret_audit"))
    rerun_gate = _as_dict(report.get("handoff_rerun_audit_gate"))

    closure_key_by_ledger = _closure_key_by_ledger(exports)
    reconciled_by_key = _by_key(_as_list(reconciliation.get("entries")))
    audit_issue_refs = _audit_issue_entries(reconciliation)

    policies: list[dict[str, Any]] = []
    for entry in _as_list(closure_ledger.get("ledger_entries")):
        if not isinstance(entry, dict):
            continue
        ledger_id = str(entry.get("ledger_entry_id") or "")
        closure_key = closure_key_by_ledger.get(ledger_id, "")
        closure_recon = reconciled_by_key.get(closure_key, {}) if closure_key else {}
        closure_reconciled = bool(closure_recon) and closure_recon.get("reconciliation_status") == "reconciled_import_confirmed"
        status, rationale, blocked = _sync_status(
            entry,
            reconciliation=reconciliation,
            import_gate=import_gate,
            secret_audit=secret_audit,
            rerun_gate=rerun_gate,
            closure_reconciled=closure_reconciled,
        )
        external_refs = []
        if closure_recon:
            external_refs.append({
                "system": str(closure_recon.get("system") or "closure_ledger"),
                "external_tracking_key": closure_key,
                "external_id": str(closure_recon.get("external_id") or ""),
                "external_url": str(closure_recon.get("external_url") or ""),
            })
        # Attach reconciled Jira/Linear audit issues as evidence/comment targets.
        for issue in audit_issue_refs[:10]:
            external_refs.append({
                "system": str(issue.get("system") or ""),
                "external_tracking_key": str(issue.get("external_tracking_key") or ""),
                "external_id": str(issue.get("external_id") or ""),
                "external_url": str(issue.get("external_url") or ""),
            })
        policies.append({
            "sync_policy_id": f"SYNC-{len(policies)+1:04d}",
            "ledger_entry_id": ledger_id,
            "previous_finding_id": entry.get("previous_finding_id"),
            "candidate_id": entry.get("candidate_id"),
            "endpoint": entry.get("endpoint"),
            "commercial_acceptance_status": entry.get("commercial_acceptance_status"),
            "external_closure_tracking_key": closure_key,
            "sync_status": status,
            "blocked": blocked,
            "sync_action": _sync_action(status),
            "sync_rationale": rationale,
            "external_tracker_references": external_refs,
            "required_tracker_comment_attachments": _attachment_links(report, entry),
        })

    counts: dict[str, int] = {}
    for policy in policies:
        key = str(policy.get("sync_status") or "unknown")
        counts[key] = counts.get(key, 0) + 1

    if not policies:
        status = "external_tracker_closure_sync_no_claims"
        next_action = "No closure claims are present; do not update external tracker resolution states."
    elif counts.get("sync_blocked_by_import_gate") or counts.get("sync_blocked_by_secret_audit") or counts.get("sync_blocked_by_lineage_audit") or counts.get("sync_blocked_by_reconciliation"):
        status = "external_tracker_closure_sync_blocked"
        next_action = "Resolve blocking import, secret, lineage, or reconciliation gates before syncing tracker closure."
    elif counts.get("sync_pending_reviewer_signoff") or counts.get("sync_pending_external_tracker_reconciliation"):
        status = "external_tracker_closure_sync_pending"
        next_action = "Complete reviewer signoff and external tracker reconciliation before marking issues resolved."
    elif counts.get("sync_ready_to_mark_resolved"):
        status = "external_tracker_closure_sync_ready"
        next_action = "Customer tracker owner may mark the ready policies resolved with the listed receipt and evidence links attached."
    else:
        status = "external_tracker_closure_sync_not_applicable"
        next_action = "No policy is eligible for external tracker closure sync."

    return {
        "engine": "runtime_external_tracker_closure_sync_policy_v1_phase93w",
        "status": status,
        "project_id": report.get("project_id") or closure_ledger.get("project_id"),
        "run_lineage_id": (_as_dict(report.get("immutable_run_receipt"))).get("run_lineage_id") or closure_ledger.get("current_run_lineage_id"),
        "import_gate_status": import_gate.get("status"),
        "secret_audit_status": secret_audit.get("status"),
        "rerun_audit_status": rerun_gate.get("status"),
        "reconciliation_status": reconciliation.get("status"),
        "sync_policy_count": len(policies),
        "status_counts": counts,
        "policies": policies,
        "customer_next_action": next_action,
    }


def _sync_action(status: str) -> str:
    return {
        "sync_ready_to_mark_resolved": "Mark the reconciled Jira/Linear/CSV tracker item resolved and attach QualiBug receipt/evidence links.",
        "sync_pending_reviewer_signoff": "Keep the external tracker item open until reviewer signoff is recorded.",
        "sync_pending_external_tracker_reconciliation": "Import/reconcile external tracker ids before changing resolution state.",
        "sync_blocked_by_lineage_audit": "Do not sync closure; rerun lineage audit blocks this claim.",
        "sync_blocked_by_import_gate": "Do not sync closure; import gate must be fixed first.",
        "sync_blocked_by_secret_audit": "Do not sync closure or attach artifacts; remove/redact leaked secrets first.",
        "sync_blocked_by_reconciliation": "Do not sync closure; external tracker reconciliation is blocked.",
        "sync_baseline_only_not_closeable": "Keep tracker items open; this run establishes a baseline only.",
        "sync_not_claimed": "No resolution update should be made for this finding.",
    }.get(status, "Review manually before changing tracker state.")


def render_external_tracker_closure_sync_policy_markdown(policy: dict[str, Any]) -> str:
    lines = [
        "# External Tracker Closure Sync Policy",
        "",
        f"- engine: `{policy.get('engine')}`",
        f"- status: `{policy.get('status')}`",
        f"- run lineage: `{policy.get('run_lineage_id')}`",
        f"- sync policies: `{policy.get('sync_policy_count', 0)}`",
        f"- import gate: `{policy.get('import_gate_status')}`",
        f"- secret audit: `{policy.get('secret_audit_status')}`",
        f"- rerun audit: `{policy.get('rerun_audit_status')}`",
        f"- reconciliation: `{policy.get('reconciliation_status')}`",
        f"- next action: {policy.get('customer_next_action')}",
        "",
    ]
    if policy.get("status_counts"):
        lines.extend(["## Status Counts", ""])
        for key, value in sorted((policy.get("status_counts") or {}).items()):
            lines.append(f"- `{key}`: `{value}`")
        lines.append("")
    lines.extend(["## Sync Policies", ""])
    for item in _as_list(policy.get("policies"))[:50]:
        if isinstance(item, dict):
            lines.append(
                f"- `{item.get('sync_policy_id')}` `{item.get('sync_status')}` for `{item.get('ledger_entry_id')}` "
                f"endpoint `{item.get('endpoint')}` — {item.get('sync_action')}"
            )
    if not _as_list(policy.get("policies")):
        lines.append("- No external tracker sync policies generated.")
    lines.append("")
    return "\n".join(lines)
