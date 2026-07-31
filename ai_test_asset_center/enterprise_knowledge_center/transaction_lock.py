"""Project-scoped enterprise-knowledge transaction lease.

Knowledge registry, immutable source files, runtime source activation and chunk
indexes form one transaction domain. Every public mutation acquires this lease,
which serializes threads and processes for the same project and records the
current owner for diagnostics.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ..private_pilot_json_io import _write_json_object_atomic
from ..project_runtime_primitives import safe_project_id

_LOCAL_LOCKS: dict[tuple[str, str], threading.RLock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


class KnowledgeTransactionBusy(RuntimeError):
    def __init__(self, owner: dict[str, Any] | None = None) -> None:
        super().__init__("enterprise knowledge transaction is already running")
        self.owner = dict(owner or {})


def _local_lock(root: Path, project: str) -> threading.RLock:
    key = (str(root.resolve()), project)
    with _LOCAL_LOCKS_GUARD:
        return _LOCAL_LOCKS.setdefault(key, threading.RLock())


def _lease_dir(root: Path, project: str) -> Path:
    return (
        root.resolve()
        / "platform_workspace"
        / safe_project_id(project)
        / ".runtime_locks"
        / "knowledge.lock"
    )


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
    return bool(
        started
        and time.time() - started > stale_after_seconds
        and not _pid_alive(pid)
    )


def _remove_stale(lease_dir: Path) -> None:
    target = lease_dir.with_name(
        f"knowledge.lock.stale.{uuid.uuid4().hex}"
    )
    try:
        os.replace(lease_dir, target)
    except FileNotFoundError:
        return
    shutil.rmtree(target, ignore_errors=True)


@contextmanager
def knowledge_transaction(
    root: Path,
    project: str,
    *,
    operation: str,
    actor: dict[str, Any] | None = None,
    wait_seconds: float = 30.0,
    stale_after_seconds: int = 60 * 60,
) -> Iterator[dict[str, Any]]:
    """Acquire the only knowledge mutation lease for a project."""

    resolved_root = root.resolve()
    safe_project = safe_project_id(project)
    lease_dir = _lease_dir(resolved_root, safe_project)
    lease_dir.parent.mkdir(parents=True, exist_ok=True)
    local = _local_lock(resolved_root, safe_project)
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    token = uuid.uuid4().hex
    owner = {
        "schema": "qualibug.knowledge-transaction-lease.v1",
        "token": token,
        "pid": os.getpid(),
        "thread_id": threading.get_ident(),
        "project_id": safe_project,
        "operation": str(operation or "mutation"),
        "actor": dict(actor or {}),
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "started_unix": time.time(),
    }
    acquired = False
    with local:
        while True:
            try:
                lease_dir.mkdir()
                _write_json_object_atomic(_owner_path(lease_dir), owner)
                acquired = True
                break
            except FileExistsError:
                if _stale(
                    lease_dir,
                    stale_after_seconds=max(60, int(stale_after_seconds)),
                ):
                    _remove_stale(lease_dir)
                    continue
                if time.monotonic() >= deadline:
                    raise KnowledgeTransactionBusy(_read_owner(lease_dir))
                time.sleep(0.1)
        try:
            yield owner
        finally:
            if acquired:
                current = _read_owner(lease_dir)
                if current.get("token") == token:
                    shutil.rmtree(lease_dir, ignore_errors=True)


__all__ = ["KnowledgeTransactionBusy", "knowledge_transaction"]
