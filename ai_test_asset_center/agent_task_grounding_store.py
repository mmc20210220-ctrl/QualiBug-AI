"""Persist Agent Task grounding results and their factual work events."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from .agent_task_store import (
    AgentTaskConflict,
    _AGENT_TASK_TERMINAL_STATUSES,
    _assert_scope,
    _event,
    _public_task,
    _read_task,
    _task_lock,
    _task_path,
    _utc_now,
)
from .private_pilot_json_io import _write_json_object_atomic


def _text(value: Any) -> str:
    return str(value or "").strip()


def apply_agent_task_grounding(
    root: Path,
    *,
    tenant_id: str,
    project_id: str,
    task_id: str,
    grounding: dict[str, Any],
    correlation_id: str = "",
) -> dict[str, Any]:
    """Atomically apply one grounding evaluation and append only factual events."""

    path = _task_path(root, project_id, task_id)
    with _task_lock(path):
        task = _read_task(path)
        _assert_scope(task, tenant_id=tenant_id, project_id=project_id)
        current_status = _text(task.get("status")).upper()
        if current_status in _AGENT_TASK_TERMINAL_STATUSES:
            raise AgentTaskConflict("agent_task_terminal")

        grounding_key = _text(grounding.get("grounding_key"))
        if grounding_key and grounding_key == _text(task.get("grounding_key")):
            return _public_task(task)

        source_snapshot = (
            copy.deepcopy(grounding.get("source_snapshot"))
            if isinstance(grounding.get("source_snapshot"), dict)
            else {"status": "NOT_PINNED", "snapshot_ref": ""}
        )
        selected_ids = [
            _text(item)
            for item in (grounding.get("selected_test_targets") or [])
            if _text(item)
        ]
        selected_snapshot = [
            copy.deepcopy(item)
            for item in (grounding.get("selected_test_target_snapshot") or [])
            if isinstance(item, dict)
        ]
        blockers = [
            copy.deepcopy(item)
            for item in (grounding.get("grounding_blockers") or [])
            if isinstance(item, dict)
        ]
        summary = (
            copy.deepcopy(grounding.get("grounding_summary"))
            if isinstance(grounding.get("grounding_summary"), dict)
            else {}
        )
        runtime_context = (
            copy.deepcopy(grounding.get("runtime_context"))
            if isinstance(grounding.get("runtime_context"), dict)
            else {}
        )
        task_status = _text(grounding.get("task_status")).upper() or "BLOCKED"
        runtime_grounding_status = (
            _text(grounding.get("runtime_grounding_status")).upper() or "BLOCKED"
        )
        now = _utc_now()
        events = task.get("events") if isinstance(task.get("events"), list) else []

        snapshot_status = _text(source_snapshot.get("status")).upper()
        snapshot_ref = _text(source_snapshot.get("snapshot_ref"))
        if snapshot_status in {"PINNED", "PINNED_STALE"} and snapshot_ref:
            events.append(
                _event(
                    task_id,
                    "UNDERSTANDING_SNAPSHOT_PINNED",
                    correlation_id=correlation_id,
                    detail={
                        "snapshot_ref": snapshot_ref,
                        "source_revision_state": _text(
                            source_snapshot.get("source_revision_state")
                        ),
                        "source_count": source_snapshot.get("source_count", 0),
                    },
                )
            )
        else:
            events.append(
                _event(
                    task_id,
                    "UNDERSTANDING_SNAPSHOT_UNAVAILABLE",
                    correlation_id=correlation_id,
                    detail={
                        "blocking_codes": [
                            _text(item.get("code")) for item in blockers if _text(item.get("code"))
                        ]
                    },
                )
            )

        events.append(
            _event(
                task_id,
                "TEST_TARGET_SELECTION_EVALUATED",
                correlation_id=correlation_id,
                detail={
                    "selected_target_count": len(selected_ids),
                    "runtime_bound_target_count": int(
                        summary.get("runtime_bound_target_count", 0) or 0
                    ),
                },
            )
        )

        if _text(task.get("intent")).lower() == "analyze_requirements":
            events.append(
                _event(
                    task_id,
                    "ANALYSIS_CONTEXT_EVALUATED",
                    correlation_id=correlation_id,
                    detail={
                        "status": task_status,
                        "blocking_codes": [
                            _text(item.get("code")) for item in blockers if _text(item.get("code"))
                        ],
                    },
                )
            )
        else:
            events.append(
                _event(
                    task_id,
                    "RUNTIME_GROUNDING_EVALUATED",
                    correlation_id=correlation_id,
                    detail={
                        "status": runtime_grounding_status,
                        "preflight_ready": bool(summary.get("preflight_ready")),
                        "selected_target_count": len(selected_ids),
                        "runtime_bound_target_count": int(
                            summary.get("runtime_bound_target_count", 0) or 0
                        ),
                        "blocking_codes": [
                            _text(item.get("code")) for item in blockers if _text(item.get("code"))
                        ],
                    },
                )
            )

        task["events"] = events
        task["source_snapshot"] = source_snapshot
        task["selected_test_targets"] = selected_ids
        task["selected_test_target_snapshot"] = selected_snapshot
        task["runtime_grounding_status"] = runtime_grounding_status
        task["runtime_context"] = runtime_context
        task["grounding_blockers"] = blockers
        task["grounding_summary"] = summary
        task["grounding_key"] = grounding_key
        task["grounding_evaluated_at"] = now
        task["status"] = task_status
        task["updated_at"] = now
        _write_json_object_atomic(path, task)
        return _public_task(task)
