from __future__ import annotations

"""Phase93U: commercial audit export import gate.

Phase93T writes customer-system import artifacts.  Phase93U validates that those
exports are structurally importable before customers load them into Jira,
Linear, CSV audit ledgers, or reviewer workflows.  It deliberately allows
customer-owned placeholders such as ``<FILL:jira_project_key>`` while blocking
missing required fields, duplicate tracking keys, and raw secret-like content.
"""

import re
from typing import Any


SECRET_VALUE_RE = re.compile(
    r"(?:bearer\s+[A-Za-z0-9._~+/=-]{16,}|password\s*[:=]\s*[^\s,;]{6,}|api[_-]?key\s*[:=]\s*[A-Za-z0-9._~+/=-]{12,}|secret\s*[:=]\s*[A-Za-z0-9._~+/=-]{12,}|cookie\s*[:=]\s*[^\s,;]{12,})",
    re.I,
)
SAFE_PLACEHOLDER_RE = re.compile(r"^<\s*(?:FILL|REDACTED)[^>]*>$", re.I)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _has_raw_secret(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        return any(_has_raw_secret(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_raw_secret(v) for v in value)
    text = str(value)
    if SAFE_PLACEHOLDER_RE.match(text.strip()):
        return False
    return bool(SECRET_VALUE_RE.search(text))


def _issue_missing(issue: dict[str, Any], required: list[str]) -> list[str]:
    missing = []
    for key in required:
        if issue.get(key) in (None, "", []):
            missing.append(key)
    return missing


def build_commercial_audit_export_import_gate(report: dict[str, Any]) -> dict[str, Any]:
    exports = _as_dict(report.get("commercial_audit_export_adapters"))
    jira_issues = [i for i in _as_list(exports.get("jira_issue_import")) if isinstance(i, dict)]
    linear_issues = [i for i in _as_list(exports.get("linear_issue_import")) if isinstance(i, dict)]
    csv_rows = [r for r in _as_list(exports.get("csv_audit_ledger_rows")) if isinstance(r, dict)]
    closure_keys = [c for c in _as_list(exports.get("closure_external_tracking_keys")) if isinstance(c, dict)]

    violations: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    placeholders: list[dict[str, Any]] = []

    if not exports:
        violations.append({"kind": "missing_export_adapters", "message": "Phase93T commercial audit export adapters are missing."})

    seen_keys: dict[str, str] = {}
    for collection_name, items in (("jira", jira_issues), ("linear", linear_issues), ("csv", csv_rows), ("closure", closure_keys)):
        for item in items:
            key = str(item.get("external_tracking_key") or "")
            if not key:
                violations.append({"kind": "missing_external_tracking_key", "collection": collection_name, "item": item.get("event_id") or item.get("ledger_entry_id")})
            elif key in seen_keys:
                violations.append({"kind": "duplicate_external_tracking_key", "external_tracking_key": key, "first_collection": seen_keys[key], "duplicate_collection": collection_name})
            else:
                seen_keys[key] = collection_name

    for issue in jira_issues:
        missing = _issue_missing(issue, ["external_tracking_key", "projectKey", "issueType", "summary", "description", "priority"])
        if missing:
            violations.append({"kind": "jira_issue_missing_required_fields", "external_tracking_key": issue.get("external_tracking_key"), "missing_fields": missing})
        if str(issue.get("projectKey") or "").startswith("<FILL:"):
            placeholders.append({"kind": "jira_project_key_placeholder", "external_tracking_key": issue.get("external_tracking_key"), "field": "projectKey"})
        if _has_raw_secret(issue):
            violations.append({"kind": "jira_issue_secret_leak", "external_tracking_key": issue.get("external_tracking_key")})

    for issue in linear_issues:
        missing = _issue_missing(issue, ["external_tracking_key", "teamId", "title", "description", "priority"])
        if missing:
            violations.append({"kind": "linear_issue_missing_required_fields", "external_tracking_key": issue.get("external_tracking_key"), "missing_fields": missing})
        if str(issue.get("teamId") or "").startswith("<FILL:"):
            placeholders.append({"kind": "linear_team_id_placeholder", "external_tracking_key": issue.get("external_tracking_key"), "field": "teamId"})
        if _has_raw_secret(issue):
            violations.append({"kind": "linear_issue_secret_leak", "external_tracking_key": issue.get("external_tracking_key")})

    required_csv_fields = {"external_tracking_key", "event_id", "event_kind", "severity", "summary", "run_lineage_id"}
    for row in csv_rows:
        missing = sorted(k for k in required_csv_fields if row.get(k) in (None, ""))
        if missing:
            violations.append({"kind": "csv_row_missing_required_fields", "external_tracking_key": row.get("external_tracking_key"), "missing_fields": missing})
        if str(row.get("commercial_acceptance_status") or "") == "blocked_by_lineage_audit" and not str(row.get("audit_blocker_ids") or "").strip():
            violations.append({"kind": "csv_blocked_closure_missing_audit_blocker_ids", "external_tracking_key": row.get("external_tracking_key"), "event_id": row.get("event_id")})
        if _has_raw_secret(row):
            violations.append({"kind": "csv_row_secret_leak", "external_tracking_key": row.get("external_tracking_key")})

    for item in closure_keys:
        if bool(item.get("blocked")) and not _as_list(item.get("audit_blocker_ids")):
            violations.append({"kind": "closure_tracking_key_missing_audit_blocker_ids", "external_tracking_key": item.get("external_tracking_key"), "ledger_entry_id": item.get("ledger_entry_id")})

    event_count = int(exports.get("event_count") or 0)
    if event_count and (len(jira_issues) != event_count or len(linear_issues) != event_count or len(csv_rows) != event_count):
        warnings.append({
            "kind": "export_count_mismatch",
            "event_count": event_count,
            "jira_issue_count": len(jira_issues),
            "linear_issue_count": len(linear_issues),
            "csv_row_count": len(csv_rows),
        })

    if violations:
        status = "commercial_audit_import_gate_blocked"
        import_ready = False
        next_action = "Fix export adapter violations before importing into customer tracking systems."
    elif placeholders:
        status = "commercial_audit_import_gate_ready_after_customer_system_ids"
        import_ready = True
        next_action = "Fill customer-owned Jira project key and Linear team id, then import the generated artifacts."
    else:
        status = "commercial_audit_import_gate_ready"
        import_ready = True
        next_action = "Import Jira, Linear, CSV and reviewer packet artifacts into the customer systems."

    return {
        "engine": "runtime_commercial_audit_export_import_gate_v1_phase93u",
        "status": status,
        "project_id": report.get("project_id") or exports.get("project_id"),
        "run_lineage_id": exports.get("run_lineage_id"),
        "import_ready": import_ready,
        "violation_count": len(violations),
        "warning_count": len(warnings),
        "placeholder_count": len(placeholders),
        "jira_issue_count": len(jira_issues),
        "linear_issue_count": len(linear_issues),
        "csv_row_count": len(csv_rows),
        "closure_tracking_key_count": len(closure_keys),
        "unique_external_tracking_key_count": len(seen_keys),
        "violations": violations,
        "warnings": warnings,
        "customer_fill_placeholders": placeholders,
        "customer_next_action": next_action,
    }


def render_commercial_audit_import_gate_markdown(gate: dict[str, Any]) -> str:
    lines = [
        "# Commercial Audit Export Import Gate",
        "",
        f"- engine: `{gate.get('engine')}`",
        f"- status: `{gate.get('status')}`",
        f"- import ready: `{gate.get('import_ready')}`",
        f"- Jira issues: `{gate.get('jira_issue_count', 0)}`",
        f"- Linear issues: `{gate.get('linear_issue_count', 0)}`",
        f"- CSV rows: `{gate.get('csv_row_count', 0)}`",
        f"- unique external keys: `{gate.get('unique_external_tracking_key_count', 0)}`",
        f"- violations: `{gate.get('violation_count', 0)}`",
        f"- placeholders: `{gate.get('placeholder_count', 0)}`",
        f"- next action: {gate.get('customer_next_action')}",
        "",
    ]
    if gate.get("violations"):
        lines.extend(["## Violations", ""])
        for item in gate.get("violations") or []:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('kind')}` {item}")
        lines.append("")
    if gate.get("customer_fill_placeholders"):
        lines.extend(["## Customer-Owned Placeholders", ""])
        for item in gate.get("customer_fill_placeholders") or []:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('kind')}` `{item.get('field')}` for `{item.get('external_tracking_key')}`")
        lines.append("")
    return "\n".join(lines)
