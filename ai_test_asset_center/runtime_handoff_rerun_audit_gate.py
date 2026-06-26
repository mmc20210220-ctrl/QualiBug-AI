from __future__ import annotations

"""Phase93O: rerun audit gate for commercial closure claims.

The receipt comparator explains what changed.  Phase93O turns that comparison
into an operational gate: can this rerun be used to close findings or claim the
same commercial handoff lineage, or must the customer treat it as a new run?
"""

from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _changes_by_category(comparison: dict[str, Any], category: str) -> list[dict[str, Any]]:
    return [
        change for change in (comparison.get("changes") or [])
        if isinstance(change, dict) and str(change.get("category") or "") == category
    ]


def build_handoff_rerun_audit_gate(report: dict[str, Any]) -> dict[str, Any]:
    comparison = _as_dict(report.get("handoff_receipt_comparison"))
    fix_index = _as_dict(report.get("fix_verification_loop_index"))
    lifecycle = _as_dict(report.get("finding_lifecycle_registry"))
    previous_present = bool(comparison.get("previous_receipt_present"))
    status = str(comparison.get("status") or "comparison_missing")

    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not comparison:
        blockers.append({"gate_id": "RERUN-COMPARISON-MISSING", "severity": "P0", "reason": "Immutable receipt comparison was not built."})
    if status == "current_receipt_missing":
        blockers.append({"gate_id": "RERUN-CURRENT-RECEIPT-MISSING", "severity": "P0", "reason": "Current run has no immutable receipt."})
    if status == "rerun_input_changed_new_lineage":
        blockers.append({"gate_id": "RERUN-INPUT-LINEAGE-CHANGED", "severity": "P0", "reason": "Probe plan/input hash changed; do not use this rerun to close previous findings."})
    commercial_gate_changes = _changes_by_category(comparison, "commercial_gate")
    acceptance_gate_changes = _changes_by_category(comparison, "acceptance_gate")
    if commercial_gate_changes:
        blockers.append({
            "gate_id": "RERUN-MINIMUM-COMMERCIAL-GATE-CHANGED",
            "severity": "P0",
            "reason": "Minimum commercial gate failures or commercial blockers changed; rerun closure requires a fresh commercial acceptance review.",
            "changed_fields": [str(change.get("field")) for change in commercial_gate_changes if change.get("field")],
        })
    if acceptance_gate_changes:
        blockers.append({
            "gate_id": "RERUN-CUSTOMER-ACCEPTANCE-GATE-CHANGED",
            "severity": "P0",
            "reason": "Customer acceptance gate or violation state changed; do not close previous commercial claims without re-acceptance.",
            "changed_fields": [str(change.get("field")) for change in acceptance_gate_changes if change.get("field")],
        })
    if status == "rerun_same_input_delivery_changed":
        warnings.append({"gate_id": "RERUN-DELIVERY-ARCHIVE-CHANGED", "severity": "P1", "reason": "Input hash is stable, but SLA/handoff/artifact hashes changed; require reviewer approval before closure claim."})
    if not previous_present:
        warnings.append({"gate_id": "RERUN-NO-PREVIOUS-BASELINE", "severity": "P2", "reason": "No previous receipt was supplied; this run can establish a baseline but cannot prove closure against a prior handoff."})

    closed_count = int(fix_index.get("closed_by_rerun_count") or len(fix_index.get("closed_by_rerun") or []))
    reopened_count = int(fix_index.get("reopened_finding_count") or len(fix_index.get("reopened") or []))
    stable_match_count = int(lifecycle.get("stable_match_count") or 0)

    if blockers:
        gate_status = "rerun_closure_audit_blocked"
        closure_allowed = False
        commercial_lineage_claim = "new_or_invalid_lineage"
        recommendation = "Do not close previous commercial findings from this rerun; resolve lineage blockers or start a new baseline."
    elif status == "rerun_same_input_same_handoff_archive":
        gate_status = "rerun_closure_audit_ready"
        closure_allowed = True
        commercial_lineage_claim = "same_input_same_handoff_archive"
        recommendation = "Rerun can be used for closure verification against the previous handoff receipt."
    elif status == "rerun_same_input_delivery_changed":
        gate_status = "rerun_closure_conditional_reviewer_required"
        closure_allowed = True
        commercial_lineage_claim = "same_input_delivery_changed"
        recommendation = "Closure verification is allowed only with reviewer approval because delivery evidence changed."
    elif not previous_present:
        gate_status = "rerun_baseline_only_no_previous_receipt"
        closure_allowed = False
        commercial_lineage_claim = "baseline_only"
        recommendation = "Store this receipt as the baseline for future closure reruns."
    else:
        gate_status = "rerun_closure_conditional_review"
        closure_allowed = not blockers
        commercial_lineage_claim = "review_required"
        recommendation = "Review receipt comparison before using the rerun for commercial closure."

    return {
        "engine": "runtime_handoff_rerun_audit_gate_v1_phase93o",
        "status": gate_status,
        "closure_verification_allowed": closure_allowed,
        "commercial_lineage_claim": commercial_lineage_claim,
        "previous_receipt_present": previous_present,
        "comparison_status": status,
        "current_run_lineage_id": comparison.get("current_run_lineage_id"),
        "previous_run_lineage_id": comparison.get("previous_run_lineage_id"),
        "lineage_match": bool(comparison.get("lineage_match")),
        "input_hash_match": bool(comparison.get("input_hash_match")),
        "artifact_archive_hash_match": bool(comparison.get("artifact_archive_hash_match")),
        "change_count": comparison.get("change_count", 0),
        "blocker_count": len(blockers),
        "warning_count": len(warnings),
        "blockers": blockers,
        "warnings": warnings,
        "closure_metrics": {
            "closed_by_rerun_count": closed_count,
            "reopened_finding_count": reopened_count,
            "stable_lifecycle_match_count": stable_match_count,
        },
        "recommendation": recommendation,
    }


def render_handoff_rerun_audit_gate_markdown(gate: dict[str, Any]) -> str:
    lines = [
        "# Commercial Rerun Audit Gate",
        "",
        f"- engine: `{gate.get('engine')}`",
        f"- status: `{gate.get('status')}`",
        f"- closure verification allowed: `{gate.get('closure_verification_allowed')}`",
        f"- commercial lineage claim: `{gate.get('commercial_lineage_claim')}`",
        f"- previous receipt present: `{gate.get('previous_receipt_present')}`",
        f"- comparison status: `{gate.get('comparison_status')}`",
        f"- current lineage: `{gate.get('current_run_lineage_id')}`",
        f"- previous lineage: `{gate.get('previous_run_lineage_id')}`",
        f"- input hash match: `{gate.get('input_hash_match')}`",
        f"- artifact archive hash match: `{gate.get('artifact_archive_hash_match')}`",
        f"- blockers: `{gate.get('blocker_count')}`",
        f"- warnings: `{gate.get('warning_count')}`",
        f"- recommendation: {gate.get('recommendation')}",
        "",
    ]
    if gate.get("blockers"):
        lines.extend(["## Blockers", ""])
        for item in gate.get("blockers") or []:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('gate_id')}` severity `{item.get('severity')}` — {item.get('reason')}")
        lines.append("")
    if gate.get("warnings"):
        lines.extend(["## Warnings", ""])
        for item in gate.get("warnings") or []:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('gate_id')}` severity `{item.get('severity')}` — {item.get('reason')}")
        lines.append("")
    return "\n".join(lines)
