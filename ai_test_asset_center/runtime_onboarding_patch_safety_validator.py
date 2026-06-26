from __future__ import annotations

"""Phase93H: onboarding patch safety validator.

Phase93G generates the minimal customer onboarding delta patch.  Phase93H checks
that the patch is safe to send and safe to merge into a runtime probe_config:
no production targets, no pasted credentials, only supported cleanup strategies,
and placeholders retained for sensitive values.
"""

import re
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

PLACEHOLDER_RE = re.compile(r"<\s*(?:FILL|TODO|REQUIRED|SANDBOX|REPLACE)[^>]*>", re.I)
SENSITIVE_KEY_RE = re.compile(r"(?:password|passwd|secret|token|authorization|cookie|api[_-]?key|session)", re.I)
PROD_HOST_RE = re.compile(r"(?:^|[.-])(?:prod|production|live)(?:[.-]|$)|^www\.", re.I)
NON_PROD_HINT_RE = re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0|staging|stage|test|qa|uat|dev|sandbox|mock|local|preprod)", re.I)
SUPPORTED_CLEANUP = {"fixture_reset", "auto_delete", "transaction_rollback", "ephemeral_reset", "qualibug_auto_fixture_cleanup", "manual_disposable"}


def _walk(value: Any, path: str = "$"):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _walk(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, value


def _redact_sensitive(value: Any, parent_key: str = "") -> Any:
    if isinstance(value, dict):
        return {k: _redact_sensitive(v, k) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_sensitive(v, parent_key) for v in value]
    if isinstance(value, str) and SENSITIVE_KEY_RE.search(parent_key) and not PLACEHOLDER_RE.search(value):
        return "<REDACTED:customer_secret>"
    return value


def _issue(issue_id: str, severity: str, path: str, message: str, customer_action: str) -> dict[str, Any]:
    return {
        "issue_id": issue_id,
        "severity": severity,
        "path": path,
        "message": message,
        "customer_action": customer_action,
    }


def _base_url_issue(patch: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    base_url = str(patch.get("base_url") or "")
    environment_kind = str(patch.get("environment_kind") or (patch.get("test_environment") or {}).get("kind") or "")
    if not base_url:
        return issues
    parsed = urlparse(base_url.replace("<FILL:staging-host>", "staging.local"))
    host = parsed.netloc or parsed.path
    if PROD_HOST_RE.search(host) or environment_kind.lower() in {"prod", "production", "live"}:
        issues.append(_issue(
            "PATCH-PRODUCTION-TARGET",
            "P0",
            "$.base_url",
            "Patch appears to target a production-like host or production-declared environment.",
            "Replace base_url/environment_kind with a staging, QA, UAT or sandbox target before enabling runtime probes.",
        ))
    elif base_url and not (NON_PROD_HINT_RE.search(base_url) or PLACEHOLDER_RE.search(base_url) or environment_kind.lower() in {"staging", "stage", "test", "qa", "uat", "dev", "sandbox", "preprod"}):
        issues.append(_issue(
            "PATCH-NONPROD-AMBIGUOUS",
            "P1",
            "$.base_url",
            "Patch target is not clearly non-production.",
            "Add environment_kind='staging' or use an unmistakable staging/QA/sandbox URL.",
        ))
    return issues


def validate_onboarding_patch_safety(report_or_patch: dict[str, Any]) -> dict[str, Any]:
    """Validate Phase93G minimal onboarding patch or a raw patch dict."""

    if "runtime_sla_gap_prioritizer" in report_or_patch:
        source = report_or_patch.get("runtime_sla_gap_prioritizer") if isinstance(report_or_patch.get("runtime_sla_gap_prioritizer"), dict) else {}
        patch = source.get("minimal_next_onboarding_patch") if isinstance(source.get("minimal_next_onboarding_patch"), dict) else {}
        source_engine = source.get("engine")
    else:
        patch = report_or_patch if isinstance(report_or_patch, dict) else {}
        source_engine = "raw_patch"

    patch = deepcopy(patch)
    issues: list[dict[str, Any]] = []
    issues.extend(_base_url_issue(patch))

    for path, value in _walk(patch):
        key_name = path.split(".")[-1].split("[")[0]
        if isinstance(value, str) and SENSITIVE_KEY_RE.search(key_name):
            if not PLACEHOLDER_RE.search(value):
                issues.append(_issue(
                    "PATCH-RAW-SECRET",
                    "P0",
                    path,
                    "Sensitive field contains a concrete value instead of a customer-side placeholder.",
                    "Replace this value with <FILL:customer_staging_secret> or provide it only in the customer's secret manager.",
                ))
        if isinstance(value, str) and "base_url" in path and PROD_HOST_RE.search(value):
            issues.append(_issue(
                "PATCH-PRODUCTION-URL",
                "P0",
                path,
                "Production-like URL was detected inside the onboarding patch.",
                "Use a disposable staging/QA/sandbox URL only.",
            ))

    cleanup = ((patch.get("test_environment") or {}) if isinstance(patch.get("test_environment"), dict) else {}).get("cleanup_strategy")
    if cleanup and str(cleanup) not in SUPPORTED_CLEANUP:
        issues.append(_issue(
            "PATCH-UNSUPPORTED-CLEANUP",
            "P1",
            "$.test_environment.cleanup_strategy",
            f"Cleanup strategy '{cleanup}' is not in the supported repeatable cleanup set.",
            "Use fixture_reset, auto_delete, transaction_rollback, ephemeral_reset or qualibug_auto_fixture_cleanup.",
        ))
    test_env = patch.get("test_environment") if isinstance(patch.get("test_environment"), dict) else {}
    if test_env.get("allow_write_probes") and not cleanup:
        issues.append(_issue(
            "PATCH-WRITE-WITHOUT-CLEANUP",
            "P0",
            "$.test_environment",
            "Patch enables write probes but does not declare a cleanup strategy.",
            "Declare cleanup_strategy before running write-sandbox probes.",
        ))

    p0 = [i for i in issues if i.get("severity") == "P0"]
    p1 = [i for i in issues if i.get("severity") == "P1"]
    if p0:
        status = "unsafe_blocked"
    elif p1:
        status = "needs_customer_review"
    else:
        status = "safe_to_send"

    return {
        "engine": "runtime_onboarding_patch_safety_validator_v1_phase93h",
        "source_engine": source_engine,
        "status": status,
        "safe_to_send_to_customer": not p0,
        "safe_to_merge_without_secrets": not p0 and not any(i.get("issue_id") == "PATCH-RAW-SECRET" for i in issues),
        "issue_count": len(issues),
        "p0_issue_count": len(p0),
        "p1_issue_count": len(p1),
        "issues": issues,
        "sanitized_patch_preview": _redact_sensitive(patch),
        "patch_contract": {
            "requires_non_production_target": True,
            "requires_placeholder_for_sensitive_values": True,
            "requires_cleanup_when_write_probes_enabled": True,
            "production_secrets_required": False,
            "raw_tokens_required": False,
        },
        "customer_safe_note": "This validator checks onboarding patch safety only; it does not execute probes or validate business behavior.",
    }


def render_onboarding_patch_safety_markdown(validation: dict[str, Any]) -> str:
    lines = [
        "# Onboarding Patch Safety Validation",
        "",
        f"- engine: `{validation.get('engine')}`",
        f"- status: `{validation.get('status')}`",
        f"- safe to send to customer: `{validation.get('safe_to_send_to_customer')}`",
        f"- safe to merge without secrets: `{validation.get('safe_to_merge_without_secrets')}`",
        f"- issues: `{validation.get('issue_count')}`; P0: `{validation.get('p0_issue_count')}`; P1: `{validation.get('p1_issue_count')}`",
        "",
    ]
    issues = [i for i in (validation.get("issues") or []) if isinstance(i, dict)]
    if issues:
        lines.extend(["## Issues", ""])
        for issue in issues:
            lines.append(f"- **{issue.get('issue_id')}** `{issue.get('severity')}` `{issue.get('path')}` — {issue.get('customer_action')}")
        lines.append("")
    lines.extend([
        "## Sanitized patch preview",
        "",
        "```json",
        _json_dumps(validation.get("sanitized_patch_preview") or {}),
        "```",
        "",
        f"> {validation.get('customer_safe_note')}",
    ])
    return "\n".join(lines)


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)
