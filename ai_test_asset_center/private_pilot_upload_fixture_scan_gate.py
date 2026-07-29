"""Typed private-pilot scan boundary for governed UI upload fixture bindings.

Runtime hydration remains the single authority. This boundary runs the same pure
binding check before the large scan handler so revoked, missing or drifted fixture
identities become actionable 400/409 responses instead of falling into the generic
V12 exception envelope with a traceback.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import private_pilot_scan_handlers as _handlers
from .ui_upload_fixture_runtime_binding import _hydrate_bindings

_INSTALL_MARKER = "_qualibug_upload_fixture_scan_gate_installed"
_ORIGINAL_HANDLER = "_qualibug_scan_handler_before_upload_fixture_gate"


def _fixture_binding_requested(body: dict[str, Any]) -> bool:
    return "ui_upload_fixture_ids" in body or "ui_file_bindings" in body


def install_upload_fixture_scan_gate() -> None:
    if getattr(_handlers.ScanHandlersMixin, _INSTALL_MARKER, False):
        return
    original = getattr(
        _handlers.ScanHandlersMixin,
        _ORIGINAL_HANDLER,
        _handlers.ScanHandlersMixin._handle_v12_scan,
    )
    setattr(_handlers.ScanHandlersMixin, _ORIGINAL_HANDLER, original)

    def handle_v12_scan_with_upload_fixture_gate(
        self: Any,
        project: str,
        root: Path,
        actor: dict[str, str],
        body: dict[str, Any],
    ) -> None:
        payload = dict(body or {})
        if _fixture_binding_requested(payload):
            try:
                _hydrate_bindings(project, Path(root), payload)
            except KeyError as exc:
                return self._json(
                    {
                        "ok": False,
                        "error": "UPLOAD_FIXTURE_BINDING_NOT_ACTIVE",
                        "message": (
                            "所选上传 Fixture 已撤销、不存在或不属于当前项目；"
                            "请刷新运行中心并重新选择活动审批 binding_ref。"
                        ),
                        "detail": str(exc)[:160],
                    },
                    409,
                )
            except PermissionError as exc:
                return self._json(
                    {
                        "ok": False,
                        "error": "UPLOAD_FIXTURE_BINDING_FORBIDDEN",
                        "message": "上传 Fixture 超出当前项目允许范围。",
                        "detail": str(exc)[:160],
                    },
                    403,
                )
            except ValueError as exc:
                return self._json(
                    {
                        "ok": False,
                        "error": "UPLOAD_FIXTURE_BINDING_BAD_REQUEST",
                        "message": "上传 Fixture 绑定请求格式无效，请刷新后重新选择。",
                        "detail": str(exc)[:200],
                    },
                    400,
                )
            except RuntimeError as exc:
                return self._json(
                    {
                        "ok": False,
                        "error": "UPLOAD_FIXTURE_BINDING_INTEGRITY_FAILED",
                        "message": (
                            "上传 Fixture 文件完整性校验失败；文件可能漂移、缺失或登记表不可用。"
                        ),
                        "detail": str(exc)[:200],
                    },
                    409,
                )
        return original(self, project, root, actor, body)

    _handlers.ScanHandlersMixin._handle_v12_scan = (
        handle_v12_scan_with_upload_fixture_gate
    )
    setattr(_handlers.ScanHandlersMixin, _INSTALL_MARKER, True)


__all__ = ["install_upload_fixture_scan_gate"]
