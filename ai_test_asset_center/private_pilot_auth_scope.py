"""HTTP auth, scope, and response helpers for PrivatePilotHandler."""
from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .private_pilot_debug_client import _dbg_report
from .private_pilot_project_assets import _known_project_exists, _root, _truthy_env
from .private_pilot_tenant_auth import (
    PROJECT_SCOPE_HEADER,
    TenantAuthenticationError,
    _actor,
    _parse_project_scopes,
    _tenant_from_headers,
)
from .product_logging import get_logger
from .real_project_onboarding import _safe_project_id

_http_logger = get_logger("qualibug.http")

class AuthScopeMixin:
    def _root(self) -> Path:
        configured = getattr(self.server, "qualibug_private_root", None)
        return Path(configured).resolve() if configured else _root()

    def _json(self, body: Any, status: int = 200, extra_headers: dict[str, str] | None = None) -> None:
        # --- Product request logging ---
        _req_start = getattr(self, "_qualibug_req_start", 0.0)
        _elapsed_ms = int((time.time() - _req_start) * 1000) if _req_start else -1
        _corr_id = getattr(self, "_qualibug_corr_id", "")
        _method = getattr(self, "command", "?")
        _path = getattr(self, "path", "?")
        if status >= 500:
            _http_logger.error(
                f"{_method} {_path} -> {status} ({_elapsed_ms}ms)",
                extra={"error_code": "QB-S999", "context": {
                    "method": _method, "path": _path, "status": status,
                    "elapsed_ms": _elapsed_ms, "correlation_id": _corr_id,
                }},
            )
        elif status >= 400:
            _http_logger.warning(
                f"{_method} {_path} -> {status} ({_elapsed_ms}ms)",
                extra={"context": {
                    "method": _method, "path": _path, "status": status,
                    "elapsed_ms": _elapsed_ms, "correlation_id": _corr_id,
                }},
            )
        else:
            _http_logger.info(
                f"{_method} {_path} -> {status} ({_elapsed_ms}ms)",
                extra={"context": {
                    "method": _method, "path": _path, "status": status,
                    "elapsed_ms": _elapsed_ms, "correlation_id": _corr_id,
                }},
            )
        try:
            raw = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            if extra_headers:
                for _hk, _hv in extra_headers.items():
                    self.send_header(_hk, _hv)
            self.end_headers()
            self.wfile.write(raw)
        except (ConnectionAbortedError, ConnectionResetError, OSError):
            pass  # client disconnected
        except Exception as exc:
            _http_logger.error(
                f"JSON response failed: {type(exc).__name__}: {exc}",
                exc_info=True,
                extra={"error_code": "QB-S999", "context": {"status": status, "path": _path}},
            )
            _dbg_report(
                hypothesis_id="A",
                msg=f"[DEBUG] json-response-failed status={status}",
                data={"exc_type": type(exc).__name__, "exc": str(exc)},
            )
            raise

    def _html(self, body: str, status: int = 200) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _project(self) -> str:
        query = parse_qs(urlparse(self.path).query)
        return _safe_project_id((query.get("project") or [""])[0])

    def _request_tenant(self) -> str:
        tenant_id = str(getattr(self, "_validated_tenant_id", "") or "").strip()
        if tenant_id:
            return tenant_id
        tenant_id = _tenant_from_headers(dict(self.headers), root=self._root())
        self._validated_tenant_id = tenant_id
        return tenant_id

    def _require_tenant(self, root: Path) -> str | None:
        try:
            tenant_id = _tenant_from_headers(dict(self.headers), root=root)
        except TenantAuthenticationError as exc:
            self._json(
                {
                    "ok": False,
                    "error": "INVALID_TENANT_CREDENTIAL",
                    "message": str(exc),
                },
                401,
            )
            return None
        self._validated_tenant_id = tenant_id
        return tenant_id

    def _body(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0") or 0)
        if not size:
            return {}
        if size > 2_000_000:
            raise ValueError("Request body exceeds the private service limit.")
        raw = self.rfile.read(size)
        if not raw:
            return {}
        # Try UTF-8 first, then latin-1, then raw bytes
        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                parsed = json.loads(raw.decode(encoding))
                return parsed if isinstance(parsed, dict) else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        return {}

    def _require_actor(self) -> dict[str, str] | None:
        actor = _actor(self.headers)
        if actor is None:
            server_host = str(getattr(self.server, "server_address", ("", 0))[0] or "")
            # Fail-closed by construction: the synthetic local-dev actor is a
            # deliberate, explicit opt-in (QUALIBUG_LOCAL_DEV_ACTOR=1), never a
            # default. A default-on flag silently authenticated every unauthenticated
            # localhost request as a real actor, including in CI/container images
            # that happen to bind to 127.0.0.1. When enabled, the synthetic actor
            # also never defaults to project_owner: an operator must explicitly opt
            # into an elevated role via QUALIBUG_LOCAL_ROLE, otherwise it gets the
            # lowest-privilege role so local dev cannot silently exercise
            # owner-only paths (destructive project/campaign operations).
            local_dev_actor_allowed = (
                _truthy_env("QUALIBUG_LOCAL_DEV_ACTOR", "")
                and server_host in {"127.0.0.1", "localhost", "::1"}
                and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1"
                and str(self.headers.get("X-QualiBug-No-Local-Dev") or "").strip() != "1"
            )
            if local_dev_actor_allowed:
                return {
                    "name": os.environ.get("QUALIBUG_LOCAL_ACTOR", "local_dev")[:120],
                    "role": os.environ.get("QUALIBUG_LOCAL_ROLE", "viewer")[:64],
                }
        if actor is None:
            self._json(
                {
                    "ok": False,
                    "error": "MISSING_TRUSTED_ACTOR",
                    "message": "The private service requires trusted X-QualiBug-Actor and X-QualiBug-Role headers, unless localhost-only local development actor mode is enabled.",
                },
                401,
            )
        return actor

    def _require_role(self, actor: dict[str, str], allowed: set[str], action: str) -> bool:
        if actor.get("role") in allowed:
            return True
        self._json(
            {
                "ok": False,
                "error": "FORBIDDEN",
                "message": f"{action} requires one of: {', '.join(sorted(allowed))}.",
            },
            403,
        )
        return False

    def _require_project_scope(self, project: str) -> bool:
        """Require an explicit trusted project scope outside localhost dev mode.

        Actor and role headers establish *who* is calling, but they do not by
        themselves establish which customer/project data the caller may read or
        change.  In a public/private-cloud binding, the trusted reverse proxy
        must inject a comma-separated allow-list (or ``*`` for an explicitly
        authorized platform operator) through ``X-QualiBug-Project-Scopes``.

        The localhost-only development fallback remains intentionally narrow:
        it is available only while public binding is disabled, matching the
        existing local actor fallback used by the self-contained pilot demo.
        """
        raw = str(self.headers.get(PROJECT_SCOPE_HEADER) or self.headers.get(PROJECT_SCOPE_HEADER.lower()) or "")
        scopes, wildcard = _parse_project_scopes(raw)
        if wildcard or _safe_project_id(project) in scopes:
            return True

        server_host = str(getattr(self.server, "server_address", ("", 0))[0] or "")
        local_development = (
            server_host in {"127.0.0.1", "localhost", "::1"}
            and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1"
            and _truthy_env("QUALIBUG_LOCAL_DEV_ACTOR", "")
        )
        if local_development:
            return True
        self._json(
            {
                "ok": False,
                "error": "PROJECT_SCOPE_FORBIDDEN",
                "message": f"Requested project is outside the trusted {PROJECT_SCOPE_HEADER} allow-list.",
            },
            403,
        )
        return False

    def _project_list_scope_filter(self) -> tuple[set[str], bool]:
        server_host = str(getattr(self.server, "server_address", ("", 0))[0] or "")
        local_development = (
            server_host in {"127.0.0.1", "localhost", "::1"}
            and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1"
        )
        if local_development:
            return set(), True
        raw = str(self.headers.get(PROJECT_SCOPE_HEADER) or self.headers.get(PROJECT_SCOPE_HEADER.lower()) or "")
        return _parse_project_scopes(raw)

    def _require_known_project(self, project: str, root: Path) -> bool:
        project = _safe_project_id(project)
        if _known_project_exists(root, project):
            return True
        self._json(
            {
                "ok": False,
                "error": "PROJECT_NOT_FOUND",
                "message": f"项目 '{project}' 不存在，请先选择有效项目。",
            },
            404,
        )
        return False
