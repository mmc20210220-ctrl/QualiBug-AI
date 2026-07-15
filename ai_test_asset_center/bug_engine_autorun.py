"""QualiBug bug-engine autorun helper.

This is an operations layer, not a verifier shortcut.  It is designed for
reboot recovery: start the bundled local BugLab when requested, reconcile stale
runtime state, run the supervised self-evolution worker, and persist a durable
latest report for the operator.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .autonomous_evolution_orchestrator import run_evolution_orchestrated
from .bug_engine_reporter import build_customer_bug_report
from .bug_engine_gap_analyzer import build_evidence_gap_report
from .bug_engine_deep_probe_planner import build_deep_probe_plan_files
from .loop_runtime import LoopRuntimeSession
from .target_endpoint import resolve_target_base_url


ROOT = Path(__file__).resolve().parents[1]


def _utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _health_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= int(resp.status) < 300
    except Exception:
        return False


def ensure_local_buglab(*, host: str = "127.0.0.1", port: int = 8000, wait_seconds: float = 12.0) -> dict[str, Any]:
    """Start bundled MES BugLab if its health endpoint is not already OK."""
    health_url = f"http://{host}:{port}/api/health"
    if _health_ok(health_url):
        return {"started": False, "status": "already_running", "health_url": health_url}

    backend = ROOT / "mes_target" / "mes-buglab-target" / "backend"
    if not backend.exists():
        return {"started": False, "status": "missing_backend", "health_url": health_url, "backend": str(backend)}

    out_dir = ROOT / "platform_outputs" / "runtime"
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "mes_buglab_autorun.log"
    pid_path = out_dir / "mes_buglab_autorun.pid"
    log = open(log_path, "ab", buffering=0)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", host, "--port", str(port)],
        cwd=str(backend),
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    log.close()  # File descriptor transferred to subprocess, safe to close
    pid_path.write_text(str(proc.pid), encoding="utf-8")

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return {
                "started": True,
                "status": "failed_to_start",
                "pid": proc.pid,
                "returncode": proc.returncode,
                "health_url": health_url,
                "log": str(log_path),
            }
        if _health_ok(health_url):
            return {"started": True, "status": "healthy", "pid": proc.pid, "health_url": health_url, "log": str(log_path)}
        time.sleep(0.5)

    return {"started": True, "status": "health_timeout", "pid": proc.pid, "health_url": health_url, "log": str(log_path)}



def _is_pid_alive(pid: int) -> bool:
    """Cross-platform best-effort process liveness check."""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            import ctypes.wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return False
            try:
                code = ctypes.wintypes.DWORD()
                ok = ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                return bool(ok and code.value == 259)  # STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(handle)
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {}


def _fetch_openapi_spec(base_url: str | None = None, timeout: float = 5.0) -> dict[str, Any]:
    """Best-effort local target OpenAPI fetch for deep-probe planning.

    Failure is safe: the planner can still use runtime evidence.
    """
    base = resolve_target_base_url(base_url).rstrip("/")
    if base.endswith("/api"):
        base = base[:-4]
    url = base + "/openapi.json"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            if 200 <= int(resp.status) < 300:
                return json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return {}
    return {}


def start_bug_engine_daemon(
    *,
    project_id: str = "real_project_demo",
    cycles: int = 0,
    interval_seconds: float = 60.0,
    local_bootstrap_only: bool = True,
    bootstrap_target: bool = True,
    graph_mode: str = "shadow",
    reset_stale_runtime: bool = True,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Start bug-engine-auto as a detached background process.

    This is the Windows-friendly path for operators who want the engine to keep
    running after the PowerShell prompt returns.  It writes stdout/stderr and a
    PID file under the selected output directory.
    """
    output_dir = Path(out_dir or ROOT / "platform_outputs" / project_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    pid_path = output_dir / "bug_engine_autorun.pid"
    existing = _read_json(pid_path)
    existing_pid = int(existing.get("pid") or 0) if isinstance(existing, dict) else 0
    if existing_pid and _is_pid_alive(existing_pid):
        return {
            "started": False,
            "status": "already_running",
            "pid": existing_pid,
            "pid_file": str(pid_path),
            "latest_report": str(output_dir / "bug_engine_autorun_latest.json"),
        }

    log_path = output_dir / "bug_engine_autorun_background.log"
    cmd = [
        sys.executable, "-m", "aitestops.cli", "bug-engine-auto",
        "--project", project_id,
        "--cycles", str(cycles),
        "--interval-seconds", str(interval_seconds),
        "--graph-mode", graph_mode,
        "--out-dir", str(output_dir),
    ]
    if local_bootstrap_only:
        cmd.append("--local-bootstrap-only")
    if not bootstrap_target:
        cmd.append("--no-bootstrap-target")
    if not reset_stale_runtime:
        cmd.append("--no-reset-stale-runtime")

    env = os.environ.copy()
    env.setdefault("PYTHONPATH", str(ROOT))
    env.setdefault("QUALIBUG_ALLOW_UNAUTH_WRITE_PROBES", "0")
    log_fh = open(log_path, "ab", buffering=0)
    kwargs: dict[str, Any] = {
        "cwd": str(ROOT),
        "stdout": log_fh,
        "stderr": subprocess.STDOUT,
        "env": env,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, **kwargs)
    log_fh.close()  # Transferred to subprocess, safe to close our handle
    payload = {
        "pid": proc.pid,
        "project_id": project_id,
        "cmd": cmd,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "log": str(log_path),
        "latest_report": str(output_dir / "bug_engine_autorun_latest.json"),
        "cycles": cycles,
        "interval_seconds": interval_seconds,
    }
    pid_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    (output_dir / "bug_engine_daemon_status.json").write_text(
        json.dumps({**payload, "alive": True, "status": "started"}, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return {"started": True, "status": "started", "pid": proc.pid, "pid_file": str(pid_path), "log": str(log_path), "latest_report": str(output_dir / "bug_engine_autorun_latest.json")}


def bug_engine_status(*, project_id: str = "real_project_demo", out_dir: str | Path | None = None) -> dict[str, Any]:
    """Return daemon and latest-report status without starting anything."""
    output_dir = Path(out_dir or ROOT / "platform_outputs" / project_id)
    pid_path = output_dir / "bug_engine_autorun.pid"
    latest_path = output_dir / "bug_engine_autorun_latest.json"
    heartbeat_path = output_dir / "bug_engine_autorun_heartbeat.json"
    payload = _read_json(pid_path)
    pid = int(payload.get("pid") or 0) if isinstance(payload, dict) else 0
    latest = _read_json(latest_path)
    last_cycle = latest.get("last_cycle") if isinstance(latest.get("last_cycle"), dict) else {}
    status = {
        "project_id": project_id,
        "output_dir": str(output_dir),
        "pid": pid or None,
        "alive": bool(pid and _is_pid_alive(pid)),
        "pid_file": str(pid_path),
        "latest_report": str(latest_path),
        "latest_report_exists": latest_path.exists(),
        "heartbeat": str(heartbeat_path),
        "heartbeat_exists": heartbeat_path.exists(),
        "last_heartbeat": _read_json(heartbeat_path),
        "last_cycle": last_cycle,
        "customer_report": str(output_dir / "validated_bug_report.md"),
        "customer_report_exists": (output_dir / "validated_bug_report.md").exists(),
        "evidence_gap_report": str(output_dir / "evidence_gap_report.md"),
        "evidence_gap_report_exists": (output_dir / "evidence_gap_report.md").exists(),
        "deep_probe_plan": str(output_dir / "deep_probe_plan.md"),
        "deep_probe_plan_exists": (output_dir / "deep_probe_plan.md").exists(),
        "log": payload.get("log") if isinstance(payload, dict) else str(output_dir / "bug_engine_autorun_background.log"),
    }
    log_path = Path(str(status.get("log") or ""))
    if log_path.exists():
        try:
            raw_tail = log_path.read_text(encoding="utf-8", errors="replace")[-4000:]
            status["log_tail"] = raw_tail.splitlines()[-40:]
        except Exception:
            status["log_tail"] = []
    if latest_path.exists():
        try:
            age = max(0.0, time.time() - latest_path.stat().st_mtime)
            status["latest_report_age_seconds"] = round(age, 3)
        except Exception:
            pass
    (output_dir / "bug_engine_daemon_status.json").write_text(
        json.dumps(status, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return status

def reconcile_stale_runtime(project_id: str, output_dir: Path) -> dict[str, Any]:
    """Repair or remove stale runtime state left by reboot/crash.

    A healthy live owner is never disturbed.  Expired/dead owners are finalized
    as FAILED_RETRYABLE and their SQLite lease file can be safely replaced by the
    next LoopRuntimeSession.acquire().
    """
    owner = LoopRuntimeSession.current_owner(project_id, output_dir)
    if not owner:
        return {"action": "none", "reason": "no_existing_owner"}
    if owner.get("alive") and not owner.get("expired"):
        return {"action": "kept", "reason": "healthy_owner", "owner": owner}

    LoopRuntimeSession.reconcile_terminal(
        project_id,
        output_dir,
        "FAILED_RETRYABLE",
        error="stale runtime owner recovered by bug-engine-auto",
        detail="stale runtime lease reconciled before autorun",
    )
    return {"action": "reconciled", "reason": "dead_or_expired_owner", "owner": owner}


def run_bug_engine_auto(
    *,
    project_id: str = "real_project_demo",
    cycles: int = 1,
    interval_seconds: float = 60.0,
    local_bootstrap_only: bool = True,
    bootstrap_target: bool = True,
    graph_mode: str = "shadow",
    reset_stale_runtime: bool = True,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Run supervised bug-engine autorun cycles and persist reports.

    cycles=0 means run until interrupted by the operator.  For CI and ChatGPT
    patch verification, use cycles=1 or a small bounded number.
    """
    output_dir = Path(out_dir or ROOT / "platform_outputs" / project_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    os.environ["QUALIBUG_PROJECT"] = project_id
    os.environ["QUALIBUG_GRAPH_CONTEXT_MODE"] = graph_mode
    if local_bootstrap_only:
        os.environ["QUALIBUG_LOCAL_BOOTSTRAP_ONLY"] = "1"
    # Mutating unauthenticated probes remain opt-in only.
    os.environ.setdefault("QUALIBUG_ALLOW_UNAUTH_WRITE_PROBES", "0")

    target = ensure_local_buglab() if bootstrap_target else {"started": False, "status": "skipped"}
    stale = reconcile_stale_runtime(project_id, output_dir) if reset_stale_runtime else {"action": "skipped"}
    base_url_for_reports = resolve_target_base_url(None)
    openapi_spec = _fetch_openapi_spec(base_url_for_reports)

    summary: dict[str, Any] = {
        "autorun_version": "phase92f-v1",
        "project_id": project_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target": target,
        "runtime_recovery": stale,
        "cycles_requested": cycles,
        "cycles": [],
        "latest_report": str(output_dir / "bug_engine_autorun_latest.json"),
    }

    cycle = 0
    while cycles == 0 or cycle < cycles:
        cycle += 1
        cycle_started = time.time()
        result = run_evolution_orchestrated(project_id=project_id, max_evolution_cycles=1)
        discovery = result.get("discovery_result") or {}
        compact = {
            "cycle": cycle,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(cycle_started)),
            "ended_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "terminal": discovery.get("terminal"),
            "execution_status": discovery.get("execution_status"),
            "total_bugs": discovery.get("total_bugs"),
            "validated_candidates": discovery.get("validated_candidates"),
            "raw_confirmed_signals": discovery.get("raw_confirmed_signals"),
            "needs_more_evidence": discovery.get("needs_more_evidence"),
            "inconclusive_rate": discovery.get("inconclusive_rate"),
            "signals": result.get("signals", []),
            "evolution": result.get("evolution"),
        }
        report_path = output_dir / f"bug_engine_autorun_{_utc_stamp()}_cycle{cycle}.json"
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        compact["report"] = str(report_path)
        try:
            compact["customer_report"] = build_customer_bug_report(
                result,
                output_dir,
                project_id=project_id,
                base_url=base_url_for_reports,
            )
        except Exception as report_error:
            compact["customer_report_error"] = str(report_error)[:300]
        try:
            compact["evidence_gap_report"] = build_evidence_gap_report(result, output_dir)
        except Exception as gap_error:
            compact["evidence_gap_report_error"] = str(gap_error)[:300]
        try:
            compact["deep_probe_plan"] = build_deep_probe_plan_files(
                result,
                output_dir,
                api_spec=openapi_spec,
                project_id=project_id,
                max_items=int(os.environ.get("QUALIBUG_DEEP_PROBE_PLAN_LIMIT", "24") or 24),
            )
        except Exception as plan_error:
            compact["deep_probe_plan_error"] = str(plan_error)[:300]
        summary["cycles"].append(compact)
        # Keep the latest status JSON bounded during cycles=0 background runs.
        max_history = int(os.environ.get("QUALIBUG_AUTORUN_HISTORY_LIMIT", "25") or 25)
        if max_history > 0 and len(summary["cycles"]) > max_history:
            summary["cycles"] = summary["cycles"][-max_history:]
            summary["history_truncated"] = True
            summary["history_limit"] = max_history
        summary["last_cycle"] = compact
        (output_dir / "bug_engine_autorun_heartbeat.json").write_text(
            json.dumps({
                "project_id": project_id,
                "cycle": cycle,
                "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "terminal": compact.get("terminal"),
                "validated_candidates": compact.get("validated_candidates"),
                "gap_report": compact.get("evidence_gap_report"),
                "deep_probe_plan": compact.get("deep_probe_plan"),
            }, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (output_dir / "bug_engine_autorun_latest.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )

        if cycles != 0 and cycle >= cycles:
            break
        time.sleep(max(1.0, float(interval_seconds)))

    summary["ended_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (output_dir / "bug_engine_autorun_latest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return summary
