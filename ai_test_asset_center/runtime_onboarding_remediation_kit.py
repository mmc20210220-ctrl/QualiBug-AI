from __future__ import annotations

"""Phase93C: customer onboarding remediation kit.

When preflight/capability checks are blocked or degraded, this module converts
machine checks into a safe customer-facing setup packet: what to fix, why it
matters, and a redacted probe_config patch template.  It never asks for real
secrets; placeholders remain explicit and non-executable.
"""

from typing import Any

SENSITIVE_PLACEHOLDER = "<FILL:customer_staging_secret>"


def _check(preflight: dict[str, Any], name: str) -> dict[str, Any]:
    for item in preflight.get("checks") or []:
        if isinstance(item, dict) and item.get("name") == name:
            return item
    return {}


def _missing_roles(preflight: dict[str, Any]) -> list[str]:
    roles = preflight.get("role_coverage") if isinstance(preflight.get("role_coverage"), dict) else {}
    return [str(x) for x in (roles.get("missing_recommended_roles") or [])]


def _capability_gap_counts(matrix: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in matrix.get("rows") or []:
        if not isinstance(row, dict):
            continue
        for name in list(row.get("missing_blocking_capabilities") or []) + list(row.get("missing_optional_capabilities") or []):
            key = str(name)
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _action(action_id: str, title: str, reason: str, owner: str, priority: str, config_keys: list[str], validation: str) -> dict[str, Any]:
    return {
        "action_id": action_id,
        "priority": priority,
        "title": title,
        "reason": reason,
        "recommended_owner": owner,
        "probe_config_keys": config_keys,
        "validation_after_change": validation,
    }


def build_onboarding_remediation_kit(preflight: dict[str, Any], matrix: dict[str, Any]) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    if not (_check(preflight, "base_url_configured") or {}).get("ok"):
        actions.append(_action(
            "ONBOARD-BASE-URL",
            "Configure staging/QA base_url",
            "Runtime evidence cannot be collected without a reachable customer test target.",
            "customer-qa-or-platform-owner",
            "P0",
            ["base_url", "environment_kind"],
            "Rerun preflight and confirm base_url_configured/base_url_reachable pass.",
        ))
    if not (_check(preflight, "non_production_target") or {}).get("ok"):
        actions.append(_action(
            "ONBOARD-NON-PROD",
            "Switch to a clearly non-production target",
            "QualiBug must not run write probes against production-like URLs or production-declared environments.",
            "customer-platform-owner",
            "P0",
            ["base_url", "environment_kind", "test_environment.kind"],
            "Rerun preflight and confirm non_production_target passes before enabling writes.",
        ))
    if not (_check(preflight, "auth_session_ready") or {}).get("ok"):
        actions.append(_action(
            "ONBOARD-AUTH",
            "Provide auth_flow and test accounts",
            "Auth, privacy and ownership probes need real staging sessions; raw tokens are optional compatibility only.",
            "customer-qa-or-auth-owner",
            "P1",
            ["auth_flow", "accounts", "default_account"],
            "Rerun preflight and confirm auth_session_ready has at least one successful session.",
        ))
    roles = _missing_roles(preflight)
    if roles:
        actions.append(_action(
            "ONBOARD-ROLES",
            "Add recommended role and tenant coverage",
            "Boundary and tenant-isolation probes are weaker without normal/admin/owner/cross-tenant accounts.",
            "customer-qa-or-tenant-admin",
            "P1",
            ["accounts.normal_user", "accounts.admin_user", "accounts.owner_user", "accounts.cross_tenant_user"],
            "Rerun preflight and confirm role_coverage has no missing recommended roles.",
        ) | {"missing_roles": roles})
    sandbox = preflight.get("sandbox_readiness") if isinstance(preflight.get("sandbox_readiness"), dict) else {}
    if not sandbox.get("enabled") or not sandbox.get("auto_fixture_enabled"):
        actions.append(_action(
            "ONBOARD-AUTO-FIXTURE",
            "Enable disposable auto fixture creation",
            "High-value write probes need QualiBug-created qb_auto_* data so customers do not supply business IDs manually.",
            "customer-qa-or-backend-owner",
            "P1",
            ["test_environment.enabled", "test_environment.allow_write_probes", "auto_fixture.enabled"],
            "Rerun preflight and confirm auto_fixture_create_permission passes.",
        ))
    if not sandbox.get("cleanup_strategy_supported"):
        actions.append(_action(
            "ONBOARD-CLEANUP",
            "Declare a supported cleanup/reset strategy",
            "Before/after write probes must leave the staging environment clean and repeatable.",
            "customer-qa-or-platform-owner",
            "P0",
            ["test_environment.cleanup_strategy"],
            "Rerun preflight and confirm cleanup_health_declared passes.",
        ))
    if not (_check(preflight, "snapshot_observer_ready") or {}).get("ok"):
        actions.append(_action(
            "ONBOARD-SNAPSHOT",
            "Expose read-only snapshot observers",
            "P0/P1 runtime validation needs before/after detail, list, ledger, inventory or audit views to prove side effects.",
            "customer-backend-domain-owner",
            "P1",
            ["input OpenAPI read endpoints", "snapshots"],
            "Rerun preflight and confirm snapshot_observer_ready passes; capability rows should move from degraded to ready.",
        ))
    if not (_check(preflight, "config_placeholders_resolved") or {}).get("ok"):
        actions.append(_action(
            "ONBOARD-PLACEHOLDERS",
            "Replace non-executable template placeholders",
            "Generated probe_config templates deliberately contain placeholders and must not be executed unchanged.",
            "customer-qa-owner",
            "P0",
            ["probe_config"],
            "Rerun preflight and confirm config_placeholders_resolved passes.",
        ))

    gap_counts = _capability_gap_counts(matrix)

    # Integrate with CapabilityGapResolver for gap tracking
    try:
        from .capability_gap_resolver import CapabilityGapResolver
        from .gap_tracker import GapTracker

        resolver = CapabilityGapResolver(project_id="")
        detected_gaps = resolver.detect_from_preflight(preflight, matrix)
        gap_report = resolver.build_gap_report(detected_gaps)
    except Exception:
        gap_report = None

    return {
        "engine": "runtime_onboarding_remediation_kit_v1_phase93c",
        "status": "ready" if not actions else ("blocked" if any(a.get("priority") == "P0" for a in actions) else "needs_improvement"),
        "action_count": len(actions),
        "p0_action_count": sum(1 for a in actions if a.get("priority") == "P0"),
        "p1_action_count": sum(1 for a in actions if a.get("priority") == "P1"),
        "capability_gap_counts": gap_counts,
        "actions": actions,
        "recommended_probe_config_patch": _recommended_patch(actions),
        "customer_safe_note": "Do not paste production secrets. Use disposable staging accounts and rotate credentials after onboarding if required by customer policy.",
        "gap_resolution": gap_report,
    }


def _recommended_patch(actions: list[dict[str, Any]]) -> dict[str, Any]:
    ids = {str(a.get("action_id")) for a in actions}
    patch: dict[str, Any] = {}
    if "ONBOARD-BASE-URL" in ids or "ONBOARD-NON-PROD" in ids:
        patch.update({"base_url": "https://<FILL:staging-host>", "environment_kind": "staging"})
    if "ONBOARD-AUTH" in ids or "ONBOARD-ROLES" in ids:
        patch["auth_flow"] = {
            "login_path": "/<FILL:login-path>",
            "method": "POST",
            "username_field": "username",
            "password_field": "password",
            "token_json_path": "data.access_token",
            "token_header_name": "Authorization",
            "token_header_prefix": "Bearer",
        }
        patch["accounts"] = {
            "normal_user": {"role": "normal_user", "username": "<FILL:staging-normal-user>", "password": SENSITIVE_PLACEHOLDER, "tenant_id": "<FILL:tenant-A>"},
            "admin_user": {"role": "admin", "username": "<FILL:staging-admin-user>", "password": SENSITIVE_PLACEHOLDER, "tenant_id": "<FILL:tenant-A>"},
            "owner_user": {"role": "owner", "username": "<FILL:staging-owner-user>", "password": SENSITIVE_PLACEHOLDER, "tenant_id": "<FILL:tenant-A>"},
            "cross_tenant_user": {"role": "other_tenant_user", "username": "<FILL:staging-tenant-b-user>", "password": SENSITIVE_PLACEHOLDER, "tenant_id": "<FILL:tenant-B>"},
        }
        patch["default_account"] = "normal_user"
    if "ONBOARD-AUTO-FIXTURE" in ids or "ONBOARD-CLEANUP" in ids:
        patch["test_environment"] = {
            "enabled": True,
            "allow_write_probes": True,
            "kind": "staging",
            "cleanup_strategy": "fixture_reset",
        }
        patch["auto_fixture"] = {"enabled": True}
    if "ONBOARD-SNAPSHOT" in ids:
        patch["snapshots"] = {
            "*": {
                "before": [{"method": "GET", "path": "/<FILL:resource-detail-or-ledger-observer>", "observer_kind": "primary_resource_detail"}],
                "after": [{"method": "GET", "path": "/<FILL:resource-detail-or-ledger-observer>", "observer_kind": "primary_resource_detail"}],
            }
        }
    return patch


def render_onboarding_remediation_markdown(kit: dict[str, Any]) -> str:
    lines = [
        "# QualiBug Runtime Onboarding Remediation Kit",
        "",
        f"- engine: `{kit.get('engine')}`",
        f"- status: `{kit.get('status')}`",
        f"- actions: `{kit.get('action_count')}`; P0: `{kit.get('p0_action_count')}`; P1: `{kit.get('p1_action_count')}`",
        "",
        "## Required customer actions",
        "",
    ]
    actions = kit.get("actions") or []
    if not actions:
        lines.append("No onboarding remediation actions are required. Runtime environment is ready.")
        lines.append("")
    for action in actions:
        if not isinstance(action, dict):
            continue
        lines.extend([
            f"### {action.get('action_id')} — {action.get('title')}",
            "",
            f"- priority: `{action.get('priority')}`",
            f"- owner: `{action.get('recommended_owner')}`",
            f"- why: {action.get('reason')}",
            f"- config keys: `{', '.join(str(x) for x in (action.get('probe_config_keys') or []))}`",
            f"- validate: {action.get('validation_after_change')}",
            "",
        ])
    lines.extend([
        "## Safe probe_config patch template",
        "",
        "```json",
        _json_dumps(kit.get("recommended_probe_config_patch") or {}),
        "```",
        "",
        f"> {kit.get('customer_safe_note')}",
    ])
    return "\n".join(lines)


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)
