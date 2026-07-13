from __future__ import annotations

"""Phase93L: commercial handoff secret/redaction audit.

Patch safety validation protects the onboarding delta patch.  Phase93L audits the
commercial handoff payload itself so a customer-facing bundle is not shipped
with raw passwords, bearer tokens, cookies or API keys embedded in report
sections.
"""

import copy
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


def _issue_section(issue: dict[str, Any]) -> str:
    path = str(issue.get("path") or "")
    if path.startswith("$."):
        return path[2:].split(".", 1)[0].split("[", 1)[0]
    return "unknown"


def _redaction_replacement_for_issue(issue: dict[str, Any]) -> str:
    key = str(issue.get("key") or "").lower()
    issue_id = str(issue.get("issue_id") or "")
    if "authorization" in key or "HANDOFF-RAW-SECRET-VALUE" in issue_id:
        return "<REDACTED_RUNTIME_SECRET>"
    if "cookie" in key or "session" in key:
        return "<REDACTED_COOKIE>"
    if "password" in key or "passwd" in key:
        return "<REDACTED_PASSWORD>"
    if "api" in key and "key" in key:
        return "<REDACTED_API_KEY>"
    if "token" in key or "secret" in key:
        return "<REDACTED_TOKEN>"
    return "<REDACTED>"


def build_handoff_secret_redaction_plan(report: dict[str, Any], audit: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build an actionable redaction/remediation plan from secret-audit findings.

    The plan deliberately does not mutate evidence artifacts.  It gives a
    deterministic redaction queue that can be applied by the artifact producer,
    followed by regeneration of the secret audit, commercial handoff bundle and
    archive manifest.
    """

    audit = audit if isinstance(audit, dict) else audit_commercial_handoff_secrets(report)
    issues = [x for x in audit.get("issues") or [] if isinstance(x, dict)]
    actions: list[dict[str, Any]] = []
    affected_sections: list[str] = []
    affected_runtime_sections: list[str] = []

    for index, issue in enumerate(issues, start=1):
        section = _issue_section(issue)
        if section not in affected_sections:
            affected_sections.append(section)
        if section in RUNTIME_EVIDENCE_SECTIONS and section not in affected_runtime_sections:
            affected_runtime_sections.append(section)
        actions.append({
            "action_id": f"REDACT-{index:03d}",
            "priority": "P0",
            "source_issue_id": issue.get("issue_id"),
            "section": section,
            "path": issue.get("path"),
            "key": issue.get("key"),
            "value_preview": issue.get("value_preview"),
            "replacement": _redaction_replacement_for_issue(issue),
            "owner": "security_owner" if section not in RUNTIME_EVIDENCE_SECTIONS else "runtime_evidence_owner",
            "blocks_customer_handoff": True,
            "required_follow_up": [
                "replace_raw_secret_with_placeholder_or_remove_field",
                "regenerate_runtime_evidence_artifact_if_source_artifact_changed",
                "rerun_commercial_handoff_secret_audit",
                "rerun_runtime_delivery_manifest_hash_verification",
            ],
            "verification": "Secret audit must return safe_for_customer_handoff=true and runtime_evidence_issue_count=0 before customer handoff.",
        })

    if actions:
        status = "handoff_secret_redaction_required"
        safe_after_regeneration = False
        next_action = "Apply every P0 redaction action, regenerate affected artifacts, then rerun the secret audit and delivery manifest verification."
    else:
        status = "handoff_secret_redaction_not_required"
        safe_after_regeneration = True
        next_action = "No redaction actions are required; keep the secret audit artifact with the delivery archive."

    return {
        "engine": "runtime_handoff_secret_redaction_plan_v1_phase95",
        "status": status,
        "redaction_required": bool(actions),
        "safe_for_customer_handoff_after_regeneration": safe_after_regeneration,
        "source_audit_status": audit.get("status"),
        "source_audit_issue_count": audit.get("issue_count", 0),
        "source_runtime_evidence_issue_count": audit.get("runtime_evidence_issue_count", 0),
        "action_count": len(actions),
        "p0_action_count": sum(1 for action in actions if action.get("priority") == "P0"),
        "affected_sections": affected_sections,
        "affected_runtime_evidence_sections": affected_runtime_sections,
        "redaction_actions": actions,
        "replacement_policy": {
            "never_emit_raw_secret_values": True,
            "allowed_placeholders": ["<REDACTED>", "<REDACTED_TOKEN>", "<REDACTED_COOKIE>", "<REDACTED_RUNTIME_SECRET>", "***"],
            "requires_regeneration_after_redaction": bool(actions),
            "does_not_mutate_original_artifacts": True,
        },
        "customer_safe_note": "This plan is safe to share because it contains only paths, previews and placeholder replacements, not full secret values.",
        "next_action": next_action,
    }


def _path_tokens(path: str) -> list[str | int]:
    if not isinstance(path, str) or not path.startswith("$."):
        return []
    tokens: list[str | int] = []
    tail = path[2:]
    for part in tail.split("."):
        if not part:
            continue
        match = re.match(r"^([^\[]+)", part)
        if match:
            tokens.append(match.group(1))
        for index in re.findall(r"\[(\d+)\]", part):
            tokens.append(int(index))
    return tokens


def _get_path_value(root: Any, path: str) -> tuple[bool, Any]:
    current = root
    for token in _path_tokens(path):
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                return False, None
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                return False, None
            current = current[token]
    return True, current


def _set_path_value(root: Any, path: str, value: Any) -> bool:
    tokens = _path_tokens(path)
    if not tokens:
        return False
    current = root
    for token in tokens[:-1]:
        if isinstance(token, int):
            if not isinstance(current, list) or token >= len(current):
                return False
            current = current[token]
        else:
            if not isinstance(current, dict) or token not in current:
                return False
            current = current[token]
    last = tokens[-1]
    if isinstance(last, int):
        if not isinstance(current, list) or last >= len(current):
            return False
        current[last] = value
        return True
    if not isinstance(current, dict):
        return False
    current[last] = value
    return True


def _redact_issue_value(value: Any, issue: dict[str, Any]) -> Any:
    replacement = _redaction_replacement_for_issue(issue)
    if isinstance(value, str):
        redacted = RAW_SECRET_VALUE_RE.sub(replacement, value)
        key = str(issue.get("key") or "")
        if redacted == value and SENSITIVE_KEY_RE.search(key):
            return replacement
        return redacted
    return replacement


def build_handoff_redacted_runtime_evidence_pack(
    report: dict[str, Any],
    audit: dict[str, Any] | None = None,
    plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a customer-safe redacted evidence pack without mutating originals.

    The pack is a convenience artifact for security review and customer
    delivery.  It keeps the original runtime/handoff evidence unchanged, applies
    the deterministic redaction actions to a deep copy, and re-runs the same
    handoff secret audit against the redacted copy.
    """

    audit = audit if isinstance(audit, dict) else audit_commercial_handoff_secrets(report)
    plan = plan if isinstance(plan, dict) else build_handoff_secret_redaction_plan(report, audit)
    redacted_report = copy.deepcopy(report)
    actions = [x for x in plan.get("redaction_actions") or [] if isinstance(x, dict)]
    applied_actions: list[dict[str, Any]] = []
    skipped_actions: list[dict[str, Any]] = []
    redacted_sections: dict[str, Any] = {}

    for action in actions:
        path = str(action.get("path") or "")
        found, original = _get_path_value(redacted_report, path)
        if not found:
            skipped_actions.append({
                "action_id": action.get("action_id"),
                "path": path,
                "reason": "path_not_found_in_report_copy",
            })
            continue
        replacement_issue = {
            "issue_id": action.get("source_issue_id"),
            "key": action.get("key"),
        }
        redacted_value = _redact_issue_value(original, replacement_issue)
        if not _set_path_value(redacted_report, path, redacted_value):
            skipped_actions.append({
                "action_id": action.get("action_id"),
                "path": path,
                "reason": "failed_to_write_redacted_value",
            })
            continue
        section = str(action.get("section") or _issue_section({"path": path}))
        applied_actions.append({
            "action_id": action.get("action_id"),
            "section": section,
            "path": path,
            "replacement": _redaction_replacement_for_issue(replacement_issue),
            "redaction_applied": True,
        })

    for section in HANDOFF_SECTIONS:
        if section in redacted_report and (section in RUNTIME_EVIDENCE_SECTIONS or section in {"commercial_handoff_bundle", "commercial_handoff_acceptance_gate"}):
            redacted_sections[section] = redacted_report.get(section)

    verification_audit = audit_commercial_handoff_secrets(redacted_report)
    blockers: list[str] = []
    if skipped_actions:
        blockers.append("redaction_action_path_missing_or_failed")
    if verification_audit.get("safe_for_customer_handoff") is False:
        blockers.append("redacted_pack_still_contains_secret_indicators")

    if not actions:
        status = "handoff_redacted_runtime_evidence_not_required"
        next_action = "No redacted evidence pack is required; keep the clean secret audit with the delivery archive."
    elif blockers:
        status = "handoff_redacted_runtime_evidence_blocked"
        next_action = "Review skipped redaction paths or remaining secret indicators, then regenerate the source evidence artifacts."
    else:
        status = "handoff_redacted_runtime_evidence_ready"
        next_action = "Use the redacted runtime evidence pack for security review/customer handoff and regenerate delivery manifest hashes if it replaces source artifacts."

    applied_runtime_sections = []
    for action in applied_actions:
        section = str(action.get("section") or "")
        if section in RUNTIME_EVIDENCE_SECTIONS and section not in applied_runtime_sections:
            applied_runtime_sections.append(section)

    return {
        "engine": "runtime_handoff_redacted_evidence_pack_v1_phase95",
        "status": status,
        "redaction_applied": bool(applied_actions),
        "safe_for_customer_handoff_after_redaction": bool(verification_audit.get("safe_for_customer_handoff")),
        "source_audit_status": audit.get("status"),
        "source_issue_count": audit.get("issue_count", 0),
        "source_runtime_evidence_issue_count": audit.get("runtime_evidence_issue_count", 0),
        "redaction_action_count": len(actions),
        "applied_action_count": len(applied_actions),
        "skipped_action_count": len(skipped_actions),
        "redacted_runtime_evidence_sections": applied_runtime_sections,
        "redacted_runtime_evidence_section_count": len(applied_runtime_sections),
        "redacted_sections": redacted_sections,
        "redacted_section_count": len(redacted_sections),
        "applied_actions": applied_actions,
        "skipped_actions": skipped_actions,
        "verification_audit": verification_audit,
        "verification_issue_count": verification_audit.get("issue_count", 0),
        "blockers": blockers,
        "blocker_count": len(blockers),
        "replacement_policy": {
            "mutates_original_report": False,
            "replacement_value": "<REDACTED_RUNTIME_SECRET>",
            "safe_placeholders_allowed": ["<REDACTED>", "<REDACTED_RUNTIME_SECRET>", "<REDACTED_TOKEN>", "<REDACTED_COOKIE>", "***"],
            "requires_secret_audit_after_redaction": True,
        },
        "customer_safe_note": "This pack contains redacted copies of runtime/handoff evidence sections. It does not expose full raw secret values.",
        "next_action": next_action,
    }


def render_handoff_redacted_runtime_evidence_markdown(pack: dict[str, Any]) -> str:
    lines = [
        "# Commercial Handoff Redacted Runtime Evidence Pack",
        "",
        f"- engine: `{pack.get('engine')}`",
        f"- status: `{pack.get('status')}`",
        f"- redaction applied: `{pack.get('redaction_applied')}`",
        f"- safe after redaction: `{pack.get('safe_for_customer_handoff_after_redaction')}`",
        f"- source issue count: `{pack.get('source_issue_count')}`",
        f"- applied action count: `{pack.get('applied_action_count')}`",
        f"- skipped action count: `{pack.get('skipped_action_count')}`",
        f"- redacted runtime evidence sections: `{', '.join(str(x) for x in pack.get('redacted_runtime_evidence_sections') or [])}`",
        f"- blocker count: `{pack.get('blocker_count')}`",
        f"- next action: {pack.get('next_action')}",
        "",
    ]
    blockers = [str(x) for x in pack.get("blockers") or [] if str(x)]
    if blockers:
        lines.extend(["## Blockers", ""])
        for blocker in blockers:
            lines.append(f"- `{blocker}`")
        lines.append("")
    actions = [x for x in pack.get("applied_actions") or [] if isinstance(x, dict)]
    if actions:
        lines.extend(["## Applied redactions", "", "| Action | Section | Path | Replacement |", "| --- | --- | --- | --- |"] )
        for action in actions:
            lines.append(
                f"| `{action.get('action_id')}` | `{action.get('section')}` | `{action.get('path')}` | `{action.get('replacement')}` |"
            )
        lines.append("")
    verification = pack.get("verification_audit") if isinstance(pack.get("verification_audit"), dict) else {}
    lines.extend([
        "## Verification",
        "",
        f"- verification audit status: `{verification.get('status')}`",
        f"- verification issue count: `{verification.get('issue_count', 0)}`",
        "",
        f"> {pack.get('customer_safe_note')}",
    ])
    return "\n".join(lines)


def render_handoff_secret_redaction_plan_markdown(plan: dict[str, Any]) -> str:
    lines = [
        "# Commercial Handoff Secret Redaction Plan",
        "",
        f"- engine: `{plan.get('engine')}`",
        f"- status: `{plan.get('status')}`",
        f"- redaction required: `{plan.get('redaction_required')}`",
        f"- action count: `{plan.get('action_count')}`",
        f"- P0 action count: `{plan.get('p0_action_count')}`",
        f"- affected sections: `{', '.join(str(x) for x in plan.get('affected_sections') or [])}`",
        f"- affected runtime evidence sections: `{', '.join(str(x) for x in plan.get('affected_runtime_evidence_sections') or [])}`",
        f"- next action: {plan.get('next_action')}",
        "",
    ]
    actions = [x for x in plan.get("redaction_actions") or [] if isinstance(x, dict)]
    if actions:
        lines.extend(["## Redaction queue", "", "| Action | Priority | Section | Path | Replacement |", "| --- | --- | --- | --- | --- |"] )
        for action in actions:
            lines.append(
                f"| `{action.get('action_id')}` | `{action.get('priority')}` | `{action.get('section')}` | `{action.get('path')}` | `{action.get('replacement')}` |"
            )
        lines.append("")
        lines.extend(["## Follow-up verification", ""])
        for action in actions:
            lines.append(f"- `{action.get('action_id')}`: {action.get('verification')}")
        lines.append("")
    lines.append(f"> {plan.get('customer_safe_note')}")
    return "\n".join(lines)


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
