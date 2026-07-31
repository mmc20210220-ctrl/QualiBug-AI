"""Managed Feishu synchronization and automatic refresh supervisor.

One application authority owns checkpoint validation, connector sync execution, checkpoint
commit, scheduling, retry backoff, and process-local operator status. It creates no second
connector registry or source pipeline.
"""
from __future__ import annotations

import atexit
import calendar
import hashlib
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .connector_connection_profiles import (
    ConnectorProfileError,
    commit_connector_sync_checkpoint,
    list_connector_connection_profiles,
    load_connector_sync_checkpoint,
    resolve_connector_connection_profile,
)
from .connector_sync_authority import ConnectorSyncError, list_connector_instances
from .enterprise_knowledge_center._common import ROOT
from .feishu_connector_adapter import (
    FeishuConnectorError,
    sync_feishu_connector,
    test_feishu_connector_connection,
)
from .real_project_onboarding import _safe_project_id

_AUTO_SYNC_ACTOR = {"name": "qualibug_auto_sync", "role": "knowledge_admin"}
_STATE_LOCK = threading.RLock()
_SUPERVISORS: dict[str, dict[str, Any]] = {}
_ATTEMPTS: dict[tuple[str, str, str], dict[str, Any]] = {}
_ATEXIT_REGISTERED = False


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = _text(os.environ.get(name), 32)
    try:
        parsed = int(raw) if raw else default
    except ValueError:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _enabled() -> bool:
    return _text(os.environ.get("QUALIBUG_CONNECTOR_AUTO_SYNC_ENABLED", "1"), 8).lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _policy() -> dict[str, int]:
    return {
        "refresh_seconds": _env_int(
            "QUALIBUG_CONNECTOR_AUTO_SYNC_INTERVAL_SECONDS",
            6 * 60 * 60,
            15 * 60,
            7 * 24 * 60 * 60,
        ),
        "sweep_seconds": _env_int(
            "QUALIBUG_CONNECTOR_AUTO_SYNC_SWEEP_SECONDS",
            60,
            10,
            60 * 60,
        ),
        "initial_delay_seconds": _env_int(
            "QUALIBUG_CONNECTOR_AUTO_SYNC_INITIAL_DELAY_SECONDS",
            10,
            0,
            10 * 60,
        ),
        "retry_base_seconds": _env_int(
            "QUALIBUG_CONNECTOR_AUTO_SYNC_RETRY_BASE_SECONDS",
            60,
            10,
            60 * 60,
        ),
        "retry_max_seconds": _env_int(
            "QUALIBUG_CONNECTOR_AUTO_SYNC_RETRY_MAX_SECONDS",
            60 * 60,
            60,
            24 * 60 * 60,
        ),
    }


def _parse_utc(value: Any) -> float:
    text = _text(value, 80)
    if not text:
        return 0.0
    try:
        return float(calendar.timegm(time.strptime(text, "%Y-%m-%dT%H:%M:%SZ")))
    except ValueError:
        return 0.0


def _utc(timestamp: float | None = None) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() if timestamp is None else timestamp),
    )


def _instance(
    project: str,
    connector: str,
    root: Path,
) -> dict[str, Any]:
    rows = list_connector_instances(
        project,
        root=root,
        include_disabled=True,
    ).get("connector_instances") or []
    row = next(
        (
            dict(item)
            for item in rows
            if isinstance(item, dict)
            and _text(item.get("connector_instance_id"), 160) == connector
        ),
        None,
    )
    if row is None:
        raise ConnectorSyncError("connector_instance_not_registered")
    return row


def _profile_resolver(project: str, root: Path):
    def resolve(profile_ref: str) -> dict[str, str]:
        return resolve_connector_connection_profile(
            project,
            profile_ref,
            root=root,
        )

    return resolve


def validate_connector_checkpoint(
    project_id: str,
    connector_instance_id: str,
    checkpoint: str,
    *,
    root: Path | None = None,
) -> None:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    instance = _instance(project, connector, resolved_root)
    expected = _text(instance.get("last_committed_cursor_fingerprint"), 128)
    if not expected:
        if checkpoint:
            raise ConnectorProfileError(
                "connector_checkpoint_exists_without_registry_commit"
            )
        return
    if not checkpoint:
        raise ConnectorProfileError(
            "connector_checkpoint_missing_for_registry_commit"
        )
    actual = hashlib.sha256(checkpoint.encode("utf-8")).hexdigest()
    if actual != expected:
        raise ConnectorProfileError("connector_checkpoint_registry_mismatch")


def test_managed_feishu_connection(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    timeout: float = 15.0,
    transport: Any = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    return test_feishu_connector_connection(
        project,
        connector_instance_id=connector_instance_id,
        resolve_connection_profile=_profile_resolver(project, resolved_root),
        root=resolved_root,
        timeout=timeout,
        transport=transport,
        sleeper=sleeper,
    )


def run_managed_feishu_sync(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    actor: dict[str, Any] | None = None,
    deletion_policy: str = "RETAIN",
    max_retire_count: int = 100,
    max_retire_ratio: float = 0.25,
    max_nodes: int = 5000,
    max_export_polls: int = 20,
    export_poll_interval: float = 0.5,
    allow_raw_text_fallback: bool = False,
    timeout: float = 15.0,
    transport: Any = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Execute the only trusted Feishu sync path used by HTTP and automation."""
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    clean_actor = dict(actor or _AUTO_SYNC_ACTOR)
    previous_cursor = load_connector_sync_checkpoint(
        project,
        connector,
        root=resolved_root,
    )
    validate_connector_checkpoint(
        project,
        connector,
        previous_cursor,
        root=resolved_root,
    )
    run = sync_feishu_connector(
        project,
        connector_instance_id=connector,
        resolve_connection_profile=_profile_resolver(project, resolved_root),
        root=resolved_root,
        actor=clean_actor,
        previous_cursor=previous_cursor,
        deletion_policy=deletion_policy,
        max_retire_count=max_retire_count,
        max_retire_ratio=max_retire_ratio,
        max_nodes=max_nodes,
        max_export_polls=max_export_polls,
        export_poll_interval=export_poll_interval,
        allow_raw_text_fallback=allow_raw_text_fallback,
        timeout=timeout,
        transport=transport,
        sleeper=sleeper,
    )
    if run.get("status") != "COMPLETE":
        return run
    checkpoint = _text(run.get("next_cursor"), 500)
    if not checkpoint:
        raise ConnectorProfileError(
            "connector_sync_checkpoint_missing_after_complete_run"
        )
    checkpoint_fingerprint = hashlib.sha256(
        checkpoint.encode("utf-8")
    ).hexdigest()
    committed_fingerprint = _text(
        run.get("committed_cursor_fingerprint"), 128
    )
    if committed_fingerprint and committed_fingerprint != checkpoint_fingerprint:
        raise ConnectorProfileError(
            "connector_sync_checkpoint_commit_mismatch"
        )
    commit_connector_sync_checkpoint(
        project,
        connector,
        checkpoint,
        sync_epoch_id=_text(run.get("sync_epoch_id"), 160),
        root=resolved_root,
        actor=clean_actor,
    )
    return run


def _project_ids(root: Path) -> list[str]:
    workspace = root / "platform_workspace"
    if not workspace.exists():
        return []
    projects: list[str] = []
    for profile_path in workspace.glob(
        "*/enterprise_knowledge_center/connector_connection_profiles.json"
    ):
        raw = profile_path.parent.parent.name
        try:
            project = _safe_project_id(raw)
        except ValueError:
            continue
        projects.append(project)
    return sorted(set(projects))


def _configured_profiles(project: str, root: Path) -> set[str]:
    payload = list_connector_connection_profiles(project, root=root)
    return {
        _text(row.get("connector_instance_id"), 160)
        for row in payload.get("profiles") or []
        if isinstance(row, dict) and row.get("credentials_configured") is True
    }


def _key(root: Path, project: str, connector: str) -> tuple[str, str, str]:
    return str(root.resolve()), project, connector


def _due(
    instance: dict[str, Any],
    attempt: dict[str, Any],
    *,
    now: float,
    refresh_seconds: int,
) -> bool:
    if instance.get("status") != "ACTIVE":
        return False
    if _text(instance.get("connector_type"), 160).lower() != "feishu":
        return False
    if instance.get("active_sync_epoch_id"):
        return False
    next_attempt = float(attempt.get("next_attempt_unix") or 0)
    if next_attempt and now < next_attempt:
        return False
    last_success = _parse_utc(instance.get("last_successful_sync_at_utc"))
    last_failure = _parse_utc(instance.get("last_failed_sync_at_utc"))
    if last_failure > last_success:
        return True
    return not last_success or now - last_success >= refresh_seconds


def _failure_category(exc: Exception) -> str:
    message = str(exc or "").lower()
    if "credential" in message or "profile" in message or "token" in message:
        return "AUTHORIZATION_REQUIRED"
    if "permission" in message or "forbidden" in message:
        return "PERMISSION_REQUIRED"
    if "already_running" in message or "lock_held" in message:
        return "BUSY"
    if "checkpoint" in message or "cursor" in message:
        return "RECOVERY_REQUIRED"
    if "transport" in message or "api_failed" in message:
        return "REMOTE_UNAVAILABLE"
    return "RETRYING"


def _record_success(key: tuple[str, str, str], run: dict[str, Any], now: float) -> None:
    with _STATE_LOCK:
        _ATTEMPTS[key] = {
            "state": "healthy",
            "failure_count": 0,
            "last_attempt_at_utc": _utc(now),
            "last_success_at_utc": _utc(now),
            "last_sync_epoch_id": _text(run.get("sync_epoch_id"), 160),
            "next_attempt_unix": 0.0,
            "next_attempt_at_utc": "",
            "last_error_category": "",
        }


def _record_failure(
    key: tuple[str, str, str],
    exc: Exception,
    now: float,
    *,
    retry_base_seconds: int,
    retry_max_seconds: int,
) -> None:
    with _STATE_LOCK:
        previous = dict(_ATTEMPTS.get(key) or {})
        failure_count = int(previous.get("failure_count") or 0) + 1
        delay = min(
            retry_base_seconds * (2 ** max(0, failure_count - 1)),
            retry_max_seconds,
        )
        next_attempt = now + delay
        _ATTEMPTS[key] = {
            "state": "retrying",
            "failure_count": failure_count,
            "last_attempt_at_utc": _utc(now),
            "next_attempt_unix": next_attempt,
            "next_attempt_at_utc": _utc(next_attempt),
            "last_error_category": _failure_category(exc),
            "last_error_type": type(exc).__name__,
            "raw_error_persisted": False,
        }


def run_connector_auto_sync_sweep(
    root: Path,
    *,
    now: float | None = None,
    sync_runner: Callable[..., dict[str, Any]] = run_managed_feishu_sync,
) -> dict[str, Any]:
    """Run one bounded sequential sweep; useful for the supervisor and tests."""
    resolved_root = root.resolve()
    timestamp = time.time() if now is None else float(now)
    policy = _policy()
    attempted = succeeded = failed = skipped = 0
    for project in _project_ids(resolved_root):
        try:
            configured = _configured_profiles(project, resolved_root)
            instances = list_connector_instances(
                project,
                root=resolved_root,
                include_disabled=True,
            ).get("connector_instances") or []
        except Exception:
            failed += 1
            continue
        for raw in instances:
            if not isinstance(raw, dict):
                continue
            connector = _text(raw.get("connector_instance_id"), 160)
            if not connector or connector not in configured:
                skipped += 1
                continue
            key = _key(resolved_root, project, connector)
            with _STATE_LOCK:
                attempt = dict(_ATTEMPTS.get(key) or {})
            if not _due(
                raw,
                attempt,
                now=timestamp,
                refresh_seconds=policy["refresh_seconds"],
            ):
                skipped += 1
                continue
            attempted += 1
            with _STATE_LOCK:
                _ATTEMPTS[key] = {
                    **attempt,
                    "state": "running",
                    "last_attempt_at_utc": _utc(timestamp),
                }
            try:
                run = sync_runner(
                    project,
                    connector,
                    root=resolved_root,
                    actor=_AUTO_SYNC_ACTOR,
                )
                if run.get("status") != "COMPLETE":
                    raise ConnectorSyncError("connector_auto_sync_incomplete")
            except Exception as exc:
                failed += 1
                _record_failure(
                    key,
                    exc,
                    timestamp,
                    retry_base_seconds=policy["retry_base_seconds"],
                    retry_max_seconds=policy["retry_max_seconds"],
                )
            else:
                succeeded += 1
                _record_success(key, run, timestamp)
    return {
        "enabled": _enabled(),
        "attempted": attempted,
        "succeeded": succeeded,
        "failed": failed,
        "skipped": skipped,
        "completed_at_utc": _utc(timestamp),
        "new_registry_created": False,
    }


def connector_auto_sync_status(
    root: Path,
    project_id: str,
    connector_instance_id: str,
) -> dict[str, Any]:
    resolved_root = root.resolve()
    project = _safe_project_id(project_id)
    connector = _text(connector_instance_id, 160)
    policy = _policy()
    with _STATE_LOCK:
        state = dict(_ATTEMPTS.get(_key(resolved_root, project, connector)) or {})
    status = _text(state.get("state"), 32) or ("scheduled" if _enabled() else "disabled")
    messages = {
        "scheduled": "已开启自动更新",
        "running": "正在自动更新",
        "healthy": "自动更新正常",
        "retrying": "更新暂时中断，系统会自动重试",
        "disabled": "自动更新已关闭",
    }
    return {
        "enabled": _enabled(),
        "state": status,
        "message": messages.get(status, "已开启自动更新"),
        "last_attempt_at_utc": _text(state.get("last_attempt_at_utc"), 80),
        "last_success_at_utc": _text(state.get("last_success_at_utc"), 80),
        "next_attempt_at_utc": _text(state.get("next_attempt_at_utc"), 80),
        "failure_count": int(state.get("failure_count") or 0),
        "attention": _text(state.get("last_error_category"), 80),
        "refresh_interval_seconds": policy["refresh_seconds"],
        "maintenance_required_by_user": status == "retrying"
        and state.get("last_error_category") in {
            "AUTHORIZATION_REQUIRED",
            "PERMISSION_REQUIRED",
            "RECOVERY_REQUIRED",
        },
        "raw_error_returned": False,
    }


def _supervisor_loop(root: Path, stop_event: threading.Event) -> None:
    policy = _policy()
    if stop_event.wait(policy["initial_delay_seconds"]):
        return
    while not stop_event.is_set():
        run_connector_auto_sync_sweep(root)
        stop_event.wait(policy["sweep_seconds"])


def ensure_connector_auto_sync_supervisor(
    root: Path,
) -> dict[str, Any]:
    """Start one daemon supervisor per deployment root, idempotently."""
    global _ATEXIT_REGISTERED
    resolved_root = root.resolve()
    key = str(resolved_root)
    if not _enabled():
        return {"enabled": False, "started": False, "root": key}
    with _STATE_LOCK:
        current = _SUPERVISORS.get(key)
        thread = current.get("thread") if isinstance(current, dict) else None
        if isinstance(thread, threading.Thread) and thread.is_alive():
            return {"enabled": True, "started": False, "already_running": True, "root": key}
        stop_event = threading.Event()
        thread = threading.Thread(
            target=_supervisor_loop,
            args=(resolved_root, stop_event),
            name="qualibug-connector-auto-sync",
            daemon=True,
        )
        _SUPERVISORS[key] = {
            "thread": thread,
            "stop_event": stop_event,
            "started_at_utc": _utc(),
        }
        thread.start()
        if not _ATEXIT_REGISTERED:
            atexit.register(stop_all_connector_auto_sync_supervisors)
            _ATEXIT_REGISTERED = True
    return {"enabled": True, "started": True, "root": key}


def stop_connector_auto_sync_supervisor(
    root: Path,
    *,
    join_timeout: float = 5.0,
) -> dict[str, Any]:
    key = str(root.resolve())
    with _STATE_LOCK:
        entry = _SUPERVISORS.pop(key, None)
    if not isinstance(entry, dict):
        return {"stopped": False, "root": key}
    stop_event = entry.get("stop_event")
    thread = entry.get("thread")
    if isinstance(stop_event, threading.Event):
        stop_event.set()
    if isinstance(thread, threading.Thread) and thread is not threading.current_thread():
        thread.join(max(0.0, float(join_timeout)))
    return {
        "stopped": True,
        "root": key,
        "thread_alive": bool(isinstance(thread, threading.Thread) and thread.is_alive()),
    }


def stop_all_connector_auto_sync_supervisors() -> None:
    with _STATE_LOCK:
        roots = list(_SUPERVISORS)
    for value in roots:
        stop_connector_auto_sync_supervisor(Path(value), join_timeout=1.0)


__all__ = [
    "connector_auto_sync_status",
    "ensure_connector_auto_sync_supervisor",
    "run_connector_auto_sync_sweep",
    "run_managed_feishu_sync",
    "stop_all_connector_auto_sync_supervisors",
    "stop_connector_auto_sync_supervisor",
    "test_managed_feishu_connection",
    "validate_connector_checkpoint",
]
