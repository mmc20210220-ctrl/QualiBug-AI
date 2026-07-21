from __future__ import annotations

"""Phase93Y: external tracker sync payload safety/import gate.

Phase93X emits offline tracker update payloads.  Phase93Y validates that those
payloads are still safe to hand to customer tracker owners: no raw secrets, no
non-dry-run mutations, no resolved transitions when the source closure sync
policy is blocked/pending, and required evidence comments are present.
"""

import re
from typing import Any
import warnings


SECRET_RE = re.compile(
    r"(?:bearer\s+[A-Za-z0-9._~+/=-]{16,}|password\s*[:=]\s*[^\s,;]{6,}|api[_-]?key\s*[:=]\s*[A-Za-z0-9._~+/=-]{12,}|secret\s*[:=]\s*[A-Za-z0-9._~+/=-]{12,}|cookie\s*[:=]\s*[^\s,;]{12,})",
    re.I,
)
SAFE_PLACEHOLDER_RE = re.compile(r"<\s*(?:FILL|REDACTED)[^>]*>", re.I)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _has_secret(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        return any(_has_secret(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_secret(v) for v in value)
    text = str(value)
    text = SAFE_PLACEHOLDER_RE.sub("<SAFE_PLACEHOLDER>", text)
    return bool(SECRET_RE.search(text))


def validate_external_tracker_sync_payloads(report: dict[str, Any]) -> dict[str, Any]:
    payloads = _as_dict(report.get("external_tracker_sync_payloads"))
    source_policy = _as_dict(report.get("external_tracker_closure_sync_policy"))
    source_status = str(source_policy.get("status") or payloads.get("source_policy_status") or "")
    policy_by_id = {
        str(policy.get("sync_policy_id")): policy
        for policy in _as_list(source_policy.get("policies"))
        if isinstance(policy, dict) and policy.get("sync_policy_id")
    }
    blocked_policy_ids = {
        policy_id
        for policy_id, policy in policy_by_id.items()
        if bool(policy.get("blocked")) or str(policy.get("sync_status") or "").startswith("sync_blocked")
    }
    violations: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not payloads:
        violations.append({"kind": "missing_sync_payloads", "message": "Phase93X external tracker sync payloads are missing."})

    if payloads.get("dry_run_only") is not True:
        violations.append({"kind": "non_dry_run_payloads", "message": "External tracker sync payloads must remain dry-run only."})

    if source_status in {"external_tracker_closure_sync_blocked", "external_tracker_closure_sync_pending", "external_tracker_closure_sync_no_claims"}:
        if _as_list(payloads.get("jira_transition_payloads")) or _as_list(payloads.get("linear_update_payloads")) or _as_list(payloads.get("csv_status_updates")):
            violations.append({"kind": "resolution_payloads_generated_from_non_ready_policy", "source_policy_status": source_status})

    for collection_name, items in (
        ("jira", _as_list(payloads.get("jira_transition_payloads"))),
        ("linear", _as_list(payloads.get("linear_update_payloads"))),
        ("csv", _as_list(payloads.get("csv_status_updates"))),
        ("hold", _as_list(payloads.get("hold_items"))),
    ):
        for item in items:
            if not isinstance(item, dict):
                violations.append({"kind": "invalid_payload_item", "collection": collection_name})
                continue
            if _has_secret(item):
                violations.append({"kind": "payload_secret_leak", "collection": collection_name, "sync_policy_id": item.get("sync_policy_id")})
            sync_policy_id = str(item.get("sync_policy_id") or "")
            source_entry = policy_by_id.get(sync_policy_id, {})
            if collection_name in {"jira", "linear", "csv"} and sync_policy_id in blocked_policy_ids:
                violations.append({
                    "kind": "resolution_payload_from_blocked_policy",
                    "collection": collection_name,
                    "sync_policy_id": sync_policy_id,
                    "audit_blocker_ids": source_entry.get("audit_blocker_ids") or [],
                })
            if collection_name == "hold" and sync_policy_id in blocked_policy_ids:
                source_blocker_ids = _as_list(source_entry.get("audit_blocker_ids"))
                hold_blocker_ids = _as_list(item.get("audit_blocker_ids"))
                if source_blocker_ids and not hold_blocker_ids:
                    violations.append({
                        "kind": "hold_item_missing_audit_blockers",
                        "sync_policy_id": sync_policy_id,
                        "expected_audit_blocker_ids": source_blocker_ids,
                    })
            if collection_name in {"jira", "linear"} and item.get("dry_run_only") is not True:
                violations.append({"kind": "mutation_payload_not_marked_dry_run", "collection": collection_name, "sync_policy_id": item.get("sync_policy_id")})
            if collection_name == "jira":
                transition = _as_dict(item.get("transition"))
                if not item.get("issue_id_or_key"):
                    violations.append({"kind": "jira_payload_missing_issue_id", "sync_policy_id": item.get("sync_policy_id")})
                if transition.get("target_status") != "Resolved":
                    warnings.append({"kind": "jira_payload_nonstandard_target_status", "sync_policy_id": item.get("sync_policy_id"), "target_status": transition.get("target_status")})
                if not item.get("comment"):
                    violations.append({"kind": "jira_payload_missing_evidence_comment", "sync_policy_id": item.get("sync_policy_id")})
            if collection_name == "linear":
                if not item.get("issue_id"):
                    violations.append({"kind": "linear_payload_missing_issue_id", "sync_policy_id": item.get("sync_policy_id")})
                if not item.get("comment"):
                    violations.append({"kind": "linear_payload_missing_evidence_comment", "sync_policy_id": item.get("sync_policy_id")})
            if collection_name == "csv" and not item.get("external_tracking_key"):
                violations.append({"kind": "csv_update_missing_external_tracking_key", "sync_policy_id": item.get("sync_policy_id")})

    ready_count = int(payloads.get("jira_transition_payload_count") or 0) + int(payloads.get("linear_update_payload_count") or 0) + int(payloads.get("csv_status_update_count") or 0)
    if not ready_count and int(payloads.get("hold_item_count") or 0):
        warnings.append({"kind": "hold_only_payloads", "message": "Only hold/comment guidance was generated; no tracker resolution updates should be applied."})

    if violations:
        status = "external_tracker_sync_payload_gate_blocked"
        import_ready = False
        next_action = "Fix payload gate violations before handing tracker updates to the customer."
    elif ready_count:
        status = "external_tracker_sync_payload_gate_ready_for_customer_review"
        import_ready = True
        next_action = "Customer tracker owner may review and apply dry-run update payloads through an approved integration."
    else:
        status = "external_tracker_sync_payload_gate_hold_only"
        import_ready = False
        next_action = "Do not apply resolution updates yet; keep tracker items open until closure sync policy is ready."

    return {
        "engine": "runtime_external_tracker_sync_payload_gate_v1_phase93y",
        "status": status,
        "project_id": report.get("project_id") or payloads.get("project_id"),
        "run_lineage_id": payloads.get("run_lineage_id"),
        "source_policy_status": source_status,
        "payload_import_ready": import_ready,
        "violation_count": len(violations),
        "warning_count": len(warnings),
        "ready_update_payload_count": ready_count,
        "hold_item_count": int(payloads.get("hold_item_count") or 0),
        "violations": violations,
        "warnings": warnings,
        "customer_next_action": next_action,
    }


def render_external_tracker_sync_payload_gate_markdown(gate: dict[str, Any]) -> str:
    lines = [
        "# External Tracker Sync Payload Gate",
        "",
        f"- engine: `{gate.get('engine')}`",
        f"- status: `{gate.get('status')}`",
        f"- payload import ready: `{gate.get('payload_import_ready')}`",
        f"- source policy: `{gate.get('source_policy_status')}`",
        f"- ready update payloads: `{gate.get('ready_update_payload_count', 0)}`",
        f"- hold items: `{gate.get('hold_item_count', 0)}`",
        f"- violations: `{gate.get('violation_count', 0)}`",
        f"- warnings: `{gate.get('warning_count', 0)}`",
        f"- next action: {gate.get('customer_next_action')}",
        "",
    ]
    if gate.get("violations"):
        lines.extend(["## Violations", ""])
        for item in _as_list(gate.get("violations")):
            if isinstance(item, dict):
                lines.append(f"- `{item.get('kind')}` {item}")
        lines.append("")
    if gate.get("warnings"):
        lines.extend(["## Warnings", ""])
        for item in _as_list(gate.get("warnings")):
            if isinstance(item, dict):
                lines.append(f"- `{item.get('kind')}` {item}")
        lines.append("")
    return "\n".join(lines)
