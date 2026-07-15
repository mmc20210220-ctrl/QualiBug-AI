from __future__ import annotations

"""Phase93P: commercial evidence lineage dashboard.

Phase93M/N/O provide immutable receipts, receipt comparisons and a rerun audit
closure gate.  This module turns those low-level hashes and gate states into a
customer-readable lineage dashboard: whether this run belongs to the same
commercial evidence lineage, which hashes matched, what changed, and whether
finding closure claims are allowed, blocked or require reviewer signoff.
"""

from typing import Any


_HASH_FIELDS = [
    ("probe_plan_hash", "Grounded probe plan", "input"),
    ("runtime_evidence_sla_gate_hash", "Runtime evidence SLA gate", "sla"),
    ("runtime_sla_execution_policy_hash", "Runtime SLA execution policy", "sla"),
    ("commercial_handoff_bundle_hash", "Commercial handoff bundle", "handoff"),
    ("commercial_handoff_acceptance_gate_hash", "Handoff acceptance gate", "handoff"),
    ("commercial_handoff_secret_audit_hash", "Secret/redaction audit", "safety"),
    ("remediation_verification_hash", "Remediation verification artifact", "remediation"),
    ("artifact_archive_hash", "Artifact archive", "archive"),
]


_ALLOWED_STATUSES = {
    "rerun_closure_audit_ready",
}
_CONDITIONAL_STATUSES = {
    "rerun_closure_conditional_reviewer_required",
    "rerun_closure_conditional_review",
}
_BASELINE_ONLY_STATUSES = {
    "rerun_baseline_only_no_previous_receipt",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _short_hash(value: Any) -> str:
    text = str(value or "")
    return text[:12] + ("…" if len(text) > 12 else "") if text else ""


def _matrix_by_field(comparison: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for row in _as_list(comparison.get("comparison_matrix")):
        if isinstance(row, dict) and row.get("field"):
            out[str(row.get("field"))] = row
    return out


def _hash_consistency_cards(receipt: dict[str, Any], comparison: dict[str, Any]) -> list[dict[str, Any]]:
    matrix = _matrix_by_field(comparison)
    previous_present = bool(comparison.get("previous_receipt_present"))
    cards: list[dict[str, Any]] = []
    for field, label, category in _HASH_FIELDS:
        row = matrix.get(field, {})
        current = row.get("current") if row else receipt.get(field)
        previous = row.get("previous") if row else None
        match = bool(row.get("match")) if row else False
        if not previous_present:
            status = "baseline_not_comparable"
            action = "Store this hash as the baseline for the next commercial rerun."
        elif match:
            status = "matched"
            action = "No reviewer action required for this hash."
        elif current and previous:
            status = "changed"
            action = "Reviewer must confirm whether this change is expected before accepting closure claims."
        elif current and not previous:
            status = "new_current_hash"
            action = "Previous receipt did not contain this hash; review before comparing closure lineage."
        elif previous and not current:
            status = "missing_current_hash"
            action = "Current run is missing this hash; do not rely on it for closure evidence."
        else:
            status = "missing_both"
            action = "Hash is unavailable in both receipts; regenerate archive receipts if this artifact is required."
        cards.append({
            "field": field,
            "label": label,
            "category": category,
            "status": status,
            "match": match,
            "current_hash_prefix": _short_hash(current),
            "previous_hash_prefix": _short_hash(previous),
            "customer_action": action,
        })
    return cards


def _closure_claim_state(gate: dict[str, Any], comparison: dict[str, Any]) -> tuple[str, str, list[str]]:
    status = str(gate.get("status") or "rerun_audit_gate_missing")
    blockers = [str((b or {}).get("gate_id") or "UNKNOWN-BLOCKER") for b in _as_list(gate.get("blockers")) if isinstance(b, dict)]
    warnings = [str((w or {}).get("gate_id") or "UNKNOWN-WARNING") for w in _as_list(gate.get("warnings")) if isinstance(w, dict)]
    reasons = blockers + warnings
    if status in _ALLOWED_STATUSES and bool(gate.get("closure_verification_allowed")):
        return "closure_claim_allowed", "This rerun can be used to support closure verification for the previous handoff lineage.", reasons
    if status in _CONDITIONAL_STATUSES and bool(gate.get("closure_verification_allowed")):
        return "closure_claim_reviewer_approval_required", "Closure verification can proceed only after reviewer approval of delivery evidence changes.", reasons
    if status in _BASELINE_ONLY_STATUSES or not comparison.get("previous_receipt_present"):
        return "closure_claim_baseline_only", "This run establishes a baseline receipt but cannot close previous findings without a previous receipt.", reasons
    return "closure_claim_blocked", "Do not use this run to close previous findings until lineage/audit blockers are resolved.", reasons


def _finding_closure_claims(report: dict[str, Any], closure_state: str) -> list[dict[str, Any]]:
    fix_index = _as_dict(report.get("fix_verification_loop_index"))
    lifecycle = _as_dict(report.get("finding_lifecycle_registry"))
    claims: list[dict[str, Any]] = []
    for item in _as_list(fix_index.get("closed_by_rerun")) + _as_list(lifecycle.get("closed_by_rerun")):
        if not isinstance(item, dict):
            continue
        claim_status = "eligible_for_customer_closure" if closure_state == "closure_claim_allowed" else (
            "requires_reviewer_signoff" if closure_state == "closure_claim_reviewer_approval_required" else "closure_claim_blocked_by_lineage_gate"
        )
        claims.append({
            "previous_finding_id": item.get("previous_finding_id"),
            "candidate_id": item.get("candidate_id"),
            "endpoint": item.get("endpoint"),
            "primary_lifecycle_signature": item.get("primary_lifecycle_signature"),
            "close_basis": item.get("close_basis"),
            "claim_status": claim_status,
        })
    # Deduplicate because Phase92X and Phase92Y may both report the same closed finding.
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for claim in claims:
        key = (str(claim.get("previous_finding_id") or ""), str(claim.get("primary_lifecycle_signature") or claim.get("candidate_id") or claim.get("endpoint") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(claim)
    return deduped[:100]


def build_commercial_evidence_lineage_dashboard(report: dict[str, Any]) -> dict[str, Any]:
    """Build a customer-readable lineage dashboard from Phase93M/N/O artifacts."""

    receipt = _as_dict(report.get("immutable_run_receipt"))
    comparison = _as_dict(report.get("handoff_receipt_comparison"))
    gate = _as_dict(report.get("handoff_rerun_audit_gate"))
    cards = _hash_consistency_cards(receipt, comparison)
    changed_cards = [c for c in cards if c.get("status") in {"changed", "missing_current_hash", "new_current_hash"}]
    closure_state, closure_summary, closure_reasons = _closure_claim_state(gate, comparison)
    closure_claims = _finding_closure_claims(report, closure_state)

    reviewer_items: list[dict[str, Any]] = []
    for change in _as_list(comparison.get("changes")):
        if isinstance(change, dict):
            reviewer_items.append({
                "item_id": change.get("change_id"),
                "category": change.get("category"),
                "field": change.get("field"),
                "reason": change.get("reason"),
                "required_for": "closure_claim" if closure_state == "closure_claim_reviewer_approval_required" else "audit_traceability",
            })
    for warning in _as_list(gate.get("warnings")):
        if isinstance(warning, dict):
            reviewer_items.append({
                "item_id": warning.get("gate_id"),
                "category": "rerun_audit_warning",
                "field": None,
                "reason": warning.get("reason"),
                "required_for": "closure_claim",
            })

    if not receipt:
        status = "lineage_dashboard_blocked_missing_current_receipt"
    elif closure_state == "closure_claim_blocked":
        status = "lineage_dashboard_closure_blocked"
    elif closure_state == "closure_claim_baseline_only":
        status = "lineage_dashboard_baseline_only"
    elif closure_state == "closure_claim_reviewer_approval_required":
        status = "lineage_dashboard_reviewer_approval_required"
    else:
        status = "lineage_dashboard_closure_ready"

    return {
        "engine": "runtime_commercial_evidence_lineage_dashboard_v1_phase93p",
        "status": status,
        "project_id": report.get("project_id"),
        "created_at": report.get("created_at"),
        "current_run_lineage_id": receipt.get("run_lineage_id") or comparison.get("current_run_lineage_id"),
        "previous_run_lineage_id": comparison.get("previous_run_lineage_id"),
        "previous_receipt_present": bool(comparison.get("previous_receipt_present")),
        "comparison_status": comparison.get("status"),
        "rerun_audit_status": gate.get("status"),
        "commercial_lineage_claim": gate.get("commercial_lineage_claim"),
        "closure_claim_state": closure_state,
        "closure_claim_summary": closure_summary,
        "closure_blocker_or_warning_ids": closure_reasons,
        "lineage_match": bool(comparison.get("lineage_match")),
        "input_hash_match": bool(comparison.get("input_hash_match")),
        "artifact_archive_hash_match": bool(comparison.get("artifact_archive_hash_match")),
        "hash_consistency_cards": cards,
        "matched_hash_count": sum(1 for c in cards if c.get("status") == "matched"),
        "changed_or_missing_hash_count": len(changed_cards),
        "changed_or_missing_hashes": changed_cards,
        "reviewer_signoff_required": closure_state == "closure_claim_reviewer_approval_required" or bool(gate.get("blocker_count")),
        "reviewer_signoff_items": reviewer_items[:100],
        "finding_closure_claim_count": len(closure_claims),
        "finding_closure_claims": closure_claims,
        "customer_next_action": _next_action(status),
    }


def _next_action(status: str) -> str:
    if status == "lineage_dashboard_closure_ready":
        return "Customer/reviewer may use this rerun dashboard with the immutable receipt to accept eligible finding closure claims."
    if status == "lineage_dashboard_reviewer_approval_required":
        return "Review changed delivery/SLA/artifact hashes and sign off before accepting finding closure claims."
    if status == "lineage_dashboard_baseline_only":
        return "Store this dashboard and receipt as the baseline; provide it as previous_execution_report on the next rerun."
    if status == "lineage_dashboard_blocked_missing_current_receipt":
        return "Regenerate the run with Phase93M immutable receipt enabled before presenting lineage evidence."
    return "Resolve rerun audit blockers before claiming closure against a previous commercial handoff."


def render_commercial_evidence_lineage_dashboard_markdown(dashboard: dict[str, Any]) -> str:
    lines = [
        "# Commercial Evidence Lineage Dashboard",
        "",
        f"- engine: `{dashboard.get('engine')}`",
        f"- status: `{dashboard.get('status')}`",
        f"- current lineage: `{dashboard.get('current_run_lineage_id')}`",
        f"- previous lineage: `{dashboard.get('previous_run_lineage_id')}`",
        f"- previous receipt present: `{dashboard.get('previous_receipt_present')}`",
        f"- comparison status: `{dashboard.get('comparison_status')}`",
        f"- rerun audit status: `{dashboard.get('rerun_audit_status')}`",
        f"- closure claim state: `{dashboard.get('closure_claim_state')}`",
        f"- matched hashes: `{dashboard.get('matched_hash_count')}`",
        f"- changed/missing hashes: `{dashboard.get('changed_or_missing_hash_count')}`",
        f"- finding closure claims: `{dashboard.get('finding_closure_claim_count')}`",
        f"- next action: {dashboard.get('customer_next_action')}",
        "",
        "## Hash Consistency",
        "",
    ]
    for card in dashboard.get("hash_consistency_cards") or []:
        if isinstance(card, dict):
            lines.append(
                f"- `{card.get('field')}`: `{card.get('status')}` "
                f"current `{card.get('current_hash_prefix')}` previous `{card.get('previous_hash_prefix')}`"
            )
    lines.append("")
    if dashboard.get("reviewer_signoff_items"):
        lines.extend(["## Reviewer Signoff Items", ""])
        for item in dashboard.get("reviewer_signoff_items") or []:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('item_id')}` `{item.get('category')}` — {item.get('reason')}")
        lines.append("")
    if dashboard.get("finding_closure_claims"):
        lines.extend(["## Finding Closure Claims", ""])
        for claim in dashboard.get("finding_closure_claims") or []:
            if isinstance(claim, dict):
                lines.append(
                    f"- previous finding `{claim.get('previous_finding_id')}` endpoint `{claim.get('endpoint')}` "
                    f"status `{claim.get('claim_status')}`"
                )
        lines.append("")
    return "\n".join(lines)
