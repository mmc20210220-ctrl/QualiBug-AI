"""HTTP surface for operator authority decisions on unresolved conflicts.

Routes:

    GET  /api/v1/projects/{project}/authority-decisions
    POST /api/v1/projects/{project}/authority-decisions

POST body actions:
- SELECT_FACT — operator explicitly chooses the winning source-backed fact
- LEAVE_UNRESOLVED — operator explicitly keeps the conflict blocked

Never auto-picks authority. LLM / recency / filename / order / confidence are
not accepted decision authorities.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from .enterprise_knowledge_center._chinese_business_authority_decision import (
    ACTION_LEAVE_UNRESOLVED,
    ACTION_SELECT_FACT,
    list_operator_authority_decisions,
    record_operator_authority_decision,
)
from .private_pilot_http_routing import HttpRoutingMixin
from .real_project_onboarding import _safe_project_id

_INSTALL_MARKER = "_qualibug_authority_decision_http_patch_installed"
_ORIGINAL_GET = "_qualibug_original_get_before_authority_decisions"
_ORIGINAL_POST = "_qualibug_original_post_before_authority_decisions"
_ALLOWED_MUTATION_ROLES = {"knowledge_admin", "project_owner", "qa_lead", "admin"}
_DEFAULT_MAX_BODY = 1024 * 1024


def _text(value: Any, *, limit: int = 2000) -> str:
    return str(value or "").strip()[:limit]


def _route_project(path: str) -> str:
    parts = [unquote(part) for part in urlparse(path).path.split("/") if part]
    if not (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "projects"]
        and parts[4] == "authority-decisions"
    ):
        return ""
    raw = parts[3].strip()
    safe = _safe_project_id(raw)
    if not raw or raw != safe:
        return ""
    return safe


def _maximum_body_bytes() -> int:
    raw = os.environ.get("QUALIBUG_MAX_REQUEST_BODY", str(_DEFAULT_MAX_BODY))
    try:
        value = int(raw or _DEFAULT_MAX_BODY)
    except (TypeError, ValueError):
        return _DEFAULT_MAX_BODY
    return value if value > 0 else _DEFAULT_MAX_BODY


def _read_json_body(handler: Any) -> dict[str, Any]:
    try:
        size = int(handler.headers.get("Content-Length", "0") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("authority_decision_content_length_invalid") from exc
    if size <= 0:
        return {}
    if size > _maximum_body_bytes():
        raise ValueError("authority_decision_request_body_too_large")
    raw = handler.rfile.read(size)
    if len(raw) != size:
        raise ValueError("authority_decision_request_body_incomplete")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("authority_decision_request_json_invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("authority_decision_request_object_required")
    return payload


def _error_response(handler: Any, exc: Exception) -> Any:
    code = _text(exc)
    if isinstance(exc, PermissionError):
        return handler._json(
            {"ok": False, "error": "FORBIDDEN", "message": code},
            403,
        )
    if isinstance(exc, KeyError):
        return handler._json(
            {"ok": False, "error": "AUTHORITY_DECISION_NOT_FOUND", "message": code},
            404,
        )
    if isinstance(exc, ValueError) and code == "authority_decision_request_body_too_large":
        return handler._json(
            {"ok": False, "error": "PAYLOAD_TOO_LARGE", "message": code},
            413,
        )
    if isinstance(exc, ValueError):
        return handler._json(
            {"ok": False, "error": "AUTHORITY_DECISION_BAD_REQUEST", "message": code},
            400,
        )
    return handler._json(
        {
            "ok": False,
            "error": "AUTHORITY_DECISION_INTERNAL_ERROR",
            "message": code[:300],
        },
        500,
    )


def _authorize(handler: Any, project: str, *, mutation: bool) -> tuple[Any, Path] | None:
    root = handler._root()
    actor = handler._require_actor()
    if actor is None:
        return None
    if handler._require_tenant(root) is None:
        return None
    if not handler._require_project_scope(project):
        return None
    if not handler._require_known_project(project, root):
        return None
    if mutation and not handler._require_role(
        actor,
        _ALLOWED_MUTATION_ROLES,
        "operator authority decision",
    ):
        return None
    return actor, root


def _handle_get(handler: Any, project: str) -> Any:
    authorized = _authorize(handler, project, mutation=False)
    if authorized is None:
        return None
    _actor, root = authorized
    try:
        result = list_operator_authority_decisions(project, root=root)
    except Exception as exc:  # noqa: BLE001 - typed at HTTP boundary
        return _error_response(handler, exc)
    return handler._json({"ok": True, "data": result})


def _handle_post(handler: Any, project: str) -> Any:
    authorized = _authorize(handler, project, mutation=True)
    if authorized is None:
        return None
    actor, root = authorized
    try:
        body = _read_json_body(handler)
        action = _text(body.get("action")).upper()
        if action not in {ACTION_SELECT_FACT, ACTION_LEAVE_UNRESOLVED}:
            raise ValueError(
                "authority_decision_action_invalid_use_SELECT_FACT_or_LEAVE_UNRESOLVED"
            )
        result = record_operator_authority_decision(
            project,
            conflict_id=_text(body.get("conflict_id")),
            action=action,
            actor=actor,
            root=root,
            selected_fact_id=_text(body.get("selected_fact_id")),
            rationale=_text(body.get("rationale"), limit=2000),
            document_version=_text(body.get("document_version"), limit=200),
            rebuild=True,
        )
        return handler._json({"ok": True, "action": action, "data": result}, 201)
    except Exception as exc:  # noqa: BLE001 - typed at HTTP boundary
        return _error_response(handler, exc)


def install_authority_decision_http_patch() -> None:
    if getattr(HttpRoutingMixin, _INSTALL_MARKER, False):
        return
    original_get = HttpRoutingMixin.do_GET
    original_post = HttpRoutingMixin.do_POST
    setattr(HttpRoutingMixin, _ORIGINAL_GET, original_get)
    setattr(HttpRoutingMixin, _ORIGINAL_POST, original_post)

    def get_with_authority_decisions(handler: Any) -> Any:
        project = _route_project(handler.path)
        if not project:
            return original_get(handler)
        handler._init_request_context()
        return _handle_get(handler, project)

    def post_with_authority_decisions(handler: Any) -> Any:
        project = _route_project(handler.path)
        if not project:
            return original_post(handler)
        handler._init_request_context()
        return _handle_post(handler, project)

    HttpRoutingMixin.do_GET = get_with_authority_decisions
    HttpRoutingMixin.do_POST = post_with_authority_decisions
    setattr(HttpRoutingMixin, _INSTALL_MARKER, True)


def restore_authority_decision_http_patch() -> None:
    original_get = getattr(HttpRoutingMixin, _ORIGINAL_GET, None)
    original_post = getattr(HttpRoutingMixin, _ORIGINAL_POST, None)
    if callable(original_get):
        HttpRoutingMixin.do_GET = original_get
    if callable(original_post):
        HttpRoutingMixin.do_POST = original_post
    setattr(HttpRoutingMixin, _INSTALL_MARKER, False)


__all__ = [
    "install_authority_decision_http_patch",
    "restore_authority_decision_http_patch",
]
