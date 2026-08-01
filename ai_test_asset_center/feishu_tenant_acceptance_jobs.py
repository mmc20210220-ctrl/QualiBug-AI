"""Persistent asynchronous jobs for long-running connector tenant acceptance.

A Pilot or enterprise acceptance can take several minutes and must not depend on one HTTP
connection staying open. This authority creates one atomically locked job per connector, runs the
existing acceptance authority in a daemon worker, persists bounded status, and recovers jobs whose
owner process or worker thread disappeared. Customer content, credentials, raw cursors, and
filesystem paths are never stored in the public job projection.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

from .connector_sync_authority import list_connector_instances
from .enterprise_knowledge_center._common import ROOT
from .feishu_tenant_acceptance import (
    CONNECTOR_TENANT_ACCEPTANCE_SCHEMA,
    FEISHU_TENANT_ACCEPTANCE_SCHEMA,
    run_connector_tenant_acceptance,
    run_feishu_tenant_acceptance,
)
from .private_pilot_json_io import _read_json_object, _write_json_object_atomic
from .real_project_onboarding import _safe_project_id

FEISHU_TENANT_ACCEPTANCE_JOB_SCHEMA = "qualibug.feishu-tenant-acceptance-job.v1"
CONNECTOR_TENANT_ACCEPTANCE_JOB_SCHEMA = "qualibug.connector-tenant-acceptance-job.v1"
_CONNECTOR_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_JOB_ID_RE = re.compile(r"^(?:ftaj|ctaj)_[a-f0-9]{24}$")
_REPORT_ID_RE = re.compile(r"^[0-9]{8}T[0-9]{6}Z_[a-f0-9]{12}$")
_TERMINAL_STATES = {"COMPLETE", "FAILED", "INTERRUPTED"}
_STARTUP_GRACE_SECONDS = 30.0
_LOCAL_LOCK = threading.RLock()
_THREADS: dict[str, threading.Thread] = {}
_PROCESS_TOKEN = uuid.uuid4().hex

AcceptanceRunner = Callable[..., Mapping[str, Any]]
ThreadStarter = Callable[[Callable[[], None], str], Any]


class FeishuTenantAcceptanceJobError(RuntimeError):
    """A tenant acceptance job could not be created or inspected safely."""


def _text(value: Any, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _utc(timestamp: float | None = None) -> str:
    return time.strftime(
        "%Y-%m-%dT%H:%M:%SZ",
        time.gmtime(time.time() if timestamp is None else timestamp),
    )


def _connector_id(value: Any) -> str:
    connector = _text(value, 160)
    if not _CONNECTOR_ID_RE.fullmatch(connector):
        raise FeishuTenantAcceptanceJobError("acceptance_job_connector_invalid")
    return connector


def _job_id(value: Any) -> str:
    job = _text(value, 80)
    if not _JOB_ID_RE.fullmatch(job):
        raise FeishuTenantAcceptanceJobError("acceptance_job_id_invalid")
    return job


def _connector_dir(root: Path, project: str, connector: str) -> Path:
    return (
        root.resolve()
        / "platform_workspace"
        / project
        / "enterprise_knowledge_center"
        / "connector_acceptance_jobs"
        / connector
    )


def _current_path(root: Path, project: str, connector: str) -> Path:
    return _connector_dir(root, project, connector) / "current.json"


def _history_path(root: Path, project: str, connector: str, job: str) -> Path:
    # Keep the on-disk name compact: Windows installations can otherwise cross the
    # MAX_PATH boundary once the project/worktree prefix and atomic-write suffix are added.
    storage_key = _text(job, 80).rsplit("_", 1)[-1][:20]
    return _connector_dir(root, project, connector) / "history" / f"{storage_key}.json"


def _legacy_history_path(root: Path, project: str, connector: str, job: str) -> Path:
    """Locate the pre-compact history name while old jobs are being read."""
    return _connector_dir(root, project, connector) / "history" / f"{job}.json"


def _lock_path(root: Path, project: str, connector: str) -> Path:
    return _connector_dir(root, project, connector) / "active.lock"


def _process_marker(pid: int) -> str:
    if pid <= 0:
        return ""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
    except OSError:
        return ""
    return fields[21] if len(fields) > 21 else ""


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        # ``os.kill(pid, 0)`` is not a non-destructive probe on Windows; on some
        # runtimes it terminates the current process instead of checking liveness.
        return True
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            kernel32.CloseHandle(handle)
            return True
        except (AttributeError, OSError):
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


def _native_thread_alive(pid: int, native_thread_id: int) -> bool:
    if pid <= 0 or native_thread_id <= 0:
        return False
    task_path = Path(f"/proc/{pid}/task/{native_thread_id}")
    try:
        return task_path.is_dir()
    except OSError:
        return False


def _timestamp(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _recent_startup(payload: Mapping[str, Any], pid: int) -> bool:
    recent = max(
        _timestamp(payload.get("updated_unix")),
        _timestamp(payload.get("requested_unix")),
        _timestamp(payload.get("created_unix")),
    )
    return (
        _pid_alive(pid)
        and recent > 0
        and time.time() - recent <= _STARTUP_GRACE_SECONDS
    )


def _owner_alive(payload: Mapping[str, Any]) -> bool:
    job = _text(payload.get("job_id"), 80)
    pid = int(payload.get("owner_pid") or 0)
    native_thread_id = int(payload.get("owner_native_thread_id") or 0)
    same_process = _text(payload.get("process_token"), 80) == _PROCESS_TOKEN
    if same_process:
        thread = _THREADS.get(job)
        if thread is not None:
            return thread.is_alive()
        if pid == os.getpid() and _native_thread_alive(pid, native_thread_id):
            return True
        return pid == os.getpid() and _recent_startup(payload, pid)
    if not _pid_alive(pid):
        return False
    expected_marker = _text(payload.get("owner_process_marker"), 120)
    current_marker = _process_marker(pid)
    if expected_marker and current_marker and expected_marker != current_marker:
        return False
    if native_thread_id > 0:
        return _native_thread_alive(pid, native_thread_id)
    return _recent_startup(payload, pid)


def _ensure_connector_registered(
    project: str,
    connector: str,
    root: Path,
    *,
    expected_connector_type: str | None = "feishu",
) -> None:
    rows = list_connector_instances(
        project,
        root=root,
        include_disabled=True,
    ).get("connector_instances") or []
    row = next(
        (
            item
            for item in rows
            if isinstance(item, dict)
            and _text(item.get("connector_instance_id"), 160) == connector
        ),
        None,
    )
    if row is None:
        raise FeishuTenantAcceptanceJobError(
            "acceptance_job_connector_not_registered"
        )
    if (
        expected_connector_type is not None
        and _text(row.get("connector_type"), 80).lower()
        != _text(expected_connector_type, 80).lower()
    ):
        raise FeishuTenantAcceptanceJobError(
            "acceptance_job_connector_type_mismatch"
        )
    if _text(row.get("status"), 40) != "ACTIVE":
        raise FeishuTenantAcceptanceJobError(
            "acceptance_job_connector_not_active"
        )


def _persist(root: Path, project: str, connector: str, payload: dict[str, Any]) -> None:
    current_path = _current_path(root, project, connector)
    history_path = _history_path(
        root,
        project,
        connector,
        _job_id(payload.get("job_id")),
    )
    current_path.parent.mkdir(parents=True, exist_ok=True)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_object_atomic(current_path, payload)
    _write_json_object_atomic(history_path, payload)


def _persist_recovered(
    root: Path,
    project: str,
    connector: str,
    payload: dict[str, Any],
) -> None:
    job = _job_id(payload.get("job_id"))
    history_path = _history_path(root, project, connector, job)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json_object_atomic(
        history_path,
        payload,
    )
    current_path = _current_path(root, project, connector)
    current = _read_json_object(current_path)
    if _text(current.get("job_id"), 80) == job:
        _write_json_object_atomic(current_path, payload)


def _public_job(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": _text(payload.get("schema"), 120)
        or FEISHU_TENANT_ACCEPTANCE_JOB_SCHEMA,
        "job_id": _text(payload.get("job_id"), 80),
        "project_id": _text(payload.get("project_id"), 160),
        "connector_instance_id": _text(
            payload.get("connector_instance_id"), 160
        ),
        "profile": _text(payload.get("profile"), 40),
        "status": _text(payload.get("status"), 40),
        "requested_at_utc": _text(payload.get("requested_at_utc"), 80),
        "started_at_utc": _text(payload.get("started_at_utc"), 80),
        "completed_at_utc": _text(payload.get("completed_at_utc"), 80),
        "report_id": _text(payload.get("report_id"), 80),
        "verdict": _text(payload.get("verdict"), 40),
        "acceptance_ready": payload.get("acceptance_ready") is True,
        "error_type": _text(payload.get("error_type"), 120),
        "terminal": _text(payload.get("status"), 40) in _TERMINAL_STATES,
        "governance": {
            "customer_material_access": "NON_MUTATING_READ_ONLY",
            "deletion_policy": "RETAIN",
            "source_content_returned": False,
            "raw_cursor_returned": False,
            "credential_values_returned": False,
            "filesystem_path_returned": False,
            "background_execution": True,
        },
    }


def _write_lock(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(dict(payload), stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _read_lock(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}


def _release_lock(path: Path, job: str) -> None:
    try:
        payload = _read_lock(path)
        if _text(payload.get("job_id"), 80) == job:
            path.unlink(missing_ok=True)
    except OSError:
        return


def _recover_interrupted(
    root: Path,
    project: str,
    connector: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    if _text(payload.get("status"), 40) not in {"PENDING", "RUNNING"}:
        return payload
    if _owner_alive(payload):
        return payload
    now_unix = time.time()
    recovered = dict(payload)
    recovered.update(
        {
            "status": "INTERRUPTED",
            "completed_at_utc": _utc(now_unix),
            "error_type": "ACCEPTANCE_OWNER_DISAPPEARED",
            "acceptance_ready": False,
            "updated_at_utc": _utc(now_unix),
            "updated_unix": now_unix,
        }
    )
    _persist_recovered(root, project, connector, recovered)
    _release_lock(_lock_path(root, project, connector), _text(payload.get("job_id"), 80))
    return recovered


def get_current_feishu_tenant_acceptance_job(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    expected_connector_type: str | None = "feishu",
    schema: str = FEISHU_TENANT_ACCEPTANCE_JOB_SCHEMA,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _connector_id(connector_instance_id)
    _ensure_connector_registered(
        project,
        connector,
        resolved_root,
        expected_connector_type=expected_connector_type,
    )
    with _LOCAL_LOCK:
        payload = _read_json_object(_current_path(resolved_root, project, connector))
        if not payload:
            return {
                "schema": schema,
                "project_id": project,
                "connector_instance_id": connector,
                "status": "NOT_STARTED",
                "terminal": True,
                "acceptance_ready": False,
                "report_id": "",
                "governance": {
                    "customer_material_access": "NON_MUTATING_READ_ONLY",
                    "deletion_policy": "RETAIN",
                    "source_content_returned": False,
                    "raw_cursor_returned": False,
                    "credential_values_returned": False,
                    "filesystem_path_returned": False,
                    "background_execution": True,
                },
            }
        payload = _recover_interrupted(
            resolved_root,
            project,
            connector,
            dict(payload),
        )
        return _public_job(payload)


def get_feishu_tenant_acceptance_job(
    project_id: str,
    connector_instance_id: str,
    job_id: str,
    *,
    root: Path | None = None,
    expected_connector_type: str | None = "feishu",
    schema: str = FEISHU_TENANT_ACCEPTANCE_JOB_SCHEMA,
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _connector_id(connector_instance_id)
    job = _job_id(job_id)
    _ensure_connector_registered(
        project,
        connector,
        resolved_root,
        expected_connector_type=expected_connector_type,
    )
    with _LOCAL_LOCK:
        payload = _read_json_object(
            _history_path(resolved_root, project, connector, job)
        )
        if not payload:
            payload = _read_json_object(
                _legacy_history_path(resolved_root, project, connector, job)
            )
        if not payload:
            raise FeishuTenantAcceptanceJobError("acceptance_job_not_found")
        payload = _recover_interrupted(
            resolved_root,
            project,
            connector,
            dict(payload),
        )
        return _public_job(payload)


def _default_thread_starter(target: Callable[[], None], name: str) -> threading.Thread:
    thread = threading.Thread(target=target, name=name, daemon=True)
    thread.start()
    return thread


def _validated_completion(report: Mapping[str, Any]) -> tuple[str, str, bool]:
    report_id = Path(_text(report.get("report_path"), 1000)).stem
    verdict = _text(report.get("verdict"), 40)
    acceptance_ready = report.get("acceptance_ready")
    if not _REPORT_ID_RE.fullmatch(report_id):
        raise FeishuTenantAcceptanceJobError(
            "acceptance_job_report_identity_invalid"
        )
    if verdict not in {"PASS", "FAIL"}:
        raise FeishuTenantAcceptanceJobError(
            "acceptance_job_verdict_invalid"
        )
    if not isinstance(acceptance_ready, bool):
        raise FeishuTenantAcceptanceJobError(
            "acceptance_job_readiness_invalid"
        )
    if (verdict == "PASS") is not acceptance_ready:
        raise FeishuTenantAcceptanceJobError(
            "acceptance_job_verdict_readiness_mismatch"
        )
    return report_id, verdict, acceptance_ready


def start_feishu_tenant_acceptance_job(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    profile: str = "pilot",
    actor: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    runner: AcceptanceRunner = run_feishu_tenant_acceptance,
    thread_starter: ThreadStarter = _default_thread_starter,
    expected_connector_type: str | None = "feishu",
    schema: str = FEISHU_TENANT_ACCEPTANCE_JOB_SCHEMA,
    job_prefix: str = "ftaj",
    thread_name_prefix: str = "feishu",
) -> dict[str, Any]:
    resolved_root = (root or ROOT).resolve()
    project = _safe_project_id(project_id)
    connector = _connector_id(connector_instance_id)
    _ensure_connector_registered(
        project,
        connector,
        resolved_root,
        expected_connector_type=expected_connector_type,
    )
    profile_name = _text(profile, 40).lower() or "pilot"
    if profile_name not in {"smoke", "pilot", "enterprise"}:
        raise FeishuTenantAcceptanceJobError("acceptance_job_profile_invalid")
    clean_actor = {
        "name": _text(dict(actor or {}).get("name"), 160) or "operator",
        "role": _text(dict(actor or {}).get("role"), 80) or "knowledge_admin",
    }
    clean_options = dict(options or {})
    job = _text(job_prefix, 20) + "_" + uuid.uuid4().hex[:24]
    now_unix = time.time()
    now = _utc(now_unix)
    payload = {
        "schema": schema,
        "job_id": job,
        "project_id": project,
        "connector_instance_id": connector,
        "profile": profile_name,
        "status": "PENDING",
        "requested_at_utc": now,
        "requested_unix": now_unix,
        "started_at_utc": "",
        "completed_at_utc": "",
        "updated_at_utc": now,
        "updated_unix": now_unix,
        "report_id": "",
        "verdict": "",
        "acceptance_ready": False,
        "error_type": "",
        "owner_pid": os.getpid(),
        "owner_process_marker": _process_marker(os.getpid()),
        "owner_native_thread_id": 0,
        "process_token": _PROCESS_TOKEN,
        "actor": clean_actor,
        "options": {
            key: value
            for key, value in clean_options.items()
            if key
            in {
                "runs",
                "min_discovered_resources",
                "min_coverage_ratio",
                "max_unsupported_ratio",
                "max_run_duration_seconds",
                "max_nodes",
                "max_export_polls",
                "export_poll_interval",
                "allow_raw_text_fallback",
                "timeout",
            }
        },
        "governance": {
            "customer_material_access": "NON_MUTATING_READ_ONLY",
            "deletion_policy": "RETAIN",
            "customer_source_content_in_job": False,
            "raw_cursor_values_in_job": False,
            "credential_values_in_job": False,
        },
    }
    lock = _lock_path(resolved_root, project, connector)

    with _LOCAL_LOCK:
        current = _read_json_object(_current_path(resolved_root, project, connector))
        if current:
            current = _recover_interrupted(
                resolved_root,
                project,
                connector,
                dict(current),
            )
            if _text(current.get("status"), 40) in {"PENDING", "RUNNING"}:
                raise FeishuTenantAcceptanceJobError(
                    "acceptance_job_already_running"
                )
        lock_payload = {
            "job_id": job,
            "owner_pid": os.getpid(),
            "owner_process_marker": _process_marker(os.getpid()),
            "owner_native_thread_id": 0,
            "process_token": _PROCESS_TOKEN,
            "created_at_utc": now,
            "created_unix": now_unix,
        }
        try:
            _write_lock(lock, lock_payload)
        except FileExistsError:
            stale_lock = _read_lock(lock)
            if stale_lock and not _owner_alive(stale_lock):
                lock.unlink(missing_ok=True)
                _write_lock(lock, lock_payload)
            else:
                raise FeishuTenantAcceptanceJobError(
                    "acceptance_job_already_running"
                )
        _persist(resolved_root, project, connector, payload)

    def execute() -> None:
        started_unix = time.time()
        running = dict(payload)
        running.update(
            {
                "status": "RUNNING",
                "started_at_utc": _utc(started_unix),
                "updated_at_utc": _utc(started_unix),
                "updated_unix": started_unix,
                "owner_native_thread_id": threading.get_native_id(),
            }
        )
        with _LOCAL_LOCK:
            _persist(resolved_root, project, connector, running)
        completed = dict(running)
        try:
            report = dict(
                runner(
                    project,
                    connector,
                    root=resolved_root,
                    profile=profile_name,
                    actor=clean_actor,
                    **dict(running.get("options") or {}),
                )
            )
            report_id, verdict, acceptance_ready = _validated_completion(report)
            completed_unix = time.time()
            completed.update(
                {
                    "status": "COMPLETE",
                    "completed_at_utc": _utc(completed_unix),
                    "updated_at_utc": _utc(completed_unix),
                    "updated_unix": completed_unix,
                    "report_id": report_id,
                    "verdict": verdict,
                    "acceptance_ready": acceptance_ready,
                    "error_type": "",
                }
            )
        except BaseException as exc:
            completed_unix = time.time()
            completed.update(
                {
                    "status": "FAILED",
                    "completed_at_utc": _utc(completed_unix),
                    "updated_at_utc": _utc(completed_unix),
                    "updated_unix": completed_unix,
                    "report_id": "",
                    "verdict": "FAIL",
                    "acceptance_ready": False,
                    "error_type": _text(type(exc).__name__, 120),
                }
            )
        finally:
            with _LOCAL_LOCK:
                _persist(resolved_root, project, connector, completed)
                _release_lock(lock, job)
                _THREADS.pop(job, None)

    try:
        thread = thread_starter(
            execute,
            f"qualibug-{thread_name_prefix}-acceptance-{connector}",
        )
        if isinstance(thread, threading.Thread) and thread.is_alive():
            with _LOCAL_LOCK:
                _THREADS[job] = thread
    except Exception as exc:
        failed_unix = time.time()
        failed = dict(payload)
        failed.update(
            {
                "status": "FAILED",
                "completed_at_utc": _utc(failed_unix),
                "updated_at_utc": _utc(failed_unix),
                "updated_unix": failed_unix,
                "verdict": "FAIL",
                "acceptance_ready": False,
                "error_type": _text(type(exc).__name__, 120),
            }
        )
        with _LOCAL_LOCK:
            _persist(resolved_root, project, connector, failed)
            _release_lock(lock, job)
        raise FeishuTenantAcceptanceJobError(
            "acceptance_job_start_failed"
        ) from exc

    return get_feishu_tenant_acceptance_job(
        project,
        connector,
        job,
        root=resolved_root,
        expected_connector_type=expected_connector_type,
        schema=schema,
    )


def start_connector_tenant_acceptance_job(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
    profile: str = "pilot",
    actor: dict[str, Any] | None = None,
    options: dict[str, Any] | None = None,
    runner: AcceptanceRunner = run_connector_tenant_acceptance,
    thread_starter: ThreadStarter = _default_thread_starter,
) -> dict[str, Any]:
    """Start generic acceptance for any active registry connector."""
    return start_feishu_tenant_acceptance_job(
        project_id,
        connector_instance_id,
        root=root,
        profile=profile,
        actor=actor,
        options=options,
        runner=runner,
        thread_starter=thread_starter,
        expected_connector_type=None,
        schema=CONNECTOR_TENANT_ACCEPTANCE_JOB_SCHEMA,
        job_prefix="ctaj",
        thread_name_prefix="connector",
    )


def get_current_connector_tenant_acceptance_job(
    project_id: str,
    connector_instance_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Return the current generic acceptance job without exposing private job state."""
    return get_current_feishu_tenant_acceptance_job(
        project_id,
        connector_instance_id,
        root=root,
        expected_connector_type=None,
        schema=CONNECTOR_TENANT_ACCEPTANCE_JOB_SCHEMA,
    )


def get_connector_tenant_acceptance_job(
    project_id: str,
    connector_instance_id: str,
    job_id: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    """Return one generic acceptance job without exposing private job state."""
    return get_feishu_tenant_acceptance_job(
        project_id,
        connector_instance_id,
        job_id,
        root=root,
        expected_connector_type=None,
        schema=CONNECTOR_TENANT_ACCEPTANCE_JOB_SCHEMA,
    )


__all__ = [
    "CONNECTOR_TENANT_ACCEPTANCE_JOB_SCHEMA",
    "FEISHU_TENANT_ACCEPTANCE_JOB_SCHEMA",
    "FeishuTenantAcceptanceJobError",
    "get_connector_tenant_acceptance_job",
    "get_current_connector_tenant_acceptance_job",
    "get_current_feishu_tenant_acceptance_job",
    "get_feishu_tenant_acceptance_job",
    "start_connector_tenant_acceptance_job",
    "start_feishu_tenant_acceptance_job",
]
