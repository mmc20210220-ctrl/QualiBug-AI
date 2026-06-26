"""
Phase78A: Unified Safe HTTP Transport

Single entry point for ALL HTTP requests in QualiBug.
Production environment → BLOCKED_BY_SAFETY, zero network requests.

Every module that makes HTTP calls MUST use this transport.
Direct urllib.request usage is forbidden.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any


# ═══════════════════════════════════════════════════════════════════
# Verdicts
# ═══════════════════════════════════════════════════════════════════

BLOCKED_BY_SAFETY = "BLOCKED_BY_SAFETY"
ALLOWED = "ALLOWED"


# ═══════════════════════════════════════════════════════════════════
# Execution Policy
# ═══════════════════════════════════════════════════════════════════

PRODUCTION_INDICATORS = {
    "production", "prod", "prd", "live", "online", "release",
}

SANDBOX_ENVIRONMENTS = {"sandbox"}
TEST_ENVIRONMENTS = {"test", "staging", "dev", "development", "qa", "uat"}


class ExecutionPolicy:
    """Determines what HTTP operations are allowed based on environment."""

    def __init__(self, environment: str = "", allow_destructive: bool = False):
        self.environment = str(environment or "").lower().strip()
        self.allow_destructive = allow_destructive
        self._request_count = 0
        self._blocked_count = 0

    @property
    def is_production(self) -> bool:
        return self.environment in PRODUCTION_INDICATORS or any(
            ind in self.environment for ind in PRODUCTION_INDICATORS
        )

    @property
    def is_sandbox(self) -> bool:
        return self.environment in SANDBOX_ENVIRONMENTS

    @property
    def is_test(self) -> bool:
        return self.environment in TEST_ENVIRONMENTS

    @property
    def is_unknown(self) -> bool:
        return not self.is_production and not self.is_sandbox and not self.is_test

    def can_execute(self, method: str = "GET") -> tuple[bool, str]:
        """Check if an HTTP request of the given method is allowed."""
        if self.is_production:
            return False, "Production environment: all HTTP requests blocked"
        if self.is_unknown:
            # Unknown → safest mode: allow GET, block writes
            if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
                return False, "Unknown environment: write operations blocked"
        if method.upper() in ("POST", "PUT", "PATCH", "DELETE") and not self.allow_destructive:
            if not self.is_sandbox:
                return False, "Write operations require sandbox environment or allow_destructive=true"
        return True, "OK"

    def record_request(self):
        self._request_count += 1

    def record_blocked(self):
        self._blocked_count += 1

    @property
    def request_count(self) -> int:
        return self._request_count

    @property
    def blocked_count(self) -> int:
        return self._blocked_count


# ═══════════════════════════════════════════════════════════════════
# Unified HTTP Response
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SafeHttpResponse:
    """Canonical response from SafeHttpTransport. All callers get this shape."""

    ok: bool
    status_code: int | None
    body: str          # raw text body
    json: dict | None  # parsed JSON (None if not JSON or parse failed)
    error: str | None
    blocked: bool = False
    block_reason: str = ""

    def get(self, key: str, default=None):
        """Dict-like access for backward compat with legacy _http() return shape."""
        if key == "_http":
            return self.status_code
        if key == "_error":
            return self.error
        if self.json and key in self.json:
            return self.json[key]
        return default


# ═══════════════════════════════════════════════════════════════════
# Safe HTTP Transport — THE SINGLE HTTP ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

class SafeHttpTransport:
    """
    Unified HTTP transport with mandatory production safety gate.

    Usage:
        transport = SafeHttpTransport(policy)
        resp = transport.request("GET", "http://example.com/api/items")
        if resp.blocked:
            ...  # BLOCKED_BY_SAFETY
        else:
            data = resp.json or resp.body
    """

    def __init__(
        self,
        policy: ExecutionPolicy | None = None,
        base_url: str = "",
        default_timeout: int = 10,
        max_body_bytes: int = 2 * 1024 * 1024,
    ):
        self.policy = policy or ExecutionPolicy()
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.default_timeout = default_timeout
        self.max_body_bytes = max_body_bytes

    # ── Public API ──

    def request(
        self,
        method: str,
        url: str,
        body: Any = None,
        headers: dict[str, str] | None = None,
        timeout: int | None = None,
        token: str | None = None,
        json_body: bool = True,
    ) -> SafeHttpResponse:
        """Make an HTTP request through the safety gate."""

        # ── Safety gate ──
        can_exec, reason = self.policy.can_execute(method)
        if not can_exec:
            self.policy.record_blocked()
            return SafeHttpResponse(
                ok=False, status_code=None, body="", json=None,
                error=reason, blocked=True, block_reason=reason,
            )

        # ── Build request ──
        full_url = self._join_url(url)
        headers = dict(headers or {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if json_body and body is not None and not isinstance(body, bytes):
            headers.setdefault("Content-Type", "application/json")
            body_bytes = json.dumps(body).encode("utf-8")
        elif isinstance(body, bytes):
            body_bytes = body
        else:
            body_bytes = None

        req = urllib.request.Request(
            full_url, data=body_bytes, headers=headers, method=method.upper()
        )

        # ── Execute ──
        timeout_val = timeout if timeout is not None else self.default_timeout
        try:
            with urllib.request.urlopen(req, timeout=timeout_val) as resp:
                raw = resp.read(self.max_body_bytes)
                text = raw.decode("utf-8", errors="replace")
                parsed = None
                try:
                    parsed = json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    pass
                self.policy.record_request()
                return SafeHttpResponse(
                    ok=True, status_code=resp.status, body=text,
                    json=parsed, error=None,
                )
        except urllib.error.HTTPError as e:
            self.policy.record_request()
            error_body = ""
            try:
                error_body = e.read(min(self.max_body_bytes, 200 * 1024)).decode("utf-8", errors="replace")
            except Exception:
                pass
            return SafeHttpResponse(
                ok=False, status_code=e.code, body=error_body, json=None,
                error=f"HTTP {e.code}: {e.reason}",
            )
        except urllib.error.URLError as e:
            return SafeHttpResponse(
                ok=False, status_code=None, body="", json=None,
                error=f"Connection error: {e.reason}",
            )
        except Exception as e:
            return SafeHttpResponse(
                ok=False, status_code=None, body="", json=None,
                error=f"Request failed: {e}",
            )

    def get(self, url: str, **kwargs) -> SafeHttpResponse:
        return self.request("GET", url, **kwargs)

    def post(self, url: str, body=None, **kwargs) -> SafeHttpResponse:
        return self.request("POST", url, body=body, **kwargs)

    def put(self, url: str, body=None, **kwargs) -> SafeHttpResponse:
        return self.request("PUT", url, body=body, **kwargs)

    def delete(self, url: str, **kwargs) -> SafeHttpResponse:
        return self.request("DELETE", url, **kwargs)

    # ── Backward-compat adapters ──

    def fetch(self, url: str, method="GET", body=None, headers=None, timeout=None) -> dict:
        """Adapter matching real_project_onboarding._fetch() return shape."""
        resp = self.request(method, url, body=body, headers=headers, timeout=timeout, json_body=False)
        if resp.blocked:
            return {"ok": False, "status_code": None, "body": "", "error": resp.block_reason}
        return {"ok": resp.ok, "status_code": resp.status_code, "body": resp.body, "error": resp.error}

    def fetch_json_or_text(self, url: str, method="GET", body=None, token=None, timeout=None) -> dict:
        """Adapter matching real_project_defect_discovery._fetch_json_or_text() return shape."""
        resp = self.request(method, url, body=body, token=token, timeout=timeout)
        if resp.blocked:
            return {"ok": False, "status_code": None, "body": "", "error": resp.block_reason}
        return {"ok": resp.ok, "status_code": resp.status_code, "body": resp.body, "error": resp.error}

    def fetch_http(self, method: str, path: str, data=None, no_auth=False, role="admin",
                   tokens: dict | None = None) -> dict:
        """Adapter matching discovery_engine._http() return shape: {_http, ...merged keys}."""
        headers = {"Content-Type": "application/json"}
        token = None
        if not no_auth and tokens and role in tokens:
            token = tokens.get(role)
        resp = self.request(method, path, body=data, token=token, headers=headers)
        if resp.blocked:
            return {"_http": 0, "_error": resp.block_reason}
        if resp.ok and resp.json:
            return {"_http": resp.status_code, **resp.json}
        return {"_http": resp.status_code or 0, "_error": resp.error or str(resp.body)[:500]}

    def fetch_concurrency(self, url: str, method: str, body=None, headers=None, timeout=8) -> dict:
        """Adapter matching concurrency_async_sandbox._http() return shape: {ok, status_code, payload, error}."""
        resp = self.request(method, url, body=body, headers=headers, timeout=timeout)
        if resp.blocked:
            return {"ok": False, "status_code": None, "payload": None, "error": resp.block_reason}
        return {"ok": resp.ok, "status_code": resp.status_code, "payload": resp.json, "error": resp.error}

    # ── Helpers ──

    def _join_url(self, path_or_url: str) -> str:
        """Join base_url with path, handling absolute URLs."""
        if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
            url = path_or_url
        elif self.base_url:
            path = path_or_url if path_or_url.startswith("/") else f"/{path_or_url}"
            url = f"{self.base_url}{path}"
        else:
            url = path_or_url
        # Fix double /api prefix
        return url.replace("/api/api/", "/api/")

    @property
    def request_count(self) -> int:
        return self.policy.request_count

    @property
    def blocked_count(self) -> int:
        return self.policy.blocked_count


# ═══════════════════════════════════════════════════════════════════
# Global transport instance (set during initialization)
# ═══════════════════════════════════════════════════════════════════

_global_transport: SafeHttpTransport | None = None


def set_global_transport(transport: SafeHttpTransport):
    global _global_transport
    _global_transport = transport


def get_global_transport() -> SafeHttpTransport:
    global _global_transport
    if _global_transport is None:
        _global_transport = SafeHttpTransport(ExecutionPolicy(environment="unknown"))
    return _global_transport


# ═══════════════════════════════════════════════════════════════════
# Quick helpers for common call patterns
# ═══════════════════════════════════════════════════════════════════

def safe_get(url: str, **kwargs) -> SafeHttpResponse:
    return get_global_transport().get(url, **kwargs)


def safe_post(url: str, body=None, **kwargs) -> SafeHttpResponse:
    return get_global_transport().post(url, body=body, **kwargs)
