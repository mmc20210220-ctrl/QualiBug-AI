#!/usr/bin/env python
"""Canonical continuous supervisor for QualiBug discovery.

The daemon never owns discovery state itself.  Every run goes through
run_loop_worker.py, whose SQLite lease and heartbeat prevent duplicate execution
when a cron task or legacy launcher is also present.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
PROJECT_ID = os.environ.get("QUALIBUG_PROJECT", "real_project_demo")
OUT = ROOT / "platform_outputs" / PROJECT_ID
OUT.mkdir(parents=True, exist_ok=True)
LOG_PATH = OUT / "loop_daemon.log"
COOLDOWN_SECONDS = int(os.environ.get("QUALIBUG_LOOP_COOLDOWN_SECONDS", "120"))
_shutdown = False


def log(message: str) -> None:
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), message)
    print(line, flush=True)
    with open(LOG_PATH, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def _on_signal(signum, _frame) -> None:
    global _shutdown
    log("Received signal %s; stopping after current worker." % signum)
    _shutdown = True


def main() -> int:
    global _shutdown
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    from run_loop_worker import main as run_worker

    log("=== QualiBug supervised daemon started (project=%s) ===" % PROJECT_ID)
    run_count = 0
    while not _shutdown:
        run_count += 1
        log("--- supervised run #%s ---" % run_count)
        code = run_worker()
        result_path = OUT / ".discovery_result.json"
        terminal = "UNKNOWN"
        if result_path.exists():
            try:
                terminal = json.loads(result_path.read_text(encoding="utf-8")).get("terminal", terminal)
            except Exception:
                pass
        log("Worker exited code=%s terminal=%s" % (code, terminal))
        if _shutdown:
            break
        # Failed runs are durable results.  Do not spin aggressively; let the
        # next supervisor cycle retry from persisted state after cooldown.
        time.sleep(COOLDOWN_SECONDS)
    log("=== QualiBug supervised daemon stopped ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
