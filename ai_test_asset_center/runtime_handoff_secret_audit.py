from __future__ import annotations

"""Phase93L: commercial handoff secret/redaction audit.

Patch safety validation protects the onboarding delta patch.  Phase93L audits the
commercial handoff payload itself so a customer-facing bundle is not shipped
with raw passwords, bearer tokens, cookies or API keys embedded in report
sections.
"""

import re
from typing import Any

SENSITIVE_KEY_RE = re.compile(r"(?:password|passwd|secret|token|authorization|cookie|api[_-]?key|session)", re.I)
RAW_SECRET_VALUE_RE = re.compile(r"(?:bearer\s+[a-z0-9._\-]{12,}|basic\s+[a-z0-9+/=]{12,}|sk-[a-z0-9]{12,}|eyj[a-z0-9._\-]{16,})", re.I)
SAFE_PLACEHOLDER_RE = re.compile(r"(?:<\s*(?:FILL|REDACTED|TODO|REPLACE|SANDBOX)[^>]*>|\*\*\*|redacted|placeholder)", re.I)
SENSITIVE_METADATA_KEY_RE = re.compile(
    r"(?:secret_audit|raw_tokens_required|token_header_name|token_header_prefix|token_json_path|token_path|password_field|"
    r"safe_for_customer|status|count|enabled|required|present|hash|json|md|path|mode|name|prefix)$",
    re.I,
)

HANDOFF_SECTIONS = [
    "summary",
    "outputs",
    "governance",
    "commercial_handoff_bundle",
    "commercial_handoff_acceptance_gate",
    "onboarding_remediation_kit",
    "onboarding_patch_safety_validation",
    "write_sandbox_approval_packet",
    "runtime_sla_gap_prioritizer",
    "remediation_verification_artifact",
    # Phase95 runtime evidence artifacts are customer-facing after promotion.
    # They can include reconstructed curl commands, request metadata and
    # reproduction traces, so scan them before commercial handoff/archive.
    "runtime_evidence_scoreboard",
    "runtime_evidence_probe_ledger",
    "runtime_customer_reproduction_pack",
    "runtime_evidence_remediation_plan",
    "runtime_evidence_carry_forward",
    "runtime_evidence_progress_delta",
    "runtime_evidence_promotion_gate",
    "runtime_evidence_customer_delivery_manifest",
    "runtime_evidence_delivery_manifest_verification",
]

RUNTIME_EVIDENCE_SECTIONS = {
    "runtime_evidence_scoreboard",
    "runtime_evidence_probe_ledger",
    "runtime_customer_reproduction_pack",
    "runtime_evidence_remediation_plan",
    "runtime_evidence_carry_forward",
    "runtime_evidence_progress_delta",
    "runtime_evidence_promotion_gate",
    "runtime_evidence_customer_delivery_manifest",
    "runtime_evidence_delivery_manifest_verification",
}


def _is_safe_placeholder(value: Any) -> bool:
    return bool(SAFE_PLACEHOLDER_RE.search(str(value)))


def _walk(value: Any, path: str = "$", *, limit: int = 2000) -> list[tuple[str, str, Any]]:
    out: list[tuple[str, str, Any]] = []
    if limit <= 0:
        return out
    if isinstance(value, dict):
        for k, v in value.items():
            key = str(k)
            child_path = f"{path}.{key}"
            out.append((child_path, key, v))
            out.extend(_walk(v, child_path, limit=limit - len(out) - 1))
            if len(out) >= limit:
                break
    elif isinstance(value, list):
        for i, item in enumerate(value[:200]):
            child_path = f"{path}[{i}]"
            out.append((child_path, "", item))
            out.extend(_walk(item, child_path, limit=limit - len(out) - 1))
            if len(out) >= limit:
                break
    return out


def _preview(value: Any) -> str:
    text = str(value)
    if len(text) <= 24:
        return text
    return text[:8] + "…" + text[-4:]


def _is_sensitive_metadata_key(path: str, key: str) -> bool:
    key_text = str(key or "")
    path_text = str(path or "")
    return bool(SENSITIVE_METADATA_KEY_RE.search(key_text) or "secret_audit" in path_text.lower())


def audit_commercial_handoff_secrets(report: dict[str, Any]) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    scanned_sections: list[str] = []
    scanned_runtime_evidence_sections: list[str] = []
    scanned_field_count = 0

    for section in HANDOFF_SECTIONS:
        if section not in report:
            continue
        scanned_sections.append(section)
        if section in RUNTIME_EVIDENCE_SECTIONS:
            scanned_runtime_evidence_sections.append(section)
        for path, key, value in _walk(report.get(section), f"$.{section}"):
            scanned_field_count += 1
            if isinstance(value, (dict, list)):
                continue
            text = str(value)
            if not text or _is_safe_placeholder(text):
                continue
            if isinstance(value, str) and SENSITIVE_KEY_RE.search(key) and len(text.strip()) >= 4 and not _is_sensitive_metadata_key(path, key):
                issues.append({
                    "issue_id": "HANDOFF-RAW-SENSITIVE-FIELD",
                    "severity": "P0",
                    "path": path,
                    "key": key,
                    "value_preview": _preview(text),
                    "reason": "Sensitive-looking field contains a non-placeholder value in a customer-facing handoff section.",
                })
            elif RAW_SECRET_VALUE_RE.search(text):
                issues.append({
                    "issue_id": "HANDOFF-RAW-SECRET-VALUE",
                    "severity": "P0",
                    "path": path,
                    "key": key,
                    "value_preview": _preview(text),
                    "reason": "Bearer/basic/API-key/JWT-looking raw secret value appears in a handoff section.",
                })

    if issues:
        status = "handoff_secret_audit_blocked"
        safe = False
        recommendation = "Remove or redact raw secrets before sending the handoff bundle to the customer."
    else:
        status = "handoff_secret_audit_passed"
        safe = True
        recommendation = "No raw secrets were detected in customer-facing handoff sections."

    runtime_evidence_issue_count = sum(
        1
        for issue in issues
        if str(issue.get("path") or "").split(".")[1:2] and str(issue.get("path") or "").split(".")[1] in RUNTIME_EVIDENCE_SECTIONS
    )

    return {
        "engine": "runtime_handoff_secret_audit_v1_phase93l",
        "status": status,
        "safe_for_customer_handoff": safe,
        "issue_count": len(issues),
        "runtime_evidence_issue_count": runtime_evidence_issue_count,
        "issues": issues,
        "scanned_sections": scanned_sections,
        "scanned_runtime_evidence_sections": scanned_runtime_evidence_sections,
        "scanned_section_count": len(scanned_sections),
        "scanned_runtime_evidence_section_count": len(scanned_runtime_evidence_sections),
        "scanned_field_count": scanned_field_count,
        "recommendation": recommendation,
        "customer_safe_note": "This audit is heuristic and complements, but does not replace, customer-side secret scanning before external distribution.",
    }


def render_handoff_secret_audit_markdown(audit: dict[str, Any]) -> str:
    lines = [
        "# Commercial Handoff Secret Audit",
        "",
        f"- engine: `{audit.get('engine')}`",
        f"- status: `{audit.get('status')}`",
        f"- safe for customer handoff: `{audit.get('safe_for_customer_handoff')}`",
        f"- issue count: `{audit.get('issue_count')}`",
        f"- runtime evidence issue count: `{audit.get('runtime_evidence_issue_count', 0)}`",
        f"- scanned sections: `{', '.join(str(x) for x in audit.get('scanned_sections') or [])}`",
        f"- scanned runtime evidence sections: `{', '.join(str(x) for x in audit.get('scanned_runtime_evidence_sections') or [])}`",
        f"- recommendation: {audit.get('recommendation')}",
        "",
    ]
    if audit.get("issues"):
        lines.extend(["## Issues", ""])
        for issue in audit.get("issues") or []:
            if isinstance(issue, dict):
                lines.append(f"- `{issue.get('issue_id')}` severity `{issue.get('severity')}` at `{issue.get('path')}` — {issue.get('reason')}")
        lines.append("")
    lines.append(f"> {audit.get('customer_safe_note')}")
    return "\n".join(lines)
