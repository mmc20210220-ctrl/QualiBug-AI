"""Project-scoped HTTP lifecycle for formal UI visual baselines.

The patch intercepts only:

    GET  /api/v1/projects/{project}/visual-baselines
    POST /api/v1/projects/{project}/visual-baselines

All other requests delegate unchanged to ``HttpRoutingMixin``. Registration
accepts base64 PNG bytes only; a client-supplied server ``file_path`` is never
consumed. The temporary upload is project-scoped and deleted after the governed
registry has copied and verified it.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .private_pilot_http_routing import HttpRoutingMixin
from .real_project_onboarding import _safe_project_id
from .visual_baseline_registry import (
    approve_visual_baseline,
    list_visual_baselines,
    register_visual_baseline,
    revoke_visual_baseline,
)

_INSTALL_MARKER = "_qualibug_visual_baseline_http_patch_installed"
_ORIGINAL_GET = "_qualibug_original_get_before_visual_baselines"
_ORIGINAL_POST = "_qualibug_original_post_before_visual_baselines"
_ALLOWED_MUTATION_ROLES = {"knowledge_admin", "project_owner", "qa_lead", "admin"}
_DEFAULT_MAX_BODY = 10 * 1024 * 1024


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _route_project(path: str) -> str:
    parts = [unquote(part) for part in urlparse(path).path.split("/") if part]
    if not (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "projects"]
        and parts[4] == "visual-baselines"
    ):
        return ""
    raw = parts[3].strip()
    safe = _safe_project_id(raw)
    # Do not let this route reinterpret an encoded slash, whitespace or any
    # other invalid identity as a different existing project.
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
        raise ValueError("visual_baseline_content_length_invalid") from exc
    if size <= 0:
        return {}
    if size > _maximum_body_bytes():
        raise ValueError("visual_baseline_request_body_too_large")
    raw = handler.rfile.read(size)
    if len(raw) != size:
        raise ValueError("visual_baseline_request_body_incomplete")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("visual_baseline_request_json_invalid") from exc
    if not isinstance(payload, dict):
        raise ValueError("visual_baseline_request_object_required")
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
            {"ok": False, "error": "VISUAL_BASELINE_NOT_FOUND", "message": code},
            404,
        )
    if isinstance(exc, ValueError) and code == "visual_baseline_request_body_too_large":
        return handler._json(
            {"ok": False, "error": "PAYLOAD_TOO_LARGE", "message": code},
            413,
        )
    if isinstance(exc, ValueError):
        return handler._json(
            {"ok": False, "error": "VISUAL_BASELINE_BAD_REQUEST", "message": code},
            400,
        )
    if isinstance(exc, RuntimeError) and code in {
        "visual_baseline_active_identity_conflict",
        "visual_baseline_active_identity_ambiguous",
        "visual_baseline_registry_busy",
        "visual_baseline_immutable_path_conflict",
    }:
        return handler._json(
            {"ok": False, "error": "VISUAL_BASELINE_CONFLICT", "message": code},
            409,
        )
    if isinstance(exc, RuntimeError) and code in {
        "visual_baseline_registry_corrupt",
        "visual_baseline_registry_schema_invalid",
    }:
        return handler._json(
            {
                "ok": False,
                "error": "VISUAL_BASELINE_REGISTRY_UNAVAILABLE",
                "message": code,
            },
            503,
        )
    return handler._json(
        {
            "ok": False,
            "error": "VISUAL_BASELINE_INTERNAL_ERROR",
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
        "visual baseline governance",
    ):
        return None
    return actor, root


def _handle_get(handler: Any, project: str) -> Any:
    authorized = _authorize(handler, project, mutation=False)
    if authorized is None:
        return None
    _actor, root = authorized
    include_revoked = (
        parse_qs(urlparse(handler.path).query).get("include_revoked", ["false"])[0]
        .strip()
        .lower()
        in {"1", "true", "yes", "on"}
    )
    try:
        result = list_visual_baselines(
            project,
            root=root,
            include_revoked=include_revoked,
        )
    except Exception as exc:  # noqa: BLE001 - typed at HTTP boundary
        return _error_response(handler, exc)
    return handler._json({"ok": True, "data": result})


def _register_from_body(
    handler: Any,
    project: str,
    body: dict[str, Any],
    root: Path,
    actor: dict[str, str],
) -> Any:
    content = _text(
        body.get("content") or body.get("data"),
        limit=_maximum_body_bytes() * 2,
    )
    if not content:
        raise ValueError("visual_baseline_base64_content_required")
    try:
        raw = base64.b64decode(content, validate=True)
    except Exception as exc:
        raise ValueError("visual_baseline_base64_decode_failed") from exc
    filename = Path(_text(body.get("filename") or "baseline.png", limit=240)).name
    if Path(filename).suffix.lower() != ".png":
        raise ValueError("visual_baseline_png_required")
    staging = root / "platform_workspace" / project / "visual_baseline_uploads"
    staging.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=".visual-baseline-upload-",
        suffix=".png",
        dir=str(staging),
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        result = register_visual_baseline(
            project,
            file_path=temporary,
            baseline_name=_text(body.get("baseline_name") or Path(filename).stem, limit=180),
            viewport_width=body.get("viewport_width"),
            viewport_height=body.get("viewport_height"),
            full_page=body.get("full_page"),
            root=root,
            actor=actor,
        )
    finally:
        Path(temporary).unlink(missing_ok=True)
    return handler._json(
        {"ok": True, "action": "register", "data": result},
        201 if result.get("status") == "REGISTERED" else 200,
    )


def _handle_post(handler: Any, project: str) -> Any:
    authorized = _authorize(handler, project, mutation=True)
    if authorized is None:
        return None
    actor, root = authorized
    try:
        body = _read_json_body(handler)
        submitted_project = _text(body.get("project_id"))
        if submitted_project:
            submitted_safe = _safe_project_id(submitted_project)
            if submitted_project != submitted_safe or submitted_safe != project:
                raise ValueError("visual_baseline_project_id_mismatch")
        action = _text(body.get("action") or "register").lower()
        if action == "register":
            return _register_from_body(handler, project, body, root, actor)
        if action == "approve":
            result = approve_visual_baseline(
                project,
                baseline_id=_text(body.get("baseline_id")),
                root=root,
                actor=actor,
            )
            return handler._json(
                {"ok": True, "action": "approve", "data": result},
                201 if result.get("status") == "APPROVED" else 200,
            )
        if action == "revoke":
            result = revoke_visual_baseline(
                project,
                baseline_id=_text(body.get("baseline_id")),
                reason=_text(body.get("reason"), limit=500),
                root=root,
                actor=actor,
            )
            return handler._json(
                {"ok": True, "action": "revoke", "data": result}
            )
        raise ValueError(
            "unsupported_visual_baseline_action_use_register_approve_or_revoke"
        )
    except Exception as exc:  # noqa: BLE001 - typed at HTTP boundary
        return _error_response(handler, exc)


def install_visual_baseline_http_patch() -> None:
    if getattr(HttpRoutingMixin, _INSTALL_MARKER, False):
        return
    original_get = HttpRoutingMixin.do_GET
    original_post = HttpRoutingMixin.do_POST
    setattr(HttpRoutingMixin, _ORIGINAL_GET, original_get)
    setattr(HttpRoutingMixin, _ORIGINAL_POST, original_post)

    def get_with_visual_baselines(handler: Any) -> Any:
        project = _route_project(handler.path)
        if not project:
            return original_get(handler)
        handler._init_request_context()
        return _handle_get(handler, project)

    def post_with_visual_baselines(handler: Any) -> Any:
        project = _route_project(handler.path)
        if not project:
            return original_post(handler)
        handler._init_request_context()
        return _handle_post(handler, project)

    HttpRoutingMixin.do_GET = get_with_visual_baselines
    HttpRoutingMixin.do_POST = post_with_visual_baselines
    setattr(HttpRoutingMixin, _INSTALL_MARKER, True)


def restore_visual_baseline_http_patch() -> None:
    original_get = getattr(HttpRoutingMixin, _ORIGINAL_GET, None)
    original_post = getattr(HttpRoutingMixin, _ORIGINAL_POST, None)
    if callable(original_get):
        HttpRoutingMixin.do_GET = original_get
    if callable(original_post):
        HttpRoutingMixin.do_POST = original_post
    setattr(HttpRoutingMixin, _INSTALL_MARKER, False)


__all__ = [
    "install_visual_baseline_http_patch",
    "restore_visual_baseline_http_patch",
]
