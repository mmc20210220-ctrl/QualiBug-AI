"""Project-scoped private-pilot routes for governed UI upload fixtures."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import private_pilot_http_routing as _routing
from .real_project_onboarding import _safe_project_id
from .ui_upload_fixture_registry import (
    list_upload_fixtures,
    operate_upload_fixture_registry,
)

_INSTALL_MARKER = "_qualibug_upload_fixture_routes_installed"
_WRAPPER_MARKER = "_qualibug_upload_fixture_route_wrapper"
_DELEGATE_MARKER = "_qualibug_upload_fixture_route_delegate"
_ROUTE_TAIL = "ui-upload-fixtures"
_MUTATING_ACTIONS = frozenset({"register", "approve", "revoke"})


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _route_project(path: str) -> str:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "projects"]
        and parts[4] == _ROUTE_TAIL
    ):
        return _safe_project_id(parts[3])
    return ""


def _authorize(handler: Any, project: str, root: Path) -> dict[str, Any] | None:
    actor = handler._require_actor()
    if actor is None:
        return None
    if handler._require_tenant(root) is None:
        return None
    if not handler._require_project_scope(project):
        return None
    return actor


def _delegate(method: Any) -> Any:
    return getattr(method, _DELEGATE_MARKER, method)


def install_private_pilot_upload_fixture_routes() -> None:
    current_get = _routing.HttpRoutingMixin.do_GET
    current_post = _routing.HttpRoutingMixin.do_POST
    if (
        getattr(current_get, _WRAPPER_MARKER, False)
        and getattr(current_post, _WRAPPER_MARKER, False)
    ):
        setattr(_routing.HttpRoutingMixin, _INSTALL_MARKER, True)
        return

    # If another installer replaced one method after our first installation, wrap
    # the currently active method rather than jumping back to an early stale alias.
    original_get = _delegate(current_get)
    original_post = _delegate(current_post)

    def get_with_upload_fixture_registry(self: Any) -> None:
        parsed = urlparse(self.path)
        project = _route_project(parsed.path)
        if not project:
            return original_get(self)
        self._init_request_context()
        root = self._root()
        if _authorize(self, project, root) is None:
            return
        include_revoked = str(
            parse_qs(parsed.query).get("include_revoked", [""])[0]
        ).strip().lower() in {"1", "true", "yes", "on"}
        try:
            return self._json(
                list_upload_fixtures(
                    project,
                    root=root,
                    include_revoked=include_revoked,
                )
            )
        except RuntimeError as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "UPLOAD_FIXTURE_REGISTRY_UNAVAILABLE",
                    "message": str(exc),
                },
                409,
            )

    def post_with_upload_fixture_registry(self: Any) -> None:
        parsed = urlparse(self.path)
        project = _route_project(parsed.path)
        if not project:
            return original_post(self)
        self._init_request_context()
        root = self._root()
        actor = _authorize(self, project, root)
        if actor is None:
            return
        try:
            body = self._body()
        except Exception:
            return self._json({"ok": False, "error": "BAD_REQUEST"}, 400)
        action = _text(body.get("action") or "list", limit=40).lower()
        if action in _MUTATING_ACTIONS:
            from . import private_pilot_service as _service

            if not self._require_role(
                actor,
                _service.CONFIG_MANAGER_ROLES,
                "UI upload fixture governance",
            ):
                return
        try:
            result = operate_upload_fixture_registry(
                project,
                action,
                body.get("payload") if isinstance(body.get("payload"), dict) else body,
                root=root,
                actor=actor,
            )
            status_code = 201 if action == "register" else 200
            return self._json(result, status_code)
        except PermissionError as exc:
            return self._json(
                {"ok": False, "error": "FORBIDDEN", "message": str(exc)},
                403,
            )
        except (ValueError, KeyError, FileNotFoundError) as exc:
            return self._json(
                {"ok": False, "error": "BAD_REQUEST", "message": str(exc)},
                400,
            )
        except RuntimeError as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "UPLOAD_FIXTURE_REGISTRY_CONFLICT",
                    "message": str(exc),
                },
                409,
            )

    setattr(get_with_upload_fixture_registry, _WRAPPER_MARKER, True)
    setattr(get_with_upload_fixture_registry, _DELEGATE_MARKER, original_get)
    setattr(post_with_upload_fixture_registry, _WRAPPER_MARKER, True)
    setattr(post_with_upload_fixture_registry, _DELEGATE_MARKER, original_post)
    _routing.HttpRoutingMixin.do_GET = get_with_upload_fixture_registry
    _routing.HttpRoutingMixin.do_POST = post_with_upload_fixture_registry
    setattr(_routing.HttpRoutingMixin, _INSTALL_MARKER, True)


__all__ = ["install_private_pilot_upload_fixture_routes"]
