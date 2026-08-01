"""Cross-thread/process scan lease authority.

All scan entrypoints for one project share one atomic directory lease. This
prevents manual, continuous and ingest-triggered scans from writing the same
report/counter/state artifacts concurrently.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .private_pilot_json_io import _write_json_object_atomic
from .project_runtime_primitives import safe_project_id

_LOCAL_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


class ScanLeaseBusy(RuntimeError):
    def __init__(self, owner: dict[str, Any] | None = None) -> None:
        super().__init__("project scan is already running")
        self.owner = dict(owner or {})


def _local_lock(root: Path, project: str) -> threading.RLock:
    key = (str(root.resolve()), project)
    with _LOCAL_LOCKS_GUARD:
        return _LOCAL_LOCKS.setdefault(key, threading.RLock())


def _lease_dir(root: Path, project: str) -> Path:
    safe = safe_project_id(project)
    return root.resolve() / "platform_workspace" / safe / ".runtime_locks" / "scan.lock"


def _owner_path(lease_dir: Path) -> Path:
    return lease_dir / "owner.json"


def _read_owner(lease_dir: Path) -> dict[str, Any]:
    path = _owner_path(lease_dir)
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        process_query_limited_information = 0x1000
        synchronize = 0x00100000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(
            process_query_limited_information | synchronize,
            False,
            pid,
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return True
            return exit_code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _stale(lease_dir: Path, *, stale_after_seconds: int) -> bool:
    owner = _read_owner(lease_dir)
    try:
        pid = int(owner.get("pid") or 0)
        started = float(owner.get("started_unix") or 0)
    except (TypeError, ValueError):
        return False
    # A dead owner cannot recover the lease. Waiting hours before reclaiming it
    # leaves every subsequent real scan permanently blocked after a worker or
    # service restart. The PID liveness check is the authoritative local fact;
    # ``stale_after_seconds`` remains part of the API for callers that still
    # supply it, but must not delay recovery of an owner that is already gone.
    return bool(started and not _pid_alive(pid))


def _remove_stale_lease(lease_dir: Path) -> None:
    stale_name = lease_dir.with_name(
        f"scan.lock.stale.{uuid.uuid4().hex}"
    )
    try:
        os.replace(lease_dir, stale_name)
    except FileNotFoundError:
        return
    shutil.rmtree(stale_name, ignore_errors=True)


def active_scan_owner(root: Path, project: str) -> dict[str, Any]:
    return _read_owner(_lease_dir(root, project))


@contextmanager
def project_scan_lease(
    root: Path,
    project: str,
    *,
    mode: str,
    tenant_id: str,
    actor: dict[str, Any] | None = None,
    wait_seconds: float = 0.0,
    stale_after_seconds: int = 6 * 60 * 60,
) -> Iterator[dict[str, Any]]:
    """Acquire the only scan lease for a project or raise ``ScanLeaseBusy``."""

    resolved_root = root.resolve()
    safe_project = safe_project_id(project)
    local = _local_lock(resolved_root, safe_project)
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    token = uuid.uuid4().hex
    owner = {
        "schema": "qualibug.project-scan-lease.v1",
        "token": token,
        "pid": os.getpid(),
        "thread_id": threading.get_ident(),
        "project_id": safe_project,
        "tenant_id": str(tenant_id or ""),
        "mode": str(mode or "scan"),
        "actor": dict(actor or {}),
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "started_unix": time.time(),
    }
    lease_dir = _lease_dir(resolved_root, safe_project)
    lease_dir.parent.mkdir(parents=True, exist_ok=True)
    acquired = False
    while True:
        with local:
            try:
                lease_dir.mkdir()
                _write_json_object_atomic(_owner_path(lease_dir), owner)
                acquired = True
            except FileExistsError:
                if _stale(
                    lease_dir,
                    stale_after_seconds=max(60, int(stale_after_seconds)),
                ):
                    _remove_stale_lease(lease_dir)
                    continue
                owner_snapshot = _read_owner(lease_dir)
            else:
                owner_snapshot = None
        if acquired:
            break
        if time.monotonic() >= deadline:
            raise ScanLeaseBusy(owner_snapshot)
        time.sleep(0.25)
    try:
        yield owner
    finally:
        if acquired:
            with local:
                current = _read_owner(lease_dir)
                if current.get("token") == token:
                    shutil.rmtree(lease_dir, ignore_errors=True)


__all__ = [
    "ScanLeaseBusy",
    "active_scan_owner",
    "project_scan_lease",
]
