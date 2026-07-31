"""Process ownership and heartbeat for one managed connector synchronization."""
from __future__ import annotations

import os
import re
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable
import uuid

from .private_pilot_json_io import _read_json_object, _write_json_object_atomic
from .real_project_onboarding import _safe_project_id

SYNC_OWNERSHIP_SCHEMA = "qualibug.connector-sync-ownership.v1"
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_PROCESS_TOKEN = uuid.uuid4().hex
_LOCAL_LOCK = threading.RLock()
_HEARTBEATS: dict[str, dict[str, Any]] = {}


class ConnectorSyncOwnershipError(RuntimeError):
    """A synchronization owner could not be established or inspected safely."""


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _connector_id(value: Any) -> str:
    connector = _text(value, 160)
    if not _IDENTIFIER_RE.fullmatch(connector):
        raise ConnectorSyncOwnershipError("connector_sync_owner_connector_invalid")
    return connector


def _path(root: Path, project: str, connector: str) -> Path:
    return (
        root.resolve()
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
        / "connector_sync_ownership"
        / f"{connector}.json"
    )


def _utc(timestamp: float | None = None) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() if timestamp is None else timestamp),
    )


def _heartbeat_seconds() -> int:
    raw = _text(os.environ.get("QUALIBUG_CONNECTOR_SYNC_HEARTBEAT_SECONDS"), 32)
    try:
        value = int(raw) if raw else 10
    except ValueError:
        value = 10
    return max(2, min(value, 60))


def _process_marker(pid: int) -> str:
    if pid <= 0:
        return ""
    path = Path(f"/proc/{pid}/stat")
    try:
        fields = path.read_text(encoding="utf-8").split()
    except OSError:
        return ""
    return fields[21] if len(fields) > 21 else ""


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _thread_alive(thread_id: int) -> bool:
    if thread_id <= 0:
        return False
    return any(thread.ident == thread_id for thread in threading.enumerate())


def _operation_code(thread_id: int):
    frame = sys._current_frames().get(thread_id)
    while frame is not None:
        module = str(frame.f_globals.get("__name__") or "")
        if module not in {
            __name__,
            "ai_test_asset_center.connector_checkpoint_recovery",
        }:
            return frame.f_code
        frame = frame.f_back
    return None


def _operation_alive(thread_id: int, operation_code: Any) -> bool:
    if operation_code is None:
        return _thread_alive(thread_id)
    frame = sys._current_frames().get(thread_id)
    while frame is not None:
        if frame.f_code is operation_code:
            return True
        frame = frame.f_back
    return False


def read_connector_sync_ownership(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path,
) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    connector = _connector_id(connector_instance_id)
    return _read_json_object(_path(root, project, connector))


def heartbeat_connector_sync_ownership(
    project_id: str,
    connector_instance_id: str,
    attempt_id: str,
    *,
    root: Path,
    active_epoch_id: str = "",
    state: str = "ACTIVE",
) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    connector = _connector_id(connector_instance_id)
    attempt = _text(attempt_id, 160)
    path = _path(root, project, connector)
    with _LOCAL_LOCK:
        payload = _read_json_object(path)
        if _text(payload.get("attempt_id"), 160) != attempt:
            raise ConnectorSyncOwnershipError(
                "connector_sync_owner_attempt_mismatch"
            )
        now = time.time()
        payload.update(
            {
                "state": _text(state, 80) or "ACTIVE",
                "active_sync_epoch_id": _text(active_epoch_id, 160),
                "last_heartbeat_unix": now,
                "last_heartbeat_at_utc": _utc(now),
                "updated_at_utc": _utc(now),
            }
        )
        _write_json_object_atomic(path, payload)
    return {
        "ok": True,
        "attempt_id": attempt,
        "active_sync_epoch_id": payload["active_sync_epoch_id"],
        "heartbeat_recorded": True,
    }


def _heartbeat_loop(
    root: Path,
    project: str,
    connector: str,
    attempt: str,
    owner_thread_id: int,
    operation_code: Any,
    stop_event: threading.Event,
    epoch_provider: Callable[[], str] | None,
) -> None:
    interval = _heartbeat_seconds()
    while not stop_event.wait(interval):
        if not _operation_alive(owner_thread_id, operation_code):
            try:
                heartbeat_connector_sync_ownership(
                    project,
                    connector,
                    attempt,
                    root=root,
                    state="OWNER_THREAD_EXITED",
                )
            except Exception:
                pass
            return
        epoch = ""
        if epoch_provider is not None:
            try:
                epoch = _text(epoch_provider(), 160)
            except Exception:
                epoch = ""
        try:
            heartbeat_connector_sync_ownership(
                project,
                connector,
                attempt,
                root=root,
                active_epoch_id=epoch,
            )
        except ConnectorSyncOwnershipError:
            return
        except Exception:
            continue


def begin_connector_sync_ownership(
    project_id: str,
    connector_instance_id: str,
    attempt_id: str,
    *,
    root: Path,
    epoch_provider: Callable[[], str] | None = None,
) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    connector = _connector_id(connector_instance_id)
    attempt = _text(attempt_id, 160)
    if not attempt:
        raise ConnectorSyncOwnershipError("connector_sync_owner_attempt_required")
    path = _path(root, project, connector)
    owner_thread_id = int(threading.get_ident())
    operation_code = _operation_code(owner_thread_id)
    now = time.time()
    payload = {
        "schema": SYNC_OWNERSHIP_SCHEMA,
        "project_id": project,
        "connector_instance_id": connector,
        "attempt_id": attempt,
        "state": "ACTIVE",
        "pid": os.getpid(),
        "owner_thread_id": owner_thread_id,
        "process_token": _PROCESS_TOKEN,
        "process_start_marker": _process_marker(os.getpid()),
        "hostname": socket.gethostname()[:240],
        "active_sync_epoch_id": "",
        "started_unix": now,
        "started_at_utc": _utc(now),
        "last_heartbeat_unix": now,
        "last_heartbeat_at_utc": _utc(now),
        "updated_at_utc": _utc(now),
        "raw_credentials_persisted": False,
        "source_content_persisted": False,
    }
    key = str(path)
    with _LOCAL_LOCK:
        existing = _read_json_object(path)
        if existing:
            status = inspect_connector_sync_ownership(
                project,
                connector,
                root=root,
            )
            if status.get("owner_alive") is True:
                raise ConnectorSyncOwnershipError("connector_sync_owner_active")
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json_object_atomic(path, payload)
        previous = _HEARTBEATS.pop(key, None)
        if isinstance(previous, dict):
            old_stop = previous.get("stop_event")
            if isinstance(old_stop, threading.Event):
                old_stop.set()
        stop_event = threading.Event()
        thread = threading.Thread(
            target=_heartbeat_loop,
            args=(
                root.resolve(),
                project,
                connector,
                attempt,
                owner_thread_id,
                operation_code,
                stop_event,
                epoch_provider,
            ),
            name=f"qualibug-sync-heartbeat-{connector}",
            daemon=True,
        )
        _HEARTBEATS[key] = {
            "attempt_id": attempt,
            "stop_event": stop_event,
            "thread": thread,
        }
        thread.start()
    return {
        "ok": True,
        "attempt_id": attempt,
        "pid": os.getpid(),
        "heartbeat_started": True,
        "plaintext_credentials_persisted": False,
    }


def stop_connector_sync_ownership(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path,
    expected_attempt_id: str = "",
    remove: bool = True,
    join_timeout: float = 2.0,
) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    connector = _connector_id(connector_instance_id)
    path = _path(root, project, connector)
    key = str(path)
    expected = _text(expected_attempt_id, 160)
    with _LOCAL_LOCK:
        payload = _read_json_object(path)
        if expected and payload:
            if _text(payload.get("attempt_id"), 160) != expected:
                raise ConnectorSyncOwnershipError(
                    "connector_sync_owner_attempt_mismatch"
                )
        entry = _HEARTBEATS.pop(key, None)
        if isinstance(entry, dict):
            stop_event = entry.get("stop_event")
            if isinstance(stop_event, threading.Event):
                stop_event.set()
        if remove:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise ConnectorSyncOwnershipError(
                    "connector_sync_owner_delete_failed"
                ) from exc
    thread = entry.get("thread") if isinstance(entry, dict) else None
    if isinstance(thread, threading.Thread) and thread is not threading.current_thread():
        thread.join(max(0.0, float(join_timeout)))
    return {
        "ok": True,
        "stopped": bool(entry),
        "removed": bool(remove),
        "thread_alive": bool(
            isinstance(thread, threading.Thread) and thread.is_alive()
        ),
    }


def inspect_connector_sync_ownership(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path,
    stale_after_seconds: int = 120,
) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    connector = _connector_id(connector_instance_id)
    payload = _read_json_object(_path(root, project, connector))
    if not payload:
        return {
            "state": "MISSING",
            "owner_alive": None,
            "owner_dead": False,
            "heartbeat_stale": False,
        }
    try:
        pid = int(payload.get("pid") or 0)
        owner_thread_id = int(payload.get("owner_thread_id") or 0)
        heartbeat = float(payload.get("last_heartbeat_unix") or 0)
    except (TypeError, ValueError):
        return {
            **payload,
            "state": "INVALID",
            "owner_alive": None,
            "owner_dead": False,
            "heartbeat_stale": True,
        }
    alive = _pid_alive(pid)
    recorded_marker = _text(payload.get("process_start_marker"), 200)
    current_marker = _process_marker(pid) if alive else ""
    reused = bool(
        alive
        and recorded_marker
        and current_marker
        and recorded_marker != current_marker
    )
    local_thread_dead = bool(
        alive
        and not reused
        and pid == os.getpid()
        and owner_thread_id
        and not _thread_alive(owner_thread_id)
    )
    declared_thread_exit = payload.get("state") == "OWNER_THREAD_EXITED"
    heartbeat_stale = not heartbeat or (
        time.time() - heartbeat > max(10, int(stale_after_seconds))
    )
    owner_dead = bool(not alive or reused or local_thread_dead or declared_thread_exit)
    if not alive:
        state = "DEAD_PROCESS"
    elif reused:
        state = "REUSED_PROCESS_ID"
    elif local_thread_dead or declared_thread_exit:
        state = "DEAD_OWNER_THREAD"
    elif heartbeat_stale:
        state = "STALE_HEARTBEAT_OWNER_ALIVE"
    else:
        state = "ACTIVE"
    return {
        **payload,
        "state": state,
        "owner_alive": bool(alive and not owner_dead),
        "owner_dead": owner_dead,
        "heartbeat_stale": heartbeat_stale,
        "process_id_reused": reused,
    }


__all__ = [
    "SYNC_OWNERSHIP_SCHEMA",
    "ConnectorSyncOwnershipError",
    "begin_connector_sync_ownership",
    "heartbeat_connector_sync_ownership",
    "inspect_connector_sync_ownership",
    "read_connector_sync_ownership",
    "stop_connector_sync_ownership",
]
