"""Typed scan boundary for governed UI upload scenarios."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from . import private_pilot_scan_handlers as _handlers
from .ui_upload_scenario_runtime_binding import _hydrate_scenarios

_INSTALL_MARKER = "_qualibug_upload_scenario_scan_gate_installed"
_ORIGINAL_HANDLER = "_qualibug_scan_handler_before_upload_scenario_gate"


def _scenario_requested(body: dict[str, Any]) -> bool:
    return "ui_upload_scenario_ids" in body


def install_ui_upload_scenario_scan_gate() -> None:
    if getattr(_handlers.ScanHandlersMixin, _INSTALL_MARKER, False):
        return
    original = getattr(
        _handlers.ScanHandlersMixin,
        _ORIGINAL_HANDLER,
        _handlers.ScanHandlersMixin._handle_v12_scan,
    )
    setattr(_handlers.ScanHandlersMixin, _ORIGINAL_HANDLER, original)

    def handle_v12_scan_with_upload_scenario_gate(
        self: Any,
        project: str,
        root: Path,
        actor: dict[str, str],
        body: dict[str, Any],
    ) -> None:
        payload = dict(body or {})
        if _scenario_requested(payload):
            try:
                payload = _hydrate_scenarios(project, Path(root), payload)
            except KeyError as exc:
                return self._json(
                    {
                        "ok": False,
                        "error": "UPLOAD_SCENARIO_NOT_ACTIVE",
                        "message": (
                            "所选上传场景已撤销、不存在，或其来源/Fixture 已失效；"
                            "请刷新运行中心并重新选择活动审批场景。"
                        ),
                        "detail": str(exc)[:160],
                    },
                    409,
                )
            except PermissionError as exc:
                return self._json(
                    {
                        "ok": False,
                        "error": "UPLOAD_SCENARIO_FORBIDDEN",
                        "message": "上传场景超出当前项目允许范围。",
                        "detail": str(exc)[:160],
                    },
                    403,
                )
            except ValueError as exc:
                return self._json(
                    {
                        "ok": False,
                        "error": "UPLOAD_SCENARIO_BAD_REQUEST",
                        "message": "上传场景选择或合同身份格式无效。",
                        "detail": str(exc)[:200],
                    },
                    400,
                )
            except RuntimeError as exc:
                return self._json(
                    {
                        "ok": False,
                        "error": "UPLOAD_SCENARIO_INTEGRITY_FAILED",
                        "message": (
                            "上传场景完整性校验失败；来源版本、合同或 Fixture 可能已变化。"
                        ),
                        "detail": str(exc)[:200],
                    },
                    409,
                )
        return original(self, project, root, actor, payload)

    _handlers.ScanHandlersMixin._handle_v12_scan = (
        handle_v12_scan_with_upload_scenario_gate
    )
    setattr(_handlers.ScanHandlersMixin, _INSTALL_MARKER, True)


__all__ = ["install_ui_upload_scenario_scan_gate"]
