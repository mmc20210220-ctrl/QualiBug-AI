#!/usr/bin/env python
"""Non-overlapping cron scheduler for the supervised QualiBug loop.

This script never mutates source code, never auto-promotes policy and never
silences worker stderr.  It only collects the last durable result and starts one
worker when no healthy project lease exists.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
PROJECT = os.environ.get("QUALIBUG_PROJECT", "real_project_demo")
OUT = ROOT / "platform_outputs" / PROJECT
OUT.mkdir(parents=True, exist_ok=True)
PID_FILE = OUT / ".discovery_pid.json"
RESULT_FILE = OUT / ".discovery_result.json"
LOG_FILE = OUT / "cron_loop.log"
WORKER_LOG = OUT / "cron_worker.log"


def log(message: str) -> None:
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
    print(line, flush=True)
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _collect_result() -> None:
    if not RESULT_FILE.exists():
        return
    try:
        result = json.loads(RESULT_FILE.read_text(encoding="utf-8"))
        terminal = str(result.get("terminal", "UNKNOWN"))
        log("Collected: terminal=%s rounds=%s bugs=%s inconclusive=%s" % (
            terminal,
            result.get("rounds", 0),
            result.get("total_bugs", 0),
            result.get("inconclusive_rate", 0),
        ))
        if terminal.startswith("FAILED"):
            log("Failure retained for inspection; no memory learning, auto-patch, or policy evolution will run.")
            (OUT / ".last_loop_failure.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
            )
    except Exception as exc:
        log("Result parse error: %s" % exc)
    finally:
        RESULT_FILE.unlink(missing_ok=True)
        PID_FILE.unlink(missing_ok=True)


def main() -> int:
    _collect_result()
    from ai_test_asset_center.loop_runtime import LoopRuntimeSession
    owner = LoopRuntimeSession.current_owner(PROJECT, OUT)
    if owner and owner.get("alive") and not owner.get("expired"):
        log("Discovery already running (pid=%s step=%s status=%s) — skip" % (
            owner.get("pid"), owner.get("step"), owner.get("status"),
        ))
        return 0

    log("Starting supervised loop worker")
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    with open(WORKER_LOG, "a", encoding="utf-8") as worker_log:
        worker_log.write("\n=== WORKER START %s ===\n" % time.strftime("%Y-%m-%d %H:%M:%S"))
        worker_log.flush()
        proc = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "run_loop_worker.py")],
            cwd=str(ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            start_new_session=(os.name != "nt"),
            close_fds=(os.name != "nt"),
        )
    PID_FILE.write_text(json.dumps({
        "pid": proc.pid,
        "started_at": time.time(),
        "worker_log": str(WORKER_LOG),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    log("Started worker pid=%s; stdout/stderr=%s" % (proc.pid, WORKER_LOG))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
