"""HTTP authentication, authorization scope and response helpers.

Every protected request is authorized from one authenticated principal. Tenant,
actor and role come from the same credential; project access comes from the
server-side tenant/project registry. Caller-provided actor, role and scope
headers are never authorization authorities.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from . import db_persistence as db_persist
from .private_pilot_debug_client import _dbg_report
from .private_pilot_project_assets import _known_project_exists, _root
from .private_pilot_request_limits import MAX_JSON_BODY_BYTES, content_length
from .private_pilot_tenant_auth import (
    TenantAuthenticationError,
    _principal_from_headers,
)
from .product_logging import get_logger
from .real_project_onboarding import _safe_project_id

_http_logger = get_logger("qualibug.http")


class AuthScopeMixin:
    def _root(self) -> Path:
        configured = getattr(self.server, "qualibug_private_root", None)
        return Path(configured).resolve() if configured else _root()

    def _json(
        self,
        body: Any,
        status: int = 200,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        req_start = getattr(self, "_qualibug_req_start", 0.0)
        elapsed_ms = int((time.time() - req_start) * 1000) if req_start else -1
        correlation_id = getattr(self, "_qualibug_corr_id", "")
        method = getattr(self, "command", "?")
        path = getattr(self, "path", "?")
        context = {
            "method": method,
            "path": path,
            "status": status,
            "elapsed_ms": elapsed_ms,
            "correlation_id": correlation_id,
        }
        if status >= 500:
            _http_logger.error(
                f"{method} {path} -> {status} ({elapsed_ms}ms)",
                extra={"error_code": "QB-S999", "context": context},
            )
        elif status >= 400:
            _http_logger.warning(
                f"{method} {path} -> {status} ({elapsed_ms}ms)",
                extra={"context": context},
            )
        else:
            _http_logger.info(
                f"{method} {path} -> {status} ({elapsed_ms}ms)",
                extra={"context": context},
            )
        try:
            raw = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if extra_headers:
                for key, value in extra_headers.items():
                    self.send_header(key, value)
            self.end_headers()
            self.wfile.write(raw)
        except (ConnectionAbortedError, ConnectionResetError, OSError):
            pass
        except Exception as exc:
            _http_logger.error(
                f"JSON response failed: {type(exc).__name__}: {exc}",
                exc_info=True,
                extra={
                    "error_code": "QB-S999",
                    "context": {"status": status, "path": path},
                },
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
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def _project(self) -> str:
        query = parse_qs(urlparse(self.path).query)
        return _safe_project_id((query.get("project") or [""])[0])

    def _principal(self) -> dict[str, str]:
        cached = getattr(self, "_validated_principal", None)
        if isinstance(cached, dict) and cached.get("tenant_id"):
            return dict(cached)
        principal = _principal_from_headers(dict(self.headers), root=self._root())
        if principal.get("auth_type") == "local_development":
            server_host = str(
                getattr(self.server, "server_address", ("", 0))[0] or ""
            )
            if server_host not in {"127.0.0.1", "localhost", "::1"}:
                raise TenantAuthenticationError(
                    "local development authentication is restricted to loopback binding"
                )
        self._validated_principal = dict(principal)
        self._validated_tenant_id = str(principal.get("tenant_id") or "")
        return dict(principal)

    def _request_tenant(self) -> str:
        return str(self._principal().get("tenant_id") or "")

    def _require_tenant(self, root: Path) -> str | None:
        del root
        try:
            return self._request_tenant()
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

    def _body(self) -> dict[str, Any]:
        size = content_length(self.headers)
        if not size:
            return {}
        if size > MAX_JSON_BODY_BYTES:
            raise ValueError(
                f"JSON request body exceeds {MAX_JSON_BODY_BYTES} byte limit."
            )
        raw = self.rfile.read(size)
        if len(raw) != size:
            raise ValueError("Request body ended before Content-Length bytes were read.")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body must be a UTF-8 JSON object.") from exc
        if not isinstance(parsed, dict):
            raise ValueError("Request body must be a JSON object.")
        return parsed

    def _require_actor(self) -> dict[str, str] | None:
        try:
            principal = self._principal()
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
        return {
            "name": str(principal.get("name") or "")[:120],
            "role": str(principal.get("role") or "")[:64],
        }

    def _require_role(
        self,
        actor: dict[str, str],
        allowed: set[str],
        action: str,
    ) -> bool:
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

    def _tenant_project_ids(self) -> set[str]:
        principal = self._principal()
        if principal.get("auth_type") == "local_development":
            return set()
        tenant_id = str(principal.get("tenant_id") or "")
        if not tenant_id:
            return set()
        rows = db_persist.list_projects(self._root(), tenant_id)
        return {
            _safe_project_id(row.get("project_id"))
            for row in rows
            if isinstance(row, dict) and str(row.get("project_id") or "").strip()
        }

    def _require_project_scope(self, project: str) -> bool:
        try:
            safe_project = _safe_project_id(project)
            principal = self._principal()
            if principal.get("auth_type") == "local_development":
                return True
            if safe_project in self._tenant_project_ids():
                return True
        except (TenantAuthenticationError, ValueError) as exc:
            self._json(
                {
                    "ok": False,
                    "error": "PROJECT_SCOPE_FORBIDDEN",
                    "message": str(exc),
                },
                403,
            )
            return False
        self._json(
            {
                "ok": False,
                "error": "PROJECT_SCOPE_FORBIDDEN",
                "message": "Requested project is not owned by the authenticated tenant.",
            },
            403,
        )
        return False

    def _project_list_scope_filter(self) -> tuple[set[str], bool]:
        principal = self._principal()
        if principal.get("auth_type") == "local_development":
            return set(), True
        return self._tenant_project_ids(), False

    def _require_known_project(self, project: str, root: Path) -> bool:
        try:
            safe_project = _safe_project_id(project)
        except ValueError:
            self._json(
                {
                    "ok": False,
                    "error": "PROJECT_NOT_FOUND",
                    "message": "项目标识不合法。",
                },
                404,
            )
            return False
        if _known_project_exists(root, safe_project):
            return True
        self._json(
            {
                "ok": False,
                "error": "PROJECT_NOT_FOUND",
                "message": f"项目 '{safe_project}' 不存在，请先选择有效项目。",
            },
            404,
        )
        return False


from .private_pilot_visual_baseline_http_patch import (  # noqa: E402
    install_visual_baseline_http_patch,
)

install_visual_baseline_http_patch()
