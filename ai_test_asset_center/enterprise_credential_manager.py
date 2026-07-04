from __future__ import annotations

"""
Enterprise Credential Manager — per-service, per-role credential routing.

Supports enterprise multi-module scenarios where each service has:
- Its own base URL
- Its own authentication scheme (password_login / bearer_token / api_key / oauth2)
- Its own login API
- Its own test accounts per role
- Its own database connection

Configuration sources (priority order):
1. Environment variables: QUALIBUG_SVC_<SERVICE>_<KEY>
2. multi_service_config.json: services[].auth.*, services[].accounts.*
3. test_accounts.json per service
4. Fallback: single-service legacy QUALIBUG_* env vars
"""

import base64
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

_SERVICE_NAME_RE = re.compile(r"[a-z][a-z0-9_-]*$", re.I)


class ServiceCredential:
    """Credentials for a single service × role combination."""

    __slots__ = ("service", "role", "auth_type", "token", "refresh_token",
                 "username", "password", "api_key", "bearer_token",
                 "db_connection", "login_api", "base_url",
                 "expires_at", "extra_headers", "login_lock")

    def __init__(self, service: str, role: str = "admin",
                 auth_type: str = "password_login"):
        self.service = service
        self.role = role
        self.auth_type = auth_type
        self.token: str = ""
        self.refresh_token: str = ""
        self.username: str = ""
        self.password: str = ""
        self.api_key: str = ""
        self.bearer_token: str = ""
        self.db_connection: dict[str, str] = {}
        self.login_api: str = "/auth/login"
        self.base_url: str = ""
        self.expires_at: float = 0.0
        self.extra_headers: dict[str, str] = {}
        self.login_lock = threading.Lock()  # Prevent concurrent login races

    def is_valid(self) -> bool:
        """Check if credential has a token that isn't expired."""
        if not self.token and not self.bearer_token and not self.api_key:
            return False
        if self.expires_at and time.time() > self.expires_at:
            return False
        return True

    def needs_refresh(self, buffer_seconds: int = 60) -> bool:
        """Check if token expires within buffer_seconds and needs refresh."""
        if not self.token or not self.expires_at:
            return False
        return time.time() + buffer_seconds >= self.expires_at

    def auth_header(self) -> tuple[str, str]:
        """Return (header_name, header_value) for HTTP Authorization."""
        header_name = "Authorization"
        if self.bearer_token:
            return header_name, f"Bearer {self.bearer_token}"
        if self.api_key:
            return "X-API-Key", self.api_key
        if self.token:
            # Use token from most recent login (could be JWT access token)
            return header_name, f"Bearer {self.token}"
        if self.username and self.password:
            encoded = base64.b64encode(
                f"{self.username}:{self.password}".encode()
            ).decode()
            return header_name, f"Basic {encoded}"
        return header_name, ""

    def to_dict(self, safe: bool = True) -> dict[str, Any]:
        d = {
            "service": self.service, "role": self.role,
            "auth_type": self.auth_type, "base_url": self.base_url,
            "login_api": self.login_api,
            "has_token": bool(self.token),
            "has_bearer": bool(self.bearer_token),
            "has_api_key": bool(self.api_key),
            "has_db": bool(self.db_connection),
        }
        if not safe:
            d["username"] = self.username
            if self.token:
                d["token"] = self.token
            if self.bearer_token:
                d["bearer_token"] = self.bearer_token
            if self.api_key:
                d["api_key"] = self.api_key[:4] + "****"
        return d


class CredentialStore:
    """Thread-safe store of ServiceCredential keyed by (service, role)."""

    def __init__(self):
        self._creds: dict[tuple[str, str], ServiceCredential] = {}
        self._lock = threading.Lock()

    def get(self, service: str, role: str = "admin") -> ServiceCredential | None:
        with self._lock:
            return self._creds.get((service.lower(), role.lower()))

    def set(self, cred: ServiceCredential) -> None:
        with self._lock:
            self._creds[(cred.service.lower(), cred.role.lower())] = cred

    def remove(self, service: str, role: str = "admin") -> None:
        with self._lock:
            self._creds.pop((service.lower(), role.lower()), None)

    def list_services(self) -> list[str]:
        with self._lock:
            return sorted(set(k[0] for k in self._creds))

    def list_for_service(self, service: str) -> list[ServiceCredential]:
        with self._lock:
            return [c for (s, _), c in self._creds.items()
                    if s == service.lower()]

    def all(self) -> list[ServiceCredential]:
        with self._lock:
            return list(self._creds.values())


# ---------------------------------------------------------------------------
# Main credential manager
# ---------------------------------------------------------------------------

class EnterpriseCredentialManager:
    """Central credential router for multi-module enterprise projects.

    Usage:
        mgr = EnterpriseCredentialManager(project_id, root)
        mgr.load_config("multi_service_config.json")
        token = mgr.get_auth_header("order-service", "admin")
        # → ("Authorization", "Bearer eyJ...")

    Environment variable convention:
        QUALIBUG_SVC_<SERVICE>_ADMIN_USER      — admin username
        QUALIBUG_SVC_<SERVICE>_ADMIN_PASS      — admin password
        QUALIBUG_SVC_<SERVICE>_BEARER_TOKEN    — pre-authenticated bearer token
        QUALIBUG_SVC_<SERVICE>_API_KEY         — API key
        QUALIBUG_SVC_<SERVICE>_LOGIN_API       — login endpoint path
        QUALIBUG_SVC_<SERVICE>_BASE_URL        — base URL
        QUALIBUG_SVC_<SERVICE>_DB_HOST         — database host
        QUALIBUG_SVC_<SERVICE>_DB_NAME         — database name
        QUALIBUG_SVC_<SERVICE>_DB_USER         — database user
        QUALIBUG_SVC_<SERVICE>_DB_PASS         — database password

        QUALIBUG_SVC_<SERVICE>_VIEWER_USER     — viewer username
        QUALIBUG_SVC_<SERVICE>_VIEWER_PASS     — viewer password
    """

    def __init__(self, project_id: str = "", root: Path | None = None):
        self.project_id = project_id
        self.root = root or Path(__file__).resolve().parents[1]
        self.store = CredentialStore()
        self._config_path: Path | None = None
        self._config_data: dict[str, Any] = {}

    # ── Configuration loading ──

    def load_from_file(self, config_path: str | Path) -> dict[str, Any]:
        """Load multi_service_config.json and extract credentials."""
        path = Path(config_path)
        self._config_path = path
        if not path.exists():
            return {}
        self._config_data = _load_json(path, {})
        self._load_service_credentials()
        return self._config_data

    def load_from_dict(self, config: dict[str, Any]) -> None:
        """Load credentials from an already-parsed config dict."""
        self._config_data = config
        self._load_service_credentials()

    def _load_service_credentials(self) -> None:
        """Parse all services from config and extract credentials."""
        services = self._config_data.get("services", [])
        for svc in services:
            if not isinstance(svc, dict):
                continue
            name = _safe_service_name(svc.get("name", ""))
            if not name:
                continue
            base_url = svc.get("base_url", "")
            auth = svc.get("auth") or svc.get("credentials", {})
            if isinstance(auth, dict):
                self._load_service_auth(name, base_url, auth)
            accounts = svc.get("accounts", {})
            if isinstance(accounts, dict):
                self._load_service_accounts(name, base_url, accounts)

    def _load_service_auth(self, name: str, base_url: str,
                           auth: dict[str, Any]) -> None:
        """Load auth section from config — supports arbitrary role keys."""
        auth_type = auth.get("type") or auth.get("auth_type", "password_login")
        login_api = auth.get("login_api", "/auth/login")

        # Discover all role keys in the auth dict (skip metadata keys)
        METADATA_KEYS = {"type", "auth_type", "login_api", "bearer_token", "api_key"}
        roles_found = [k for k in auth if k not in METADATA_KEYS and isinstance(auth.get(k), dict)]

        # If no explicit roles, default to admin/viewer
        if not roles_found and auth_type == "password_login":
            roles_found = ["admin", "viewer"]

        for role in roles_found[:10]:  # Max 10 roles
            role_cfg = auth.get(role, {})
            if not isinstance(role_cfg, dict):
                continue
            cred = ServiceCredential(name, role, auth_type)
            cred.base_url = base_url
            cred.login_api = role_cfg.get("login_api", login_api)
            cred.username = str(role_cfg.get("username", ""))
            cred.password = str(role_cfg.get("password", ""))
            if not cred.username and not cred.password:
                continue  # Skip empty roles
            cred.bearer_token = str(role_cfg.get("bearer_token", ""))
            cred.api_key = str(role_cfg.get("api_key", ""))
            cred.db_connection = role_cfg.get("db", {}) or {}
            self.store.set(cred)

        # Global bearer_token / api_key (applies to all roles)
        if auth.get("bearer_token") and not self.store.get(name, "admin"):
            cred = ServiceCredential(name, "admin", "bearer_token")
            cred.base_url = base_url
            cred.login_api = login_api
            cred.bearer_token = str(auth["bearer_token"])
            self.store.set(cred)

        if auth.get("api_key") and not self.store.get(name, "admin"):
            cred = ServiceCredential(name, "admin", "api_key")
            cred.base_url = base_url
            cred.login_api = login_api
            cred.api_key = str(auth["api_key"])
            self.store.set(cred)

    def _load_service_accounts(self, name: str, base_url: str,
                               accounts: dict[str, Any]) -> None:
        """Load accounts section (legacy format from test_accounts.json)."""
        login_api = accounts.get("login_api", "/auth/login")
        for role, acc in accounts.items():
            if role in ("login_api", "auth_type"):
                continue
            if isinstance(acc, dict) and (acc.get("username") or acc.get("token")):
                # Skip if already loaded from auth section
                if self.store.get(name, role):
                    continue
                cred = ServiceCredential(name, role,
                    acc.get("auth_type", accounts.get("auth_type", "password_login")))
                cred.base_url = acc.get("base_url", base_url)
                cred.login_api = acc.get("login_api", login_api)
                cred.username = str(acc.get("username", ""))
                cred.password = str(acc.get("password", ""))
                cred.bearer_token = str(acc.get("bearer_token", "") or acc.get("token", ""))
                cred.api_key = str(acc.get("api_key", ""))
                self.store.set(cred)

    # ── Environment variable loading ──

    def load_from_env(self, services: list[str] | None = None) -> None:
        """Load credentials from QUALIBUG_SVC_* environment variables.

        Pattern: QUALIBUG_SVC_<SERVICE>_<KEY>
        """
        targets = services or self._detect_services_from_env()
        if not targets:
            targets = ["default"]

        for svc_name in targets:
            sn = _safe_service_name(svc_name)
            if not sn:
                continue
            prefix = f"QUALIBUG_SVC_{sn.upper()}"

            # Check if any env vars exist for this service
            has_any = any(
                k.startswith(prefix) for k in os.environ
            )
            if not has_any:
                continue

            base_url = os.environ.get(f"{prefix}_BASE_URL", "")
            login_api = os.environ.get(f"{prefix}_LOGIN_API", "/auth/login")

            for role in ("admin", "viewer"):
                user_k = f"{prefix}_{role.upper()}_USER"
                pass_k = f"{prefix}_{role.upper()}_PASS"
                token_k = f"{prefix}_{role.upper()}_TOKEN"
                api_k = f"{prefix}_{role.upper()}_API_KEY"

                has_role_creds = any(
                    k in os.environ for k in (user_k, pass_k, token_k, api_k)
                )
                if not has_role_creds:
                    continue

                if self.store.get(sn, role):
                    cred = self.store.get(sn, role)
                else:
                    cred = ServiceCredential(sn, role, "password_login")

                if os.environ.get(user_k):
                    cred.username = os.environ[user_k]
                if os.environ.get(pass_k):
                    cred.password = os.environ[pass_k]
                if os.environ.get(token_k):
                    cred.bearer_token = os.environ[token_k]
                if os.environ.get(api_k):
                    cred.api_key = os.environ[api_k]

                cred.base_url = base_url or cred.base_url
                cred.login_api = login_api
                self.store.set(cred)

            # Global bearer / api key for this service
            global_token = os.environ.get(f"{prefix}_BEARER_TOKEN")
            global_api_key = os.environ.get(f"{prefix}_API_KEY")
            if global_token and not self.store.get(sn, "admin"):
                cred = ServiceCredential(sn, "admin", "bearer_token")
                cred.base_url = base_url
                cred.bearer_token = global_token
                self.store.set(cred)
            if global_api_key and not self.store.get(sn, "admin"):
                cred = ServiceCredential(sn, "admin", "api_key")
                cred.base_url = base_url
                cred.api_key = global_api_key
                self.store.set(cred)

            # DB credentials
            db = {}
            for k in ("DB_HOST", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASS"):
                v = os.environ.get(f"{prefix}_{k}")
                if v:
                    db[k.lower().replace("_", "")[2:]] = v
            if db.get("host"):
                for role_cred in self.store.list_for_service(sn):
                    role_cred.db_connection = db

    def _detect_services_from_env(self) -> list[str]:
        """Scan environment for QUALIBUG_SVC_<NAME>_ patterns."""
        services: set[str] = set()
        for k in os.environ:
            if k.startswith("QUALIBUG_SVC_"):
                parts = k.split("_", 3)
                if len(parts) >= 3:
                    services.add(parts[2].lower())
        return sorted(services)

    # ── Legacy single-service fallback ──

    def load_legacy_fallback(self) -> None:
        """Load legacy single-service QUALIBUG_* env vars as 'default' service."""
        admin_user = os.environ.get("QUALIBUG_ADMIN_USER", "")
        admin_pass = os.environ.get("QUALIBUG_ADMIN_PASS", "")
        viewer_user = os.environ.get("QUALIBUG_VIEWER_USER", "")
        viewer_pass = os.environ.get("QUALIBUG_VIEWER_PASS", "")
        viewer_token = os.environ.get("QUALIBUG_VIEWER_TOKEN", "") or os.environ.get("QUALIBUG_BEARER_TOKEN", "")
        base_url = os.environ.get("QUALIBUG_TARGET_BASE_URL", "")

        if self.store.get("default", "admin"):
            return  # Already configured

        admin = ServiceCredential("default", "admin", "password_login")
        admin.username = admin_user
        admin.password = admin_pass
        admin.base_url = base_url
        self.store.set(admin)

        viewer = ServiceCredential("default", "viewer", "bearer_token" if viewer_token else "password_login")
        viewer.username = viewer_user
        viewer.password = viewer_pass
        viewer.bearer_token = viewer_token
        viewer.base_url = base_url
        self.store.set(viewer)

    # ── Token acquisition (runtime login) ──

    def login(self, service: str, role: str = "admin",
              timeout: int = 10,
              openapi_spec: dict[str, Any] | None = None) -> ServiceCredential | None:
        """Perform actual login against the service to acquire a token.

        Auto-detects login endpoint: first from OpenAPI spec, then probes
        common paths as fallback. No hardcoded path dependency.
        """
        cred = self.store.get(service, role)
        if not cred or not cred.base_url:
            return None

        if cred.bearer_token and cred.is_valid():
            return cred

        username = cred.username or role
        password = cred.password or f"{role}123"

        # ── Build candidate list ──
        candidates = []
        if cred.login_api:
            candidates.append(cred.login_api)

        # 1. Extract from OpenAPI spec (if available)
        if openapi_spec and isinstance(openapi_spec, dict):
            spec_paths = self._extract_login_paths_from_spec(openapi_spec)
            candidates.extend(spec_paths)

        # 2. Probe common paths as safety net
        candidates.extend([
            "/auth/login", "/api/auth/login", "/api/v1/auth/login",
            "/login", "/api/login", "/api/v1/login",
            "/auth/token", "/oauth/token",
        ])

        # Deduplicate preserving order
        seen = set()
        unique: list[str] = []
        for c in candidates:
            c = c.strip().strip("/")
            if c and c not in seen:
                seen.add(c)
                unique.append(c)

        for login_path in unique:
            url = cred.base_url.rstrip("/") + "/" + login_path
            body = json.dumps({
                "username": username,
                "password": password,
            }).encode()

            req = urllib.request.Request(
                url, method="POST",
                data=body,
                headers={"Content-Type": "application/json"},
            )

            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    data = json.loads(resp.read().decode())
            except Exception:
                continue  # Try next path

            # Extract token from common response patterns
            token = (
                (data.get("data") or {}).get("accessToken") or
                (data.get("data") or {}).get("access_token") or
                (data.get("data") or {}).get("token") or
                data.get("accessToken") or
                data.get("access_token") or
                data.get("token") or
                ""
            )

            if token:
                cred.token = token
                cred.login_api = "/" + login_path  # Remember detected path
                # ── Extract expiry from JWT or response ──
                cred.expires_at = self._extract_expiry(token, data)
                # ── Extract refresh token if present ──
                refresh = (
                    (data.get("data") or {}).get("refreshToken") or
                    (data.get("data") or {}).get("refresh_token") or
                    data.get("refreshToken") or data.get("refresh_token") or ""
                )
                if refresh:
                    cred.refresh_token = refresh
                self.store.set(cred)
                print(f"  [OK] {role} token for {service} via "
                      f"/{login_path} (len={len(token)}, "
                      f"exp={time.strftime('%H:%M', time.localtime(cred.expires_at))}"
                      f"{', refreshable' if refresh else ''})", flush=True)
                return cred

        print(f"  [WARN] {service}/{role}: login failed on all paths "
              f"({len(unique)} tried){' (可能存在验证码)' if len(unique) <= 3 else ''}",
              flush=True)
        return None

    # ── Token lifecycle: expiry extraction + auto-refresh ──

    @staticmethod
    def _extract_expiry(token: str, response_data: dict[str, Any]) -> float:
        """Extract token expiry from JWT payload or response fields.

        Priority:
        1. JWT 'exp' claim (Unix timestamp)
        2. Response 'expires_in' / 'expiresIn' (seconds from now)
        3. Response 'expires_at' (Unix timestamp)
        4. Default: 3600s from now
        """
        now = time.time()

        # 1. JWT payload
        try:
            parts = token.split(".")
            if len(parts) == 3:
                payload = parts[1]
                # Add padding
                payload += "=" * (4 - len(payload) % 4) if len(payload) % 4 else ""
                decoded = json.loads(base64.urlsafe_b64decode(payload))
                if isinstance(decoded, dict) and decoded.get("exp"):
                    return min(decoded["exp"], now + 86400)  # Cap at 24h
        except Exception:
            pass

        # 2. Response fields
        expires_in = (
            response_data.get("expires_in") or
            response_data.get("expiresIn") or
            (response_data.get("data") or {}).get("expires_in") or
            (response_data.get("data") or {}).get("expiresIn") or
            0
        )
        if expires_in:
            try:
                return now + int(expires_in)
            except (ValueError, TypeError):
                pass

        expires_at = (
            response_data.get("expires_at") or
            response_data.get("expiresAt") or
            (response_data.get("data") or {}).get("expires_at") or 0
        )
        if expires_at:
            try:
                return min(float(expires_at), now + 86400)
            except (ValueError, TypeError):
                pass

        # 4. Default
        return now + 3600

    def _try_refresh_token(self, cred: ServiceCredential) -> bool:
        """Attempt to use refresh token to get a new access token."""
        if not cred.refresh_token or not cred.login_api or not cred.base_url:
            return False
        with cred.login_lock:
            if not cred.needs_refresh(0):
                return True  # Already refreshed by another thread
            try:
                url = cred.base_url.rstrip("/") + "/" + cred.login_api.lstrip("/")
                body = json.dumps({"refresh_token": cred.refresh_token}).encode()
                req = urllib.request.Request(
                    url, method="POST", data=body,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode())
                new_token = (
                    (data.get("data") or {}).get("accessToken") or
                    (data.get("data") or {}).get("access_token") or
                    data.get("accessToken") or data.get("access_token") or ""
                )
                if new_token:
                    cred.token = new_token
                    cred.expires_at = self._extract_expiry(new_token, data)
                    print(f"  [OK] Token refreshed for {cred.service}/{cred.role} "
                          f"(exp={time.strftime('%H:%M', time.localtime(cred.expires_at))})",
                          flush=True)
                    return True
            except Exception:
                pass
        return False

    # ── OpenAPI-aware login path extraction ──

    @staticmethod
    def _extract_login_paths_from_spec(spec: dict[str, Any]) -> list[str]:
        """Extract candidate login endpoints from an OpenAPI/Swagger spec.

        Sources checked (in order of confidence):
        1. securitySchemes → OAuth2 tokenUrl / password flow
        2. OperationId containing 'login' / 'auth' / 'token'
        3. Path summary/description containing login keywords
        4. Request body schema with username+password fields
        """
        paths: list[str] = []

        # 1. OAuth2 / security scheme token URLs
        components = spec.get("components", {}) if isinstance(spec.get("components"), dict) else {}
        security_schemes = components.get("securitySchemes", {})
        if not security_schemes:
            security_schemes = components.get("securityDefinitions", {})
        if not isinstance(security_schemes, dict):
            security_schemes = {}
        for scheme in security_schemes.values():
            if not isinstance(scheme, dict):
                continue
            flows = scheme.get("flows", {})
            if isinstance(flows, dict):
                for flow in flows.values():
                    if isinstance(flow, dict) and flow.get("tokenUrl"):
                        paths.append(flow["tokenUrl"])
            # Direct tokenUrl
            if scheme.get("tokenUrl"):
                paths.append(scheme["tokenUrl"])

        # 2. Scan all paths for login-like endpoints
        api_paths = spec.get("paths", {})
        if isinstance(api_paths, dict):
            for path, methods in api_paths.items():
                if not isinstance(methods, dict):
                    continue
                path_lower = path.lower()
                for method, detail in methods.items():
                    if method not in ("post", "put"):
                        continue
                    if not isinstance(detail, dict):
                        continue

                    score = 0

                    # operationId hints
                    op_id = str(detail.get("operationId", "")).lower()
                    if any(k in op_id for k in ("login", "auth", "signin", "token", "authenticate")):
                        score += 3

                    # Summary / description hints
                    summary = str(detail.get("summary", "")).lower()
                    desc = str(detail.get("description", "")).lower()
                    for text in (summary, desc):
                        if any(k in text for k in ("login", "登录", "认证", "token", "jwt", "sign in")):
                            score += 2

                    # Request body hints: username + password fields
                    req_body = detail.get("requestBody", {})
                    if isinstance(req_body, dict):
                        content = req_body.get("content", {})
                        if isinstance(content, dict):
                            for ct in content.values():
                                if isinstance(ct, dict):
                                    schema = ct.get("schema", {})
                                    if isinstance(schema, dict):
                                        props = schema.get("properties", {})
                                        if isinstance(props, dict):
                                            prop_names = [k.lower() for k in props]
                                            has_user = any(
                                                u in prop_names
                                                for u in ("username", "user", "email", "login", "account")
                                            )
                                            has_pass = any(
                                                p in prop_names
                                                for p in ("password", "pass", "pwd", "secret")
                                            )
                                            if has_user and has_pass:
                                                score += 4

                    if score >= 2:
                        paths.append(path)

        # Return unique, keeping order
        seen = set()
        result = []
        for p in paths:
            p = p.strip()
            if p and p not in seen:
                seen.add(p)
                result.append(p)
        return result

    def login_all_services(self, timeout: int = 10) -> dict[str, dict[str, bool]]:
        """Attempt login for all services × roles."""
        results: dict[str, dict[str, bool]] = {}
        for cred in self.store.all():
            svc, role = cred.service, cred.role
            results.setdefault(svc, {})
            if cred.bearer_token or cred.api_key:
                results[svc][role] = True  # Pre-configured, skip login
            else:
                result = self.login(svc, role, timeout)
                results[svc][role] = result is not None and result.token != ""
        return results

    # ── Auth header resolution ──

    def get_auth_header(self, service: str = "default",
                        role: str = "admin") -> tuple[str, str]:
        """Get (header_name, header_value) for authenticated HTTP request."""
        cred = self.store.get(service, role)
        if not cred:
            # Try fallback to legacy single-service
            cred = self.store.get("default", role)
        if not cred:
            return "Authorization", ""
        return cred.auth_header()

    def get_token(self, service: str = "default",
                  role: str = "admin",
                  auto_refresh: bool = True) -> str:
        """Get the raw token string for a service×role.

        If auto_refresh=True and the token is expired, automatically re-login.
        """
        cred = self.store.get(service, role)
        if not cred:
            cred = self.store.get("default", role)
        if not cred:
            return ""

        # Auto-refresh expired tokens
        if auto_refresh and cred.token and cred.needs_refresh(60):
            if self._try_refresh_token(cred):
                self.store.set(cred)
            elif cred.username and cred.password:
                # Full re-login
                refreshed = self.login(service, role)
                if refreshed and refreshed.token:
                    cred = refreshed

        return (cred.token or cred.bearer_token or cred.api_key or "")

    def get_base_url(self, service: str = "default") -> str:
        """Get the base URL for a service."""
        cred = self.store.get(service, "admin") or self.store.get("default", "admin")
        return cred.base_url if cred else ""

    def get_db_connection(self, service: str = "default") -> dict[str, str]:
        """Get DB connection details for a service."""
        cred = self.store.get(service, "admin") or self.store.get("default", "admin")
        return cred.db_connection if cred else {}

    def get_config_hint(self, service: str) -> dict[str, Any]:
        """Generate human-readable config guidance for a service."""
        cred = self.store.get(service, "admin") or self.store.get(service, "viewer")
        sn = service.upper().replace("-", "_")
        if not cred:
            return {"service": service, "configured": False,
                    "hint": f"未配置 {service} 的凭证。请设置 "
                            f"QUALIBUG_SVC_{sn}_ADMIN_USER 环境变量，"
                            f"或在 multi_service_config.json 中添加 services[].auth 字段。"}

        hints = []
        if not cred.bearer_token and not cred.api_key and not cred.token:
            hints.append(
                f"设置 QUALIBUG_SVC_{sn}_BEARER_TOKEN 或 "
                f"QUALIBUG_SVC_{sn}_ADMIN_PASS"
            )
        if not cred.base_url:
            hints.append(f"设置 QUALIBUG_SVC_{sn}_BASE_URL")

        # Captcha guidance
        if cred.auth_type == "password_login":
            hints.append(
                "如果登录接口有验证码，请改用 Bearer Token："
                f"先手动登录获取 Token，设置 QUALIBUG_SVC_{sn}_BEARER_TOKEN"
            )
            hints.append(
                "Token 过期会自动重新登录刷新，"
                f"当前有效至 {time.strftime('%H:%M', time.localtime(cred.expires_at)) if cred.expires_at else '未知'}"
            )

        return {
            "service": service,
            "configured": bool(cred.token or cred.bearer_token or
                             cred.api_key or cred.username),
            "auth_type": cred.auth_type,
            "base_url": cred.base_url,
            "has_db": bool(cred.db_connection),
            "env_vars_prefix": f"QUALIBUG_SVC_{sn}",
            "captcha_workaround": "使用 Bearer Token 代替密码登录",
            "setup_hints": hints,
        }

    # ── Health / status ──

    def status(self, safe: bool = True) -> dict[str, Any]:
        """Return comprehensive credential status for all services."""
        services_status = {}
        for cred in self.store.all():
            services_status.setdefault(cred.service, {})
            services_status[cred.service][cred.role] = cred.to_dict(safe=safe)

        return {
            "services_configured": self.store.list_services(),
            "total_credentials": sum(
                1 for _ in self.store.all()
            ),
            "details": services_status,
            "config_source": str(self._config_path) if self._config_path else "env",
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_service_name(name: str) -> str:
    """Normalize a service name to a safe identifier."""
    cleaned = re.sub(r"[^a-z0-9_-]", "_", name.strip().lower())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    return cleaned if _SERVICE_NAME_RE.match(cleaned) else ""


def _load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8")
