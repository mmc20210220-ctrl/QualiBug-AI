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

import json
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

from .runtime_finding_evidence_packager import package_runtime_finding_evidence
from .runtime_finding_customer_triage import triage_runtime_finding
from .runtime_customer_report_builder import build_customer_delivery_index
from .runtime_reproduction_asset_linker import link_reproduction_assets
from .runtime_fix_verification_loop import attach_fix_verification_loop
from .runtime_finding_lifecycle_registry import apply_lifecycle_registry
from .runtime_remediation_artifact_builder import build_remediation_verification_artifact, render_remediation_markdown
from .runtime_onboarding_preflight import run_runtime_onboarding_preflight
from .runtime_probe_capability_matrix import build_runtime_probe_capability_matrix, annotate_decisions_with_capability
from .runtime_onboarding_remediation_kit import build_onboarding_remediation_kit, render_onboarding_remediation_markdown
from .runtime_execution_runbook import build_runtime_execution_runbook, render_runtime_execution_runbook_markdown
from .runtime_evidence_readiness_sla_gate import build_runtime_evidence_readiness_sla_gate, render_runtime_evidence_readiness_markdown
from .runtime_sla_execution_policy import build_runtime_sla_execution_policy, render_runtime_sla_execution_policy_markdown
from .runtime_sla_gap_prioritizer import build_runtime_sla_gap_prioritizer, render_runtime_sla_gap_prioritizer_markdown
from .runtime_onboarding_patch_safety_validator import validate_onboarding_patch_safety, render_onboarding_patch_safety_markdown
from .runtime_write_sandbox_approval_packet import build_write_sandbox_approval_packet, render_write_sandbox_approval_markdown
from .runtime_commercial_handoff_bundle import build_commercial_handoff_bundle, render_commercial_handoff_markdown
from .runtime_commercial_handoff_acceptance_gate import validate_commercial_handoff_acceptance, render_commercial_handoff_acceptance_markdown
from .runtime_handoff_secret_audit import audit_commercial_handoff_secrets, render_handoff_secret_audit_markdown
from .runtime_handoff_archive_manifest import (
    build_handoff_archive_manifest,
    render_handoff_archive_manifest_markdown,
    render_immutable_run_receipt_markdown,
)
from .runtime_handoff_receipt_comparator import compare_immutable_run_receipts, render_handoff_receipt_comparison_markdown
from .runtime_handoff_rerun_audit_gate import build_handoff_rerun_audit_gate, render_handoff_rerun_audit_gate_markdown
from .runtime_commercial_evidence_lineage_dashboard import build_commercial_evidence_lineage_dashboard, render_commercial_evidence_lineage_dashboard_markdown
from .runtime_commercial_lineage_reviewer_signoff import build_commercial_lineage_reviewer_signoff_packet, render_commercial_lineage_reviewer_signoff_markdown
from .runtime_commercial_closure_acceptance_ledger import build_commercial_closure_acceptance_ledger, render_commercial_closure_acceptance_ledger_markdown
from .runtime_commercial_audit_event_stream import build_commercial_audit_event_stream, render_commercial_audit_event_stream_markdown
from .runtime_commercial_audit_export_adapters import (
    build_commercial_audit_export_adapters,
    render_commercial_audit_exports_markdown,
    render_csv_audit_ledger,
)
from .runtime_commercial_audit_export_import_gate import (
    build_commercial_audit_export_import_gate,
    render_commercial_audit_import_gate_markdown,
)
from .runtime_commercial_external_tracker_reconciliation import (
    build_commercial_external_tracker_reconciliation,
    render_commercial_external_tracker_reconciliation_markdown,
)
from .runtime_external_tracker_closure_sync_policy import (
    build_external_tracker_closure_sync_policy,
    render_external_tracker_closure_sync_policy_markdown,
)
from .runtime_external_tracker_sync_payload_builder import (
    build_external_tracker_sync_payloads,
    render_external_tracker_sync_payloads_markdown,
)
from .runtime_external_tracker_sync_payload_gate import (
    validate_external_tracker_sync_payloads,
    render_external_tracker_sync_payload_gate_markdown,
)
from .runtime_external_tracker_sync_receipt_ledger import (
    build_external_tracker_sync_receipt_ledger,
    render_external_tracker_sync_receipt_ledger_markdown,
)
from .bug_discovery_probe_expander import expand_bug_discovery_probes

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
FIXTURE_BACKED_READ_RISKS = AUTH_BOUNDARY_RISKS
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

def _probe_has_strict_document_grounding(probe: dict[str, Any]) -> bool:
    refs = probe.get("source_refs") if isinstance(probe.get("source_refs"), list) else []
    kinds = {str(r.get("kind") or "") for r in refs if isinstance(r, dict)}
    basis = probe.get("grounding_basis") if isinstance(probe.get("grounding_basis"), dict) else {}
    has_endpoint = "endpoint_contract" in kinds or int(basis.get("endpoint_contract_refs") or 0) >= 1
    has_support = bool(kinds - {"endpoint_contract", ""}) or int(basis.get("supporting_requirement_refs") or 0) >= 1
    return bool(has_endpoint and has_support)


def _get_mapping_value(mapping: dict[str, Any], candidate_id: str, method: str, path: str) -> Any:
    for key in (candidate_id, f"{method} {path}", path, "*"):
        if key in mapping:
            return mapping[key]
    return None


def _auto_fixture_enabled(config: dict[str, Any]) -> bool:
    # Product default: QualiBug creates test data.  Customers should not fill
    # order IDs/request bodies except as an advanced override.
    cfg = config.get("auto_fixture") or config.get("auto_fixtures") or config.get("auto_test_data") or {}
    if isinstance(cfg, dict) and "enabled" in cfg:
        return bool(cfg.get("enabled"))
    return bool(config.get("qualibug_auto_create_test_data", True))


def _auto_fixture_bundle(config: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    cid = str(probe.get("candidate_id") or "")
    cache = config.setdefault("_auto_fixture_runtime", {})
    if cid in cache and isinstance(cache[cid], dict):
        return cache[cid]
    try:
        from .auto_test_data_factory import build_auto_fixture_for_probe

        bundle = build_auto_fixture_for_probe(
            probe,
            input_dir=config.get("input_dir") or config.get("project_input_dir"),
            config=config,
        )
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        bundle = {"error": f"auto_fixture_generation_failed:{type(exc).__name__}:{exc}"}
    cache[cid] = bundle
    return bundle


def _configured_path_params(config: dict[str, Any], probe: dict[str, Any], method: str, path: str) -> dict[str, Any]:
    candidate_id = str(probe.get("candidate_id") or "")
    path_param_cfg = dict(config.get("path_params") or {})
    merged = dict(path_param_cfg.get("*") or {})
    if _auto_fixture_enabled(config) and (method in WRITE_METHODS or bool(config.get("auto_read_path_params")) or _fixture_backed_read_probe(probe, method, path)):
        bundle = _auto_fixture_bundle(config, probe)
        if isinstance(bundle.get("path_params"), dict):
            merged.update(bundle.get("path_params") or {})
    merged.update(path_param_cfg.get(candidate_id) or {})
    merged.update(path_param_cfg.get(f"{method} {path}") or {})
    return merged


def _configured_body(config: dict[str, Any], candidate_id: str, method: str, path: str, probe: dict[str, Any] | None = None) -> tuple[Any, str]:
    bodies = config.get("request_bodies") or config.get("bodies") or {}
    if isinstance(bodies, dict):
        body = _get_mapping_value(bodies, candidate_id, method, path)
        if isinstance(body, dict) and set(body.keys()) == {"json"}:
            body = body.get("json")
        if body not in (None, {}, [], ""):
            return body, "customer_or_advanced_override_configured"
    elif bodies:
        return None, "request_bodies_not_object"

    if probe is not None and _auto_fixture_enabled(config):
        bundle = _auto_fixture_bundle(config, probe)
        body = bundle.get("request_body") if isinstance(bundle, dict) else None
        if body not in (None, {}, [], ""):
            return body, "auto_fixture_generated_by_qualibug"
        return None, str((bundle or {}).get("error") or "auto_fixture_body_generation_failed")
    return None, "write_probe_body_not_document_configured"


def _production_guard_allows(config: dict[str, Any], base_url: str) -> tuple[bool, str]:
    env_kind = str(config.get("environment_kind") or config.get("target_environment") or "").lower()
    if env_kind in {"prod", "production", "live"}:
        return False, "production_environment_kind_blocked"
    host = _url_host(base_url)
    if host and PRODUCTION_HOST_RE.search(host) and not NON_PROD_HINT_RE.search(host):
        return False, "production_like_host_blocked"
    # If customers point a test URL incorrectly, product blocks obvious production
    # patterns and keeps all created data prefixed with qb_auto.  Beyond that the
    # test environment is customer-maintained and intended to be operable.
    return True, "production_guard_passed"


def _approval_enabled(config: dict[str, Any], base_url: str, approval_id: str) -> tuple[bool, str, dict[str, Any]]:
    ok_guard, guard_reason = _production_guard_allows(config, base_url)
    if not ok_guard:
        return False, guard_reason, {}
    sandbox = config.get("disposable_sandbox") or config.get("sandbox") or config.get("test_environment") or {}
    if not isinstance(sandbox, dict):
        sandbox = {}
    if not sandbox and not config.get("allow_write_probes"):
        return False, "sandbox_config_missing_or_disabled", {}
    # Phase92N commercial UX: a staging/test target is expected to be operable.
    # We still require a clear non-production/test-env config or CLI flag, but no
    # longer require customers to provide business data/body IDs.
    enabled = bool(sandbox.get("enabled") or sandbox.get("allow_write_probes") or config.get("allow_write_probes"))
    if not enabled:
        return False, "test_environment_write_execution_not_enabled", sandbox
    expected_approval = str(sandbox.get("approval_id") or sandbox.get("id") or "")
    if expected_approval and approval_id and expected_approval != str(approval_id):
        return False, "sandbox_approval_id_mismatch", sandbox
    target_kind = str(sandbox.get("target_kind") or sandbox.get("kind") or config.get("environment_kind") or "")
    if target_kind and target_kind.lower() in {"prod", "production", "live"}:
        return False, f"unsupported_sandbox_target_kind:{target_kind}", sandbox
    cleanup = str(sandbox.get("cleanup_strategy") or sandbox.get("reset_strategy") or "qualibug_auto_fixture_cleanup")
    if cleanup not in SANDBOX_CLEANUP_STRATEGIES:
        return False, "sandbox_cleanup_strategy_required", sandbox
    allow_hosts = [str(h).lower() for h in (sandbox.get("base_url_allowlist") or sandbox.get("host_allowlist") or [])]
    host = _url_host(base_url)
    if allow_hosts and host not in allow_hosts:
        return False, "sandbox_base_url_not_in_allowlist", sandbox
    return True, "test_environment_write_execution_approved", sandbox


def _configured_replay_count(config: dict[str, Any], probe: dict[str, Any]) -> int:
    cid = str(probe.get("candidate_id") or "")
    ep = probe.get("endpoint") or {}
    method = str(ep.get("method") or "GET").upper()
    path = str(ep.get("path") or "")
    replay_cfg = config.get("replay") or {}
    value = _get_mapping_value(replay_cfg, cid, method, path) if isinstance(replay_cfg, dict) else None
    if isinstance(value, dict):
        value = value.get("count")
    try:
        n = int(value or 2)
    except Exception:
        n = 2
    return max(1, min(n, 5))


def _headers_for_probe(probe: dict[str, Any], config: dict[str, Any]) -> dict[str, str]:
    headers = _headers_from_config(config)
    probe_plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    auth_boundary = _auth_boundary_plan(probe)
    if _is_auth_boundary_risk(probe):
        actor = str(auth_boundary.get("actor") or "anonymous").lower()
        profile = str(auth_boundary.get("credential_profile") or actor or "").strip()
        resolved = config.get("_resolved_account_headers") if isinstance(config.get("_resolved_account_headers"), dict) else {}
        if actor == "anonymous" or profile in {"", "no_credentials", "anonymous"}:
            headers = _negative_headers(headers, list(probe_plan.get("negative_headers") or AUTH_HEADER_NAMES))
        elif isinstance(resolved.get(profile), dict) and resolved.get(profile):
            headers = dict(resolved.get(profile) or {})
        elif isinstance(resolved.get(actor), dict) and resolved.get(actor):
            headers = dict(resolved.get(actor) or {})
        elif "tenant" in actor or "tenant" in profile:
            headers["X-Tenant-Id"] = f"qb_auto_forbidden_{profile or actor}"
    # Per-candidate headers can add safe idempotency keys etc.  Values are supplied by the customer config, not invented.
    ep = probe.get("endpoint") or {}
    cid = str(probe.get("candidate_id") or "")
    method = str(ep.get("method") or "GET").upper()
    path = str(ep.get("path") or "")
    if _auto_fixture_enabled(config):
        bundle = _auto_fixture_bundle(config, probe)
        if isinstance(bundle.get("headers"), dict):
            headers.update({str(k): str(v) for k, v in (bundle.get("headers") or {}).items()})
    per = config.get("headers") or {}
    if isinstance(per, dict):
        value = _get_mapping_value(per, cid, method, path)
        if isinstance(value, dict):
            headers.update({str(k): str(v) for k, v in value.items()})
    auth_boundary = _auth_boundary_plan(probe)
    if _is_auth_boundary_risk(probe):
        actor = str(auth_boundary.get("actor") or "anonymous").lower()
        profile = str(auth_boundary.get("credential_profile") or actor or "").strip()
        if actor == "anonymous" or profile in {"", "no_credentials", "anonymous"}:
            headers = _negative_headers(headers, list(probe_plan.get("negative_headers") or AUTH_HEADER_NAMES))
    return headers


def _decide_probe(probe: dict[str, Any], *, base_url: str, config: dict[str, Any], options: dict[str, Any]) -> ProbeDecision:
    ep = probe.get("endpoint") or {}
    method = str(ep.get("method") or "GET").upper()
    path = str(ep.get("path") or "")
    risk_type = str(probe.get("risk_type") or "unknown")
    execution_policy = str(probe.get("execution_policy") or "")
    candidate_id = str(probe.get("candidate_id") or "")
    merged_path_params = _configured_path_params(config, probe, method, path)
    rendered, missing = _render_path(path, merged_path_params)

    headers = _headers_for_probe(probe, config)
    body = None
    body_reason = "not_needed"
    if method in WRITE_METHODS:
        body, body_reason = _configured_body(config, candidate_id, method, path, probe)
    req = {
        "method": method,
        "url": _join_url(base_url, rendered),
        "path": rendered,
        "headers": _redact(headers),
        "body": _redact(body),
    }
    read_fixture_setup_required = False
    if method in READ_METHODS and _auto_fixture_enabled(config) and _fixture_backed_read_probe(probe, method, path):
        bundle = _auto_fixture_bundle(config, probe)
        read_fixture_setup_required = bool(isinstance(bundle, dict) and bundle.get("setup_requests"))
        if read_fixture_setup_required:
            req["runtime_fixture_setup_required"] = True
            req["runtime_fixture_setup_reason"] = "fixture_backed_read_probe"

    if os.environ.get("QUALIBUG_STRICT_PROBE_GROUNDING", "1") != "0" and not _probe_has_strict_document_grounding(probe):
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", "ungrounded_probe_missing_endpoint_or_requirement_source_refs", req)
    if missing:
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", f"missing_path_params:{','.join(missing)}", req)
    # Probe config templates deliberately contain <FILL:...> placeholders.
    # Never execute read or write probes until the customer replaces them with
    # disposable-sandbox values.  Dry-run reports can still render placeholders.
    if base_url and _has_unresolved_placeholder({"path": rendered, "headers": headers, "body": body}):
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", "probe_config_contains_unresolved_placeholders", req)
    if method in WRITE_METHODS:
        approval_id = str(options.get("approval_id") or "")
        allow_write = bool(options.get("allow_write_sandbox") or config.get("allow_write_probes") or ((config.get("test_environment") or {}).get("allow_write_probes") if isinstance(config.get("test_environment"), dict) else False))
        if not allow_write:
            return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", "write_probe_requires_test_environment_execution_enabled", req)
        if execution_policy != "disposable_sandbox_required":
            return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", f"write_probe_policy_not_disposable_sandbox_required:{execution_policy}", req)
        if not base_url:
            return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", "write_probe_base_url_required", req)
        ok, reason, _sandbox = _approval_enabled(config, base_url, approval_id)
        if not ok:
            return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", reason, req)
        if body is None:
            return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", body_reason, req)
        if risk_type == "idempotency_replay_probe":
            raw_headers = _headers_for_probe(probe, config)
            has_key = any(k.lower() == "idempotency-key" for k in raw_headers) or any(str(k).lower() in {"idempotency_key", "idempotencykey", "business_key", "request_id", "external_event_id"} for k in (body.keys() if isinstance(body, dict) else []))
            if not has_key:
                return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", "idempotency_probe_requires_configured_idempotency_key_or_business_key", req)
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "execute_write_sandbox", "eligible_disposable_sandbox_write_probe", req)
    if not base_url:
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "dry_run_only", "base_url_not_configured", req)
    if method not in READ_METHODS:
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", f"unsupported_method:{method}", req)
    if execution_policy != "read_only_safe":
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", f"policy_not_read_only_safe:{execution_policy}", req)
    if not options.get("execute_readonly"):
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "dry_run_only", "execute_readonly_not_enabled", req)
    if read_fixture_setup_required:
        ok, setup_reason = _read_fixture_setup_approval(config, base_url, options)
        if not ok:
            return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "blocked", setup_reason, req)
        return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "execute_readonly", f"eligible_read_only_probe_with_fixture_setup:{setup_reason}", req)
    return ProbeDecision(candidate_id, risk_type, method, path, execution_policy, "execute_readonly", "eligible_read_only_probe", req)


def _verify_observation(probe: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    risk_type = str(probe.get("risk_type") or "")
    status = response.get("status_code")
    payload = response.get("payload")
    sensitive_keys = _find_sensitive_keys(payload)
    summary = _safe_payload_summary(payload)

    if status is None:
        return {"verdict": "inconclusive", "reason": response.get("error") or "network_error", "confidence": 0.0, "payload_summary": summary, "sensitive_keys": sensitive_keys}
    if _is_auth_boundary_risk(probe, risk_type):
        auth = _auth_boundary_plan(probe)
        actor = str(auth.get("actor") or "anonymous")
        expected = _expected_negative_statuses(probe)
        if int(status) in expected:
            return {"verdict": "falsified_or_protected", "reason": f"{actor} access boundary rejected with HTTP {status}", "confidence": 0.82, "payload_summary": summary, "sensitive_keys": sensitive_keys}
        if 200 <= int(status) < 300 and _has_business_data(payload):
            return {"verdict": "validated_candidate", "reason": f"{actor} access boundary returned HTTP {status} with non-empty business data", "confidence": 0.91, "payload_summary": summary, "sensitive_keys": sensitive_keys}
        if 200 <= int(status) < 300:
            return {"verdict": "needs_more_evidence", "reason": f"{actor} HTTP {status} but no non-empty business payload", "confidence": 0.45, "payload_summary": summary, "sensitive_keys": sensitive_keys}
        return {"verdict": "inconclusive", "reason": f"unexpected HTTP {status} for {actor} access boundary", "confidence": 0.35, "payload_summary": summary, "sensitive_keys": sensitive_keys}

    if risk_type == "audit_privacy_probe":
        if 200 <= int(status) < 300 and sensitive_keys:
            return {"verdict": "validated_candidate", "reason": "read-only response contains sensitive-looking fields that require role/desensitization review", "confidence": 0.72, "payload_summary": summary, "sensitive_keys": sensitive_keys}
        if 200 <= int(status) < 300:
            return {"verdict": "observed_no_finding", "reason": "read-only response observed without obvious sensitive keys", "confidence": 0.55, "payload_summary": summary, "sensitive_keys": sensitive_keys}
    return {"verdict": "observed_no_finding", "reason": f"read-only observation HTTP {status}; no runtime oracle matched", "confidence": 0.4, "payload_summary": summary, "sensitive_keys": sensitive_keys}


def _expected_negative_statuses(probe: dict[str, Any]) -> set[int]:
    probe_plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    values = probe_plan.get("expected_status") or probe_plan.get("expected_statuses") or []
    statuses: set[int] = set()
    for v in values:
        try:
            statuses.add(int(v))
        except Exception:
            pass
    if statuses:
        return statuses
    risk_type = str(probe.get("risk_type") or "")
    if _is_auth_boundary_risk(probe, risk_type):
        return EXPECTED_AUTH_FAILURES
    if risk_type == "ownership_scope_probe":
        return {403, 404}
    if risk_type in {"state_transition_probe", "idempotency_replay_probe", "async_external_event_probe"}:
        return {409, 422}
    return DEFAULT_NEGATIVE_WRITE_FAILURES


def _extract_id_like(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("id", "order_id", "payment_id", "transaction_id", "event_id", "resource_id", "uuid", "code"):
            if key in value and value[key] not in (None, ""):
                return str(value[key])
        for v in value.values():
            hit = _extract_id_like(v)
            if hit:
                return hit
    if isinstance(value, list) and value:
        return _extract_id_like(value[0])
    return ""


def _find_negative_resource_values(value: Any, prefix: str = "") -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (int, float)) and v < 0 and NEGATIVE_NUMBER_KEY_RE.search(str(k)):
                hits.append({"path": path, "value": v})
            hits.extend(_find_negative_resource_values(v, path))
    elif isinstance(value, list):
        for idx, item in enumerate(value[:20]):
            hits.extend(_find_negative_resource_values(item, f"{prefix}[{idx}]" if prefix else f"[{idx}]"))
    return hits[:30]


def _replace_fixture_runtime_value(value: Any, old_id: str, new_id: str) -> Any:
    if not old_id or not new_id or old_id == new_id:
        return value
    if isinstance(value, dict):
        return {k: _replace_fixture_runtime_value(v, old_id, new_id) for k, v in value.items()}
    if isinstance(value, list):
        return [_replace_fixture_runtime_value(v, old_id, new_id) for v in value]
    if isinstance(value, str):
        return value.replace(old_id, new_id)
    return value


def _bind_auto_fixture_response_id(config: dict[str, Any], probe: dict[str, Any], item: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    bind_to = item.get("bind_response_id_to") or []
    if isinstance(bind_to, str):
        bind_to = [bind_to]
    bind_fields = [str(x) for x in bind_to if str(x or "").strip()]
    if not bind_fields:
        return {}
    response_id = _extract_id_like(response.get("payload"))
    if not response_id:
        return {"bound": False, "reason": "setup_response_missing_bindable_id", "bind_response_id_to": bind_fields}
    bundle = _auto_fixture_bundle(config, probe)
    old_id = str(((bundle.get("receipt") or {}).get("primary_fixture_id") if isinstance(bundle.get("receipt"), dict) else "") or "")
    path_params = bundle.setdefault("path_params", {}) if isinstance(bundle, dict) else {}
    if isinstance(path_params, dict):
        for field in bind_fields:
            path_params[field] = response_id
    for key in ("request_body", "snapshots", "cleanup_requests"):
        if key in bundle:
            bundle[key] = _replace_fixture_runtime_value(bundle.get(key), old_id, response_id)
    runtime_bindings = bundle.setdefault("runtime_bindings", [])
    binding = {
        "bound": True,
        "source": "setup_response",
        "response_id": response_id,
        "previous_fixture_id": old_id,
        "path_params": bind_fields,
    }
    if isinstance(runtime_bindings, list):
        runtime_bindings.append(binding)
    receipt = bundle.setdefault("receipt", {})
    if isinstance(receipt, dict):
        receipt["runtime_bound_fixture_id"] = response_id
        receipt["runtime_bound_path_params"] = bind_fields
    return binding


def _effective_runtime_request(probe: dict[str, Any], decision: ProbeDecision, config: dict[str, Any], base_url: str, body: Any) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    path_params = _configured_path_params(config, probe, decision.method, decision.path)
    rendered, missing = _render_path(decision.path, path_params)
    headers = _headers_for_probe(probe, config)
    request = {
        "method": decision.method,
        "url": _join_url(base_url, rendered),
        "path": rendered,
        "headers": _redact(headers),
        "body": _redact(body),
        "path_params_bound_at_execution": _redact(path_params),
    }
    return request, headers, missing


def _verify_write_observation(probe: dict[str, Any], responses: list[dict[str, Any]], snapshots: dict[str, Any]) -> dict[str, Any]:
    risk_type = str(probe.get("risk_type") or "")
    expected = _expected_negative_statuses(probe)
    first = responses[0] if responses else {}
    status = first.get("status_code")
    payload = first.get("payload")
    summary = _safe_payload_summary(payload)
    sensitive_keys = _find_sensitive_keys(payload)
    negative_values = _find_negative_resource_values(payload)
    invariant_eval: dict[str, Any] = {}
    try:
        from .business_invariant_before_after import evaluate_business_invariants_before_after

        invariant_eval = evaluate_business_invariants_before_after(probe, responses, snapshots)
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        invariant_eval = {"verdict": "inconclusive", "reason": f"before_after_invariant_evaluator_error:{type(exc).__name__}:{exc}", "checked_count": 0}

    if invariant_eval.get("verdict") == "failed":
        failed_results = [r for r in (invariant_eval.get("results") or []) if isinstance(r, dict) and r.get("verdict") == "failed"]
        failed_fields: list[str] = []
        for item in failed_results:
            failed_fields.extend([str(x) for x in (item.get("failed_fields") or [])])
        return {
            "verdict": "validated_candidate",
            "reason": f"before/after business invariant failed: {invariant_eval.get('reason')}",
            "confidence": max(0.87, float(invariant_eval.get("confidence") or 0.0)),
            "payload_summary": summary,
            "sensitive_keys": sensitive_keys,
            "business_invariant_evaluation": invariant_eval,
            "failed_fields": list(dict.fromkeys(failed_fields))[:30],
        }

    if status is None:
        return {"verdict": "inconclusive", "reason": first.get("error") or "network_error", "confidence": 0.0, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval}
    try:
        code = int(status)
    except Exception:
        code = 0

    if code in expected:
        return {"verdict": "falsified_or_protected", "reason": f"negative sandbox write was rejected with expected HTTP {code}", "confidence": 0.82, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval}

    if _is_auth_boundary_risk(probe, risk_type) or risk_type in {"ownership_scope_probe", "state_transition_probe", "async_external_event_probe"}:
        if 200 <= code < 300:
            return {"verdict": "validated_candidate", "reason": f"negative sandbox write was accepted with HTTP {code}; expected one of {sorted(expected)}", "confidence": 0.86, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval}
        return {"verdict": "inconclusive", "reason": f"negative sandbox write returned unexpected HTTP {code}", "confidence": 0.38, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval}

    if risk_type == "idempotency_replay_probe":
        ok_responses = [r for r in responses if isinstance(r.get("status_code"), int) and 200 <= int(r.get("status_code")) < 300]
        ids = [_extract_id_like(r.get("payload")) for r in ok_responses]
        ids = [x for x in ids if x]
        if len(ok_responses) >= 2 and len(set(ids)) >= 2:
            return {"verdict": "validated_candidate", "reason": f"replayed sandbox write produced multiple distinct resource identifiers: {ids[:3]}", "confidence": 0.88, "payload_summary": summary, "sensitive_keys": sensitive_keys, "replay_ids": ids[:5], "business_invariant_evaluation": invariant_eval}
        if len(ok_responses) >= 2 and ids and len(set(ids)) == 1:
            return {"verdict": "observed_no_finding", "reason": "replayed sandbox write returned the same resource identifier; no duplicate side effect observed by response oracle", "confidence": 0.62, "payload_summary": summary, "sensitive_keys": sensitive_keys, "replay_ids": ids[:5], "business_invariant_evaluation": invariant_eval}
        if len(ok_responses) >= 2:
            return {"verdict": "needs_more_evidence", "reason": "replayed write was accepted, but response lacks stable resource identifiers or side-effect oracle", "confidence": 0.5, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval}
        if code in expected:
            return {"verdict": "falsified_or_protected", "reason": f"replay rejected with HTTP {code}", "confidence": 0.76, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval}

    if risk_type == "conservation_probe":
        if negative_values:
            return {"verdict": "validated_candidate", "reason": "sandbox write response exposed negative resource-like values", "confidence": 0.83, "payload_summary": summary, "sensitive_keys": sensitive_keys, "negative_values": negative_values, "business_invariant_evaluation": invariant_eval}
        if 200 <= code < 300:
            snap_count = len(snapshots.get("before") or []) + len(snapshots.get("after") or [])
            reason = "write accepted; conservation requires configured before/after DB or API snapshots for confirmation"
            if snap_count:
                reason = "write accepted and snapshots captured; manual/advanced reconciliation is required before confirmation"
            if invariant_eval.get("verdict") == "passed" and snap_count:
                return {"verdict": "observed_no_finding", "reason": "write accepted, but derived before/after conservation/resource invariants passed on observed snapshots", "confidence": 0.62, "payload_summary": summary, "sensitive_keys": sensitive_keys, "snapshot_count": snap_count, "business_invariant_evaluation": invariant_eval}
            return {"verdict": "needs_more_evidence", "reason": reason, "confidence": 0.48, "payload_summary": summary, "sensitive_keys": sensitive_keys, "snapshot_count": snap_count, "business_invariant_evaluation": invariant_eval}

    if 200 <= code < 300:
        if invariant_eval.get("verdict") == "passed":
            return {"verdict": "observed_no_finding", "reason": f"sandbox write returned HTTP {code}; derived before/after invariants passed", "confidence": 0.58, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval}
        return {"verdict": "observed_no_finding", "reason": f"sandbox write returned HTTP {code}; no runtime oracle matched", "confidence": 0.4, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval}
    return {"verdict": "inconclusive", "reason": f"sandbox write returned HTTP {code}; no runtime oracle matched", "confidence": 0.35, "payload_summary": summary, "sensitive_keys": sensitive_keys, "business_invariant_evaluation": invariant_eval}


def _snapshot_requests(config: dict[str, Any], probe: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    cid = str(probe.get("candidate_id") or "")
    ep = probe.get("endpoint") or {}
    key = f"{str(ep.get('method') or '').upper()} {str(ep.get('path') or '')}"
    snapshots = config.get("snapshots") or {}
    reqs: list[dict[str, Any]] = []
    if isinstance(snapshots, dict):
        selected = snapshots.get(cid) or snapshots.get(key) or snapshots.get("*") or {}
        if isinstance(selected, dict):
            raw = selected.get(phase) or []
            if isinstance(raw, dict):
                raw = [raw]
            reqs.extend([r for r in raw if isinstance(r, dict)])
    # Commercial default: when customer did not configure snapshots, use the
    # OpenAPI-derived snapshot reads planned by the auto data factory.
    if not reqs and _auto_fixture_enabled(config):
        bundle = _auto_fixture_bundle(config, probe)
        auto_snaps = bundle.get("snapshots") if isinstance(bundle, dict) else {}
        raw = (auto_snaps or {}).get(phase) if isinstance(auto_snaps, dict) else []
        if isinstance(raw, dict):
            raw = [raw]
        reqs.extend([r for r in raw if isinstance(r, dict)])
    return reqs[:5]


def _execute_snapshots(config: dict[str, Any], base_url: str, probe: dict[str, Any], phase: str, timeout: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in _snapshot_requests(config, probe, phase):
        method = str(item.get("method") or "GET").upper()
        if method not in READ_METHODS:
            out.append({"skipped": True, "reason": f"snapshot_method_not_read_only:{method}"})
            continue
        path_params = dict((config.get("path_params") or {}).get("*") or {})
        if _auto_fixture_enabled(config):
            bundle = _auto_fixture_bundle(config, probe)
            if isinstance(bundle.get("path_params"), dict):
                path_params.update(bundle.get("path_params") or {})
        if isinstance(item.get("path_params"), dict):
            path_params.update(item.get("path_params") or {})
        path, missing = _render_path(str(item.get("path") or ""), path_params)
        if missing:
            out.append({"skipped": True, "reason": f"snapshot_missing_path_params:{','.join(missing)}", "path": item.get("path"), "observer_kind": item.get("observer_kind")})
            continue
        query_string = _render_query(item.get("query"), path_params)
        request_path = path + (("?" + query_string) if query_string else "")
        headers = _headers_from_config(config)
        response = _http_request(method, _join_url(base_url, request_path), headers, timeout=timeout)
        out.append({
            "method": method,
            "path": request_path,
            "observer_kind": item.get("observer_kind"),
            "evidence_goal": item.get("evidence_goal"),
            "source": item.get("source"),
            "response": {"status_code": response.get("status_code"), "error": response.get("error"), "payload": _redact(response.get("payload")), "duration_ms": response.get("duration_ms")},
        })
    return out


def _auto_fixture_requests(config: dict[str, Any], probe: dict[str, Any], key: str) -> list[dict[str, Any]]:
    if not _auto_fixture_enabled(config):
        return []
    bundle = _auto_fixture_bundle(config, probe)
    raw = bundle.get(key) if isinstance(bundle, dict) else []
    return [r for r in (raw if isinstance(raw, list) else []) if isinstance(r, dict)][:5]


def _execute_auto_fixture_requests(config: dict[str, Any], base_url: str, probe: dict[str, Any], key: str, timeout: float) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for item in _auto_fixture_requests(config, probe, key):
        method = str(item.get("method") or "POST").upper()
        if method not in WRITE_METHODS:
            receipts.append({"status": "skipped", "reason": f"auto_fixture_method_not_write:{method}", "path": item.get("path")})
            continue
        path_params = dict((config.get("path_params") or {}).get("*") or {})
        if isinstance(item.get("path_params"), dict):
            path_params.update(item.get("path_params") or {})
        path, missing = _render_path(str(item.get("path") or ""), path_params)
        if missing:
            receipts.append({"status": "blocked", "reason": f"auto_fixture_missing_path_params:{','.join(missing)}", "path": item.get("path")})
            continue
        headers = _fixture_control_headers(config)
        if isinstance(item.get("headers"), dict):
            headers.update({str(k): str(v) for k, v in (item.get("headers") or {}).items()})
        response = _http_request(method, _join_url(base_url, path), headers, body=item.get("body"), timeout=timeout)
        code = response.get("status_code")
        accepted = bool(isinstance(code, int) and 200 <= int(code) < 300)
        binding = _bind_auto_fixture_response_id(config, probe, item, response) if accepted else {}
        receipts.append({
            "status": "executed",
            "purpose": item.get("purpose") or key,
            "accepted": accepted,
            "method": method,
            "path": path,
            "used_fixture_control_headers": True,
            "runtime_binding": binding,
            "response": {"status_code": code, "error": response.get("error"), "payload": _redact(response.get("payload")), "duration_ms": response.get("duration_ms")},
        })
    return receipts


def _execute_parallel_write_attempts(method: str, url: str, headers: dict[str, str], body: Any, *, attempts: int, timeout: float) -> list[dict[str, Any]]:
    attempts = max(2, min(int(attempts or 2), 8))
    barrier = threading.Barrier(attempts)
    lock = threading.Lock()
    results: list[dict[str, Any]] = []

    def worker(idx: int) -> None:
        try:
            barrier.wait(timeout=max(1.0, min(timeout, 5.0)))
        except Exception:
            pass
        started = time.time()
        response = _http_request(method, url, headers, body=body, timeout=timeout)
        response["attempt"] = idx + 1
        response["parallel"] = True
        response["started_at_ms"] = int(started * 1000)
        with lock:
            results.append(response)

    threads = [threading.Thread(target=worker, args=(idx,), daemon=True) for idx in range(attempts)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout + 1.0)
    return sorted(results, key=lambda r: int(r.get("attempt") or 0))


def _verify_concurrency_observation(probe: dict[str, Any], responses: list[dict[str, Any]], snapshots: dict[str, Any]) -> dict[str, Any] | None:
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    if plan.get("strategy") != "concurrency_race_runtime_probe":
        return None
    invariant_eval: dict[str, Any] = {}
    try:
        from .business_invariant_before_after import evaluate_business_invariants_before_after

        invariant_eval = evaluate_business_invariants_before_after(probe, responses, snapshots)
    except Exception as exc:  # pragma: no cover
        invariant_eval = {"verdict": "inconclusive", "reason": f"before_after_invariant_evaluator_error:{type(exc).__name__}:{exc}"}
    if invariant_eval.get("verdict") == "failed":
        return {
            "verdict": "validated_candidate",
            "reason": f"parallel race probe broke before/after invariant: {invariant_eval.get('reason')}",
            "confidence": max(0.9, float(invariant_eval.get("confidence") or 0.0)),
            "payload_summary": _safe_payload_summary([r.get("payload") for r in responses]),
            "sensitive_keys": [],
            "business_invariant_evaluation": invariant_eval,
        }
    ok = [r for r in responses if isinstance(r.get("status_code"), int) and 200 <= int(r.get("status_code")) < 300]
    ids = [_extract_id_like(r.get("payload")) for r in ok]
    ids = [x for x in ids if x]
    race_family = str(plan.get("race_family") or "")
    if race_family in {"idempotency_race", "stock_oversell_race", "terminal_transition_race", "approval_double_decision_race", "generic_duplicate_write_race"} and len(ok) >= 2:
        if len(set(ids)) >= 2:
            return {
                "verdict": "validated_candidate",
                "reason": f"parallel race probe accepted multiple attempts and produced distinct side-effect identifiers: {ids[:4]}",
                "confidence": 0.9,
                "payload_summary": _safe_payload_summary([r.get("payload") for r in responses]),
                "sensitive_keys": [],
                "replay_ids": ids[:8],
                "business_invariant_evaluation": invariant_eval,
            }
        return {
            "verdict": "needs_more_evidence",
            "reason": f"parallel race probe accepted {len(ok)} attempts; response did not expose enough side-effect identifiers, before/after observers must decide",
            "confidence": 0.58,
            "payload_summary": _safe_payload_summary([r.get("payload") for r in responses]),
            "sensitive_keys": [],
            "business_invariant_evaluation": invariant_eval,
        }
    if len(ok) <= 1:
        return {
            "verdict": "falsified_or_protected",
            "reason": "parallel race probe did not accept multiple writes; no duplicate side effect observed by HTTP oracle",
            "confidence": 0.72,
            "payload_summary": _safe_payload_summary([r.get("payload") for r in responses]),
            "sensitive_keys": [],
            "business_invariant_evaluation": invariant_eval,
        }
    return None


def _execute_read_probe(probe: dict[str, Any], decision: ProbeDecision, config: dict[str, Any], base_url: str, timeout: float) -> dict[str, Any]:
    setup_required = bool((decision.request or {}).get("runtime_fixture_setup_required"))
    setup_receipts = _execute_auto_fixture_requests(config, base_url, probe, "setup_requests", timeout) if setup_required else []
    setup_blocked = any(r.get("status") == "blocked" for r in setup_receipts)
    effective_request, headers, missing = _effective_runtime_request(probe, decision, config, base_url, None)
    if setup_blocked:
        response = {"status_code": None, "error": "auto_fixture_setup_blocked", "payload": None, "duration_ms": 0}
        verification = {"verdict": "inconclusive", "reason": "auto_fixture_setup_blocked", "confidence": 0.0, "payload_summary": {}, "sensitive_keys": []}
    elif missing:
        response = {"status_code": None, "error": f"runtime_missing_path_params_after_read_fixture_setup:{','.join(missing)}", "payload": None, "duration_ms": 0}
        verification = {"verdict": "inconclusive", "reason": response["error"], "confidence": 0.0, "payload_summary": {}, "sensitive_keys": []}
    else:
        response = _http_request(decision.method, str(effective_request.get("url") or ""), headers, timeout=timeout)
        verification = _verify_observation(probe, response)
    cleanup_receipts = _execute_auto_fixture_requests(config, base_url, probe, "cleanup_requests", timeout) if setup_required else []
    return {
        "candidate_id": decision.candidate_id,
        "risk_type": decision.risk_type,
        "method": decision.method,
        "path": decision.path,
        "request": effective_request if not setup_blocked else (decision.request | {"setup_blocked": True}),
        "fixture_receipts": setup_receipts,
        "cleanup_receipts": cleanup_receipts,
        "response": {"status_code": response.get("status_code"), "error": response.get("error"), "payload": _redact(response.get("payload")), "duration_ms": response.get("duration_ms")},
        "verification": verification,
        "source_refs": probe.get("source_refs") or [],
        "grounding_basis": probe.get("grounding_basis") or {},
    }


def _execute_flow_probe(probe: dict[str, Any], decision: ProbeDecision, config: dict[str, Any], base_url: str, timeout: float) -> dict[str, Any]:
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    scenario = plan.get("flow_scenario") if isinstance(plan.get("flow_scenario"), dict) else {}
    steps = [s for s in (scenario.get("steps") or []) if isinstance(s, dict)]
    setup_receipts = _execute_auto_fixture_requests(config, base_url, probe, "setup_requests", timeout)
    setup_blocked = any(r.get("status") == "blocked" for r in setup_receipts)
    snapshots = {
        "before": [] if setup_blocked else _execute_snapshots(config, base_url, probe, "before", timeout),
        "after": [],
    }
    responses: list[dict[str, Any]] = []
    shared_body, _reason = _configured_body(config, decision.candidate_id, decision.method, decision.path, probe)
    shared_params = _configured_path_params(config, probe, decision.method, decision.path)
    headers = _headers_for_probe(probe, config)
    if not setup_blocked:
        for idx, step in enumerate(steps[:8]):
            method = str(step.get("method") or decision.method).upper()
            raw_path = str(step.get("path") or decision.path)
            path, missing = _render_path(raw_path, shared_params)
            if missing:
                responses.append({"attempt": idx + 1, "step": idx + 1, "status_code": None, "error": f"flow_step_missing_path_params:{','.join(missing)}", "payload": {}})
                continue
            response = _http_request(method, _join_url(base_url, path), headers, body=shared_body, timeout=timeout)
            responses.append(response | {"attempt": idx + 1, "step": idx + 1, "flow_action": step.get("action"), "flow_path": path})
        snapshots["after"] = _execute_snapshots(config, base_url, probe, "after", timeout)
    cleanup_receipts = _execute_auto_fixture_requests(config, base_url, probe, "cleanup_requests", timeout)
    verification = _verify_flow_observation(probe, responses, snapshots) if not setup_blocked else {"verdict": "inconclusive", "reason": "auto_fixture_setup_blocked", "confidence": 0.0, "payload_summary": {}, "sensitive_keys": []}
    return {
        "candidate_id": decision.candidate_id,
        "risk_type": decision.risk_type,
        "method": decision.method,
        "path": decision.path,
        "request": decision.request,
        "fixture_receipts": setup_receipts,
        "cleanup_receipts": cleanup_receipts,
        "responses": [
            {"attempt": r.get("attempt"), "step": r.get("step"), "flow_action": r.get("flow_action"), "status_code": r.get("status_code"), "error": r.get("error"), "payload": _redact(r.get("payload")), "duration_ms": r.get("duration_ms")}
            for r in responses
        ],
        "snapshots": snapshots,
        "verification": verification,
        "source_refs": probe.get("source_refs") or [],
        "grounding_basis": probe.get("grounding_basis") or {},
    }


def _verify_flow_observation(probe: dict[str, Any], responses: list[dict[str, Any]], snapshots: dict[str, Any]) -> dict[str, Any]:
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    scenario = plan.get("flow_scenario") if isinstance(plan.get("flow_scenario"), dict) else {}
    invariant_eval: dict[str, Any] = {}
    try:
        from .business_invariant_before_after import evaluate_business_invariants_before_after

        invariant_eval = evaluate_business_invariants_before_after(probe, responses, snapshots)
    except Exception as exc:  # pragma: no cover
        invariant_eval = {"verdict": "inconclusive", "reason": f"before_after_invariant_evaluator_error:{type(exc).__name__}:{exc}"}
    if invariant_eval.get("verdict") == "failed":
        return {"verdict": "validated_candidate", "reason": f"multi-step flow broke before/after invariant: {invariant_eval.get('reason')}", "confidence": max(0.89, float(invariant_eval.get("confidence") or 0.0)), "payload_summary": _safe_payload_summary([r.get("payload") for r in responses]), "sensitive_keys": [], "business_invariant_evaluation": invariant_eval}
    strategy = str(scenario.get("strategy") or plan.get("strategy") or "")
    ok = [r for r in responses if isinstance(r.get("status_code"), int) and 200 <= int(r.get("status_code")) < 300]
    if strategy == "illegal_order_inversion_flow" and ok:
        return {"verdict": "validated_candidate", "reason": f"illegal multi-step order inversion accepted {len(ok)} step(s); expected rejection/no side effect", "confidence": 0.87, "payload_summary": _safe_payload_summary([r.get("payload") for r in responses]), "sensitive_keys": [], "business_invariant_evaluation": invariant_eval}
    if responses and not ok:
        return {"verdict": "falsified_or_protected", "reason": "multi-step negative flow rejected all write steps", "confidence": 0.74, "payload_summary": _safe_payload_summary([r.get("payload") for r in responses]), "sensitive_keys": [], "business_invariant_evaluation": invariant_eval}
    return {"verdict": "needs_more_evidence", "reason": "multi-step flow executed but runtime oracle needs observer deltas for confirmation", "confidence": 0.52, "payload_summary": _safe_payload_summary([r.get("payload") for r in responses]), "sensitive_keys": [], "business_invariant_evaluation": invariant_eval}


def _execute_write_probe(probe: dict[str, Any], decision: ProbeDecision, config: dict[str, Any], base_url: str, timeout: float) -> dict[str, Any]:
    setup_receipts = _execute_auto_fixture_requests(config, base_url, probe, "setup_requests", timeout)
    setup_blocked = any(r.get("status") == "blocked" for r in setup_receipts)
    body, _reason = _configured_body(config, decision.candidate_id, decision.method, decision.path, probe)
    effective_request, headers, missing = _effective_runtime_request(probe, decision, config, base_url, body)
    snapshots = {
        "before": [] if setup_blocked or missing else _execute_snapshots(config, base_url, probe, "before", timeout),
        "after": [],
    }
    replay_count = _configured_replay_count(config, probe) if decision.risk_type in {"idempotency_replay_probe", "async_external_event_probe"} else 1
    responses: list[dict[str, Any]] = []
    if missing:
        responses.append({"attempt": 1, "status_code": None, "error": f"runtime_missing_path_params_after_setup:{','.join(missing)}", "payload": {}})
    elif not setup_blocked:
        probe_plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
        concurrency = probe_plan.get("concurrency") if isinstance(probe_plan.get("concurrency"), dict) else {}
        if probe_plan.get("strategy") == "concurrency_race_runtime_probe" and concurrency:
            responses.extend(_execute_parallel_write_attempts(
                decision.method,
                str(effective_request.get("url") or ""),
                headers,
                body,
                attempts=int(concurrency.get("parallel_attempts") or 2),
                timeout=timeout,
            ))
        else:
            for idx in range(replay_count):
                response = _http_request(decision.method, str(effective_request.get("url") or ""), headers, body=body, timeout=timeout)
                responses.append(response | {"attempt": idx + 1})
        snapshots["after"] = _execute_snapshots(config, base_url, probe, "after", timeout)
    cleanup_receipts = _execute_auto_fixture_requests(config, base_url, probe, "cleanup_requests", timeout)
    if missing:
        verification = {"verdict": "inconclusive", "reason": f"runtime_missing_path_params_after_setup:{','.join(missing)}", "confidence": 0.0, "payload_summary": {}, "sensitive_keys": []}
    elif not setup_blocked:
        concurrency_verification = _verify_concurrency_observation(probe, responses, snapshots)
        verification = concurrency_verification or _verify_write_observation(probe, responses, snapshots)
    else:
        verification = {"verdict": "inconclusive", "reason": "auto_fixture_setup_blocked", "confidence": 0.0, "payload_summary": {}, "sensitive_keys": []}
    return {
        "candidate_id": decision.candidate_id,
        "risk_type": decision.risk_type,
        "method": decision.method,
        "path": decision.path,
        "request": effective_request if not setup_blocked else (decision.request | {"setup_blocked": True}),
        "fixture_receipts": setup_receipts,
        "cleanup_receipts": cleanup_receipts,
        "responses": [
            {"attempt": r.get("attempt"), "parallel": r.get("parallel"), "status_code": r.get("status_code"), "error": r.get("error"), "payload": _redact(r.get("payload")), "duration_ms": r.get("duration_ms")}
            for r in responses
        ],
        "snapshots": snapshots,
        "verification": verification,
        "source_refs": probe.get("source_refs") or [],
        "grounding_basis": probe.get("grounding_basis") or {},
    }


def _render_markdown(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        f"# Grounded Probe Execution Report — {report.get('project_id') or ''}",
        "",
        "## Guardrail",
        "",
        f"- strict_no_peek: `{report.get('strict_no_peek')}`",
        "- source: `grounded_probe_plan.json` generated from `projects/<project>/input`",
        "- read-only probes: require explicit `--execute-readonly`",
        "- write probes: require explicit disposable sandbox approval, env flag, cleanup strategy and configured request bodies",
        "- runtime findings require observed HTTP evidence",
        "- Phase92P: before/after business invariants are auto-judged from observed snapshots",
        "- Phase92Q: OpenAPI-derived multi-observer snapshots expand runtime evidence",
        "- Phase92R: observer responses are semantically joined into a before/after business object graph",
        "- Phase92S: cross-observer conservation reconciliation compares state deltas with ledger/history deltas",
        "- Phase92T: validated findings include customer-ready evidence packages with strength score, invariant and delta summaries",
        "- Phase92U: customer impact triage assigns severity, priority, owner and escalation reason",
        "- Phase92V: report-level customer delivery index groups validated findings by priority, severity, risk family and evidence coverage",
        "- Phase92W: each finding links back to generated report, repro script and regression assets",
        "- Phase92X: P0/P1 findings include fix-verification checklists, rerun plans, close criteria and lifecycle status",
        "- Phase92Y: stable lifecycle signatures prevent false close/reopen when candidate ids or path parameters change",
        "- Phase92Z: P0/P1 findings generate developer remediation verification artifacts",
        "- Phase93A: runtime onboarding preflight reports target, auth, fixture, cleanup and observer readiness before probes run",
        "- Phase93B: per-probe runtime capability matrix explains ready, degraded and blocked execution lanes",
        "- Phase93C: onboarding remediation kit gives customers exact safe setup actions and config patch templates",
        "- Phase93D: runtime execution runbook sequences preflight, read-only, write-sandbox and fix-verification runs",
        "- Phase93E: runtime evidence readiness SLA gate quantifies commercial evidence coverage",
        "- Phase93F: SLA-gated execution policy separates must-run, degraded, blocked and supplemental probes",
        "- Phase93G: SLA gap prioritizer generates the smallest next onboarding delta patch",
        "- Phase93H: onboarding patch safety validator blocks production targets, raw secrets and unsafe cleanup gaps",
        "- Phase93I: write-sandbox approval packet packages customer approval requirements",
        "- Phase93J: commercial handoff bundle indexes all runtime onboarding, SLA, approval and remediation artifacts",
        "- Phase93K: commercial handoff acceptance gate validates bundle signoff readiness and blockers",
        "- Phase93L: commercial handoff secret audit blocks raw credentials in customer-facing artifacts",
        "- Phase93M: commercial handoff archive manifest and immutable run receipt hash delivery artifacts for rerun auditability",
        "- Phase93N: immutable handoff receipt comparison explains whether reruns use the same input and delivery archive lineage",
        "- Phase93O: commercial rerun audit gate decides whether a rerun may close previous findings under the same lineage",
        "",
        "## Summary",
        "",
        f"- probes total: {summary.get('probe_count')}",
        f"- original probes: {summary.get('original_probe_count')}; Phase94 bug-discovery probes added: {summary.get('phase94_added_probe_count')} (P0: {summary.get('phase94_added_p0_probe_count')})",
        f"- Phase94 multistep flow scenarios: {summary.get('phase94_multistep_flow_scenario_count')}",
        f"- executed read-only: {summary.get('executed_readonly_count')}",
        f"- executed write sandbox: {summary.get('executed_write_sandbox_count')}",
        f"- blocked: {summary.get('blocked_count')}",
        f"- dry-run only: {summary.get('dry_run_count')}",
        f"- validated candidates: {summary.get('validated_candidate_count')}",
        f"- protected/falsified: {summary.get('protected_count')}",
        f"- needs more evidence: {summary.get('needs_more_evidence_count')}",
        f"- customer delivery index: `{(report.get('customer_delivery_index') or {}).get('engine')}`",
        f"- by priority: `{json.dumps(((report.get('customer_delivery_index') or {}).get('by_priority') or {}), ensure_ascii=False)}`",
        f"- auto fixture setup requests: {summary.get('auto_fixture_setup_request_count')}",
        f"- auto fixture cleanup requests: {summary.get('auto_fixture_cleanup_request_count')}",
        f"- auto snapshot requests: {summary.get('auto_snapshot_request_count')}",
        f"- fix verification required: {summary.get('fix_verification_required_count')}",
        f"- closed by rerun: {summary.get('closed_by_rerun_count')}",
        f"- stable lifecycle matches: {summary.get('stable_lifecycle_match_count')}",
        f"- remediation work items: {summary.get('remediation_work_item_count')}",
        f"- onboarding preflight: `{((report.get('onboarding_preflight') or {}).get('status'))}`; P0/P1 ready: `{((report.get('onboarding_preflight') or {}).get('ready_for_p0_p1_runtime_validation'))}`",
        f"- runtime capability lanes: `{json.dumps(((report.get('runtime_capability_matrix') or {}).get('by_preflight_lane') or {}), ensure_ascii=False)}`",
        f"- onboarding remediation actions: `{((report.get('onboarding_remediation_kit') or {}).get('action_count'))}`",
        f"- runtime runbook status: `{((report.get('runtime_execution_runbook') or {}).get('status'))}`",
        f"- runtime evidence SLA gate: `{((report.get('runtime_evidence_readiness_sla_gate') or {}).get('status'))}` / score `{((report.get('runtime_evidence_readiness_sla_gate') or {}).get('commercial_readiness_score'))}`",
        f"- runtime SLA policy: `{((report.get('runtime_sla_execution_policy') or {}).get('status'))}`",
        f"- write sandbox approval: `{((report.get('write_sandbox_approval_packet') or {}).get('status'))}`",
        f"- commercial handoff bundle: `{((report.get('commercial_handoff_bundle') or {}).get('status'))}`",
        f"- commercial handoff acceptance: `{((report.get('commercial_handoff_acceptance_gate') or {}).get('status'))}`",
        f"- commercial handoff secret audit: `{((report.get('commercial_handoff_secret_audit') or {}).get('status'))}`",
        f"- handoff archive manifest: `{((report.get('handoff_archive_manifest') or {}).get('status'))}` / lineage `{((report.get('immutable_run_receipt') or {}).get('run_lineage_id'))}`",
        f"- handoff receipt comparison: `{((report.get('handoff_receipt_comparison') or {}).get('status'))}` / changes `{((report.get('handoff_receipt_comparison') or {}).get('change_count'))}`",
        f"- handoff rerun audit gate: `{((report.get('handoff_rerun_audit_gate') or {}).get('status'))}` / closure allowed `{((report.get('handoff_rerun_audit_gate') or {}).get('closure_verification_allowed'))}`",
        "",
        "## Runtime findings",
        "",
    ]
    findings = report.get("findings") or []
    if not findings:
        lines.append("No runtime-validated candidates were produced in this run.")
        lines.append("")
    for f in findings[:80]:
        evidence = f.get("evidence") or {}
        lines.extend([
            f"### {f.get('finding_id')} — {f.get('title')}",
            "",
            f"- status: `{f.get('status')}` / confidence: `{f.get('confidence')}`",
            f"- risk_type: `{f.get('risk_type')}`",
            f"- endpoint: `{f.get('method')} {f.get('path')}`",
            f"- reason: {f.get('reason')}",
            f"- evidence: HTTP `{evidence.get('status_code')}`; payload `{json.dumps((evidence.get('payload_summary') or {}), ensure_ascii=False)}`",
            f"- evidence strength: `{f.get('evidence_grade')}` / `{f.get('evidence_strength_score')}`",
            f"- triage: `{f.get('priority')}` / `{f.get('severity')}` — {f.get('customer_impact_summary')}",
            f"- violated invariants: {', '.join(str(x.get('kind')) for x in (f.get('violated_invariants') or [])[:6]) or 'n/a'}",
            f"- delta summary: `{json.dumps((f.get('delta_summary') or {}), ensure_ascii=False)[:900]}`",
            f"- source refs: {'; '.join((str(r.get('file')) + ' / ' + str(r.get('section')) + ' / ' + str(r.get('kind'))) for r in (f.get('source_refs') or [])[:4])}",
            f"- fix verification lifecycle: `{((f.get('fix_verification') or {}).get('lifecycle_status'))}`; close criteria: `{json.dumps(((f.get('fix_verification') or {}).get('fix_close_criteria') or [])[:3], ensure_ascii=False)}`",
            "",
        ])
    lines.extend(["## Decisions", ""])
    for d in (report.get("decisions") or [])[:160]:
        lines.append(f"- `{d.get('candidate_id')}` `{d.get('method')} {d.get('path')}` → **{d.get('decision')}** ({d.get('reason')})")
    return "\n".join(lines)


def _powershell_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _render_repro_ps1(report: dict[str, Any]) -> str:
    lines = [
        "# Auto-generated by QualiBug grounded probe executor.",
        "# Review target/base URL before running. Commands are generated from input-only probe plans.",
        "$ErrorActionPreference = 'Stop'",
        "",
    ]
    for decision in report.get("decisions") or []:
        req = decision.get("request") or {}
        headers = req.get("headers") or {}
        if headers:
            header_items = "; ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in headers.items())
            header_line = f"$headers = @{{ {header_items} }}"
            header_arg = " -Headers $headers"
        else:
            header_line = ""
            header_arg = ""
        if decision.get("method") in READ_METHODS:
            lines.append(f"# {decision.get('candidate_id')} {decision.get('risk_type')} — {decision.get('reason')}")
            if header_line:
                lines.append(header_line)
            lines.append(f"Invoke-WebRequest -Method {decision.get('method')} -Uri {_powershell_quote(req.get('url') or '')}{header_arg}")
            lines.append("")
        elif decision.get("decision") == "execute_write_sandbox":
            lines.append(f"# SANDBOX ONLY: {decision.get('candidate_id')} {decision.get('risk_type')} — {decision.get('reason')}")
            lines.append("# The following write command is commented out intentionally. Run only against the approved disposable sandbox.")
            if header_line:
                lines.append("# " + header_line)
            body = json.dumps(req.get("body") or {}, ensure_ascii=False).replace("'", "''")
            lines.append(f"# Invoke-WebRequest -Method {decision.get('method')} -Uri {_powershell_quote(req.get('url') or '')}{header_arg} -ContentType 'application/json' -Body '{body}'")
            lines.append("")
    return "\n".join(lines)


def _render_pytest(report: dict[str, Any]) -> str:
    read_cases: list[dict[str, Any]] = []
    write_cases: list[dict[str, Any]] = []
    for decision in report.get("decisions") or []:
        if str(decision.get("risk_type") or "") in AUTH_BOUNDARY_RISKS and decision.get("method") in READ_METHODS and (decision.get("request") or {}).get("url"):
            read_cases.append({"id": decision.get("candidate_id"), "method": decision.get("method"), "url": (decision.get("request") or {}).get("url"), "headers": (decision.get("request") or {}).get("headers") or {}})
        if decision.get("method") in WRITE_METHODS and decision.get("decision") == "execute_write_sandbox" and (decision.get("request") or {}).get("url"):
            write_cases.append({"id": decision.get("candidate_id"), "method": decision.get("method"), "url": (decision.get("request") or {}).get("url"), "headers": (decision.get("request") or {}).get("headers") or {}, "body": (decision.get("request") or {}).get("body") or {}})
    return "\n".join([
        "# Auto-generated QualiBug regression asset.",
        "# Read-only tests assert documented anonymous access boundaries.",
        "# Write tests are skipped by default and require a disposable sandbox.",
        "import os",
        "import pytest",
        "import requests",
        "",
        f"READ_CASES = {json.dumps(read_cases, ensure_ascii=False, indent=2)}",
        f"WRITE_CASES = {json.dumps(write_cases, ensure_ascii=False, indent=2)}",
        "",
        "@pytest.mark.parametrize('case', READ_CASES, ids=lambda c: c.get('id'))",
        "def test_anonymous_read_boundaries(case):",
        "    resp = requests.request(case['method'], case['url'], headers=case.get('headers') or {}, timeout=10)",
        "    assert resp.status_code in (401, 403, 404), resp.text[:500]",
        "",
        "@pytest.mark.skipif(os.environ.get('QUALIBUG_RUN_SANDBOX_WRITE_REGRESSION') != '1', reason='requires disposable sandbox')",
        "@pytest.mark.parametrize('case', WRITE_CASES, ids=lambda c: c.get('id'))",
        "def test_documented_negative_write_boundaries(case):",
        "    resp = requests.request(case['method'], case['url'], headers=case.get('headers') or {}, json=case.get('body') or {}, timeout=10)",
        "    assert resp.status_code in (400, 401, 403, 404, 409, 422), resp.text[:500]",
        "",
    ])


def _finding_from_observation(obs: dict[str, Any], finding_no: int, source: str) -> dict[str, Any]:
    verification = obs.get("verification") or {}
    evidence_status = None
    if obs.get("response"):
        evidence_status = (obs.get("response") or {}).get("status_code")
    elif obs.get("responses"):
        statuses = [r.get("status_code") for r in (obs.get("responses") or [])]
        evidence_status = statuses[0] if statuses else None
    evidence_package = package_runtime_finding_evidence(obs, source=source)
    finding = {
        "finding_id": f"GPF-{finding_no:04d}",
        "candidate_id": obs.get("candidate_id"),
        "title": f"{obs.get('risk_type')} validated for {obs.get('method')} {obs.get('path')}",
        "status": "validated_candidate",
        "risk_type": obs.get("risk_type"),
        "method": obs.get("method"),
        "path": obs.get("path"),
        "confidence": verification.get("confidence"),
        "evidence_strength_score": evidence_package.get("evidence_strength_score"),
        "evidence_grade": evidence_package.get("evidence_grade"),
        "reason": verification.get("reason"),
        "evidence": {"status_code": evidence_status, "payload_summary": verification.get("payload_summary"), "sensitive_keys": verification.get("sensitive_keys"), "replay_ids": verification.get("replay_ids"), "negative_values": verification.get("negative_values"), "business_invariant_evaluation": verification.get("business_invariant_evaluation")},
        "evidence_package": evidence_package,
        "violated_invariants": evidence_package.get("violated_invariants") or [],
        "delta_summary": evidence_package.get("delta_summary") or {},
        "source_refs": obs.get("source_refs") or [],
        "grounding_basis": obs.get("grounding_basis") or {},
        "source": source,
    }
    triage = triage_runtime_finding(finding)
    finding["customer_triage"] = triage
    finding["severity"] = triage.get("severity")
    finding["priority"] = triage.get("priority")
    finding["customer_impact_summary"] = triage.get("customer_impact_summary")
    return finding


def run_grounded_probe_executor(
    *,
    probe_plan_path: str | Path,
    out_dir: str | Path,
    base_url: str = "",
    probe_config: str | Path | None = None,
    execute_readonly: bool = False,
    allow_write_sandbox: bool = False,
    approval_id: str = "",
    max_probes: int = 0,
    timeout_seconds: float = 10.0,
    input_dir: str | Path | None = None,
) -> dict[str, Any]:
    plan_path = Path(probe_plan_path).resolve()
    output = Path(out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    plan = _read_json(plan_path)
    config = _load_config(probe_config)
    if input_dir and not config.get("input_dir"):
        config["input_dir"] = str(input_dir)
    base = str(base_url or config.get("base_url") or os.environ.get("QUALIBUG_TARGET_BASE_URL") or "").rstrip("/")
    config = _materialize_account_auth(config, base, timeout_seconds)
    original_probes = list(plan.get("probes") or [])
    bug_discovery_expansion = expand_bug_discovery_probes(plan, input_dir=config.get("input_dir"), config=config)
    probes = original_probes + list(bug_discovery_expansion.get("probes") or [])
    if max_probes and max_probes > 0:
        probes = probes[:max_probes]
    options = {"execute_readonly": execute_readonly, "allow_write_sandbox": allow_write_sandbox, "approval_id": approval_id}
    preflight = run_runtime_onboarding_preflight(
        plan={**plan, "probes": probes},
        config=config,
        base_url=base,
        execute_readonly=execute_readonly,
        allow_write_sandbox=allow_write_sandbox,
        timeout_seconds=timeout_seconds,
        requester=_http_request,
    )
    runtime_capability_matrix = build_runtime_probe_capability_matrix(probes, preflight)
    onboarding_remediation_kit = build_onboarding_remediation_kit(preflight, runtime_capability_matrix)

    decisions: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    write_observations: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []

    for probe in probes:
        decision = _decide_probe(probe, base_url=base, config=config, options=options)
        d = asdict(decision)
        decisions.append(d)
        if decision.decision == "execute_readonly":
            obs = _execute_read_probe(probe, decision, config, base, timeout_seconds)
            observations.append(obs)
            if (obs.get("verification") or {}).get("verdict") == "validated_candidate":
                findings.append(_finding_from_observation(obs, len(findings) + 1, "runtime_http_evidence_from_document_grounded_probe"))
        elif decision.decision == "execute_write_sandbox":
            probe_plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
            if decision.risk_type == "business_flow_sequence_probe" or isinstance(probe_plan.get("flow_scenario"), dict):
                obs = _execute_flow_probe(probe, decision, config, base, timeout_seconds)
            else:
                obs = _execute_write_probe(probe, decision, config, base, timeout_seconds)
            write_observations.append(obs)
            if (obs.get("verification") or {}).get("verdict") == "validated_candidate":
                findings.append(_finding_from_observation(obs, len(findings) + 1, "runtime_http_evidence_from_document_grounded_sandbox_write_probe"))

    by_decision: dict[str, int] = {}
    for d in decisions:
        by_decision[d.get("decision", "unknown")] = by_decision.get(d.get("decision", "unknown"), 0) + 1
    all_obs = observations + write_observations
    protected = sum(1 for o in all_obs if ((o.get("verification") or {}).get("verdict") == "falsified_or_protected"))
    needs_more = sum(1 for o in all_obs if ((o.get("verification") or {}).get("verdict") == "needs_more_evidence"))
    fixture_setup_count = sum(len(o.get("fixture_receipts") or []) for o in write_observations)
    fixture_cleanup_count = sum(len(o.get("cleanup_receipts") or []) for o in write_observations)
    snapshot_count = sum(len((o.get("snapshots") or {}).get("before") or []) + len((o.get("snapshots") or {}).get("after") or []) for o in write_observations)
    snapshot_observer_kinds = sorted({
        str(s.get("observer_kind"))
        for o in write_observations
        for phase in ("before", "after")
        for s in ((o.get("snapshots") or {}).get(phase) or [])
        if isinstance(s, dict) and s.get("observer_kind")
    })
    customer_delivery_index = build_customer_delivery_index(findings)
    report = {
        "engine": "grounded_probe_executor_v41_phase93z",
        "mode": "document_grounded_probe_execution",
        "strict_no_peek": True,
        "created_at": _now(),
        "project_id": plan.get("project_id"),
        "probe_plan": str(plan_path),
        "base_url_configured": bool(base),
        "execute_readonly": bool(execute_readonly),
        "allow_write_sandbox": bool(allow_write_sandbox),
        "approval_id_present": bool(approval_id),
        "governance": {
            "input_only": True,
            "oracle_files_read": False,
            "strict_document_grounding_required": os.environ.get("QUALIBUG_STRICT_PROBE_GROUNDING", "1") != "0",
            "write_probe_execution": "requires_test_environment_execution_enabled_plus_production_guard_and_document_grounding",
            "runtime_findings_require_http_evidence": True,
            "write_request_bodies_invented_by_engine": False,
            "write_request_bodies_generated_from_openapi_by_qualibug": bool(_auto_fixture_enabled(config)),
            "auto_test_data_generated_by_qualibug": bool(_auto_fixture_enabled(config)),
            "auto_fixture_setup_and_cleanup_supported": True,
            "before_after_business_invariant_auto_judgement": True,
            "snapshot_observer_planner_enabled": bool(_auto_fixture_enabled(config)),
            "phase92q_multi_observer_snapshots": True,
            "phase92r_observer_response_semantic_joiner": True,
            "phase92s_cross_observer_conservation_reconciler": True,
            "phase92t_customer_ready_evidence_packaging": True,
            "phase92u_customer_impact_triage": True,
            "phase92v_customer_delivery_index": True,
            "phase92w_reproduction_artifact_backlinks": True,
            "phase92x_fix_verification_lifecycle_loop": True,
            "phase92y_stable_finding_lifecycle_registry": True,
            "phase92z_remediation_verification_artifact": True,
            "phase93a_runtime_onboarding_preflight": True,
            "phase93b_probe_runtime_capability_matrix": True,
            "phase93c_onboarding_remediation_kit": True,
            "phase93d_runtime_execution_runbook": True,
            "phase93e_runtime_evidence_readiness_sla_gate": True,
            "phase93f_runtime_sla_execution_policy": True,
            "phase93g_sla_gap_auto_prioritizer": True,
            "phase93h_onboarding_patch_safety_validator": True,
            "phase93i_write_sandbox_approval_packet": True,
            "phase93j_commercial_handoff_bundle": True,
            "phase93k_commercial_handoff_acceptance_gate": True,
            "phase93l_handoff_secret_audit": True,
            "phase93m_handoff_archive_manifest_and_immutable_run_receipt": True,
            "phase93n_immutable_handoff_receipt_comparison": True,
            "phase93o_commercial_rerun_audit_gate": True,
            "phase93p_commercial_evidence_lineage_dashboard": True,
            "phase93q_commercial_lineage_reviewer_signoff": True,
            "phase93r_commercial_closure_acceptance_ledger": True,
            "phase93s_commercial_audit_event_stream": True,
            "phase93t_commercial_audit_export_adapters": True,
            "phase93u_commercial_audit_export_import_gate": True,
            "phase93v_commercial_external_tracker_reconciliation": True,
            "phase93w_external_tracker_closure_sync_policy": True,
            "phase93x_external_tracker_sync_payload_builder": True,
            "phase93y_external_tracker_sync_payload_gate": True,
            "phase93z_external_tracker_sync_receipt_ledger": True,
            "phase94a_business_state_machine_auto_exploration": True,
            "phase94b_multistep_business_flow_composition": True,
            "phase94c_high_value_business_mutation_probe_generation": True,
            "phase94d_concurrency_race_probe_planning": True,
            "customer_business_data_input_required": False,
            "customer_auth_input_mode": "username_password_accounts_preferred",
            "raw_tokens_required_from_customer": False,
        },
        "auth_runtime": config.get("_auth_runtime") or {},
        "summary": {
            "probe_count": len(probes),
            "original_probe_count": len(original_probes),
            "phase94_added_probe_count": int(bug_discovery_expansion.get("added_probe_count") or 0),
            "phase94_added_p0_probe_count": int(bug_discovery_expansion.get("added_p0_probe_count") or 0),
            "phase94_multistep_flow_scenario_count": int(bug_discovery_expansion.get("multistep_flow_scenario_count") or 0),
            "executed_readonly_count": len(observations),
            "executed_write_sandbox_count": len(write_observations),
            "blocked_count": by_decision.get("blocked", 0),
            "dry_run_count": by_decision.get("dry_run_only", 0),
            "validated_candidate_count": len(findings),
            "protected_count": protected,
            "needs_more_evidence_count": needs_more,
            "auto_fixture_setup_request_count": fixture_setup_count,
            "auto_fixture_cleanup_request_count": fixture_cleanup_count,
            "auto_snapshot_request_count": snapshot_count,
            "auto_snapshot_observer_kinds": snapshot_observer_kinds,
            "auto_snapshot_observer_kind_count": len(snapshot_observer_kinds),
            "semantic_joined_observer_graph_count": sum(1 for o in write_observations if (((o.get("verification") or {}).get("business_invariant_evaluation") or {}).get("semantic_observer_graph") or {}).get("engine") == "observer_response_semantic_joiner_v1_phase92r"),
            "cross_observer_conservation_checked_count": sum(1 for o in write_observations for r in ((((o.get("verification") or {}).get("business_invariant_evaluation") or {}).get("results") or [])) if isinstance(r, dict) and r.get("kind") == "cross_observer_conservation_reconciliation"),
            "customer_ready_evidence_package_count": sum(1 for f in findings if (f.get("evidence_package") or {}).get("engine") == "runtime_finding_evidence_packager_v1_phase92t"),
            "strong_evidence_finding_count": sum(1 for f in findings if f.get("evidence_grade") == "strong"),
            "critical_finding_count": sum(1 for f in findings if f.get("severity") == "critical"),
            "high_finding_count": sum(1 for f in findings if f.get("severity") == "high"),
            "by_priority": {p: sum(1 for f in findings if f.get("priority") == p) for p in sorted({str(f.get("priority")) for f in findings if f.get("priority")})},
            "by_decision": by_decision,
            "onboarding_preflight_status": preflight.get("status"),
            "onboarding_preflight_blocking_count": len(preflight.get("blocking_reasons") or []),
            "onboarding_preflight_warning_count": len(preflight.get("warning_reasons") or []),
            "ready_for_p0_p1_runtime_validation": bool(preflight.get("ready_for_p0_p1_runtime_validation")),
            "runtime_ready_probe_count": runtime_capability_matrix.get("runtime_ready_probe_count", 0),
            "runtime_degraded_probe_count": runtime_capability_matrix.get("degraded_probe_count", 0),
            "runtime_capability_blocked_probe_count": runtime_capability_matrix.get("blocked_probe_count", 0),
            "onboarding_remediation_action_count": onboarding_remediation_kit.get("action_count", 0),
            "onboarding_remediation_p0_action_count": onboarding_remediation_kit.get("p0_action_count", 0),
            "runtime_runbook_step_count": 0,
            "runtime_evidence_readiness_score": 0,
            "runtime_evidence_sla_gate_passed": False,
            "runtime_sla_must_run_count": 0,
            "runtime_sla_blocked_before_sla_count": 0,
            "runtime_sla_gap_prioritized_action_count": 0,
            "runtime_sla_estimated_score_after_top_actions": 0,
            "onboarding_patch_safety_issue_count": 0,
            "onboarding_patch_safe_to_send": False,
            "write_sandbox_approval_required": False,
            "write_sandbox_approval_ready": False,
            "commercial_handoff_status": "not_built",
            "commercial_handoff_blocker_count": 0,
            "commercial_handoff_artifact_count": 0,
            "commercial_handoff_acceptance_status": "not_built",
            "commercial_handoff_acceptance_gate_passed": False,
            "commercial_handoff_acceptance_violation_count": 0,
            "commercial_handoff_secret_audit_status": "not_built",
            "commercial_handoff_secret_audit_issue_count": 0,
            "commercial_handoff_safe_for_customer": False,
            "handoff_archive_manifest_status": "not_built",
            "handoff_archive_hashed_artifact_count": 0,
            "handoff_archive_missing_required_artifact_count": 0,
            "immutable_run_receipt_status": "not_built",
            "immutable_run_lineage_id": "",
            "handoff_receipt_comparison_status": "not_built",
            "handoff_receipt_change_count": 0,
            "handoff_receipt_lineage_match": False,
            "handoff_rerun_audit_status": "not_built",
            "handoff_rerun_closure_allowed": False,
            "handoff_rerun_audit_blocker_count": 0,
            "commercial_evidence_lineage_dashboard_status": "not_built",
            "commercial_evidence_lineage_closure_claim_state": "not_built",
            "commercial_evidence_lineage_changed_hash_count": 0,
            "commercial_lineage_reviewer_signoff_status": "not_built",
            "commercial_lineage_reviewer_signoff_required": False,
            "commercial_lineage_reviewer_signoff_item_count": 0,
            "commercial_closure_acceptance_ledger_status": "not_built",
            "commercial_closure_acceptance_ledger_entry_count": 0,
            "commercial_audit_event_stream_status": "not_built",
            "commercial_audit_event_count": 0,
            "commercial_audit_export_status": "not_built",
            "commercial_audit_jira_issue_count": 0,
            "commercial_audit_linear_issue_count": 0,
            "commercial_audit_csv_row_count": 0,
            "commercial_closure_external_tracking_key_count": 0,
            "commercial_audit_import_gate_status": "not_built",
            "commercial_audit_import_ready": False,
            "commercial_audit_import_violation_count": 0,
            "commercial_audit_import_placeholder_count": 0,
            "commercial_external_tracker_reconciliation_status": "not_built",
            "commercial_external_tracker_reconciliation_entry_count": 0,
        },
        "customer_delivery_index": customer_delivery_index,
        "bug_discovery_expansion": bug_discovery_expansion,
        "onboarding_preflight": preflight,
        "runtime_capability_matrix": runtime_capability_matrix,
        "onboarding_remediation_kit": onboarding_remediation_kit,
        "decisions": annotate_decisions_with_capability(decisions, runtime_capability_matrix),
        "observations": observations,
        "write_observations": write_observations,
        "findings": findings,
    }

    report_path = output / "grounded_probe_execution_report.json"
    md_path = output / "grounded_probe_execution_report.md"
    ps1_path = output / "grounded_probe_repro.ps1"
    pytest_path = output / "grounded_probe_regression_pytest.py"
    remediation_json_path = output / "grounded_probe_remediation_verification.json"
    remediation_md_path = output / "grounded_probe_remediation_verification.md"
    preflight_path = output / "grounded_probe_onboarding_preflight.json"
    capability_matrix_path = output / "grounded_probe_runtime_capability_matrix.json"
    onboarding_remediation_json_path = output / "grounded_probe_onboarding_remediation_kit.json"
    onboarding_remediation_md_path = output / "grounded_probe_onboarding_remediation_kit.md"
    runtime_runbook_json_path = output / "grounded_probe_runtime_execution_runbook.json"
    runtime_runbook_md_path = output / "grounded_probe_runtime_execution_runbook.md"
    readiness_sla_json_path = output / "grounded_probe_runtime_evidence_readiness_sla_gate.json"
    readiness_sla_md_path = output / "grounded_probe_runtime_evidence_readiness_sla_gate.md"
    runtime_sla_policy_json_path = output / "grounded_probe_runtime_sla_execution_policy.json"
    runtime_sla_policy_md_path = output / "grounded_probe_runtime_sla_execution_policy.md"
    runtime_sla_gap_json_path = output / "grounded_probe_runtime_sla_gap_prioritizer.json"
    runtime_sla_gap_md_path = output / "grounded_probe_runtime_sla_gap_prioritizer.md"
    onboarding_patch_safety_json_path = output / "grounded_probe_onboarding_patch_safety_validation.json"
    onboarding_patch_safety_md_path = output / "grounded_probe_onboarding_patch_safety_validation.md"
    write_sandbox_approval_json_path = output / "grounded_probe_write_sandbox_approval_packet.json"
    write_sandbox_approval_md_path = output / "grounded_probe_write_sandbox_approval_packet.md"
    commercial_handoff_json_path = output / "grounded_probe_commercial_handoff_bundle.json"
    commercial_handoff_md_path = output / "grounded_probe_commercial_handoff_bundle.md"
    commercial_handoff_acceptance_json_path = output / "grounded_probe_commercial_handoff_acceptance_gate.json"
    commercial_handoff_acceptance_md_path = output / "grounded_probe_commercial_handoff_acceptance_gate.md"
    handoff_secret_audit_json_path = output / "grounded_probe_commercial_handoff_secret_audit.json"
    handoff_secret_audit_md_path = output / "grounded_probe_commercial_handoff_secret_audit.md"
    handoff_archive_manifest_json_path = output / "grounded_probe_handoff_archive_manifest.json"
    handoff_archive_manifest_md_path = output / "grounded_probe_handoff_archive_manifest.md"
    immutable_run_receipt_json_path = output / "grounded_probe_immutable_run_receipt.json"
    immutable_run_receipt_md_path = output / "grounded_probe_immutable_run_receipt.md"
    handoff_receipt_comparison_json_path = output / "grounded_probe_handoff_receipt_comparison.json"
    handoff_receipt_comparison_md_path = output / "grounded_probe_handoff_receipt_comparison.md"
    handoff_rerun_audit_gate_json_path = output / "grounded_probe_handoff_rerun_audit_gate.json"
    handoff_rerun_audit_gate_md_path = output / "grounded_probe_handoff_rerun_audit_gate.md"
    commercial_evidence_lineage_json_path = output / "grounded_probe_commercial_evidence_lineage_dashboard.json"
    commercial_evidence_lineage_md_path = output / "grounded_probe_commercial_evidence_lineage_dashboard.md"
    commercial_lineage_signoff_json_path = output / "grounded_probe_commercial_lineage_reviewer_signoff_packet.json"
    commercial_lineage_signoff_md_path = output / "grounded_probe_commercial_lineage_reviewer_signoff_packet.md"
    commercial_closure_ledger_json_path = output / "grounded_probe_commercial_closure_acceptance_ledger.json"
    commercial_closure_ledger_md_path = output / "grounded_probe_commercial_closure_acceptance_ledger.md"
    commercial_audit_event_stream_json_path = output / "grounded_probe_commercial_audit_event_stream.json"
    commercial_audit_event_stream_md_path = output / "grounded_probe_commercial_audit_event_stream.md"
    commercial_audit_exports_json_path = output / "grounded_probe_commercial_audit_exports.json"
    commercial_audit_exports_md_path = output / "grounded_probe_commercial_audit_exports.md"
    commercial_audit_ledger_csv_path = output / "grounded_probe_commercial_audit_ledger.csv"
    jira_issue_import_json_path = output / "grounded_probe_jira_issue_import.json"
    linear_issue_import_json_path = output / "grounded_probe_linear_issue_import.json"
    reviewer_packet_export_md_path = output / "grounded_probe_reviewer_packet_export.md"
    commercial_audit_import_gate_json_path = output / "grounded_probe_commercial_audit_import_gate.json"
    commercial_audit_import_gate_md_path = output / "grounded_probe_commercial_audit_import_gate.md"
    commercial_external_tracker_reconciliation_json_path = output / "grounded_probe_commercial_external_tracker_reconciliation.json"
    commercial_external_tracker_reconciliation_md_path = output / "grounded_probe_commercial_external_tracker_reconciliation.md"
    external_tracker_closure_sync_policy_json_path = output / "grounded_probe_external_tracker_closure_sync_policy.json"
    external_tracker_closure_sync_policy_md_path = output / "grounded_probe_external_tracker_closure_sync_policy.md"
    external_tracker_sync_payloads_json_path = output / "grounded_probe_external_tracker_sync_payloads.json"
    external_tracker_sync_payloads_md_path = output / "grounded_probe_external_tracker_sync_payloads.md"
    external_tracker_sync_payload_gate_json_path = output / "grounded_probe_external_tracker_sync_payload_gate.json"
    external_tracker_sync_payload_gate_md_path = output / "grounded_probe_external_tracker_sync_payload_gate.md"
    external_tracker_sync_receipt_ledger_json_path = output / "grounded_probe_external_tracker_sync_receipt_ledger.json"
    external_tracker_sync_receipt_ledger_md_path = output / "grounded_probe_external_tracker_sync_receipt_ledger.md"
    bug_discovery_expansion_path = output / "grounded_probe_phase94_bug_discovery_expansion.json"
    report["outputs"] = {
        "execution_report": str(report_path),
        "execution_report_md": str(md_path),
        "repro_ps1": str(ps1_path),
        "regression_pytest": str(pytest_path),
        "remediation_verification_json": str(remediation_json_path),
        "remediation_verification_md": str(remediation_md_path),
        "onboarding_preflight_json": str(preflight_path),
        "runtime_capability_matrix_json": str(capability_matrix_path),
        "onboarding_remediation_kit_json": str(onboarding_remediation_json_path),
        "onboarding_remediation_kit_md": str(onboarding_remediation_md_path),
        "runtime_execution_runbook_json": str(runtime_runbook_json_path),
        "runtime_execution_runbook_md": str(runtime_runbook_md_path),
        "runtime_evidence_readiness_sla_gate_json": str(readiness_sla_json_path),
        "runtime_evidence_readiness_sla_gate_md": str(readiness_sla_md_path),
        "runtime_sla_execution_policy_json": str(runtime_sla_policy_json_path),
        "runtime_sla_execution_policy_md": str(runtime_sla_policy_md_path),
        "runtime_sla_gap_prioritizer_json": str(runtime_sla_gap_json_path),
        "runtime_sla_gap_prioritizer_md": str(runtime_sla_gap_md_path),
        "onboarding_patch_safety_validation_json": str(onboarding_patch_safety_json_path),
        "onboarding_patch_safety_validation_md": str(onboarding_patch_safety_md_path),
        "write_sandbox_approval_packet_json": str(write_sandbox_approval_json_path),
        "write_sandbox_approval_packet_md": str(write_sandbox_approval_md_path),
        "commercial_handoff_bundle_json": str(commercial_handoff_json_path),
        "commercial_handoff_bundle_md": str(commercial_handoff_md_path),
        "commercial_handoff_acceptance_gate_json": str(commercial_handoff_acceptance_json_path),
        "commercial_handoff_acceptance_gate_md": str(commercial_handoff_acceptance_md_path),
        "commercial_handoff_secret_audit_json": str(handoff_secret_audit_json_path),
        "commercial_handoff_secret_audit_md": str(handoff_secret_audit_md_path),
        "handoff_archive_manifest_json": str(handoff_archive_manifest_json_path),
        "handoff_archive_manifest_md": str(handoff_archive_manifest_md_path),
        "immutable_run_receipt_json": str(immutable_run_receipt_json_path),
        "immutable_run_receipt_md": str(immutable_run_receipt_md_path),
        "handoff_receipt_comparison_json": str(handoff_receipt_comparison_json_path),
        "handoff_receipt_comparison_md": str(handoff_receipt_comparison_md_path),
        "handoff_rerun_audit_gate_json": str(handoff_rerun_audit_gate_json_path),
        "handoff_rerun_audit_gate_md": str(handoff_rerun_audit_gate_md_path),
        "commercial_evidence_lineage_dashboard_json": str(commercial_evidence_lineage_json_path),
        "commercial_evidence_lineage_dashboard_md": str(commercial_evidence_lineage_md_path),
        "commercial_lineage_reviewer_signoff_packet_json": str(commercial_lineage_signoff_json_path),
        "commercial_lineage_reviewer_signoff_packet_md": str(commercial_lineage_signoff_md_path),
        "commercial_closure_acceptance_ledger_json": str(commercial_closure_ledger_json_path),
        "commercial_closure_acceptance_ledger_md": str(commercial_closure_ledger_md_path),
        "commercial_audit_event_stream_json": str(commercial_audit_event_stream_json_path),
        "commercial_audit_event_stream_md": str(commercial_audit_event_stream_md_path),
        "commercial_audit_exports_json": str(commercial_audit_exports_json_path),
        "commercial_audit_exports_md": str(commercial_audit_exports_md_path),
        "commercial_audit_ledger_csv": str(commercial_audit_ledger_csv_path),
        "jira_issue_import_json": str(jira_issue_import_json_path),
        "linear_issue_import_json": str(linear_issue_import_json_path),
        "reviewer_packet_export_md": str(reviewer_packet_export_md_path),
        "commercial_audit_import_gate_json": str(commercial_audit_import_gate_json_path),
        "commercial_audit_import_gate_md": str(commercial_audit_import_gate_md_path),
        "commercial_external_tracker_reconciliation_json": str(commercial_external_tracker_reconciliation_json_path),
        "commercial_external_tracker_reconciliation_md": str(commercial_external_tracker_reconciliation_md_path),
        "external_tracker_closure_sync_policy_json": str(external_tracker_closure_sync_policy_json_path),
        "external_tracker_closure_sync_policy_md": str(external_tracker_closure_sync_policy_md_path),
        "external_tracker_sync_payloads_json": str(external_tracker_sync_payloads_json_path),
        "external_tracker_sync_payloads_md": str(external_tracker_sync_payloads_md_path),
        "external_tracker_sync_payload_gate_json": str(external_tracker_sync_payload_gate_json_path),
        "external_tracker_sync_payload_gate_md": str(external_tracker_sync_payload_gate_md_path),
        "external_tracker_sync_receipt_ledger_json": str(external_tracker_sync_receipt_ledger_json_path),
        "external_tracker_sync_receipt_ledger_md": str(external_tracker_sync_receipt_ledger_md_path),
        "phase94_bug_discovery_expansion_json": str(bug_discovery_expansion_path),
    }
    _write_json(bug_discovery_expansion_path, bug_discovery_expansion)
    _write_json(preflight_path, preflight)
    _write_json(capability_matrix_path, runtime_capability_matrix)
    _write_json(onboarding_remediation_json_path, onboarding_remediation_kit)
    onboarding_remediation_md_path.write_text(render_onboarding_remediation_markdown(onboarding_remediation_kit), encoding="utf-8")
    report["runtime_execution_runbook"] = build_runtime_execution_runbook(report)
    report["summary"]["runtime_runbook_step_count"] = len(report["runtime_execution_runbook"].get("steps") or [])
    _write_json(runtime_runbook_json_path, report["runtime_execution_runbook"])
    runtime_runbook_md_path.write_text(render_runtime_execution_runbook_markdown(report["runtime_execution_runbook"]), encoding="utf-8")
    report["runtime_evidence_readiness_sla_gate"] = build_runtime_evidence_readiness_sla_gate(report)
    report["summary"]["runtime_evidence_readiness_score"] = report["runtime_evidence_readiness_sla_gate"].get("commercial_readiness_score", 0)
    report["summary"]["runtime_evidence_sla_gate_passed"] = bool(report["runtime_evidence_readiness_sla_gate"].get("sla_gate_passed"))
    _write_json(readiness_sla_json_path, report["runtime_evidence_readiness_sla_gate"])
    readiness_sla_md_path.write_text(render_runtime_evidence_readiness_markdown(report["runtime_evidence_readiness_sla_gate"]), encoding="utf-8")
    report["runtime_sla_execution_policy"] = build_runtime_sla_execution_policy(report)
    report["summary"]["runtime_sla_must_run_count"] = report["runtime_sla_execution_policy"].get("must_run_for_sla_count", 0)
    report["summary"]["runtime_sla_blocked_before_sla_count"] = report["runtime_sla_execution_policy"].get("blocked_before_sla_count", 0)
    _write_json(runtime_sla_policy_json_path, report["runtime_sla_execution_policy"])
    runtime_sla_policy_md_path.write_text(render_runtime_sla_execution_policy_markdown(report["runtime_sla_execution_policy"]), encoding="utf-8")
    report["runtime_sla_gap_prioritizer"] = build_runtime_sla_gap_prioritizer(report)
    report["summary"]["runtime_sla_gap_prioritized_action_count"] = report["runtime_sla_gap_prioritizer"].get("action_count", 0)
    report["summary"]["runtime_sla_estimated_score_after_top_actions"] = report["runtime_sla_gap_prioritizer"].get("estimated_readiness_score_after_top_actions", 0)
    _write_json(runtime_sla_gap_json_path, report["runtime_sla_gap_prioritizer"])
    runtime_sla_gap_md_path.write_text(render_runtime_sla_gap_prioritizer_markdown(report["runtime_sla_gap_prioritizer"]), encoding="utf-8")
    report["onboarding_patch_safety_validation"] = validate_onboarding_patch_safety(report)
    report["summary"]["onboarding_patch_safety_issue_count"] = report["onboarding_patch_safety_validation"].get("issue_count", 0)
    report["summary"]["onboarding_patch_safe_to_send"] = bool(report["onboarding_patch_safety_validation"].get("safe_to_send_to_customer"))
    _write_json(onboarding_patch_safety_json_path, report["onboarding_patch_safety_validation"])
    onboarding_patch_safety_md_path.write_text(render_onboarding_patch_safety_markdown(report["onboarding_patch_safety_validation"]), encoding="utf-8")
    report["write_sandbox_approval_packet"] = build_write_sandbox_approval_packet(report)
    report["summary"]["write_sandbox_approval_required"] = bool(report["write_sandbox_approval_packet"].get("write_approval_required"))
    report["summary"]["write_sandbox_approval_ready"] = bool(report["write_sandbox_approval_packet"].get("ready_for_customer_approval"))
    _write_json(write_sandbox_approval_json_path, report["write_sandbox_approval_packet"])
    write_sandbox_approval_md_path.write_text(render_write_sandbox_approval_markdown(report["write_sandbox_approval_packet"]), encoding="utf-8")
    report = link_reproduction_assets(report)
    previous_report = _load_previous_execution_report(config)
    report = attach_fix_verification_loop(report, previous_report=previous_report)
    report = apply_lifecycle_registry(report, previous_report=previous_report)
    fix_index = report.get("fix_verification_loop_index") or {}
    lifecycle_index = report.get("finding_lifecycle_registry") or {}
    report["summary"]["fix_verification_required_count"] = fix_index.get("verification_required_finding_count", 0)
    report["summary"]["closed_by_rerun_count"] = fix_index.get("closed_by_rerun_count", 0)
    report["summary"]["still_open_after_rerun_count"] = fix_index.get("still_open_after_rerun_count", 0)
    report["summary"]["reopened_finding_count"] = fix_index.get("reopened_finding_count", 0)
    report["summary"]["stable_lifecycle_match_count"] = lifecycle_index.get("stable_match_count", 0)
    remediation_artifact = build_remediation_verification_artifact(report)
    report["remediation_verification_artifact"] = remediation_artifact
    report["summary"]["remediation_work_item_count"] = remediation_artifact.get("work_item_count", 0)
    _write_json(remediation_json_path, remediation_artifact)
    remediation_md_path.write_text(render_remediation_markdown(remediation_artifact), encoding="utf-8")
    report["commercial_handoff_bundle"] = build_commercial_handoff_bundle(report)
    report["summary"]["commercial_handoff_status"] = report["commercial_handoff_bundle"].get("status")
    report["summary"]["commercial_handoff_blocker_count"] = (report["commercial_handoff_bundle"].get("executive_summary") or {}).get("handoff_blocker_count", 0)
    report["summary"]["commercial_handoff_artifact_count"] = len(report["commercial_handoff_bundle"].get("artifact_manifest") or [])
    _write_json(commercial_handoff_json_path, report["commercial_handoff_bundle"])
    commercial_handoff_md_path.write_text(render_commercial_handoff_markdown(report["commercial_handoff_bundle"]), encoding="utf-8")
    report["commercial_handoff_acceptance_gate"] = validate_commercial_handoff_acceptance(report)
    report["summary"]["commercial_handoff_acceptance_status"] = report["commercial_handoff_acceptance_gate"].get("status")
    report["summary"]["commercial_handoff_acceptance_gate_passed"] = bool(report["commercial_handoff_acceptance_gate"].get("acceptance_gate_passed"))
    report["summary"]["commercial_handoff_acceptance_violation_count"] = report["commercial_handoff_acceptance_gate"].get("violation_count", 0)
    _write_json(commercial_handoff_acceptance_json_path, report["commercial_handoff_acceptance_gate"])
    commercial_handoff_acceptance_md_path.write_text(render_commercial_handoff_acceptance_markdown(report["commercial_handoff_acceptance_gate"]), encoding="utf-8")
    report["commercial_handoff_secret_audit"] = audit_commercial_handoff_secrets(report)
    report["summary"]["commercial_handoff_secret_audit_status"] = report["commercial_handoff_secret_audit"].get("status")
    report["summary"]["commercial_handoff_secret_audit_issue_count"] = report["commercial_handoff_secret_audit"].get("issue_count", 0)
    report["summary"]["commercial_handoff_safe_for_customer"] = bool(report["commercial_handoff_secret_audit"].get("safe_for_customer_handoff"))
    _write_json(handoff_secret_audit_json_path, report["commercial_handoff_secret_audit"])
    handoff_secret_audit_md_path.write_text(render_handoff_secret_audit_markdown(report["commercial_handoff_secret_audit"]), encoding="utf-8")
    report["handoff_archive_manifest"] = build_handoff_archive_manifest(report)
    report["immutable_run_receipt"] = report["handoff_archive_manifest"].get("immutable_run_receipt") or {}
    report["summary"]["handoff_archive_manifest_status"] = report["handoff_archive_manifest"].get("status")
    report["summary"]["handoff_archive_hashed_artifact_count"] = report["handoff_archive_manifest"].get("hashed_artifact_count", 0)
    report["summary"]["handoff_archive_missing_required_artifact_count"] = report["handoff_archive_manifest"].get("missing_required_artifact_count", 0)
    report["summary"]["immutable_run_receipt_status"] = report["immutable_run_receipt"].get("receipt_status")
    report["summary"]["immutable_run_lineage_id"] = report["immutable_run_receipt"].get("run_lineage_id", "")
    _write_json(handoff_archive_manifest_json_path, report["handoff_archive_manifest"])
    handoff_archive_manifest_md_path.write_text(render_handoff_archive_manifest_markdown(report["handoff_archive_manifest"]), encoding="utf-8")
    _write_json(immutable_run_receipt_json_path, report["immutable_run_receipt"])
    immutable_run_receipt_md_path.write_text(render_immutable_run_receipt_markdown(report["immutable_run_receipt"]), encoding="utf-8")
    report["handoff_receipt_comparison"] = compare_immutable_run_receipts(report, previous_report=previous_report)
    report["summary"]["handoff_receipt_comparison_status"] = report["handoff_receipt_comparison"].get("status")
    report["summary"]["handoff_receipt_change_count"] = report["handoff_receipt_comparison"].get("change_count", 0)
    report["summary"]["handoff_receipt_lineage_match"] = bool(report["handoff_receipt_comparison"].get("lineage_match"))
    _write_json(handoff_receipt_comparison_json_path, report["handoff_receipt_comparison"])
    handoff_receipt_comparison_md_path.write_text(render_handoff_receipt_comparison_markdown(report["handoff_receipt_comparison"]), encoding="utf-8")
    report["handoff_rerun_audit_gate"] = build_handoff_rerun_audit_gate(report)
    report["summary"]["handoff_rerun_audit_status"] = report["handoff_rerun_audit_gate"].get("status")
    report["summary"]["handoff_rerun_closure_allowed"] = bool(report["handoff_rerun_audit_gate"].get("closure_verification_allowed"))
    report["summary"]["handoff_rerun_audit_blocker_count"] = report["handoff_rerun_audit_gate"].get("blocker_count", 0)
    _write_json(handoff_rerun_audit_gate_json_path, report["handoff_rerun_audit_gate"])
    handoff_rerun_audit_gate_md_path.write_text(render_handoff_rerun_audit_gate_markdown(report["handoff_rerun_audit_gate"]), encoding="utf-8")
    report["commercial_evidence_lineage_dashboard"] = build_commercial_evidence_lineage_dashboard(report)
    report["summary"]["commercial_evidence_lineage_dashboard_status"] = report["commercial_evidence_lineage_dashboard"].get("status")
    report["summary"]["commercial_evidence_lineage_closure_claim_state"] = report["commercial_evidence_lineage_dashboard"].get("closure_claim_state")
    report["summary"]["commercial_evidence_lineage_changed_hash_count"] = report["commercial_evidence_lineage_dashboard"].get("changed_or_missing_hash_count", 0)
    _write_json(commercial_evidence_lineage_json_path, report["commercial_evidence_lineage_dashboard"])
    commercial_evidence_lineage_md_path.write_text(render_commercial_evidence_lineage_dashboard_markdown(report["commercial_evidence_lineage_dashboard"]), encoding="utf-8")
    report["commercial_lineage_reviewer_signoff_packet"] = build_commercial_lineage_reviewer_signoff_packet(report)
    report["summary"]["commercial_lineage_reviewer_signoff_status"] = report["commercial_lineage_reviewer_signoff_packet"].get("status")
    report["summary"]["commercial_lineage_reviewer_signoff_required"] = bool(report["commercial_lineage_reviewer_signoff_packet"].get("signoff_required"))
    report["summary"]["commercial_lineage_reviewer_signoff_item_count"] = report["commercial_lineage_reviewer_signoff_packet"].get("signoff_item_count", 0)
    _write_json(commercial_lineage_signoff_json_path, report["commercial_lineage_reviewer_signoff_packet"])
    commercial_lineage_signoff_md_path.write_text(render_commercial_lineage_reviewer_signoff_markdown(report["commercial_lineage_reviewer_signoff_packet"]), encoding="utf-8")
    report["commercial_closure_acceptance_ledger"] = build_commercial_closure_acceptance_ledger(report)
    report["summary"]["commercial_closure_acceptance_ledger_status"] = report["commercial_closure_acceptance_ledger"].get("status")
    report["summary"]["commercial_closure_acceptance_ledger_entry_count"] = report["commercial_closure_acceptance_ledger"].get("ledger_entry_count", 0)
    _write_json(commercial_closure_ledger_json_path, report["commercial_closure_acceptance_ledger"])
    commercial_closure_ledger_md_path.write_text(render_commercial_closure_acceptance_ledger_markdown(report["commercial_closure_acceptance_ledger"]), encoding="utf-8")
    report["commercial_audit_event_stream"] = build_commercial_audit_event_stream(report)
    report["summary"]["commercial_audit_event_stream_status"] = report["commercial_audit_event_stream"].get("status")
    report["summary"]["commercial_audit_event_count"] = report["commercial_audit_event_stream"].get("event_count", 0)
    _write_json(commercial_audit_event_stream_json_path, report["commercial_audit_event_stream"])
    commercial_audit_event_stream_md_path.write_text(render_commercial_audit_event_stream_markdown(report["commercial_audit_event_stream"]), encoding="utf-8")
    report["commercial_audit_export_adapters"] = build_commercial_audit_export_adapters(report)
    report["summary"]["commercial_audit_export_status"] = report["commercial_audit_export_adapters"].get("status")
    report["summary"]["commercial_audit_jira_issue_count"] = report["commercial_audit_export_adapters"].get("jira_issue_count", 0)
    report["summary"]["commercial_audit_linear_issue_count"] = report["commercial_audit_export_adapters"].get("linear_issue_count", 0)
    report["summary"]["commercial_audit_csv_row_count"] = report["commercial_audit_export_adapters"].get("csv_row_count", 0)
    report["summary"]["commercial_closure_external_tracking_key_count"] = report["commercial_audit_export_adapters"].get("closure_tracking_key_count", 0)
    _write_json(commercial_audit_exports_json_path, report["commercial_audit_export_adapters"])
    commercial_audit_exports_md_path.write_text(render_commercial_audit_exports_markdown(report["commercial_audit_export_adapters"]), encoding="utf-8")
    commercial_audit_ledger_csv_path.write_text(render_csv_audit_ledger(report["commercial_audit_export_adapters"]), encoding="utf-8")
    _write_json(jira_issue_import_json_path, report["commercial_audit_export_adapters"].get("jira_issue_import") or [])
    _write_json(linear_issue_import_json_path, report["commercial_audit_export_adapters"].get("linear_issue_import") or [])
    reviewer_packet_export_md_path.write_text(str(report["commercial_audit_export_adapters"].get("reviewer_packet_markdown") or ""), encoding="utf-8")
    report["commercial_audit_export_import_gate"] = build_commercial_audit_export_import_gate(report)
    report["summary"]["commercial_audit_import_gate_status"] = report["commercial_audit_export_import_gate"].get("status")
    report["summary"]["commercial_audit_import_ready"] = bool(report["commercial_audit_export_import_gate"].get("import_ready"))
    report["summary"]["commercial_audit_import_violation_count"] = report["commercial_audit_export_import_gate"].get("violation_count", 0)
    report["summary"]["commercial_audit_import_placeholder_count"] = report["commercial_audit_export_import_gate"].get("placeholder_count", 0)
    _write_json(commercial_audit_import_gate_json_path, report["commercial_audit_export_import_gate"])
    commercial_audit_import_gate_md_path.write_text(render_commercial_audit_import_gate_markdown(report["commercial_audit_export_import_gate"]), encoding="utf-8")
    report["commercial_external_tracker_reconciliation"] = build_commercial_external_tracker_reconciliation(report)
    report["summary"]["commercial_external_tracker_reconciliation_status"] = report["commercial_external_tracker_reconciliation"].get("status")
    report["summary"]["commercial_external_tracker_reconciliation_entry_count"] = report["commercial_external_tracker_reconciliation"].get("entry_count", 0)
    _write_json(commercial_external_tracker_reconciliation_json_path, report["commercial_external_tracker_reconciliation"])
    commercial_external_tracker_reconciliation_md_path.write_text(render_commercial_external_tracker_reconciliation_markdown(report["commercial_external_tracker_reconciliation"]), encoding="utf-8")
    report["external_tracker_closure_sync_policy"] = build_external_tracker_closure_sync_policy(report)
    report["summary"]["external_tracker_closure_sync_status"] = report["external_tracker_closure_sync_policy"].get("status")
    report["summary"]["external_tracker_closure_sync_policy_count"] = report["external_tracker_closure_sync_policy"].get("sync_policy_count", 0)
    report["summary"]["external_tracker_closure_sync_ready_count"] = (report["external_tracker_closure_sync_policy"].get("status_counts") or {}).get("sync_ready_to_mark_resolved", 0)
    _write_json(external_tracker_closure_sync_policy_json_path, report["external_tracker_closure_sync_policy"])
    external_tracker_closure_sync_policy_md_path.write_text(render_external_tracker_closure_sync_policy_markdown(report["external_tracker_closure_sync_policy"]), encoding="utf-8")
    report["external_tracker_sync_payloads"] = build_external_tracker_sync_payloads(report)
    report["summary"]["external_tracker_sync_payload_status"] = report["external_tracker_sync_payloads"].get("status")
    report["summary"]["external_tracker_jira_transition_payload_count"] = report["external_tracker_sync_payloads"].get("jira_transition_payload_count", 0)
    report["summary"]["external_tracker_linear_update_payload_count"] = report["external_tracker_sync_payloads"].get("linear_update_payload_count", 0)
    report["summary"]["external_tracker_sync_hold_item_count"] = report["external_tracker_sync_payloads"].get("hold_item_count", 0)
    _write_json(external_tracker_sync_payloads_json_path, report["external_tracker_sync_payloads"])
    external_tracker_sync_payloads_md_path.write_text(render_external_tracker_sync_payloads_markdown(report["external_tracker_sync_payloads"]), encoding="utf-8")
    report["external_tracker_sync_payload_gate"] = validate_external_tracker_sync_payloads(report)
    report["summary"]["external_tracker_sync_payload_gate_status"] = report["external_tracker_sync_payload_gate"].get("status")
    report["summary"]["external_tracker_sync_payload_import_ready"] = bool(report["external_tracker_sync_payload_gate"].get("payload_import_ready"))
    report["summary"]["external_tracker_sync_payload_gate_violation_count"] = report["external_tracker_sync_payload_gate"].get("violation_count", 0)
    _write_json(external_tracker_sync_payload_gate_json_path, report["external_tracker_sync_payload_gate"])
    external_tracker_sync_payload_gate_md_path.write_text(render_external_tracker_sync_payload_gate_markdown(report["external_tracker_sync_payload_gate"]), encoding="utf-8")
    report["external_tracker_sync_receipt_ledger"] = build_external_tracker_sync_receipt_ledger(report)
    report["summary"]["external_tracker_sync_receipt_status"] = report["external_tracker_sync_receipt_ledger"].get("status")
    report["summary"]["external_tracker_sync_receipt_entry_count"] = report["external_tracker_sync_receipt_ledger"].get("sync_receipt_entry_count", 0)
    report["summary"]["external_tracker_sync_confirmed_count"] = (report["external_tracker_sync_receipt_ledger"].get("receipt_status_counts") or {}).get("sync_applied_confirmed", 0)
    _write_json(external_tracker_sync_receipt_ledger_json_path, report["external_tracker_sync_receipt_ledger"])
    external_tracker_sync_receipt_ledger_md_path.write_text(render_external_tracker_sync_receipt_ledger_markdown(report["external_tracker_sync_receipt_ledger"]), encoding="utf-8")
    _write_json(report_path, report)
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    ps1_path.write_text(_render_repro_ps1(report), encoding="utf-8")
    pytest_path.write_text(_render_pytest(report), encoding="utf-8")
    return report
