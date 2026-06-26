from __future__ import annotations

"""Phase93Q: commercial lineage reviewer signoff packet.

Phase93P shows lineage/hash differences to customers.  Phase93Q converts the
review-needed parts into an explicit signoff packet with required acknowledgments
and acceptance/rejection choices, so changed delivery evidence cannot silently be
used to close findings without human approval.
"""

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _signoff_items(dashboard: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in _as_list(dashboard.get("reviewer_signoff_items")):
        if not isinstance(item, dict):
            continue
        items.append({
            "signoff_item_id": item.get("item_id") or f"LINEAGE-REVIEW-{len(items)+1:03d}",
            "category": item.get("category") or "lineage_change",
            "field": item.get("field"),
            "reason": item.get("reason") or "Lineage evidence requires reviewer decision.",
            "required_for": item.get("required_for") or "closure_claim",
            "reviewer_decision": "pending",
            "allowed_decisions": ["approve_expected_change", "reject_closure_claim", "request_rerun_with_same_archive"],
        })
    if not items:
        for card in _as_list(dashboard.get("changed_or_missing_hashes")):
            if not isinstance(card, dict):
                continue
            items.append({
                "signoff_item_id": f"HASH-{len(items)+1:03d}",
                "category": card.get("category") or "hash_change",
                "field": card.get("field"),
                "reason": card.get("customer_action") or "Hash changed or is missing and needs review.",
                "required_for": "closure_claim",
                "reviewer_decision": "pending",
                "allowed_decisions": ["approve_expected_change", "reject_closure_claim", "request_rerun_with_same_archive"],
            })
    return items[:100]


def build_commercial_lineage_reviewer_signoff_packet(report: dict[str, Any]) -> dict[str, Any]:
    dashboard = _as_dict(report.get("commercial_evidence_lineage_dashboard"))
    gate = _as_dict(report.get("handoff_rerun_audit_gate"))
    closure_state = str(dashboard.get("closure_claim_state") or "unknown")
    items = _signoff_items(dashboard)
    blockers = _as_list(gate.get("blockers"))

    signoff_required = bool(dashboard.get("reviewer_signoff_required")) or closure_state == "closure_claim_reviewer_approval_required"
    blocked = closure_state == "closure_claim_blocked" or bool(blockers)

    if blocked:
        status = "lineage_signoff_blocked_by_audit_gate"
        reviewer_action = "Resolve rerun audit blockers or rerun with the same input/archive before reviewer signoff can approve closure."
    elif signoff_required or items:
        status = "lineage_reviewer_signoff_required"
        reviewer_action = "Reviewer must approve each expected delivery/hash change before closure claims can be accepted."
    else:
        status = "lineage_reviewer_signoff_not_required"
        reviewer_action = "No reviewer signoff is required; retain this packet as audit evidence."

    closure_claims = _as_list(dashboard.get("finding_closure_claims"))
    return {
        "engine": "runtime_commercial_lineage_reviewer_signoff_v1_phase93q",
        "status": status,
        "project_id": report.get("project_id"),
        "created_at": report.get("created_at"),
        "current_run_lineage_id": dashboard.get("current_run_lineage_id"),
        "previous_run_lineage_id": dashboard.get("previous_run_lineage_id"),
        "closure_claim_state": closure_state,
        "signoff_required": status == "lineage_reviewer_signoff_required",
        "signoff_blocked": status == "lineage_signoff_blocked_by_audit_gate",
        "signoff_item_count": len(items),
        "signoff_items": items,
        "blocked_gate_ids": [str((b or {}).get("gate_id") or "UNKNOWN-BLOCKER") for b in blockers if isinstance(b, dict)],
        "closure_claim_count": len(closure_claims),
        "closure_claims_under_review": [
            {
                "previous_finding_id": c.get("previous_finding_id"),
                "endpoint": c.get("endpoint"),
                "claim_status": c.get("claim_status"),
            }
            for c in closure_claims
            if isinstance(c, dict)
        ][:100],
        "reviewer_attestation_template": {
            "reviewer_name": "<FILL:reviewer_name>",
            "reviewed_at": "<FILL:iso_datetime>",
            "decision": "<approve_expected_change|reject_closure_claim|request_rerun_with_same_archive>",
            "notes": "<FILL:review_notes>",
        },
        "customer_next_action": reviewer_action,
    }


def render_commercial_lineage_reviewer_signoff_markdown(packet: dict[str, Any]) -> str:
    lines = [
        "# Commercial Lineage Reviewer Signoff Packet",
        "",
        f"- engine: `{packet.get('engine')}`",
        f"- status: `{packet.get('status')}`",
        f"- current lineage: `{packet.get('current_run_lineage_id')}`",
        f"- previous lineage: `{packet.get('previous_run_lineage_id')}`",
        f"- closure claim state: `{packet.get('closure_claim_state')}`",
        f"- signoff required: `{packet.get('signoff_required')}`",
        f"- signoff blocked: `{packet.get('signoff_blocked')}`",
        f"- signoff items: `{packet.get('signoff_item_count')}`",
        f"- closure claims under review: `{packet.get('closure_claim_count')}`",
        f"- next action: {packet.get('customer_next_action')}",
        "",
    ]
    if packet.get("signoff_items"):
        lines.extend(["## Required Signoff Items", ""])
        for item in packet.get("signoff_items") or []:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('signoff_item_id')}` `{item.get('category')}` field `{item.get('field')}` — {item.get('reason')}")
        lines.append("")
    if packet.get("closure_claims_under_review"):
        lines.extend(["## Closure Claims Under Review", ""])
        for claim in packet.get("closure_claims_under_review") or []:
            if isinstance(claim, dict):
                lines.append(f"- `{claim.get('previous_finding_id')}` `{claim.get('endpoint')}` status `{claim.get('claim_status')}`")
        lines.append("")
    return "\n".join(lines)
