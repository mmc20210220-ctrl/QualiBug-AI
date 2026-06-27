from __future__ import annotations

"""Phase93A: customer environment onboarding preflight for runtime probes.

The preflight layer is deliberately upstream of bug validation.  It does not
create findings and it does not execute destructive requests.  Its job is to
make customer-environment readiness explicit before QualiBug spends time on
runtime probes, especially P0/P1 write-verification loops that need a reachable
non-production target, usable accounts, auto fixtures, cleanup and snapshot
observers.
"""

import re
import urllib.parse
from typing import Any, Callable

from .runtime_connectivity_auth_preflight import build_runtime_connectivity_auth_preflight

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
READ_METHODS = {"GET", "HEAD"}
HIGH_VALUE_RUNTIME_RISKS = {
    "auth_boundary_probe",
    "anonymous_auth_boundary_probe",
    "cross_tenant_auth_boundary_probe",
    "role_downgrade_auth_boundary_probe",
    "ownership_scope_probe",
    "audit_privacy_probe",
    "state_transition_probe",
    "workflow_bypass_probe",
    "approval_flow_probe",
    "conservation_probe",
    "idempotency_replay_probe",
    "async_external_event_probe",
}
REQUIRED_ROLE_ALIASES: dict[str, set[str]] = {
    "normal_user": {"normal_user", "normal", "user", "member", "employee", "operator", "qa_user"},
    "admin_user": {"admin_user", "admin", "administrator", "tenant_admin", "manager"},
    "owner_user": {"owner_user", "owner", "resource_owner", "creator"},
    "cross_tenant_user": {"cross_tenant_user", "other_tenant_user", "tenant_b_user", "external_tenant", "other_org_user"},
}
SANDBOX_CLEANUP_STRATEGIES = {
    "ephemeral_reset",
    "fixture_reset",
    "transaction_rollback",
    "auto_delete",
    "manual_disposable",
    "benchmark_reset",
    "qualibug_auto_fixture_cleanup",
}
PRODUCTION_HOST_RE = re.compile(r"(?:^|[.-])(?:prod|production|live)(?:[.-]|$)|^www\.", re.I)
NON_PROD_HINT_RE = re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0|staging|stage|test|qa|uat|dev|sandbox|mock|local|preprod)", re.I)
PLACEHOLDER_RE = re.compile(r"<\s*(?:FILL|TODO|REQUIRED|SANDBOX|REPLACE)[^>]*>", re.I)

Requester = Callable[[str, str, dict[str, str], Any, float], dict[str, Any]]


def _status(ok: bool, *, failed_is_blocking: bool = False, skipped: bool = False) -> str:
    if skipped:
        return "skipped"
    if ok:
        return "passed"
    return "failed" if failed_is_blocking else "warning"


def _check(name: str, ok: bool, message: str, *, severity: str = "info", skipped: bool = False, **extra: Any) -> dict[str, Any]:
    item = {
        "name": name,
        "ok": bool(ok),
        "status": _status(bool(ok), failed_is_blocking=severity == "blocking", skipped=skipped),
        "severity": severity,
        "message": message,
    }
    item.update({k: v for k, v in extra.items() if v is not None})
    return item


def _host(base_url: str) -> str:
    try:
        return (urllib.parse.urlparse(str(base_url or "")).hostname or "").lower()
    except Exception:
        return ""


def _join_url(base_url: str, path: str) -> str:
    base = str(base_url or "").rstrip("/")
    if not base:
        return str(path or "")
    if re.match(r"^https?://", str(path or ""), re.I):
        return str(path)
    return base + "/" + str(path or "").lstrip("/")


def _contains_placeholder(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_contains_placeholder(k) or _contains_placeholder(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_placeholder(v) for v in value)
    if value is None:
        return False
    return bool(PLACEHOLDER_RE.search(str(value)))


def _is_production_like(config: dict[str, Any], base_url: str) -> tuple[bool, str]:
    env_kind = str(
        config.get("environment_kind")
        or config.get("target_environment")
        or ((config.get("test_environment") or {}).get("kind") if isinstance(config.get("test_environment"), dict) else "")
        or ""
    ).lower()
    if env_kind in {"prod", "production", "live"}:
        return True, "environment_kind_declares_production"
    host = _host(base_url)
    if host and PRODUCTION_HOST_RE.search(host) and not NON_PROD_HINT_RE.search(host):
        return True, "host_looks_like_production"
    return False, "non_production_hint_or_not_declared_production"


def _probe_counts(probes: list[dict[str, Any]]) -> dict[str, int]:
    out = {
        "total": 0,
        "read_only": 0,
        "write": 0,
        "high_value_runtime": 0,
        "strictly_grounded": 0,
    }
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        out["total"] += 1
        ep = probe.get("endpoint") if isinstance(probe.get("endpoint"), dict) else {}
        method = str(ep.get("method") or "GET").upper()
        if method in WRITE_METHODS:
            out["write"] += 1
        elif method in READ_METHODS:
            out["read_only"] += 1
        if str(probe.get("risk_type") or "") in HIGH_VALUE_RUNTIME_RISKS:
            out["high_value_runtime"] += 1
        if _has_strict_grounding(probe):
            out["strictly_grounded"] += 1
    return out


def _has_strict_grounding(probe: dict[str, Any]) -> bool:
    refs = probe.get("source_refs") if isinstance(probe.get("source_refs"), list) else []
    kinds = {str(r.get("kind") or "") for r in refs if isinstance(r, dict)}
    basis = probe.get("grounding_basis") if isinstance(probe.get("grounding_basis"), dict) else {}
    has_endpoint = "endpoint_contract" in kinds or int(basis.get("endpoint_contract_refs") or 0) >= 1
    has_support = bool(kinds - {"endpoint_contract", ""}) or int(basis.get("supporting_requirement_refs") or 0) >= 1
    return bool(has_endpoint and has_support)


def _accounts(config: dict[str, Any]) -> dict[str, Any]:
    raw = config.get("accounts") or config.get("test_accounts") or {}
    return raw if isinstance(raw, dict) else {}


def _role_coverage(config: dict[str, Any]) -> dict[str, Any]:
    accounts = _accounts(config)
    declared: set[str] = set()
    for name, account in accounts.items():
        declared.add(str(name).lower())
        if isinstance(account, dict):
            if account.get("role"):
                declared.add(str(account.get("role")).lower())
            if account.get("tenant_id"):
                declared.add("tenant_scoped_account")
    present: dict[str, bool] = {}
    for role, aliases in REQUIRED_ROLE_ALIASES.items():
        present[role] = bool(declared & aliases)
    tenant_ids = sorted({str(a.get("tenant_id")) for a in accounts.values() if isinstance(a, dict) and a.get("tenant_id")})
    present["two_tenant_coverage"] = len(tenant_ids) >= 2 or present.get("cross_tenant_user", False)
    return {
        "declared_account_count": len(accounts),
        "declared_role_tokens": sorted(declared),
        "tenant_ids": tenant_ids[:10],
        "present": present,
        "missing_recommended_roles": [role for role, ok in present.items() if not ok],
    }


def _auth_readiness(config: dict[str, Any]) -> dict[str, Any]:
    runtime = config.get("_auth_runtime") if isinstance(config.get("_auth_runtime"), dict) else {}
    accounts = _accounts(config)
    headers = config.get("default_headers") if isinstance(config.get("default_headers"), dict) else {}
    token_like = any(str(k).lower() in {"authorization", "cookie", "x-api-key", "x-auth-token", "x-session-id"} for k in headers)
    mode = str(runtime.get("mode") or "")

    if mode == "account_login":
        success = int(runtime.get("successful_session_count") or 0)
        session_health = int(runtime.get("session_health_verified_count") or 0)
        token_count = int(runtime.get("token_acquired_count") or 0)
        cookie_count = int(runtime.get("cookie_acquired_count") or 0)
        refresh_token_count = int(runtime.get("refresh_token_acquired_count") or 0)
        expiring_token_count = int(runtime.get("expiring_token_count") or 0)
        refresh_verified_count = int(runtime.get("token_refresh_verified_count") or 0)
        return {
            "ok": success > 0,
            "configured": True,
            "verified": success > 0,
            "session_health_verified": session_health > 0,
            "mode": "account_login",
            "successful_session_count": success,
            "token_acquired_count": token_count,
            "cookie_acquired_count": cookie_count,
            "session_health_verified_count": session_health,
            "refresh_token_acquired_count": refresh_token_count,
            "expiring_token_count": expiring_token_count,
            "token_refresh_verified_count": refresh_verified_count,
            "interactive_auth_blocker_count": int(runtime.get("interactive_auth_blocker_count") or 0),
            "refresh_ready": expiring_token_count == 0 or refresh_verified_count > 0,
            "message": f"{success} account session(s) derived by login flow" if success else "account login attempted but no usable token/cookie/session was derived",
        }

    if mode == "static_headers":
        session_health = int(runtime.get("session_health_verified_count") or 0)
        verified = session_health > 0
        return {
            "ok": verified,
            "configured": bool(runtime.get("configured") or token_like),
            "verified": verified,
            "session_health_verified": verified,
            "mode": "static_headers" if verified else "headers_configured_unverified",
            "successful_session_count": int(runtime.get("successful_session_count") or 0),
            "session_health_verified_count": session_health,
            "message": "static auth headers verified by session health check" if verified else "default auth headers configured but no login/session health check has verified them",
        }

    if runtime.get("blocked_reason"):
        return {
            "ok": False,
            "configured": bool(accounts or token_like or runtime.get("configured")),
            "verified": False,
            "session_health_verified": False,
            "mode": runtime.get("mode"),
            "successful_session_count": 0,
            "message": str(runtime.get("blocked_reason")),
        }

    if not accounts:
        return {
            "ok": False,
            "configured": token_like,
            "verified": False,
            "session_health_verified": False,
            "mode": "headers_configured_unverified" if token_like else "headers_or_no_accounts",
            "successful_session_count": 0,
            "message": "default auth headers configured but no login/session health check has verified them" if token_like else "no test accounts or auth headers configured",
        }

    return {
        "ok": False,
        "configured": bool(accounts),
        "verified": False,
        "session_health_verified": False,
        "mode": runtime.get("mode") or "unknown",
        "successful_session_count": 0,
        "message": "accounts configured but login flow was not resolved",
    }


def _sandbox_readiness(config: dict[str, Any], allow_write_sandbox: bool) -> dict[str, Any]:
    sandbox = config.get("disposable_sandbox") or config.get("sandbox") or config.get("test_environment") or {}
    if not isinstance(sandbox, dict):
        sandbox = {}
    enabled = bool(sandbox.get("enabled") or sandbox.get("allow_write_probes") or config.get("allow_write_probes") or allow_write_sandbox)
    cleanup = str(sandbox.get("cleanup_strategy") or sandbox.get("reset_strategy") or "qualibug_auto_fixture_cleanup")
    auto_fixture_cfg = config.get("auto_fixture") or config.get("auto_fixtures") or config.get("auto_test_data") or {}
    auto_fixture_enabled = bool(auto_fixture_cfg.get("enabled")) if isinstance(auto_fixture_cfg, dict) and "enabled" in auto_fixture_cfg else bool(config.get("qualibug_auto_create_test_data", True))
    return {
        "ok": bool(enabled and cleanup in SANDBOX_CLEANUP_STRATEGIES and auto_fixture_enabled),
        "enabled": enabled,
        "cleanup_strategy": cleanup,
        "cleanup_strategy_supported": cleanup in SANDBOX_CLEANUP_STRATEGIES,
        "auto_fixture_enabled": auto_fixture_enabled,
        "approval_id_configured": bool(sandbox.get("approval_id") or sandbox.get("id")),
    }


def _snapshot_readiness(config: dict[str, Any], probes: list[dict[str, Any]]) -> dict[str, Any]:
    configured = config.get("snapshots") if isinstance(config.get("snapshots"), dict) else {}
    configured_count = 0
    for value in configured.values():
        if not isinstance(value, dict):
            continue
        for phase in ("before", "after"):
            raw = value.get(phase) or []
            configured_count += len(raw if isinstance(raw, list) else [raw]) if raw else 0
    requires_snapshots = any("snapshot" in " ".join(map(str, probe.get("required_evidence") or [])).lower() for probe in probes if isinstance(probe, dict))
    auto_fixture_cfg = config.get("auto_fixture") or config.get("auto_fixtures") or config.get("auto_test_data") or {}
    auto_fixture_enabled = bool(auto_fixture_cfg.get("enabled")) if isinstance(auto_fixture_cfg, dict) and "enabled" in auto_fixture_cfg else bool(config.get("qualibug_auto_create_test_data", True))
    return {
        "ok": bool(configured_count > 0 or auto_fixture_enabled or not requires_snapshots),
        "configured_snapshot_request_count": configured_count,
        "auto_snapshot_planner_available": auto_fixture_enabled,
        "write_probes_require_snapshots": requires_snapshots,
    }


def _try_reachability(base_url: str, timeout_seconds: float, requester: Requester | None) -> dict[str, Any]:
    if not base_url:
        return {"ok": False, "skipped": True, "message": "base_url not configured"}
    if requester is None:
        return {"ok": False, "skipped": True, "message": "requester not provided"}
    try:
        resp = requester("GET", _join_url(base_url, "/"), {}, None, min(float(timeout_seconds or 10.0), 5.0))
    except Exception as exc:  # pragma: no cover - defensive guard around injected requester
        return {"ok": False, "status_code": None, "error": f"{type(exc).__name__}: {exc}", "message": "base_url reachability check raised an exception"}
    code = resp.get("status_code")
    # 404/401/403 still proves the host and application edge are reachable.
    reachable = isinstance(code, int) and 100 <= int(code) < 500
    return {"ok": reachable, "status_code": code, "error": resp.get("error"), "duration_ms": resp.get("duration_ms"), "message": "base_url network edge is reachable" if reachable else "base_url is not reachable"}


def run_runtime_onboarding_preflight(
    *,
    plan: dict[str, Any],
    config: dict[str, Any],
    base_url: str,
    execute_readonly: bool,
    allow_write_sandbox: bool,
    timeout_seconds: float = 10.0,
    requester: Requester | None = None,
    resolver: Callable[[str, int | None], list[Any]] | None = None,
) -> dict[str, Any]:
    probes = [p for p in (plan.get("probes") or []) if isinstance(p, dict)]
    counts = _probe_counts(probes)
    prod_like, prod_reason = _is_production_like(config, base_url)
    connectivity_auth = build_runtime_connectivity_auth_preflight(
        config=config,
        base_url=base_url,
        execute_readonly=execute_readonly,
        allow_write_sandbox=allow_write_sandbox,
        timeout_seconds=timeout_seconds,
        requester=requester,
        resolver=resolver,
        safety_skip_http=bool(prod_like and base_url),
        safety_skip_reason="HTTP/auth probes skipped because target is production-like" if prod_like and base_url else "",
    )
    auth_config = dict(config or {})
    existing_runtime = auth_config.get("_auth_runtime") if isinstance(auth_config.get("_auth_runtime"), dict) else {}
    discovered_runtime = connectivity_auth.get("auth_runtime") if isinstance(connectivity_auth.get("auth_runtime"), dict) else {}
    if discovered_runtime and (not existing_runtime or not int(existing_runtime.get("successful_session_count") or 0)):
        auth_config["_auth_runtime"] = discovered_runtime
    auth = _auth_readiness(auth_config)
    roles = _role_coverage(config)
    sandbox = _sandbox_readiness(config, allow_write_sandbox)
    snapshots = _snapshot_readiness(config, probes)
    reachability = connectivity_auth.get("http_edge") if isinstance(connectivity_auth.get("http_edge"), dict) else {}
    if not reachability:
        reachability = _try_reachability(base_url, timeout_seconds, requester) if base_url else {"ok": False, "skipped": True, "message": "base_url not configured"}
    placeholder_block = _contains_placeholder(config)
    url_parse = connectivity_auth.get("url_parse") if isinstance(connectivity_auth.get("url_parse"), dict) else {}
    dns_resolution = connectivity_auth.get("dns_resolution") if isinstance(connectivity_auth.get("dns_resolution"), dict) else {}
    auth_runtime = connectivity_auth.get("auth_runtime") if isinstance(connectivity_auth.get("auth_runtime"), dict) else {}
    expiring_token_count = int(auth_runtime.get("expiring_token_count") or auth.get("expiring_token_count") or 0)
    token_refresh_verified_count = int(auth_runtime.get("token_refresh_verified_count") or auth.get("token_refresh_verified_count") or 0)
    interactive_auth_blocker_count = int(auth_runtime.get("interactive_auth_blocker_count") or auth.get("interactive_auth_blocker_count") or 0)

    checks = [
        _check("base_url_configured", bool(base_url), "target base URL is configured" if base_url else "no target base URL; runtime execution will be plan-only", severity="blocking" if execute_readonly or allow_write_sandbox else "warning"),
        _check("url_parse_ok", bool(url_parse.get("ok")), url_parse.get("message") or "URL parse unknown", severity="blocking" if execute_readonly or allow_write_sandbox else "warning", url_parse=url_parse),
        _check("url_host_resolves", bool(dns_resolution.get("ok")), dns_resolution.get("message") or "host resolution unknown", severity="blocking" if execute_readonly or allow_write_sandbox else "warning", skipped=bool(dns_resolution.get("skipped")), dns_resolution=dns_resolution),
        _check("base_url_reachable", bool(reachability.get("ok")), reachability.get("message") or "reachability unknown", severity="blocking" if (execute_readonly or allow_write_sandbox) else "warning", skipped=bool(reachability.get("skipped")), status_code=reachability.get("status_code"), error=reachability.get("error"), duration_ms=reachability.get("duration_ms")),
        _check("non_production_target", not prod_like, prod_reason, severity="blocking"),
        _check("probe_plan_grounded", counts["total"] > 0 and counts["strictly_grounded"] == counts["total"], f"{counts['strictly_grounded']}/{counts['total']} probes have strict document grounding", severity="blocking"),
        _check("auth_session_ready", bool(auth.get("ok")), auth.get("message") or "auth readiness unknown", severity="warning", mode=auth.get("mode"), successful_session_count=auth.get("successful_session_count"), token_acquired_count=auth.get("token_acquired_count"), cookie_acquired_count=auth.get("cookie_acquired_count"), session_health_verified_count=auth.get("session_health_verified_count"), interactive_auth_blocker_count=interactive_auth_blocker_count),
        _check("interactive_auth_not_blocked", interactive_auth_blocker_count == 0, "no browser-only SSO/MFA/CAPTCHA/proxy/mTLS auth blocker was detected" if interactive_auth_blocker_count == 0 else "interactive auth blocker detected; provide a non-interactive auth path before runtime probes", severity="warning", skipped=interactive_auth_blocker_count == 0, interactive_auth_blocker_count=interactive_auth_blocker_count),
        _check("token_cookie_or_session_acquired", int(auth_runtime.get("successful_session_count") or auth.get("successful_session_count") or 0) > 0, "token/cookie/session material was acquired" if int(auth_runtime.get("successful_session_count") or auth.get("successful_session_count") or 0) > 0 else "no token/cookie/session material acquired yet", severity="warning", mode=auth_runtime.get("mode") or auth.get("mode"), successful_session_count=auth_runtime.get("successful_session_count") or auth.get("successful_session_count")),
        _check("session_health_verified", int(auth_runtime.get("session_health_verified_count") or auth.get("session_health_verified_count") or 0) > 0, "authenticated session was verified by health/me endpoint" if int(auth_runtime.get("session_health_verified_count") or auth.get("session_health_verified_count") or 0) > 0 else "session health endpoint was not verified", severity="warning", session_health_verified_count=auth_runtime.get("session_health_verified_count") or auth.get("session_health_verified_count")),
        _check(
            "auth_session_refresh_ready",
            expiring_token_count == 0 or token_refresh_verified_count > 0,
            "expiring/TTL auth sessions can refresh and re-verify" if expiring_token_count and token_refresh_verified_count else ("auth response did not expose token expiry; refresh readiness skipped" if not expiring_token_count else "auth session expiry observed but refresh was not verified"),
            severity="warning",
            skipped=expiring_token_count == 0,
            expiring_token_count=expiring_token_count,
            refresh_token_acquired_count=auth_runtime.get("refresh_token_acquired_count") or auth.get("refresh_token_acquired_count"),
            token_refresh_verified_count=token_refresh_verified_count,
        ),
        _check("role_coverage", not roles.get("missing_recommended_roles"), "recommended tenant/admin/owner/normal role coverage is present" if not roles.get("missing_recommended_roles") else "recommended role coverage is incomplete; some boundary probes may be degraded", severity="warning", role_coverage=roles),
        _check("auto_fixture_create_permission", bool(sandbox.get("enabled") and sandbox.get("auto_fixture_enabled")), "auto fixture creation is enabled for disposable test data" if sandbox.get("enabled") and sandbox.get("auto_fixture_enabled") else "auto fixture creation is not fully enabled", severity="warning", sandbox=sandbox),
        _check("cleanup_health_declared", bool(sandbox.get("cleanup_strategy_supported")), f"cleanup strategy `{sandbox.get('cleanup_strategy')}` is supported" if sandbox.get("cleanup_strategy_supported") else "cleanup strategy is missing or unsupported", severity="blocking" if allow_write_sandbox else "warning", cleanup_strategy=sandbox.get("cleanup_strategy")),
        _check("snapshot_observer_ready", bool(snapshots.get("ok")), "snapshot observer planning/config is available" if snapshots.get("ok") else "snapshot observer coverage is missing", severity="warning", snapshot_readiness=snapshots),
        _check("config_placeholders_resolved", not placeholder_block, "probe config has no executable placeholders" if not placeholder_block else "probe config still contains <FILL:...> placeholders", severity="blocking" if (execute_readonly or allow_write_sandbox) else "warning"),
    ]

    blocking = [c for c in checks if c.get("severity") == "blocking" and not c.get("ok") and not c.get("skipped")]
    warnings = [c for c in checks if c.get("severity") != "blocking" and not c.get("ok")]
    high_value_runtime_requested = counts["high_value_runtime"] > 0 and (execute_readonly or allow_write_sandbox)
    ready_for_p0_p1 = bool(
        high_value_runtime_requested
        and not blocking
        and bool(base_url)
        and not prod_like
        and counts["strictly_grounded"] == counts["total"]
        and (bool(auth.get("ok")) or counts["read_only"] == 0)
        and (sandbox.get("ok") if counts["write"] else True)
        and snapshots.get("ok")
    )
    if not base_url and not execute_readonly and not allow_write_sandbox:
        overall = "plan_only"
    elif blocking:
        overall = "blocked"
    elif warnings:
        overall = "degraded"
    else:
        overall = "ready"

    return {
        "engine": "runtime_onboarding_preflight_v1_phase93a",
        "status": overall,
        "ready_for_runtime": overall in {"ready", "degraded"} and bool(base_url) and not prod_like,
        "ready_for_p0_p1_runtime_validation": ready_for_p0_p1,
        "high_value_runtime_requested": high_value_runtime_requested,
        "probe_counts": counts,
        "checks": checks,
        "blocking_reasons": [c.get("name") for c in blocking],
        "warning_reasons": [c.get("name") for c in warnings],
        "auth_readiness": auth,
        "connectivity_auth_preflight": connectivity_auth,
        "role_coverage": roles,
        "sandbox_readiness": sandbox,
        "snapshot_readiness": snapshots,
        "recommended_next_step": _recommended_next_step(overall, ready_for_p0_p1, blocking, warnings),
    }


def _recommended_next_step(overall: str, ready_for_p0_p1: bool, blocking: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> str:
    if ready_for_p0_p1:
        return "Environment is ready for high-value P0/P1 runtime validation; proceed with approved sandbox probes and before/after evidence capture."
    if overall == "plan_only":
        return "No runtime target is configured; generate probe plans and probe_config templates, then rerun preflight with staging URL and accounts."
    if blocking:
        names = ", ".join(str(c.get("name")) for c in blocking[:4])
        return f"Resolve blocking onboarding checks before running high-value probes: {names}."
    if warnings:
        names = ", ".join(str(c.get("name")) for c in warnings[:4])
        return f"Runtime can proceed in degraded mode, but improve evidence quality by fixing: {names}."
    return "Runtime can proceed, but P0/P1 readiness was not requested or no high-value probes were present."
