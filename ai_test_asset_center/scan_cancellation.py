"""Operator-initiated scan cancellation authority.

A cancel request targets the CURRENT live scan lease of one project. The
marker file lives inside the lease directory itself, so it is removed together
with the lease when the scan finishes, dies, or is reclaimed as stale — a
finished run can never leak a cancel request into a later scan. Honoring is
cooperative: checkpoints between experiments consume the marker and defer all
remaining work with terminal receipts; an in-flight experiment is never killed
mid-transport.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from .private_pilot_json_io import _write_json_object_atomic
from .private_pilot_scan_coordinator import active_scan_owner
from .project_runtime_primitives import safe_project_id

_LOGGER = logging.getLogger(__name__)

CANCEL_REQUEST_SCHEMA = "qualibug.scan-cancel-request.v1"


def _cancel_path(root: Path, project: str) -> Path:
    safe = safe_project_id(project)
    return (
        Path(root).resolve()
        / "platform_workspace"
        / safe
        / ".runtime_locks"
        / "scan.lock"
        / "cancel.request.json"
    )


def _public_owner(owner: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": str(owner.get("schema") or ""),
        "token": str(owner.get("token") or ""),
        "project_id": str(owner.get("project_id") or ""),
        "mode": str(owner.get("mode") or ""),
        "started_at_utc": str(owner.get("started_at_utc") or ""),
    }


def request_scan_cancel(
    root: Path,
    project: str,
    *,
    requester: dict[str, Any] | None = None,
    expected_token: str = "",
) -> dict[str, Any]:
    """Register a cancel request against the live scan lease, fail-closed otherwise.

    The marker is written only inside an existing live lease directory; this
    function never creates the lease directory itself, so a cancel request can
    never block a future scan acquisition.
    """

    resolved = Path(root).resolve()
    owner = active_scan_owner(resolved, project)
    if not owner:
        return {
            "requested": False,
            "reason_code": "NO_ACTIVE_SCAN",
            "message": "当前没有正在运行的检测任务。",
            "active_scan": {},
        }
    if expected_token and str(owner.get("token") or "") != expected_token:
        return {"requested": False, "reason_code": "SCAN_OWNER_MISMATCH", "message": "该任务绑定的运行已结束或已被另一运行替换。", "active_scan": {}}
    payload = {
        "schema": CANCEL_REQUEST_SCHEMA,
        "target_token": str(owner.get("token") or ""),
        "requested_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "requester": {
            "name": str((requester or {}).get("name") or "")[:120],
            "role": str((requester or {}).get("role") or "")[:64],
        },
    }
    path = _cancel_path(resolved, project)
    try:
        # The lease directory exists because the lease is live; never mkdir.
        _write_json_object_atomic(path, payload)
    except Exception as exc:
        _LOGGER.warning(
            "scan_cancel_request_write_failed path=%s error_type=%s error=%s",
            path,
            type(exc).__name__,
            str(exc)[:240],
            exc_info=True,
        )
        return {
            "requested": False,
            "reason_code": "CANCEL_REQUEST_PERSIST_FAILED",
            "message": "取消请求写入失败，请重试。",
            "active_scan": {},
        }
    return {
        "requested": True,
        "reason_code": "SCAN_CANCEL_REQUESTED",
        "message": "取消请求已登记，将在当前实验边界安全停止。",
        "active_scan": _public_owner(owner),
    }


def read_scan_cancel_request(root: Path, project: str) -> dict[str, Any]:
    """Return the pending cancel request payload, or {} when none is valid."""

    path = _cancel_path(Path(root).resolve(), project)
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}
    if not isinstance(payload, dict) or payload.get("schema") != CANCEL_REQUEST_SCHEMA:
        return {}
    owner = active_scan_owner(Path(root).resolve(), project)
    if not owner or str(owner.get("token") or "") != str(payload.get("target_token") or ""):
        # The targeted lease is gone or was replaced; the request is obsolete.
        return {}
    return payload


def consume_scan_cancel_request(root: Path, project: str) -> dict[str, Any]:
    """Consume a valid cancel request once; returns {} when nothing is pending."""

    path = _cancel_path(Path(root).resolve(), project)
    payload = read_scan_cancel_request(root, project)
    if not payload:
        return {}
    try:
        path.unlink(missing_ok=True)
    except Exception as exc:
        _LOGGER.warning(
            "scan_cancel_request_cleanup_failed path=%s error_type=%s error=%s",
            path,
            type(exc).__name__,
            str(exc)[:240],
            exc_info=True,
        )
    return payload


__all__ = [
    "CANCEL_REQUEST_SCHEMA",
    "consume_scan_cancel_request",
    "read_scan_cancel_request",
    "request_scan_cancel",
]
