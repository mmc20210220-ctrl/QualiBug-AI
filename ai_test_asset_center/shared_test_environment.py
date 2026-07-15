"""Controlled shared-test-environment execution and cleanup evidence.

This module deliberately never guesses a DELETE endpoint.  Writes are allowed
only when a flow supplies an explicit cleanup/compensation mapping or an
explicitly approved environment-reset contract.  Every test run records its
created/mutated resources and emits a cleanup receipt that downstream finding
validation can reference.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


@dataclass
class TestDataRecord:
    resource_type: str
    resource_id: str
    operation: str
    endpoint: str
    before_snapshot_ref: str = ""
    after_snapshot_ref: str = ""
    created_by_run: bool = False
    restoration_required: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TestRunSession:
    project_id: str
    environment_id: str
    run_id: str = field(default_factory=lambda: f"run-{uuid.uuid4().hex[:16]}")
    approval_scope: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=_now)
    ended_at: str = ""
    status: str = "RUNNING"
    records: list[TestDataRecord] = field(default_factory=list)
    cleanup_status: str = "PENDING"
    cleanup_evidence_ref: str = ""
    notes: list[str] = field(default_factory=list)

    def register(self, record: TestDataRecord) -> None:
        self.records.append(record)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data


# Prevent pytest from mistaking the public domain model for a test class.
TestRunSession.__test__ = False


class DirtyTestEnvironmentGuard:
    """Persistent guard: cleanup failures block subsequent high-risk writes."""

    def __init__(self, root: Path, project_id: str, environment_id: str):
        self.path = root / "platform_workspace" / project_id / "test_run_sessions" / f"{environment_id}.dirty.json"

    def status(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"dirty": False, "reason": "", "updated_at": ""}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {"dirty": True, "reason": "invalid_guard_state"}
        except Exception:
            return {"dirty": True, "reason": "unreadable_guard_state"}

    def assert_writable(self) -> None:
        state = self.status()
        if state.get("dirty"):
            raise RuntimeError(f"DIRTY_TEST_ENVIRONMENT: {state.get('reason') or 'cleanup recovery required'}")

    def mark_dirty(self, run_id: str, reason: str) -> None:
        _atomic_write(self.path, {"dirty": True, "run_id": run_id, "reason": reason[:500], "updated_at": _now()})

    def clear(self, run_id: str, reason: str = "cleanup verified") -> None:
        _atomic_write(self.path, {"dirty": False, "run_id": run_id, "reason": reason[:500], "updated_at": _now()})


def compile_cleanup_plan(flow: dict[str, Any], *, writes: bool) -> dict[str, Any]:
    """Compile only explicit cleanup behavior; no inferred destructive requests."""
    raw = flow.get("cleanup") if isinstance(flow.get("cleanup"), dict) else {}
    if not writes:
        return {"status": "READY", "strategy": "none", "actions": [], "reason": "read_only"}
    strategy = str(raw.get("strategy") or "manual_required").lower()
    actions = [dict(item) for item in (raw.get("actions") or []) if isinstance(item, dict)]
    reset_approved = bool(raw.get("environment_reset_approved"))
    if strategy == "environment_reset" and reset_approved:
        return {"status": "READY", "strategy": strategy, "actions": [], "environment_reset_approved": True, "reason": "approved_environment_reset"}
    if strategy in {"explicit_actions", "compensation", "restore"} and actions:
        invalid = []
        for index, action in enumerate(actions):
            method = str(action.get("method") or "").upper()
            path = str(action.get("path") or "")
            if method not in _WRITE_METHODS or not path.startswith("/"):
                invalid.append(f"cleanup_action_invalid:{index}")
        if not invalid:
            return {"status": "READY", "strategy": strategy, "actions": actions, "reason": "explicit_cleanup_mapping"}
        return {"status": "BLOCKED_BY_CLEANUP", "strategy": strategy, "actions": actions, "blockers": invalid}
    return {
        "status": "BLOCKED_BY_CLEANUP",
        "strategy": strategy,
        "actions": [],
        "blockers": ["cleanup_mapping_required_for_write_flow"],
        "reason": "writes require explicit compensation/restore or approved environment reset",
    }


def session_path(root: Path, project_id: str, run_id: str) -> Path:
    return root / "platform_workspace" / project_id / "test_run_sessions" / f"{run_id}.json"


def persist_session(root: Path, session: TestRunSession) -> Path:
    path = session_path(root, session.project_id, session.run_id)
    _atomic_write(path, session.to_dict())
    return path


def _response_resource_id(response: dict[str, Any]) -> str:
    payload = response.get("payload") if isinstance(response, dict) else {}
    if isinstance(payload, dict):
        for key in ("id", "resource_id", "code", "uuid"):
            if payload.get(key) not in (None, ""):
                return str(payload[key])
        data = payload.get("data")
        if isinstance(data, dict):
            for key in ("id", "resource_id", "code", "uuid"):
                if data.get(key) not in (None, ""):
                    return str(data[key])
    return ""


def register_response(session: TestRunSession, *, method: str, path: str, response: dict[str, Any], before_snapshot_ref: str = "", after_snapshot_ref: str = "") -> None:
    method = str(method or "GET").upper()
    if method not in _WRITE_METHODS:
        return
    resource_id = _response_resource_id(response)
    session.register(TestDataRecord(
        resource_type=path.strip("/").split("/")[0] or "resource",
        resource_id=resource_id or f"unresolved:{len(session.records)+1}",
        operation=method,
        endpoint=path,
        before_snapshot_ref=before_snapshot_ref,
        after_snapshot_ref=after_snapshot_ref,
        created_by_run=method == "POST",
        restoration_required=method in {"PUT", "PATCH", "DELETE"},
    ))


def execute_cleanup_plan(
    session: TestRunSession,
    plan: dict[str, Any],
    *,
    root: Path,
    executor: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    guard: DirtyTestEnvironmentGuard | None = None,
) -> dict[str, Any]:
    """Run explicit compensation actions and emit evidence; never guess deletion."""
    status = str(plan.get("status") or "BLOCKED_BY_CLEANUP")
    if status != "READY":
        session.cleanup_status = "CLEANUP_FAILED"
        session.status = "DIRTY_TEST_ENVIRONMENT"
        session.notes.append("cleanup plan was not executable")
        if guard:
            guard.mark_dirty(session.run_id, ";".join(plan.get("blockers") or ["cleanup_plan_not_ready"]))
        path = persist_session(root, session)
        session.cleanup_evidence_ref = str(path)
        return {"status": "CLEANUP_FAILED", "evidence_ref": str(path), "actions": [], "reason": "cleanup_plan_not_ready"}

    strategy = str(plan.get("strategy") or "none")
    receipts: list[dict[str, Any]] = []
    if strategy == "none":
        session.cleanup_status = "CLEAN"
        session.status = "COMPLETED"
        session.notes.append("read-only run; cleanup not applicable")
    elif strategy == "environment_reset":
        # The enterprise owns this reset.  A reviewed integration may register
        # completion externally; no direct reset command is guessed or invoked.
        session.cleanup_status = "CLEAN"
        session.status = "COMPLETED"
        session.notes.append("approved environment reset contract recorded")
    else:
        if executor is None:
            session.cleanup_status = "CLEANUP_FAILED"
            session.status = "DIRTY_TEST_ENVIRONMENT"
            session.notes.append("explicit cleanup executor unavailable")
            if guard:
                guard.mark_dirty(session.run_id, "explicit_cleanup_executor_unavailable")
            path = persist_session(root, session)
            session.cleanup_evidence_ref = str(path)
            return {"status": "CLEANUP_FAILED", "evidence_ref": str(path), "actions": [], "reason": "executor_unavailable"}
        for index, action in enumerate(plan.get("actions") or []):
            try:
                receipt = executor(action)
                receipt = dict(receipt or {})
                accepted = bool(receipt.get("accepted", receipt.get("ok", False)))
                receipts.append({"index": index, "accepted": accepted, "receipt": receipt})
                if not accepted:
                    raise RuntimeError(str(receipt.get("error") or "cleanup_action_rejected"))
            except Exception as exc:
                session.cleanup_status = "CLEANUP_FAILED"
                session.status = "DIRTY_TEST_ENVIRONMENT"
                session.notes.append(f"cleanup action {index} failed: {str(exc)[:300]}")
                if guard:
                    guard.mark_dirty(session.run_id, session.notes[-1])
                path = persist_session(root, session)
                session.cleanup_evidence_ref = str(path)
                return {"status": "CLEANUP_FAILED", "evidence_ref": str(path), "actions": receipts, "reason": str(exc)[:300]}
        session.cleanup_status = "CLEAN"
        session.status = "COMPLETED"

    if guard:
        guard.clear(session.run_id, "cleanup evidence recorded")
    path = persist_session(root, session)
    session.cleanup_evidence_ref = str(path)
    return {"status": "CLEAN", "evidence_ref": str(path), "actions": receipts, "reason": session.notes[-1] if session.notes else ""}
