"""Durable, privacy-safe live stage projection for one project scan.

Only code that actually owns a stage boundary may update that stage. Missing
instrumentation stays PENDING/UNREPORTED; callers must never advance stages by
time or percentage heuristics.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .private_pilot_json_io import _write_json_object_atomic
from .project_runtime_primitives import safe_project_id

_STAGE_ORDER = (
    "enterprise_understanding",
    "scenario_planning",
    "runtime_execution",
    "evidence_collection",
    "test_data_assessment",
    "delivery_finalization",
)
_ALLOWED_STATUS = frozenset({"pending", "active", "completed", "failed", "blocked", "unreported"})


def _path(root: Path, project: str) -> Path:
    return (
        Path(root).resolve()
        / "platform_workspace"
        / safe_project_id(project)
        / ".runtime"
        / "scan_stage_progress.json"
    )


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _empty(project: str) -> dict[str, Any]:
    return {
        "schema": "qualibug.scan-stage-progress.v1",
        "project_id": safe_project_id(project),
        "started_at_utc": _now_iso(),
        "updated_at_utc": _now_iso(),
        "stages": {
            stage: {
                "status": "pending",
                "started_at_utc": "",
                "finished_at_utc": "",
                "detail": "",
            }
            for stage in _STAGE_ORDER
        },
    }


def read_scan_stage_progress(root: Path, project: str) -> dict[str, Any]:
    path = _path(root, project)
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}
    if not isinstance(payload, dict) or payload.get("schema") != "qualibug.scan-stage-progress.v1":
        return {}
    stages = payload.get("stages") if isinstance(payload.get("stages"), dict) else {}
    safe_stages: dict[str, dict[str, str]] = {}
    for stage in _STAGE_ORDER:
        item = stages.get(stage) if isinstance(stages.get(stage), dict) else {}
        status = str(item.get("status") or "pending").lower()
        if status not in _ALLOWED_STATUS:
            status = "unreported"
        safe_stages[stage] = {
            "status": status,
            "started_at_utc": str(item.get("started_at_utc") or "")[:64],
            "finished_at_utc": str(item.get("finished_at_utc") or "")[:64],
            "detail": str(item.get("detail") or "")[:240],
        }
    return {
        "schema": "qualibug.scan-stage-progress.v1",
        "project_id": safe_project_id(project),
        "started_at_utc": str(payload.get("started_at_utc") or "")[:64],
        "updated_at_utc": str(payload.get("updated_at_utc") or "")[:64],
        "stages": safe_stages,
    }


def begin_scan_stage_progress(root: Path, project: str) -> dict[str, Any]:
    payload = _empty(project)
    _write_json_object_atomic(_path(root, project), payload)
    return payload


def mark_scan_stage(
    root: Path,
    project: str,
    stage: str,
    status: str,
    *,
    detail: str = "",
) -> dict[str, Any]:
    stage_key = str(stage or "").strip()
    if stage_key not in _STAGE_ORDER:
        raise ValueError(f"unknown scan stage: {stage_key}")
    normalized_status = str(status or "").strip().lower()
    if normalized_status not in _ALLOWED_STATUS - {"pending", "unreported"}:
        raise ValueError(f"invalid scan stage status: {normalized_status}")

    payload = read_scan_stage_progress(root, project) or begin_scan_stage_progress(root, project)
    stages = payload["stages"]
    item = dict(stages.get(stage_key) or {})
    now = _now_iso()
    if normalized_status == "active" and not item.get("started_at_utc"):
        item["started_at_utc"] = now
    if normalized_status in {"completed", "failed", "blocked"}:
        if not item.get("started_at_utc"):
            item["started_at_utc"] = now
        item["finished_at_utc"] = now
    item["status"] = normalized_status
    item["detail"] = str(detail or "")[:240]
    stages[stage_key] = item
    payload["updated_at_utc"] = now
    _write_json_object_atomic(_path(root, project), payload)
    return payload


def fail_active_scan_stage(root: Path, project: str, *, detail: str = "") -> dict[str, Any]:
    payload = read_scan_stage_progress(root, project)
    if not payload:
        return {}
    stages = payload.get("stages") if isinstance(payload.get("stages"), dict) else {}
    for stage in _STAGE_ORDER:
        item = stages.get(stage) if isinstance(stages.get(stage), dict) else {}
        if item.get("status") == "active":
            return mark_scan_stage(root, project, stage, "failed", detail=detail)
    return payload


__all__ = [
    "begin_scan_stage_progress",
    "fail_active_scan_stage",
    "mark_scan_stage",
    "read_scan_stage_progress",
]
