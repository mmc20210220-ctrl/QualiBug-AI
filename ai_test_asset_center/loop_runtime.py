"""Reliable runtime coordination for long-running QualiBug discovery loops.

This module intentionally owns only runtime concerns: a single active owner per
project, durable heartbeats, stage progress and terminal failure semantics.  It
never changes discovery policy or customer-facing verification rules.
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


DEFAULT_HEARTBEAT_INTERVAL_S = 15.0
DEFAULT_LEASE_TTL_S = 90.0


class LoopBusyError(RuntimeError):
    """Raised when another healthy loop already owns the project lease."""


class LoopRuntimeError(RuntimeError):
    """Raised when durable runtime state cannot be written safely."""


@dataclass(frozen=True)
class RuntimeLease:
    project_id: str
    owner_id: str
    pid: int
    acquired_at: float


def _is_pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, SystemError, ValueError):
        return False


class LoopRuntimeSession:
    """Cross-process lease and heartbeat for one project loop.

    SQLite is used as the authoritative lease store.  The JSON heartbeat remains
    human-readable for existing watchdogs and operations tooling.
    """

    def __init__(
        self,
        project_id: str = "real_project_demo",
        output_dir: Path | str | None = None,
        *,
        lease_ttl_seconds: float = DEFAULT_LEASE_TTL_S,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_S,
        owner_id: str | None = None,
    ) -> None:
        self.project_id = project_id
        self.output_dir = Path(output_dir or Path("platform_outputs") / project_id)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.output_dir / ".loop_runtime.sqlite"
        self.heartbeat_path = self.output_dir / ".loop_heartbeat.json"
        self.final_state_path = self.output_dir / ".loop_runtime_final.json"
        self.lease_ttl_seconds = float(lease_ttl_seconds)
        self.heartbeat_interval_seconds = float(heartbeat_interval_seconds)
        self.owner_id = owner_id or "%s:%s:%s" % (socket.gethostname(), os.getpid(), uuid.uuid4().hex)
        self.lease: RuntimeLease | None = None
        self._stop_event = threading.Event()
        self._pump: threading.Thread | None = None
        self._lock = threading.RLock()
        self._step = "starting"
        self._detail = ""
        self._round = 0
        self._stage_started_at = time.time()
        self._last_progress_at = time.time()
        self._status = "RUNNING"
        self._last_error = ""
        self._heartbeat_write_error = ""

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS loop_lease (
                project_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                pid INTEGER NOT NULL,
                acquired_at REAL NOT NULL,
                renewed_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                step TEXT NOT NULL,
                detail TEXT NOT NULL,
                round_num INTEGER NOT NULL,
                status TEXT NOT NULL,
                last_error TEXT NOT NULL
            )
            """
        )
        return conn

    def acquire(self) -> RuntimeLease:
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT owner_id, pid, expires_at, status FROM loop_lease WHERE project_id = ?",
                (self.project_id,),
            ).fetchone()
            if row:
                active_owner, active_pid, expires_at, status = row
                # A live process with a non-expired lease is authoritative.
                if expires_at > now and active_owner != self.owner_id and _is_pid_alive(int(active_pid)):
                    conn.execute("ROLLBACK")
                    raise LoopBusyError(
                        "project %s is already owned by pid=%s owner=%s status=%s"
                        % (self.project_id, active_pid, active_owner, status)
                    )
                conn.execute("DELETE FROM loop_lease WHERE project_id = ?", (self.project_id,))
            conn.execute(
                """
                INSERT INTO loop_lease
                (project_id, owner_id, pid, acquired_at, renewed_at, expires_at,
                 step, detail, round_num, status, last_error)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self.project_id,
                    self.owner_id,
                    os.getpid(),
                    now,
                    now,
                    now + self.lease_ttl_seconds,
                    self._step,
                    self._detail,
                    self._round,
                    self._status,
                    self._last_error,
                ),
            )
            conn.execute("COMMIT")
        except LoopBusyError:
            raise
        except Exception as exc:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise LoopRuntimeError("could not acquire loop lease: %s" % exc) from exc
        finally:
            conn.close()

        self.lease = RuntimeLease(self.project_id, self.owner_id, os.getpid(), now)
        self._write_heartbeat()
        self._pump = threading.Thread(
            target=self._heartbeat_loop,
            name="qualibug-loop-heartbeat",
            daemon=True,
        )
        self._pump.start()
        return self.lease

    def _persist_lease(self) -> None:
        if self.lease is None:
            raise LoopRuntimeError("heartbeat attempted without an acquired lease")
        now = time.time()
        conn = self._connect()
        try:
            cur = conn.execute(
                """
                UPDATE loop_lease
                SET renewed_at = ?, expires_at = ?, step = ?, detail = ?, round_num = ?,
                    status = ?, last_error = ?
                WHERE project_id = ? AND owner_id = ? AND pid = ?
                """,
                (
                    now,
                    now + self.lease_ttl_seconds,
                    self._step,
                    self._detail[:500],
                    int(self._round),
                    self._status,
                    self._last_error[:1000],
                    self.project_id,
                    self.owner_id,
                    os.getpid(),
                ),
            )
            if cur.rowcount != 1:
                raise LoopRuntimeError("loop lease was lost or replaced by another owner")
        finally:
            conn.close()

    @staticmethod
    def _atomic_json_write(path: Path, payload: dict) -> None:
        """Atomically publish a durable runtime state document."""
        tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            os.replace(tmp, path)
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    def _write_final_state(self) -> None:
        payload = self._heartbeat_payload()
        payload["finished_at"] = time.time()
        self._atomic_json_write(self.final_state_path, payload)

    def _heartbeat_payload(self) -> dict:
        now = time.time()
        return {
            "ts": now,
            "project_id": self.project_id,
            "pid": os.getpid(),
            "owner_id": self.owner_id,
            "step": self._step,
            "detail": self._detail[:500],
            "round": self._round,
            "status": self._status,
            "stage_started_at": self._stage_started_at,
            "last_progress_at": self._last_progress_at,
            "lease_expires_at": now + self.lease_ttl_seconds,
            "last_error": self._last_error[:1000],
        }

    def _write_heartbeat(self) -> None:
        payload = self._heartbeat_payload()
        try:
            self._atomic_json_write(self.heartbeat_path, payload)
        except Exception as exc:
            self._heartbeat_write_error = str(exc)
            raise LoopRuntimeError("could not write heartbeat: %s" % exc) from exc

    def heartbeat(self, step: str | None = None, detail: str = "", round_num: int | None = None) -> None:
        with self._lock:
            if step and step != self._step:
                self._step = step
                self._stage_started_at = time.time()
            if detail:
                self._detail = detail
            if round_num is not None:
                self._round = int(round_num)
            self._last_progress_at = time.time()
            self._persist_lease()
            self._write_heartbeat()

    def fail(self, error: BaseException | str, *, retryable: bool = True, step: str | None = None) -> None:
        with self._lock:
            if step:
                self._step = step
            self._status = "FAILED_RETRYABLE" if retryable else "FAILED_TERMINAL"
            self._last_error = str(error)
            self._last_progress_at = time.time()
            self._persist_lease()
            self._write_heartbeat()
            self._write_final_state()

    def complete(self, terminal: str = "COMPLETED") -> None:
        with self._lock:
            self._status = terminal or "COMPLETED"
            self._step = "done"
            self._last_progress_at = time.time()
            self._persist_lease()
            self._write_heartbeat()
            self._write_final_state()

    def _heartbeat_loop(self) -> None:
        while not self._stop_event.wait(self.heartbeat_interval_seconds):
            try:
                with self._lock:
                    if self._status != "RUNNING":
                        return
                    # Keep the lease alive during long Reader/Reasoner calls without
                    # pretending the stage made new semantic progress.
                    self._persist_lease()
                    self._write_heartbeat()
            except Exception as exc:
                self._heartbeat_write_error = str(exc)
                # The foreground thread will observe this through assert_healthy().
                return

    def assert_healthy(self) -> None:
        if self._heartbeat_write_error:
            raise LoopRuntimeError("heartbeat pump failed: %s" % self._heartbeat_write_error)

    def release(self) -> None:
        self._stop_event.set()
        if self._pump and self._pump.is_alive():
            self._pump.join(timeout=max(1.0, self.heartbeat_interval_seconds + 1.0))
        if self.lease is None:
            return
        conn = self._connect()
        try:
            conn.execute(
                "DELETE FROM loop_lease WHERE project_id = ? AND owner_id = ? AND pid = ?",
                (self.project_id, self.owner_id, os.getpid()),
            )
        finally:
            conn.close()
        self.lease = None

    def __enter__(self) -> "LoopRuntimeSession":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, _tb) -> bool:
        self.release()
        return False

    @classmethod
    def reconcile_terminal(
        cls,
        project_id: str,
        output_dir: Path | str | None,
        terminal: str,
        *,
        error: str = "",
        detail: str = "worker finalized result",
    ) -> None:
        """Repair terminal observability after an out-of-band worker failure.

        The supervisor normally finalizes its own heartbeat.  This idempotent
        method is also called by the outer worker after it writes the durable
        result, preventing stale ``RUNNING`` heartbeats when an exception exits
        an inner orchestration layer unexpectedly.
        """
        out = Path(output_dir or Path("platform_outputs") / project_id)
        out.mkdir(parents=True, exist_ok=True)
        heartbeat_path = out / ".loop_heartbeat.json"
        final_path = out / ".loop_runtime_final.json"
        now = time.time()
        existing: dict = {}
        try:
            if heartbeat_path.exists():
                existing = json.loads(heartbeat_path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
        payload = {
            **existing,
            "ts": now,
            "project_id": project_id,
            "step": "done",
            "detail": detail[:500],
            "status": terminal,
            "last_error": str(error)[:1000],
            "finished_at": now,
        }
        cls._atomic_json_write(heartbeat_path, payload)
        cls._atomic_json_write(final_path, payload)
        db_path = out / ".loop_runtime.sqlite"
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path), timeout=5.0, isolation_level=None)
                try:
                    conn.execute(
                        "UPDATE loop_lease SET status=?, step=?, detail=?, last_error=?, renewed_at=?, expires_at=? WHERE project_id=?",
                        (terminal, "done", detail[:500], str(error)[:1000], now, now, project_id),
                    )
                finally:
                    conn.close()
            except Exception:
                # State files are still authoritative for an outer worker when a
                # crashed process left SQLite unavailable or already removed.
                pass

    @classmethod
    def current_owner(cls, project_id: str, output_dir: Path | str | None = None) -> Optional[dict]:
        out = Path(output_dir or Path("platform_outputs") / project_id)
        db_path = out / ".loop_runtime.sqlite"
        if not db_path.exists():
            return None
        conn = sqlite3.connect(str(db_path), timeout=5.0)
        try:
            row = conn.execute(
                "SELECT owner_id, pid, acquired_at, renewed_at, expires_at, step, detail, round_num, status, last_error "
                "FROM loop_lease WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        except sqlite3.OperationalError:
            return None
        finally:
            conn.close()
        if not row:
            return None
        keys = [
            "owner_id", "pid", "acquired_at", "renewed_at", "expires_at", "step",
            "detail", "round_num", "status", "last_error",
        ]
        data = dict(zip(keys, row))
        data["alive"] = _is_pid_alive(int(data["pid"]))
        data["expired"] = float(data["expires_at"]) <= time.time()
        return data
