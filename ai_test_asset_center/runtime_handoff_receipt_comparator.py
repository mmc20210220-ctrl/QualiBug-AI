from __future__ import annotations

"""Phase93N: immutable handoff receipt comparator.

Phase93M creates a receipt.  Phase93N compares the current receipt with a
previous run so delivery teams can tell whether a rerun used the same probe
plan, the same evidence SLA gate, the same handoff bundle and the same artifact
archive.  This is lifecycle metadata only; it never opens or closes findings by
itself.
"""

from typing import Any

_COMPARE_FIELDS: list[tuple[str, str, str]] = [
    ("probe_plan_hash", "input", "Probe plan / grounded input changed."),
    ("runtime_evidence_sla_gate_hash", "sla_gate", "Commercial evidence SLA gate changed."),
    ("runtime_sla_execution_policy_hash", "sla_policy", "SLA execution policy changed."),
    ("minimum_commercial_gate_failures", "commercial_gate", "Minimum commercial gate failure set changed."),
    ("commercial_blocking_reasons", "commercial_gate", "Commercial blocking reason set changed."),
    ("commercial_handoff_bundle_hash", "handoff_bundle", "Commercial handoff bundle changed."),
    ("commercial_handoff_acceptance_gate_hash", "acceptance_gate", "Customer acceptance gate changed."),
    ("customer_acceptance_violation_count", "acceptance_gate", "Customer acceptance violation count changed."),
    ("customer_acceptance_violation_ids", "acceptance_gate", "Customer acceptance violation set changed."),
    ("commercial_handoff_secret_audit_hash", "secret_audit", "Secret audit changed."),
    ("remediation_verification_hash", "remediation", "Developer remediation artifact changed."),
    ("artifact_archive_hash", "artifact_archive", "Generated artifact archive changed."),
]


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _receipt_from_report(report: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {}
    receipt = _as_dict(report.get("immutable_run_receipt"))
    if receipt:
        return receipt
    manifest = _as_dict(report.get("handoff_archive_manifest"))
    return _as_dict(manifest.get("immutable_run_receipt"))


def _normalize_compare_value(value: Any) -> Any:
    if isinstance(value, list):
        return sorted(str(x) for x in value)
    if isinstance(value, tuple):
        return sorted(str(x) for x in value)
    if isinstance(value, set):
        return sorted(str(x) for x in value)
    return value


def compare_immutable_run_receipts(current_report: dict[str, Any], previous_report: dict[str, Any] | None = None) -> dict[str, Any]:
    current = _receipt_from_report(current_report)
    previous = _receipt_from_report(previous_report)

    if not current:
        return {
            "engine": "runtime_handoff_receipt_comparator_v1_phase93n",
            "status": "current_receipt_missing",
            "previous_receipt_present": bool(previous),
            "lineage_match": False,
            "change_count": 0,
            "changes": [],
            "recommendation": "Regenerate the current run with Phase93M enabled before attempting archive comparison.",
        }
    if not previous:
        return {
            "engine": "runtime_handoff_receipt_comparator_v1_phase93n",
            "status": "no_previous_receipt",
            "current_run_lineage_id": current.get("run_lineage_id"),
            "previous_receipt_present": False,
            "lineage_match": False,
            "change_count": 0,
            "changes": [],
            "comparison_matrix": [],
            "recommendation": "Store this run receipt; future reruns can compare against it for auditability.",
        }

    changes: list[dict[str, Any]] = []
    matrix: list[dict[str, Any]] = []
    for field, category, reason in _COMPARE_FIELDS:
        cur = _normalize_compare_value(current.get(field))
        prev = _normalize_compare_value(previous.get(field))
        match = bool(cur and prev and cur == prev)
        row = {
            "field": field,
            "category": category,
            "match": match,
            "current": cur,
            "previous": prev,
        }
        matrix.append(row)
        if not match:
            changes.append({
                "change_id": f"RECEIPT-{category.upper()}-CHANGED",
                "field": field,
                "category": category,
                "current": cur,
                "previous": prev,
                "reason": reason if cur and prev else f"Missing value while comparing {field}.",
            })

    lineage_match = bool(current.get("run_lineage_id") and current.get("run_lineage_id") == previous.get("run_lineage_id"))
    input_changed = any(c.get("category") == "input" for c in changes)
    handoff_changed = any(c.get("category") in {"handoff_bundle", "acceptance_gate", "secret_audit", "artifact_archive", "remediation"} for c in changes)
    sla_changed = any(c.get("category") in {"sla_gate", "sla_policy"} for c in changes)

    if not changes and lineage_match:
        status = "rerun_same_input_same_handoff_archive"
        recommendation = "The rerun receipt matches the previous archive; customer can treat both receipts as equivalent evidence lineage."
    elif input_changed:
        status = "rerun_input_changed_new_lineage"
        recommendation = "Treat this as a new evidence lineage because the grounded probe plan/input hash changed."
    elif handoff_changed or sla_changed:
        status = "rerun_same_input_delivery_changed"
        recommendation = "Input lineage is stable, but delivery evidence changed; review the changed gates/artifacts before closing commercial acceptance."
    else:
        status = "rerun_receipt_changed_unclassified"
        recommendation = "Review receipt fields before using the rerun for customer audit."

    return {
        "engine": "runtime_handoff_receipt_comparator_v1_phase93n",
        "status": status,
        "current_run_lineage_id": current.get("run_lineage_id"),
        "previous_run_lineage_id": previous.get("run_lineage_id"),
        "previous_receipt_present": True,
        "lineage_match": lineage_match,
        "input_hash_match": current.get("probe_plan_hash") == previous.get("probe_plan_hash") and bool(current.get("probe_plan_hash")),
        "artifact_archive_hash_match": current.get("artifact_archive_hash") == previous.get("artifact_archive_hash") and bool(current.get("artifact_archive_hash")),
        "change_count": len(changes),
        "changes": changes,
        "comparison_matrix": matrix,
        "recommendation": recommendation,
    }


def render_handoff_receipt_comparison_markdown(comparison: dict[str, Any]) -> str:
    lines = [
        "# Immutable Handoff Receipt Comparison",
        "",
        f"- engine: `{comparison.get('engine')}`",
        f"- status: `{comparison.get('status')}`",
        f"- previous receipt present: `{comparison.get('previous_receipt_present')}`",
        f"- current lineage: `{comparison.get('current_run_lineage_id')}`",
        f"- previous lineage: `{comparison.get('previous_run_lineage_id')}`",
        f"- lineage match: `{comparison.get('lineage_match')}`",
        f"- input hash match: `{comparison.get('input_hash_match')}`",
        f"- artifact archive hash match: `{comparison.get('artifact_archive_hash_match')}`",
        f"- change count: `{comparison.get('change_count')}`",
        f"- recommendation: {comparison.get('recommendation')}",
        "",
    ]
    if comparison.get("changes"):
        lines.extend(["## Changes", ""])
        for change in comparison.get("changes") or []:
            if isinstance(change, dict):
                lines.append(f"- `{change.get('change_id')}` field `{change.get('field')}` — {change.get('reason')}")
        lines.append("")
    return "\n".join(lines)
