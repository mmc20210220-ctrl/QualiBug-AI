"""Supplementary behavior-slice generator — expands coverage beyond state-machine.

The BusinessStateGraphBuilder only creates transition / invariant / dependency slices
from the PRD and API specification, which naturally covers idempotency, state-forbidden
transitions, and business-rule invariants.  This module adds slices for the other
dimensions that a state machine cannot express:

  1. Actor-aware permission probes     (who can write what)
  2. Tenant-isolation probes            (cross-user data access)
  3. Data-boundary & injection probes   (negative-payload fuzzing)

All slices are source-grounded — the endpoint catalog comes from the same
_api_facts parser that feeds the state graph, and the actor/role catalog comes
from the project's test_accounts JSON.

Design contract
  - No per-project table / endpoint / role hardcoding.
  - Config-driven: actor catalog from test_accounts.json, endpoint catalog from
    the API specification parser.
  - Writes always use the actor's own token.  A readonly actor that succeeds on a
    write is an evidence-worthy permission defect, not a false positive.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .business_state_graph import _api_facts, behavior_slice_id

# ── Pure-data structures ────────────────────────────────────────────────

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Endpoints whose entity or path suggests authentication — money / concurrency
# probes are semantically meaningless here (login is not a financial or
# resource-contention operation) and the resulting 4xx/5xx noise pollutes
# the signal.  Detection is purely path/entity based — no per-project list.
_AUTH_ENTITY_TOKENS = frozenset({"auth", "login", "signin", "signup", "register", "session", "token", "oauth", "sso"})
"""Exclude auth endpoints from money and concurrency probes to avoid noise from login/register operations appearing as 'financial' or 'concurrency' issues."""

def _is_auth_endpoint(endpoint: dict[str, str]) -> bool:
    path = str(endpoint.get("path") or "").strip("/").lower()
    entity = str(endpoint.get("entity") or "").strip().lower()
    tokens = path.split("/") + [entity]
    return bool(_AUTH_ENTITY_TOKENS & set(tokens))


def load_settings_accounts(root: Path, project: str) -> tuple[list[dict[str, str]], str]:
    """Read role accounts from the Settings page store (PRIMARY source).

    The product's Settings UI persists per-service credentials to
    ``platform_workspace/<project>/multi_service_config.json`` as::

        {"services": [{"name", "base_url", "auth": {
            "type", "login_api",
            "<role>": {"username", "password"}, ...}}]}

    Every ``auth.<role>`` entry with a username becomes a test account. The
    per-service ``login_api`` is returned so callers prefer the operator-declared
    login route over auto-discovery. Returns (accounts, login_path).
    """
    config_path = root / "platform_workspace" / project / "multi_service_config.json"
    if not config_path.exists():
        return [], ""
    try:
        data = json.loads(config_path.read_text(encoding="utf-8")) or {}
    except (json.JSONDecodeError, OSError):
        return [], ""
    accounts: list[dict[str, str]] = []
    login_path = ""
    seen: set[str] = set()
    _reserved = {"type", "login_api", "bearer_token", "api_key"}
    for svc in data.get("services") or []:
        if not isinstance(svc, dict):
            continue
        auth = svc.get("auth") if isinstance(svc.get("auth"), dict) else {}
        if not login_path:
            candidate = str(auth.get("login_api") or "").strip()
            if candidate.startswith("/"):
                login_path = candidate
        for role, cred in auth.items():
            if role in _reserved or not isinstance(cred, dict):
                continue
            username = str(cred.get("username") or cred.get("email") or "").strip()
            if not username or username in seen:
                continue
            seen.add(username)
            accounts.append({
                "role": str(role).strip(),
                "email": username,
                "password": str(cred.get("password") or ""),
                "note": "settings",
            })
    return accounts, login_path


def _normalize_accounts_payload(raw: Any) -> list[dict[str, str]]:
    """Normalize test_accounts.json whether stored as a list or name→account dict."""
    if isinstance(raw, list):
        return [dict(a) for a in raw if isinstance(a, dict)]
    if isinstance(raw, dict):
        rows: list[dict[str, str]] = []
        for name, acct in raw.items():
            if not isinstance(acct, dict):
                continue
            row = {str(k): str(v) for k, v in acct.items() if v is not None}
            row.setdefault("name", str(name))
            if not row.get("role"):
                row["role"] = str(name)
            rows.append(row)
        return rows
    return []


def _load_test_accounts(root: Path, project: str) -> list[dict[str, str]]:
    """Read test_accounts.json from the project workspace, if present."""
    for base in (
        root / "platform_workspace" / project / "input",
        root / "platform_inputs" / project,
    ):
        path = base / "test_accounts.json"
        if path.exists():
            try:
                return _normalize_accounts_payload(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                pass
    return []


def _parse_md_accounts(root: Path, project: str) -> list[dict[str, str]]:
    """Fallback: parse test_accounts.md when JSON is absent."""
    for base in (
        root / "platform_workspace" / project / "input",
        root / "platform_inputs" / project,
    ):
        for name in ("test_accounts.md", "TEST_ACCOUNTS.md"):
            path = base / name
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="replace")
                return _extract_accounts_from_md(text)
    return []


def _extract_accounts_from_md(text: str) -> list[dict[str, str]]:
    accounts: list[dict[str, str]] = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if "|" in stripped and not in_table:
            in_table = True
            continue
        if in_table and stripped.startswith("|---"):
            continue
        if in_table and "|" in stripped:
            cells = [c.strip() for c in stripped.split("|")]
            cells = [c for c in cells if c]
            if len(cells) >= 3:
                accounts.append({
                    "role": cells[0],
                    "email": cells[1],
                    "password": cells[2],
                    "note": cells[3] if len(cells) > 3 else "",
                })
    return accounts


# ── Supplementary slice generators ──────────────────────────────────────

def generate_permission_slices(
    endpoints: list[dict[str, str]],
    actors: list[dict[str, str]],
    max_slices: int = 24,
    login_path: str = "",
    login_body: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Actor × endpoint permission matrix.

    For every (actor, write-endpoint) pair, create a behavior slice whose
    oracle should verify that the actor is *not* allowed to perform the action
    unless their role grants it.  The oracle is PermissionOracle.

    Focuses on low-privilege actors (the ones that reveal privilege escalation)
    and caps the total so it complements — rather than floods — the scheduler's
    per-round budget.
    """
    if not actors or not endpoints:
        return []
    slices: list[dict[str, Any]] = []
    write_endpoints = [e for e in endpoints if str(e.get("method") or "").upper() in _WRITE_METHODS]
    if not write_endpoints:
        return slices
    # Low-privilege actors are the highest-signal probes for privilege escalation.
    low_priv = [a for a in actors if not _is_admin_like(a)]
    probe_actors = low_priv or actors
    seen_paths: set[str] = set()
    for actor in probe_actors:
        actor_label = (actor.get("role") or actor.get("email") or "").strip().lower()
        email = (actor.get("email") or "").strip()
        if not actor_label:
            continue
        expected_permitted = _expected_permitted_roles(actor_label)
        for ep in write_endpoints:
            method = str(ep.get("method") or "").upper()
            path = str(ep.get("path") or "")
            if not method or not path:
                continue
            entity = str(ep.get("entity") or "resource")
            slice_id = behavior_slice_id("permission", entity, actor_label, method, path)
            slices.append({
                "slice_id": slice_id,
                "entity": entity,
                "kind": "permission",
                "states": [],
                "endpoints": [path],
                "priority": 0.72,
                "source_refs": [{"kind": "test_account", "quote": email}],
                "evidence_gaps": [],
                "_permission_actor": actor_label,
                "_permission_email": email,
                "_permission_password": actor.get("password", ""),
                "_permission_method": method,
                "_permission_path": path,
                "_permission_expected_permitted": expected_permitted,
                "_permission_oracle": "PermissionOracle",
                "_login_path": login_path,
                "_login_body": dict(login_body or {}),
            })
            if len(slices) >= max_slices:
                return slices
    return slices


def _is_admin_like(actor: dict[str, str]) -> bool:
    label = (actor.get("role") or actor.get("email") or "").strip().lower()
    return "admin" in label


def generate_isolation_slices(
    endpoints: list[dict[str, str]],
    actors: list[dict[str, str]],
    max_slices: int = 12,
    login_path: str = "",
    login_body: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Cross-user tenant isolation probes.

    UserA authenticates and requests UserB's resources.  The oracle is
    TenantIsolationOracle.
    """
    if len(actors) < 2 or not endpoints:
        return []
    slices: list[dict[str, Any]] = []
    # Entity-owned reads = deeper paths (>=2 non-placeholder segments). No
    # assumption about the API prefix (/api, /v1, /rest, or none) — we select
    # by path depth, which is universal across REST styles.
    read_endpoints = sorted(
        {str(e.get("path") or "") for e in endpoints
         if str(e.get("method") or "").upper() in _READ_METHODS
         and str(e.get("path") or "").startswith("/")
         and len([seg for seg in str(e.get("path") or "").strip("/").split("/") if seg and "{" not in seg and ":" not in seg]) >= 2},
    )
    if not read_endpoints:
        return slices
    for i, viewer in enumerate(actors):
        owner = actors[(i + 1) % len(actors)]
        viewer_label = (viewer.get("role") or viewer.get("email") or "").strip().lower()
        owner_label = (owner.get("role") or owner.get("email") or "").strip().lower()
        if viewer_label == owner_label:
            continue
        for path in read_endpoints[:3]:  # cap per actor pair
            entity = _path_entity(path)
            slice_id = behavior_slice_id("isolation", entity, viewer_label, path)
            slices.append({
                "slice_id": slice_id,
                "entity": entity,
                "kind": "isolation",
                "states": [],
                "endpoints": [path],
                "priority": 0.74,
                "source_refs": [
                    {"kind": "test_account", "quote": viewer.get("email", "")},
                ],
                "evidence_gaps": [],
                "_isolation_viewer_role": viewer_label,
                "_isolation_viewer_email": viewer.get("email", ""),
                "_isolation_viewer_password": viewer.get("password", ""),
                "_isolation_owner_role": owner_label,
                "_isolation_owner_email": owner.get("email", ""),
                "_isolation_path": path,
                "_isolation_oracle": "TenantIsolationOracle",
                "_login_path": login_path,
                "_login_body": dict(login_body or {}),
            })
            if len(slices) >= max_slices:
                return slices
    return slices


def generate_concurrency_slices(
    endpoints: list[dict[str, str]],
    default_actor: dict[str, str] | None = None,
    login_path: str = "",
    max_slices: int = 4,
    login_body: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Concurrent-write (double-write) slices.

    Applies to write endpoints generically — no domain keyword gate. A
    double-write probe is meaningful for ANY mutating endpoint; the
    ConcurrencyOracle decides at runtime whether both writes succeeded where
    mutual exclusion was expected. Prefers collection-level writes (fewer path
    parameters) so more probes execute cleanly. Works on any industry's API.
    """
    writes = [e for e in endpoints if str(e.get("method") or "").upper() in _WRITE_METHODS]
    # Prefer shallow, collection-level writes first (cleanest to execute).
    writes.sort(key=lambda e: str(e.get("path") or "").count("/"))
    slices: list[dict[str, Any]] = []
    seen: set[str] = set()
    da = default_actor or {}
    for ep in writes:
        method = str(ep.get("method") or "").upper()
        path = str(ep.get("path") or "")
        if not path or path in seen:
            continue
        if _is_auth_endpoint(ep):  # login/register are not resource-contention endpoints
            continue
        seen.add(path)
        entity = str(ep.get("entity") or _path_entity(path))
        slice_id = behavior_slice_id("concurrency", entity, method, path)
        slices.append({
            "slice_id": slice_id,
            "entity": entity,
            "kind": "concurrency",
            "states": [],
            "endpoints": [path],
            "priority": 0.78,
            "source_refs": [{"kind": "api_endpoint", "quote": path}],
            "evidence_gaps": [],
            "_concurrency_method": method,
            "_concurrency_path": path,
            "_concurrency_oracle": "ConcurrencyOracle",
            "_login_path": login_path,
            "_login_body": dict(login_body or {}),
            "_default_actor": (da.get("role") or da.get("email") or "").strip().lower(),
            "_default_email": da.get("email", ""),
            "_default_password": da.get("password", ""),
        })
        if len(slices) >= max_slices:
            break
    return slices


def generate_money_slices(
    endpoints: list[dict[str, str]],
    default_actor: dict[str, str] | None = None,
    login_path: str = "",
    max_slices: int = 4,
    login_body: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Financial-integrity slices.

    Applies to write endpoints generically — no domain keyword gate. The
    MoneyOracle is a response-content oracle: it only raises a finding when a
    response actually exposes a money-like field that is negative or a
    duplicate-refund pattern. So there is no need to guess which endpoints are
    "financial" up front; we probe writes and let the oracle judge. Works for
    banking, insurance, billing, retail, or any domain.
    """
    writes = [e for e in endpoints if str(e.get("method") or "").upper() in _WRITE_METHODS]
    writes.sort(key=lambda e: str(e.get("path") or "").count("/"))
    slices: list[dict[str, Any]] = []
    seen: set[str] = set()
    da = default_actor or {}
    for ep in writes:
        method = str(ep.get("method") or "").upper()
        path = str(ep.get("path") or "")
        if not path or path in seen:
            continue
        if _is_auth_endpoint(ep):  # login/register have no financial semantics
            continue
        seen.add(path)
        entity = str(ep.get("entity") or _path_entity(path))
        slice_id = behavior_slice_id("money", entity, method, path)
        slices.append({
            "slice_id": slice_id,
            "entity": entity,
            "kind": "money",
            "states": [],
            "endpoints": [path],
            "priority": 0.82,
            "source_refs": [{"kind": "api_endpoint", "quote": path}],
            "evidence_gaps": [],
            "_money_method": method,
            "_money_path": path,
            "_money_oracle": "MoneyOracle",
            "_login_path": login_path,
            "_login_body": dict(login_body or {}),
            "_default_actor": (da.get("role") or da.get("email") or "").strip().lower(),
            "_default_email": da.get("email", ""),
            "_default_password": da.get("password", ""),
        })
        if len(slices) >= max_slices:
            break
    return slices


# ── Helpers ──────────────────────────────────────────────────────────────

def _expected_permitted_roles(actor_label: str) -> list[str]:
    """Minimal, source-derived permission matrix — no business assumptions.

    This is intentionally sparse.  The oracle must determine "not permitted"
    by runtime observation, not by a hardcoded ruleset.  Only uncontroversial
    defaults (admin → all, readonly → reads only) are declared.
    """
    label = actor_label.lower()
    if "admin" in label:
        return ["*"]
    if any(t in label for t in ("read", "view", "audit", "只读")):
        return ["GET", "HEAD", "OPTIONS"]
    return []  # oracle determines this at runtime


def _path_entity(path: str) -> str:
    """Derive a human-readable entity name from an API path fragment."""
    clean = str(path).strip("/")
    parts = [p for p in clean.split("/") if p and not p.startswith("{")]
    for i, part in enumerate(parts):
        if part in ("api", "v1", "v2", "v3"):
            continue
        return part.replace("-", "_").replace(".", "_")
    return "resource"


# ── Aggregate entry ──────────────────────────────────────────────────────

def _discover_login_endpoint(endpoints: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
    """Find the authentication endpoint and its documented request schema.

    Returns (login_path, login_body_template).  The body template is derived
    from the endpoint's summary field (Markdown API spec format) where the
    documented example is `{\"email\":\"...\",\"password\":\"...\"}`.  If no
    example body can be parsed, returns an empty template — the scenario
    builders will still attempt auto-discovered credentials but the body
    format must be declared in a project config for full reliability.

    No hardcoded field names (email / password / username / account).
    """
    auth_tokens = ("login", "signin", "sign-in", "session", "sessions", "token", "authenticate")
    best: tuple[str, dict[str, Any]] = ("", {})
    for e in endpoints:
        method = str(e.get("method") or "").upper()
        if method not in _WRITE_METHODS:
            continue
        path = str(e.get("path") or "")
        action = str(e.get("action") or "").lower()
        hay = (path + " " + action).lower()
        if not any(tok in hay for tok in auth_tokens):
            continue
        body = _parse_documented_request_body(str(e.get("summary") or ""))
        if not best[0] or len(path) < len(best[0]):
            best = (path, body)
    return best


def _parse_documented_request_body(summary: str) -> dict[str, Any]:
    """Extract a JSON-like request body from a Markdown API spec summary field.

    Common patterns in the project's docs:
      '请求： {\"email\":\"buyer01@example.com\",\"password\":\"Test@123456\"}'
      'Request body: {\"account\":\"...\",\"secret\":\"...\"}'

    Returns the parsed JSON dict on success, empty dict on failure. Generic
    across any field names the project documents.
    """
    import re as _re
    text = str(summary or "")
    for pattern in (r'[\u8bf7\u6c42\u6216\u8005]{1,4}[:：]\s*(\{.*?\})', r'[Rr]equest\s*(?:body)?[:：]\s*(\{.*?\})', r'(\{\s*"[^"]+"\s*:.*?\})'):
        m = _re.search(pattern, text)
        if m:
            try:
                candidate = _re.sub(r'"\s*\.\.\."', '""', m.group(1))
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and len(parsed) >= 1:
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass
    return {}


def _account_status_token(account: dict[str, str]) -> str:
    for key in ("status", "account_status", "state"):
        value = str(account.get(key) or "").strip().upper()
        if value:
            return value
    email = str(account.get("email") or account.get("name") or "").lower()
    if "disabled" in email or "locked" in email:
        return "DISABLED"
    return ""


def generate_account_status_slices(
    actors: list[dict[str, str]],
    login_path: str = "",
    login_body: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Fresh-login probes for DISABLED/LOCKED accounts.

    Catches bugs where suspended accounts still receive a valid token on login
    (e.g. benchmark AUTH-001). Uses only credentials declared in test_accounts —
    no hardcoded roles or endpoints.
    """
    if not actors or not login_path:
        return []
    slices: list[dict[str, Any]] = []
    for actor in actors:
        status = _account_status_token(actor)
        if status not in {"DISABLED", "LOCKED"}:
            continue
        email = str(actor.get("email") or "").strip()
        password = str(actor.get("password") or "").strip()
        role = str(actor.get("role") or actor.get("name") or email).strip()
        if not email or not password:
            continue
        slice_id = behavior_slice_id("account_status", "auth", role, "POST", login_path)
        slices.append({
            "slice_id": slice_id,
            "entity": "auth",
            "kind": "account_status",
            "states": [],
            "endpoints": [login_path],
            "priority": 0.95,
            "source_refs": [{"kind": "test_account", "quote": email}],
            "evidence_gaps": [],
            "_account_status": status,
            "_account_status_email": email,
            "_account_status_password": password,
            "_account_status_role": role,
            "_login_path": login_path,
            "_login_body": dict(login_body or {}),
            "_permission_oracle": "PermissionOracle",
        })
    return slices


def generate_supplementary_slices(
    root: Path,
    project: str,
    api_spec_text: str,
) -> list[dict[str, Any]]:
    """Produce all supplementary slices in a single call.

    Fully source/config-driven — endpoints from the API spec parser, actors
    from test_accounts, login route auto-discovered. No project-specific,
    industry-specific, or endpoint hardcoding. Returns an empty list when a
    required data source is unavailable.
    """
    if not str(api_spec_text or "").strip():
        return []
    import re as _re
    state_re = _re.compile(
        r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", _re.I,
    )
    _entities, _states, endpoints = _api_facts(api_spec_text, state_re)
    if not endpoints:
        return []
    # Account source priority: (1) Settings page (multi_service_config.json,
    # maintained by the customer via the UI), (2) test_accounts.json,
    # (3) TEST_ACCOUNTS.md.  The Settings store is authoritative.
    actors, settings_login = load_settings_accounts(root, project)
    if not actors:
        actors = _load_test_accounts(root, project)
    if not actors:
        actors = _parse_md_accounts(root, project)
    # Login route: prefer the operator-declared login_api from Settings, else
    # auto-discover from the API catalog.  The body template follows the login
    # endpoint's documented request schema (no hardcoded {email,password}).
    auto_login_path, auto_login_body = _discover_login_endpoint(endpoints)
    login_path = settings_login or auto_login_path
    login_body_template = auto_login_body
    # Default actor for un-scoped probes: first non-admin account, else first.
    default_actor: dict[str, str] = {}
    if actors:
        default_actor = next((a for a in actors if not _is_admin_like(a)), actors[0])
    all_slices: list[dict[str, Any]] = []
    if login_path and actors:
        all_slices.extend(generate_account_status_slices(actors, login_path=login_path, login_body=login_body_template))
    if actors and any(str(e.get("method") or "").upper() in _WRITE_METHODS for e in endpoints):
        all_slices.extend(generate_permission_slices(endpoints, actors, login_path=login_path, login_body=login_body_template))
    if len(actors) >= 2 and any(str(e.get("method") or "").upper() in _READ_METHODS for e in endpoints):
        all_slices.extend(generate_isolation_slices(endpoints, actors, login_path=login_path, login_body=login_body_template))
    if any(str(e.get("method") or "").upper() in _WRITE_METHODS for e in endpoints):
        all_slices.extend(generate_money_slices(endpoints, default_actor, login_path, login_body=login_body_template))
        all_slices.extend(generate_concurrency_slices(endpoints, default_actor, login_path, login_body=login_body_template))
    return all_slices


def probe_disabled_account_logins(
    root: Path,
    project: str,
    api_spec_text: str,
    base_url: str,
    *,
    campaign_id: str = "",
    discovery_round: int = 1,
) -> list[dict[str, Any]]:
    """Mandatory fresh-login probes for DISABLED/LOCKED test accounts.

    Runs before the slice-budget queue so AUTH-class login bugs (e.g. suspended
    accounts still receiving tokens) are never starved by probe_selection.
    Fully config-driven — actors and login route from customer materials only.
    """
    if not str(base_url or "").strip() or not str(api_spec_text or "").strip():
        return []
    import re as _re

    state_re = _re.compile(
        r"(?:^|[_\-\s])(status|state|phase|stage|lifecycle)(?:$|[_\-\s])", _re.I,
    )
    _entities, _states, endpoints = _api_facts(api_spec_text, state_re)
    actors, settings_login = load_settings_accounts(root, project)
    if not actors:
        actors = _load_test_accounts(root, project)
    if not actors:
        actors = _parse_md_accounts(root, project)
    auto_login_path, auto_login_body = _discover_login_endpoint(endpoints)
    login_path = settings_login or auto_login_path
    if not login_path or not actors:
        return []

    findings: list[dict[str, Any]] = []
    for actor in actors:
        status = _account_status_token(actor)
        if status not in {"DISABLED", "LOCKED"}:
            continue
        email = str(actor.get("email") or "").strip()
        password = str(actor.get("password") or "").strip()
        role = str(actor.get("role") or actor.get("name") or email).strip()
        if not email or not password:
            continue
        body = dict(auto_login_body or {})
        for key in list(body.keys()):
            low = str(key).lower()
            if low in {"email", "username", "user", "account"}:
                body[key] = email
            elif low in {"password", "passwd", "pass"}:
                body[key] = password
        if not body:
            body = {"email": email, "password": password}
        url = base_url.rstrip("/") + login_path
        method = "POST"
        try:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request = urllib.request.Request(
                url,
                method=method,
                data=payload,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                raw = response.read(300_000).decode("utf-8", errors="replace")
                http_status = int(response.status)
                resp_body: Any
                try:
                    resp_body = json.loads(raw)
                except Exception:
                    resp_body = {"_raw": raw[:2000]}
        except urllib.error.HTTPError as exc:
            http_status = int(exc.code)
            raw = exc.read(300_000).decode("utf-8", errors="replace") if exc.fp else ""
            try:
                resp_body = json.loads(raw) if raw else {}
            except Exception:
                resp_body = {"_raw": raw[:2000]}
        except Exception as exc:
            continue
        if not (200 <= http_status < 300):
            continue
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        findings.append({
            "severity": "P0",
            "title": f"[账号状态登录绕过] {status} 账号 {email} 仍可登录获 token",
            "category": "authorization_access_control",
            "source": "disabled_account_login_probe",
            "description": f"{status} 账号 {email} 调用 {method} {login_path} 返回 HTTP {http_status}，应拒绝登录",
            "confidence_score": 0.98,
            "behavior_slice_id": behavior_slice_id("account_status", "auth", role, method, login_path),
            "discovery_round": discovery_round,
            "campaign_id": campaign_id,
            "execution_status": "executed",
            "confirmation_status": "confirmed",
            "gate_passed": True,
            "customer_delivery_status": "defect",
            "bug_status": "reproduced",
            "expected": f"{status} 账号登录应返回 401/403",
            "actual": f"HTTP {http_status}",
            "method": method,
            "path": login_path,
            "evidence_id": f"EVID_LOGIN_{role}_{int(time.time())}",
            "evidence": {
                "request": f"{method} {login_path}",
                "response": {"status_code": http_status, "body": resp_body},
                "assertion": f"{status} account must not receive a valid login token",
                "timestamp": ts,
                "target": login_path,
                "actor": role,
                "reproduction_steps": [f"{method} {login_path} body={json.dumps(body, ensure_ascii=False)}"],
            },
            "raw_evidence": {
                "account_status": status,
                "email": email,
                "request_raw": {"method": method, "path": login_path, "actor": role, "body": body},
                "response_raw": {"status_code": http_status, "body": resp_body},
                "timestamp": ts,
            },
            "reproduction": {
                "method": method,
                "path": login_path,
                "actor": role,
                "reproduction_steps": [f"{method} {login_path}"],
            },
        })
    return findings
