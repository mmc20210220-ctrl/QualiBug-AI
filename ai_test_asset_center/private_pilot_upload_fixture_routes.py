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
_ORIGINAL_GET = "_qualibug_http_get_before_upload_fixture_routes"
_ORIGINAL_POST = "_qualibug_http_post_before_upload_fixture_routes"
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


def install_private_pilot_upload_fixture_routes() -> None:
    if getattr(_routing.HttpRoutingMixin, _INSTALL_MARKER, False):
        return
    original_get = getattr(
        _routing.HttpRoutingMixin,
        _ORIGINAL_GET,
        _routing.HttpRoutingMixin.do_GET,
    )
    original_post = getattr(
        _routing.HttpRoutingMixin,
        _ORIGINAL_POST,
        _routing.HttpRoutingMixin.do_POST,
    )
    setattr(_routing.HttpRoutingMixin, _ORIGINAL_GET, original_get)
    setattr(_routing.HttpRoutingMixin, _ORIGINAL_POST, original_post)

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

    _routing.HttpRoutingMixin.do_GET = get_with_upload_fixture_registry
    _routing.HttpRoutingMixin.do_POST = post_with_upload_fixture_registry
    setattr(_routing.HttpRoutingMixin, _INSTALL_MARKER, True)


__all__ = ["install_private_pilot_upload_fixture_routes"]
