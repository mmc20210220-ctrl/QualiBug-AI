from __future__ import annotations

"""Execution bridge for document-grounded probe plans.

Phase92K goal
-------------
``input_grounded_candidate_compiler`` turns only ``projects/<project>/input``
materials into ``grounded_probe_plan.json``.  This module converts those
strictly document-grounded obligations into safe, reviewable and, when
explicitly allowed, executable HTTP probes.

This module deliberately does **not** read benchmark oracle, ground_truth,
BUG_MATRIX, seed or answer files.  Runtime findings are emitted only from
observed HTTP evidence collected from probes derived from customer input docs.

Safety model
------------
* dry-run planning is always allowed;
* read-only execution requires ``execute_readonly=True`` and a base URL;
* only GET/HEAD probes marked ``read_only_safe`` can run automatically;
* write probes require **all** of the following:
  - strict document grounding source refs;
  - method is POST/PUT/PATCH/DELETE and policy is ``disposable_sandbox_required``;
  - CLI flag ``allow_write_sandbox=True``;
  - non-empty approval id;
  - environment flag ``QUALIBUG_ALLOW_GROUNDED_WRITE_PROBES=1``;
  - probe config contains an enabled ``disposable_sandbox`` block matching the
    approval id and declaring a cleanup/reset strategy;
  - probe config provides a concrete request body for that candidate or endpoint.

Commercial UX note
------------------
Customers should normally provide test-environment **accounts** rather than raw
Bearer tokens.  If ``probe_config`` contains ``auth_flow`` plus ``accounts``, the
executor logs in to the sandbox target, derives Authorization/Cookie headers in
memory, redacts them from reports, and keeps the old token/header config only as
an advanced compatibility path.

QualiBug creates disposable test data from customer input documents/OpenAPI.
Customers provide only test/staging URL and accounts by default; manual request
bodies remain an advanced override.  Runtime reports keep secrets redacted.
"""

import hashlib
import json
import logging
import os
import re
import time
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

from ..real_id_resolver import infer_path_params, normalize_path_placeholders
from ..enterprise_project_config import match_production_data_exclusion
from ..runtime_finding_evidence_packager import package_runtime_finding_evidence
from ..runtime_finding_customer_triage import triage_runtime_finding
from ..runtime_customer_report_builder import build_customer_delivery_index
from ..runtime_reproduction_asset_linker import link_reproduction_assets
from ..runtime_fix_verification_loop import attach_fix_verification_loop
from ..runtime_finding_lifecycle_registry import apply_lifecycle_registry
from ..bug_discovery_probe_expander import expand_bug_discovery_probes
from ..runtime_onboarding_preflight import run_runtime_onboarding_preflight
from ..runtime_probe_capability_matrix import (
    annotate_decisions_with_capability,
    build_runtime_probe_capability_matrix,
)
from ..runtime_onboarding_remediation_kit import (
    build_onboarding_remediation_kit,
    render_onboarding_remediation_markdown,
)
from ..runtime_execution_runbook import (
    build_runtime_execution_runbook,
    render_runtime_execution_runbook_markdown,
)
from ..runtime_evidence_readiness_sla_gate import (
    build_runtime_evidence_readiness_sla_gate,
    render_runtime_evidence_readiness_markdown,
)
from ..runtime_onboarding_patch_safety_validator import (
    render_onboarding_patch_safety_markdown,
    validate_onboarding_patch_safety,
)
from ..runtime_remediation_artifact_builder import (
    build_remediation_verification_artifact,
    render_remediation_markdown,
)
from ..runtime_sla_execution_policy import (
    build_runtime_sla_execution_policy,
    render_runtime_sla_execution_policy_markdown,
)
from ..runtime_sla_gap_prioritizer import (
    build_runtime_sla_gap_prioritizer,
    render_runtime_sla_gap_prioritizer_markdown,
)
from ..runtime_write_sandbox_approval_packet import (
    build_write_sandbox_approval_packet,
    render_write_sandbox_approval_markdown,
)
from ..runtime_commercial_handoff_bundle import (
    build_commercial_handoff_bundle,
    render_commercial_handoff_markdown,
)
from ..runtime_commercial_handoff_acceptance_gate import (
    render_commercial_handoff_acceptance_markdown,
    validate_commercial_handoff_acceptance,
)
from ..runtime_handoff_secret_audit import (
    audit_commercial_handoff_secrets,
    build_handoff_secret_redaction_plan,
    build_handoff_redacted_runtime_evidence_pack,
    render_handoff_redacted_runtime_evidence_markdown,
    render_handoff_secret_audit_markdown,
    render_handoff_secret_redaction_plan_markdown,
)
from ..runtime_handoff_archive_manifest import (
    build_handoff_archive_manifest,
    render_handoff_archive_manifest_markdown,
    render_immutable_run_receipt_markdown,
)
from ..runtime_handoff_receipt_comparator import (
    compare_immutable_run_receipts,
    render_handoff_receipt_comparison_markdown,
)
from ..runtime_handoff_rerun_audit_gate import (
    build_handoff_rerun_audit_gate,
    render_handoff_rerun_audit_gate_markdown,
)
from ..runtime_commercial_evidence_lineage_dashboard import (
    build_commercial_evidence_lineage_dashboard,
    render_commercial_evidence_lineage_dashboard_markdown,
)
from ..runtime_commercial_lineage_reviewer_signoff import (
    build_commercial_lineage_reviewer_signoff_packet,
    render_commercial_lineage_reviewer_signoff_markdown,
)
from ..runtime_commercial_closure_acceptance_ledger import (
    build_commercial_closure_acceptance_ledger,
    render_commercial_closure_acceptance_ledger_markdown,
)
from ..runtime_commercial_audit_event_stream import (
    build_commercial_audit_event_stream,
    render_commercial_audit_event_stream_markdown,
)
from ..runtime_commercial_audit_export_adapters import (
    build_commercial_audit_export_adapters,
    render_commercial_audit_exports_markdown,
    render_csv_audit_ledger,
)
from ..runtime_commercial_audit_export_import_gate import (
    build_commercial_audit_export_import_gate,
    render_commercial_audit_import_gate_markdown,
)
from ..runtime_commercial_external_tracker_reconciliation import (
    build_commercial_external_tracker_reconciliation,
    render_commercial_external_tracker_reconciliation_markdown,
)
from ..runtime_external_tracker_closure_sync_policy import (
    build_external_tracker_closure_sync_policy,
    render_external_tracker_closure_sync_policy_markdown,
)
from ..runtime_external_tracker_sync_payload_builder import (
    build_external_tracker_sync_payloads,
    render_external_tracker_sync_payloads_markdown,
)
from ..runtime_external_tracker_sync_payload_gate import (
    render_external_tracker_sync_payload_gate_markdown,
    validate_external_tracker_sync_payloads,
)
from ..runtime_external_tracker_sync_receipt_ledger import (
    build_external_tracker_sync_receipt_ledger,
    render_external_tracker_sync_receipt_ledger_markdown,
)
# Canonical HTTP + basic utilities extracted to probe_http.py
from ..probe_http import *  # noqa: F401,F403
# Canonical report rendering extracted to probe_reporting.py
from ..probe_reporting import *  # noqa: F401,F403
# Canonical auth + fixture utilities extracted to probe_auth.py
from ..probe_auth import *  # noqa: F401,F403

UNRESOLVED_PLACEHOLDER_RE = re.compile(r"<\s*(?:FILL|TODO|REQUIRED|SANDBOX|REPLACE)[^>]*>", re.I)

SENSITIVE_FIELD_RE = re.compile(
    r"(?:password|passwd|secret|token|authorization|cookie|api[_-]?key|session|id[_-]?card|身份证|phone|mobile|手机号|email|邮箱)",
    re.I,
)
BUSINESS_FIELD_RE = re.compile(
    r"(?:id|tenant|org|owner|user|order|订单|amount|price|payment|inventory|库存|sku|status|流水|audit|payload|data|items|records|email|phone)",
    re.I,
)
NEGATIVE_NUMBER_KEY_RE = re.compile(r"(?:amount|price|balance|inventory|quantity|qty|stock|quota|points|额度|库存|数量|金额|余额)", re.I)
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
READ_METHODS = {"GET", "HEAD"}
EXPECTED_AUTH_FAILURES = {401, 403, 404}
DEFAULT_NEGATIVE_WRITE_FAILURES = {400, 401, 403, 404, 409, 422}
AUTH_BOUNDARY_RISKS = {"auth_boundary_probe", "anonymous_auth_boundary_probe", "cross_tenant_auth_boundary_probe", "role_downgrade_auth_boundary_probe"}
FIXTURE_BACKED_READ_RISKS = AUTH_BOUNDARY_RISKS | {"ownership_scope_probe"}
AUTH_HEADER_NAMES = ["Authorization", "Cookie", "X-Tenant-Id", "X-Org-Id", "X-Workspace-Id"]
SANDBOX_CLEANUP_STRATEGIES = {"ephemeral_reset", "fixture_reset", "transaction_rollback", "auto_delete", "manual_disposable", "benchmark_reset", "qualibug_auto_fixture_cleanup"}
PRODUCTION_HOST_RE = re.compile(r"(?:^|[.-])(?:prod|production|live)(?:[.-]|$)|^www\.", re.I)
NON_PROD_HINT_RE = re.compile(r"(?:localhost|127\.0\.0\.1|0\.0\.0\.0|staging|stage|test|qa|uat|dev|sandbox|mock|local|preprod)", re.I)



@dataclass
class ProbeDecision:
    candidate_id: str
    risk_type: str
    method: str
    path: str
    execution_policy: str
    decision: str
    reason: str
    request: dict[str, Any]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_json(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8") or "{}")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _redact(value: Any, key: str = "") -> Any:
    if SENSITIVE_FIELD_RE.search(str(key)):
        return "<REDACTED>"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, key) for v in value[:25]]
    if isinstance(value, str):
        if len(value) > 700:
            return value[:700] + "…"
        if SENSITIVE_FIELD_RE.search(value) and len(value) > 24:
            return value[:8] + "…<REDACTED>"
        return value
    return value




def _has_unresolved_placeholder(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        return any(_has_unresolved_placeholder(k) or _has_unresolved_placeholder(v) for k, v in value.items())
    if isinstance(value, list):
        return any(_has_unresolved_placeholder(v) for v in value)
    return bool(UNRESOLVED_PLACEHOLDER_RE.search(str(value)))

def _headers_from_config(config: dict[str, Any]) -> dict[str, str]:
    headers = dict(config.get("default_headers") or {})
    token = str(config.get("bearer_token") or os.environ.get("QUALIBUG_BEARER_TOKEN") or "")
    tenant = str(config.get("tenant_id") or os.environ.get("QUALIBUG_TENANT_ID") or "")
    if token and "Authorization" not in headers:
        headers["Authorization"] = f"Bearer {token}"
    if tenant and "X-Tenant-Id" not in headers:
        headers["X-Tenant-Id"] = tenant
    return {str(k): str(v) for k, v in headers.items()}


def _fixture_control_headers(config: dict[str, Any]) -> dict[str, str]:
    """Return privileged/test-environment headers for fixture setup and cleanup.

    Negative probes (anonymous/cross-tenant/role-downgrade) intentionally mutate
    the target request headers.  Disposable fixture setup/cleanup is different:
    it must run as the configured sandbox/control actor, otherwise an anonymous
    auth-boundary probe can fail before it ever reaches the boundary being
    tested.
    """
    headers = _headers_from_config(config)
    auto_cfg = config.get("auto_fixture") or config.get("auto_fixtures") or config.get("auto_test_data") or {}
    if not isinstance(auto_cfg, dict):
        auto_cfg = {}
    profile = str(auto_cfg.get("credential_profile") or config.get("fixture_credential_profile") or "").strip()
    resolved = config.get("_resolved_account_headers") if isinstance(config.get("_resolved_account_headers"), dict) else {}
    if profile and isinstance(resolved.get(profile), dict) and resolved.get(profile):
        headers = {str(k): str(v) for k, v in (resolved.get(profile) or {}).items()}
    fixture_headers = config.get("fixture_headers") or config.get("auto_fixture_headers") or auto_cfg.get("headers") or {}
    if isinstance(fixture_headers, dict):
        headers.update({str(k): str(v) for k, v in fixture_headers.items()})
    return headers


def _negative_headers(headers: dict[str, str], names: list[str]) -> dict[str, str]:
    blocked = {str(n).lower() for n in names}
    return {k: v for k, v in headers.items() if k.lower() not in blocked}


def _auth_boundary_plan(probe: dict[str, Any]) -> dict[str, Any]:
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    auth = plan.get("auth_boundary") if isinstance(plan.get("auth_boundary"), dict) else {}
    return auth if isinstance(auth, dict) else {}


def _is_auth_boundary_risk(probe: dict[str, Any] | None = None, risk_type: str = "") -> bool:
    risk = str(risk_type or ((probe or {}).get("risk_type") if isinstance(probe, dict) else "") or "")
    return risk in AUTH_BOUNDARY_RISKS or bool(_auth_boundary_plan(probe or {}))


def _fixture_backed_read_probe(probe: dict[str, Any], method: str = "", path: str = "") -> bool:
    """Return true when a read probe needs a disposable resource to be meaningful."""
    ep = probe.get("endpoint") if isinstance(probe.get("endpoint"), dict) else {}
    m = str(method or ep.get("method") or "").upper()
    p = str(path or ep.get("path") or "")
    if m not in READ_METHODS or not re.search(r"\{[^{}]+\}", p):
        return False
    risk = str(probe.get("risk_type") or "")
    return risk in FIXTURE_BACKED_READ_RISKS or _is_auth_boundary_risk(probe, risk)


def _read_fixture_setup_approval(config: dict[str, Any], base_url: str, options: dict[str, Any]) -> tuple[bool, str]:
    allow_write = bool(
        options.get("allow_write_sandbox")
        or config.get("allow_write_probes")
        or ((config.get("test_environment") or {}).get("allow_write_probes") if isinstance(config.get("test_environment"), dict) else False)
    )
    if not allow_write:
        return False, "fixture_backed_read_probe_requires_test_environment_write_execution_enabled"
    ok, reason, _sandbox = _approval_enabled(config, base_url, str(options.get("approval_id") or ""))
    return ok, reason


def _join_url(base_url: str, path: str) -> str:
    def quote_url_path(value: str) -> str:
        parsed = urllib.parse.urlsplit(value)
        quoted_path = urllib.parse.quote(parsed.path, safe="/%")
        quoted_query = urllib.parse.quote(parsed.query, safe="=&%:/?,+")
        return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, quoted_path, quoted_query, parsed.fragment))

    base = str(base_url or "").rstrip("/")
    if not base:
        return quote_url_path(str(path))
    p = str(path or "")
    if re.match(r"^https?://", p, re.I):
        return quote_url_path(p)
    return base + "/" + quote_url_path(p.lstrip("/"))


def _url_host(base_url: str) -> str:
    try:
        parsed = urllib.parse.urlparse(base_url)
        return (parsed.hostname or "").lower()
    except Exception:
        return ""


def _render_path(path: str, path_params: dict[str, Any]) -> tuple[str, list[str]]:
    missing: list[str] = []
    rendered = str(path or "")
    for name in re.findall(r"\{([^{}]+)\}", rendered):
        if name not in path_params:
            missing.append(name)
            continue
        rendered = rendered.replace("{" + name + "}", urllib.parse.quote(str(path_params[name]), safe=""))
    return rendered, missing


def _render_query(query: Any, path_params: dict[str, Any]) -> str:
    if not isinstance(query, dict) or not query:
        return ""
    rendered: dict[str, str] = {}
    for k, v in query.items():
        key = str(k)
        value = str(v)
        for name, replacement in path_params.items():
            value = value.replace("{" + str(name) + "}", str(replacement))
        if value and not _has_unresolved_placeholder(value):
            rendered[key] = value
    return urllib.parse.urlencode(rendered, doseq=True)


def _append_query(path: str, query_string: str) -> str:
    if not query_string:
        return path
    sep = "&" if "?" in str(path) else "?"
    return f"{path}{sep}{query_string}"


def _configured_query_params(config: dict[str, Any], probe: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    """Return query params for the runtime target request.

    Generated probes and OpenAPI-derived endpoints can carry query parameters
    such as ``tenant_id``, ``include=audit`` or ``line={line_id}``.  Before this
    helper, main probe execution only rendered path params, while fixture
    snapshots/cleanup already handled query binding.  That made the target probe
    hit a broader or wrong resource slice than the observer evidence.
    """
    query: dict[str, Any] = {}
    ep = probe.get("endpoint") if isinstance(probe.get("endpoint"), dict) else {}
    if isinstance(ep.get("query"), dict):
        query.update(ep.get("query") or {})
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    if isinstance(plan.get("query"), dict):
        query.update(plan.get("query") or {})
    cid = str(probe.get("candidate_id") or "")
    per = config.get("query_params") or config.get("queries") or {}
    if isinstance(per, dict):
        value = _get_mapping_value(per, cid, method, path)
        if isinstance(value, dict):
            query.update(value)
    return query


def _safe_payload_summary(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        return {"type": "object", "keys": sorted(map(str, payload.keys()))[:30], "size": len(payload)}
    if isinstance(payload, list):
        return {"type": "array", "size": len(payload), "first": _safe_payload_summary(payload[0]) if payload else None}
    if isinstance(payload, str):
        return {"type": "string", "length": len(payload), "sample": payload[:200]}
    return {"type": type(payload).__name__, "value": payload if isinstance(payload, (int, float, bool)) else str(payload)[:200]}


def _jsonish_body(raw: bytes, content_type: str) -> Any:
    text = raw.decode("utf-8", errors="replace")
    if "json" in content_type.lower() or text.strip().startswith(("{", "[")):
        try:
            return json.loads(text or "null")
        except Exception:
            return text[:2000]
    return text[:2000]


def _http_request(method: str, url: str, headers: dict[str, str], body: Any = None, timeout: float = 10.0) -> dict[str, Any]:
    data: bytes | None = None
    req_headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url=url, method=method.upper(), headers=req_headers, data=data)
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - guarded by caller safety gates
            raw = resp.read(256_000)
            payload = _jsonish_body(raw, resp.headers.get("Content-Type", ""))
            return {"ok": True, "status_code": int(resp.status), "headers": dict(resp.headers.items()), "payload": payload, "duration_ms": int((time.time() - started) * 1000)}
    except urllib.error.HTTPError as exc:
        raw = exc.read(256_000)
        payload = _jsonish_body(raw, exc.headers.get("Content-Type", "")) if raw else None
        return {"ok": False, "status_code": int(exc.code), "headers": dict(exc.headers.items()), "payload": payload, "duration_ms": int((time.time() - started) * 1000)}
    except Exception as exc:
        return {"ok": False, "status_code": None, "error": f"{type(exc).__name__}: {exc}", "payload": None, "duration_ms": int((time.time() - started) * 1000)}




def _json_path_get(payload: Any, dotted_path: str) -> Any:
    """Return a value using a tiny dot/index path syntax like data.token or data[0].token."""
    cur = payload
    for raw_part in [p for p in str(dotted_path or "").split(".") if p]:
        part = raw_part
        while part:
            m = re.match(r"^([^\[]+)(?:\[(\d+)\])?(.*)$", part)
            if not m:
                return None
            key, idx, rest = m.group(1), m.group(2), m.group(3)
            if key:
                if not isinstance(cur, dict) or key not in cur:
                    return None
                cur = cur[key]
            if idx is not None:
                if not isinstance(cur, list):
                    return None
                i = int(idx)
                if i >= len(cur):
                    return None
                cur = cur[i]
            part = rest
    return cur


def _cookie_header_from_response(resp: dict[str, Any]) -> str:
    headers = resp.get("headers") if isinstance(resp.get("headers"), dict) else {}
    cookies: list[str] = []
    for k, v in headers.items():
        if str(k).lower() == "set-cookie" and v:
            # Keep only name=value, drop attributes. Multiple cookies may be comma-joined by urllib.
            raw = str(v)
            for chunk in re.split(r",\s*(?=[A-Za-z0-9_\-]+=)", raw):
                first = chunk.split(";", 1)[0].strip()
                if first:
                    cookies.append(first)
    return "; ".join(dict.fromkeys(cookies))


def _login_account(base_url: str, auth_flow: dict[str, Any], account: dict[str, Any], timeout: float) -> tuple[dict[str, str], dict[str, Any]]:
    login_path = str(auth_flow.get("login_path") or auth_flow.get("path") or "/login")
    method = str(auth_flow.get("method") or "POST").upper()
    username_field = str(auth_flow.get("username_field") or "username")
    password_field = str(auth_flow.get("password_field") or "password")
    tenant_field = str(auth_flow.get("tenant_field") or "")
    token_json_path = str(auth_flow.get("token_json_path") or auth_flow.get("token_path") or "token")
    token_header = str(auth_flow.get("token_header_name") or "Authorization")
    token_prefix = str(auth_flow.get("token_header_prefix") or "Bearer")
    extra_body = dict(auth_flow.get("extra_body") or {}) if isinstance(auth_flow.get("extra_body"), dict) else {}
    body = dict(extra_body)
    body[username_field] = account.get("username") or account.get("user") or account.get("login") or ""
    body[password_field] = account.get("password") or ""
    if tenant_field and account.get("tenant_id"):
        body[tenant_field] = account.get("tenant_id")
    headers = dict(auth_flow.get("headers") or {}) if isinstance(auth_flow.get("headers"), dict) else {}
    resp = _http_request(method, _join_url(base_url, login_path), headers, body=body, timeout=timeout)
    payload = resp.get("payload")
    derived_headers: dict[str, str] = {}
    token = _json_path_get(payload, token_json_path) if token_json_path else None
    if token:
        if token_header.lower() == "authorization" and token_prefix:
            derived_headers[token_header] = f"{token_prefix} {token}"
        else:
            derived_headers[token_header] = str(token)
    cookie = _cookie_header_from_response(resp)
    if cookie:
        derived_headers["Cookie"] = cookie
    if account.get("tenant_id"):
        tenant_header = str(auth_flow.get("tenant_header_name") or "X-Tenant-Id")
        derived_headers.setdefault(tenant_header, str(account.get("tenant_id")))
    meta = {
        "status_code": resp.get("status_code"),
        "ok": bool(resp.get("status_code") and 200 <= int(resp.get("status_code")) < 300),
        "token_found": bool(token),
        "cookie_found": bool(cookie),
        "headers_derived": sorted(derived_headers.keys()),
        "duration_ms": resp.get("duration_ms"),
    }
    return derived_headers, meta


def _materialize_account_auth(config: dict[str, Any], base_url: str, timeout: float) -> dict[str, Any]:
    """Derive runtime headers from username/password accounts.

    This keeps commercial setup simple: customers provide staging accounts; the
    engine obtains tokens/cookies at runtime and redacts them from all reports.
    The returned config is a shallow copy with `_resolved_account_headers` and a
    default header set for compatibility with existing probe execution.
    """
    cfg = dict(config or {})
    accounts = cfg.get("accounts") or cfg.get("test_accounts") or {}
    auth_flow = cfg.get("auth_flow") or cfg.get("login") or {}
    if not base_url or not isinstance(accounts, dict) or not accounts or not isinstance(auth_flow, dict) or not auth_flow:
        cfg.setdefault("_auth_runtime", {"mode": "headers_or_no_auth", "login_attempted": False})
        return cfg
    if _has_unresolved_placeholder({"auth_flow": auth_flow, "accounts": accounts}):
        cfg.setdefault("_auth_runtime", {"mode": "account_login", "login_attempted": False, "blocked_reason": "auth_config_contains_unresolved_placeholders"})
        return cfg
    resolved: dict[str, dict[str, str]] = {}
    events: list[dict[str, Any]] = []
    for name, account in accounts.items():
        if not isinstance(account, dict):
            continue
        if account.get("anonymous") is True:
            resolved[str(name)] = {}
            events.append({"account": str(name), "role": account.get("role") or str(name), "anonymous": True, "login_attempted": False})
            continue
        if not account.get("username") or not account.get("password"):
            events.append({"account": str(name), "role": account.get("role") or str(name), "login_attempted": False, "error": "missing_username_or_password"})
            continue
        headers, meta = _login_account(base_url, auth_flow, account, timeout)
        resolved[str(name)] = headers
        events.append({"account": str(name), "role": account.get("role") or str(name), "login_attempted": True, **meta})
    cfg["_resolved_account_headers"] = resolved
    default_account = str(cfg.get("default_account") or cfg.get("default_role") or "")
    if not default_account or default_account not in resolved:
        for name, account in accounts.items():
            if isinstance(account, dict) and not account.get("anonymous") and name in resolved and resolved.get(str(name)):
                default_account = str(name)
                break
    if default_account and resolved.get(default_account):
        merged = dict(cfg.get("default_headers") or {})
        merged.update(resolved[default_account])
        cfg["default_headers"] = merged
    cfg["_auth_runtime"] = {
        "mode": "account_login",
        "login_attempted": True,
        "default_account": default_account,
        "account_count": len([a for a in accounts.values() if isinstance(a, dict)]),
        "successful_session_count": sum(1 for h in resolved.values() if h),
        "events": events,
    }
    return cfg

def _has_business_data(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, dict):
        if not value:
            return False
        keys = " ".join(str(k) for k in value.keys())
        if BUSINESS_FIELD_RE.search(keys):
            non_empty_values = [v for v in value.values() if v not in (None, "", [], {})]
            return bool(non_empty_values)
        return any(_has_business_data(v) for v in value.values())
    if isinstance(value, list):
        return bool(value) and any(_has_business_data(v) for v in value[:10])
    if isinstance(value, str):
        return len(value.strip()) > 2 and bool(BUSINESS_FIELD_RE.search(value))
    return isinstance(value, (int, float, bool))


def _find_sensitive_keys(value: Any, prefix: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(value, dict):
        for k, v in value.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if SENSITIVE_FIELD_RE.search(str(k)):
                hits.append(path)
            hits.extend(_find_sensitive_keys(v, path))
    elif isinstance(value, list):
        for idx, item in enumerate(value[:5]):
            hits.extend(_find_sensitive_keys(item, f"{prefix}[{idx}]" if prefix else f"[{idx}]"))
    return sorted(dict.fromkeys(hits))[:30]


def _load_config(config_path: str | Path | None) -> dict[str, Any]:
    if not config_path:
        return {}
    p = Path(config_path)
    if not p.exists():
        raise FileNotFoundError(f"probe config not found: {p}")
    return _read_json(p)




def _load_previous_execution_report(config: dict[str, Any]) -> dict[str, Any] | None:
    candidates: list[Any] = []
    fix_cfg = config.get("fix_verification") if isinstance(config.get("fix_verification"), dict) else {}
    candidates.extend([
        config.get("previous_execution_report"),
        config.get("previous_grounded_probe_report"),
        fix_cfg.get("previous_report"),
        fix_cfg.get("previous_execution_report"),
    ])
    for value in candidates:
        if not value:
            continue
        try:
            path = Path(str(value)).expanduser()
            if path.exists() and path.is_file():
                return _read_json(path)
        except Exception:
            continue
    return None


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


