"""Project-scoped private-pilot routes for governed UI upload scenarios."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import private_pilot_http_routing as _routing
from .real_project_onboarding import _safe_project_id
from .ui_upload_scenario_registry import (
    list_upload_scenarios,
    operate_upload_scenario_registry,
)
from .ui_upload_scenario_semantic_authority import (
    install_ui_upload_scenario_semantic_authority,
)
from .ui_upload_scenario_source_authority import (
    install_ui_upload_scenario_source_authority,
)

_INSTALL_MARKER = "_qualibug_upload_scenario_routes_installed"
_WRAPPER_MARKER = "_qualibug_upload_scenario_route_wrapper"
_DELEGATE_MARKER = "_qualibug_upload_scenario_route_delegate"
_ROUTE_TAIL = "ui-upload-scenarios"
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


def _require_mutation_role(handler: Any, actor: dict[str, Any]) -> bool:
    from . import private_pilot_service as _service

    return bool(
        handler._require_role(
            actor,
            _service.CONFIG_MANAGER_ROLES,
            "UI upload scenario governance",
        )
    )


def _delegate(method: Any) -> Any:
    return getattr(method, _DELEGATE_MARKER, method)


def install_private_pilot_ui_upload_scenario_routes() -> None:
    # Route installation is a supported standalone bootstrap in tests and private
    # deployments; always install the canonical source and semantic authorities
    # before capturing registry callables.
    install_ui_upload_scenario_source_authority()
    install_ui_upload_scenario_semantic_authority()
    current_get = _routing.HttpRoutingMixin.do_GET
    current_post = _routing.HttpRoutingMixin.do_POST
    if (
        getattr(current_get, _WRAPPER_MARKER, False)
        and getattr(current_post, _WRAPPER_MARKER, False)
    ):
        setattr(_routing.HttpRoutingMixin, _INSTALL_MARKER, True)
        return
    original_get = _delegate(current_get)
    original_post = _delegate(current_post)

    def get_with_upload_scenarios(self: Any) -> None:
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
                list_upload_scenarios(
                    project,
                    root=root,
                    include_revoked=include_revoked,
                )
            )
        except RuntimeError as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "UPLOAD_SCENARIO_REGISTRY_UNAVAILABLE",
                    "message": str(exc),
                },
                409,
            )

    def post_with_upload_scenarios(self: Any) -> None:
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
        if action in _MUTATING_ACTIONS and not _require_mutation_role(self, actor):
            return
        try:
            result = operate_upload_scenario_registry(
                project,
                action,
                body.get("payload") if isinstance(body.get("payload"), dict) else body,
                root=root,
                actor=actor,
            )
            status = 201 if action == "register" else 200
            return self._json(result, status)
        except PermissionError as exc:
            return self._json(
                {"ok": False, "error": "FORBIDDEN", "message": str(exc)},
                403,
            )
        except (TypeError, ValueError) as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "UPLOAD_SCENARIO_BAD_REQUEST",
                    "message": str(exc),
                },
                400,
            )
        except KeyError as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "UPLOAD_SCENARIO_AUTHORITY_NOT_ACTIVE",
                    "message": str(exc),
                },
                409,
            )
        except RuntimeError as exc:
            return self._json(
                {
                    "ok": False,
                    "error": "UPLOAD_SCENARIO_REGISTRY_CONFLICT",
                    "message": str(exc),
                },
                409,
            )

    setattr(get_with_upload_scenarios, _WRAPPER_MARKER, True)
    setattr(get_with_upload_scenarios, _DELEGATE_MARKER, original_get)
    setattr(post_with_upload_scenarios, _WRAPPER_MARKER, True)
    setattr(post_with_upload_scenarios, _DELEGATE_MARKER, original_post)
    _routing.HttpRoutingMixin.do_GET = get_with_upload_scenarios
    _routing.HttpRoutingMixin.do_POST = post_with_upload_scenarios
    setattr(_routing.HttpRoutingMixin, _INSTALL_MARKER, True)


__all__ = ["install_private_pilot_ui_upload_scenario_routes"]
