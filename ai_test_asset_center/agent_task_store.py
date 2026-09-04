"""Persistent Agent Task and event-ledger authority.

Agent Tasks are project-scoped orchestration records. They persist the operator's
quality goal and immutable work events without granting execution authority.
Runtime grounding, Preflight and Scan remain separate authorities until an
explicit integration records those transitions.
"""
from __future__ import annotations

import copy
import json
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .private_pilot_json_io import _write_json_object_atomic

AGENT_TASK_SCHEMA = "qualibug.agent-task.v1"
AGENT_TASK_EVENT_SCHEMA = "qualibug.agent-task-event.v1"
AGENT_TASK_INTENTS = frozenset(
    {
        "release_readiness",
        "find_blockers",
        "verify_changes",
        "analyze_requirements",
    }
)
AGENT_TASK_STATUSES = frozenset(
    {
        "CREATED",
        "UNDERSTANDING",
        "PLANNING",
        "BLOCKED",
        "READY",
        "RUNNING",
        "EVALUATING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    }
)
_AGENT_TASK_TERMINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
_AGENT_TASK_LOCKS: dict[str, threading.Lock] = {}
_AGENT_TASK_LOCKS_GUARD = threading.Lock()


class AgentTaskError(RuntimeError):
    """Base Agent Task authority error."""


class AgentTaskNotFound(AgentTaskError):
    """Task does not exist in the requested tenant/project scope."""


class AgentTaskConflict(AgentTaskError):
    """Requested mutation conflicts with the persisted task lifecycle."""


class AgentTaskValidationError(AgentTaskError):
    """Task payload does not satisfy the stable contract."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _task_dir(root: Path, project_id: str) -> Path:
    return root / "platform_workspace" / project_id / "agent_tasks"


def _validated_task_id(task_id: str) -> str:
    value = str(task_id or "").strip()
    if not value or len(value) > 96:
        raise AgentTaskValidationError("agent_task_id_invalid")
    if any(not (char.isalnum() or char in {"_", "-"}) for char in value):
        raise AgentTaskValidationError("agent_task_id_invalid")
    return value


def _task_path(root: Path, project_id: str, task_id: str) -> Path:
    return _task_dir(root, project_id) / f"{_validated_task_id(task_id)}.json"


def _task_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _AGENT_TASK_LOCKS_GUARD:
        lock = _AGENT_TASK_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _AGENT_TASK_LOCKS[key] = lock
        return lock


def _read_task(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        raise AgentTaskNotFound("agent_task_not_found")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != AGENT_TASK_SCHEMA:
        raise AgentTaskError("agent_task_artifact_invalid")
    return payload


def _assert_scope(task: dict[str, Any], *, tenant_id: str, project_id: str) -> None:
    if str(task.get("tenant_id") or "") != str(tenant_id or ""):
        raise AgentTaskNotFound("agent_task_not_found")
    if str(task.get("project_id") or "") != str(project_id or ""):
        raise AgentTaskNotFound("agent_task_not_found")


def _event(
    task_id: str,
    event_type: str,
    *,
    correlation_id: str = "",
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_type = str(event_type or "").strip().upper()
    if not normalized_type:
        raise AgentTaskValidationError("agent_task_event_type_required")
    return {
        "schema_version": AGENT_TASK_EVENT_SCHEMA,
        "event_id": f"evt_{uuid.uuid4().hex}",
        "task_id": task_id,
        "event_type": normalized_type,
        "occurred_at": _utc_now(),
        "correlation_id": str(correlation_id or "").strip()[:128],
        "detail": copy.deepcopy(detail or {}),
    }


def _public_task(task: dict[str, Any]) -> dict[str, Any]:
    events = task.get("events") if isinstance(task.get("events"), list) else []
    latest_event = events[-1] if events and isinstance(events[-1], dict) else None
    public = {key: copy.deepcopy(value) for key, value in task.items() if key != "events"}
    public["event_count"] = len(events)
    public["latest_event"] = copy.deepcopy(latest_event)
    return public


def create_agent_task(
    root: Path,
    *,
    tenant_id: str,
    project_id: str,
    goal: str,
    intent: str,
    actor_role: str = "",
    correlation_id: str = "",
) -> dict[str, Any]:
    normalized_goal = str(goal or "").strip()
    normalized_intent = str(intent or "").strip().lower()
    if not normalized_goal:
        raise AgentTaskValidationError("agent_task_goal_required")
    if len(normalized_goal) > 4000:
        raise AgentTaskValidationError("agent_task_goal_too_long")
    if normalized_intent not in AGENT_TASK_INTENTS:
        raise AgentTaskValidationError("agent_task_intent_invalid")

    task_id = f"agt_{uuid.uuid4().hex}"
    created_at = _utc_now()
    created_event = _event(
        task_id,
        "TASK_CREATED",
        correlation_id=correlation_id,
        detail={
            "intent": normalized_intent,
            "execution_authority": "NOT_REQUESTED",
        },
    )
    task = {
        "schema_version": AGENT_TASK_SCHEMA,
        "task_id": task_id,
        "tenant_id": str(tenant_id or "").strip(),
        "project_id": str(project_id or "").strip(),
        "goal": normalized_goal,
        "intent": normalized_intent,
        "intent_source": "explicit_client_contract",
        "status": "CREATED",
        "source_snapshot": {
            "status": "NOT_PINNED",
            "snapshot_ref": "",
        },
        "selected_test_targets": [],
        "execution_run_id": "",
        "runtime_grounding_status": "NOT_REQUESTED",
        "created_by_role": str(actor_role or "").strip(),
        "created_at": created_at,
        "updated_at": created_at,
        "completed_at": "",
        "cancelled_at": "",
        "events": [created_event],
    }
    path = _task_path(root, project_id, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _task_lock(path):
        if path.exists():
            raise AgentTaskConflict("agent_task_id_collision")
        _write_json_object_atomic(path, task)
    return _public_task(task)


def get_agent_task(
    root: Path,
    *,
    tenant_id: str,
    project_id: str,
    task_id: str,
) -> dict[str, Any]:
    path = _task_path(root, project_id, task_id)
    with _task_lock(path):
        task = _read_task(path)
        _assert_scope(task, tenant_id=tenant_id, project_id=project_id)
        return _public_task(task)


def list_agent_task_events(
    root: Path,
    *,
    tenant_id: str,
    project_id: str,
    task_id: str,
) -> list[dict[str, Any]]:
    path = _task_path(root, project_id, task_id)
    with _task_lock(path):
        task = _read_task(path)
        _assert_scope(task, tenant_id=tenant_id, project_id=project_id)
        events = task.get("events") if isinstance(task.get("events"), list) else []
        return [copy.deepcopy(item) for item in events if isinstance(item, dict)]


def append_agent_task_event(
    root: Path,
    *,
    tenant_id: str,
    project_id: str,
    task_id: str,
    event_type: str,
    correlation_id: str = "",
    detail: dict[str, Any] | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    path = _task_path(root, project_id, task_id)
    with _task_lock(path):
        task = _read_task(path)
        _assert_scope(task, tenant_id=tenant_id, project_id=project_id)
        current_status = str(task.get("status") or "")
        if current_status in _AGENT_TASK_TERMINAL_STATUSES:
            raise AgentTaskConflict("agent_task_terminal")
        next_status = current_status
        if status is not None:
            normalized_status = str(status or "").strip().upper()
            if normalized_status not in AGENT_TASK_STATUSES:
                raise AgentTaskValidationError("agent_task_status_invalid")
            next_status = normalized_status
        events = task.get("events") if isinstance(task.get("events"), list) else []
        events.append(
            _event(
                task_id,
                event_type,
                correlation_id=correlation_id,
                detail=detail,
            )
        )
        task["events"] = events
        task["status"] = next_status
        task["updated_at"] = _utc_now()
        if next_status in {"COMPLETED", "FAILED"}:
            task["completed_at"] = task["updated_at"]
        _write_json_object_atomic(path, task)
        return _public_task(task)


def cancel_agent_task(
    root: Path,
    *,
    tenant_id: str,
    project_id: str,
    task_id: str,
    correlation_id: str = "",
) -> dict[str, Any]:
    path = _task_path(root, project_id, task_id)
    with _task_lock(path):
        task = _read_task(path)
        _assert_scope(task, tenant_id=tenant_id, project_id=project_id)
        current_status = str(task.get("status") or "")
        if current_status in _AGENT_TASK_TERMINAL_STATUSES:
            if current_status == "CANCELLED":
                return _public_task(task)
            raise AgentTaskConflict("agent_task_terminal")
        cancelled_at = _utc_now()
        events = task.get("events") if isinstance(task.get("events"), list) else []
        events.append(
            _event(
                task_id,
                "TASK_CANCELLED",
                correlation_id=correlation_id,
                detail={"previous_status": current_status},
            )
        )
        task["events"] = events
        task["status"] = "CANCELLED"
        task["cancelled_at"] = cancelled_at
        task["updated_at"] = cancelled_at
        _write_json_object_atomic(path, task)
        return _public_task(task)
