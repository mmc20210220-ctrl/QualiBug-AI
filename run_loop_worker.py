#!/usr/bin/env python
"""Single supervised QualiBug loop worker.

All historical runners route here.  The runtime lease inside self_improving_loop
is the final authority that prevents duplicate project execution.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
PROJECT_ID = os.environ.get("QUALIBUG_PROJECT", "real_project_demo")
OUT = ROOT / "platform_outputs" / PROJECT_ID
OUT.mkdir(parents=True, exist_ok=True)
RESULT_FILE = OUT / ".discovery_result.json"

# Redirect stdout/stderr to a durable log file owned by this worker process.
# This is critical on Windows where the parent cron scheduler may close
# inherited handles.  The worker must own its own output stream.
WORKER_OUT_LOG = OUT / "worker_output.log"
_log_fh = open(str(WORKER_OUT_LOG), "a", encoding="utf-8", buffering=1)
sys.stdout = _log_fh
sys.stderr = _log_fh


def _atomic_write(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    started = time.time()
    try:
        from ai_test_asset_center.autonomous_evolution_orchestrator import run_evolution_orchestrated
        orchestrated = run_evolution_orchestrated(project_id=PROJECT_ID)
        discovery = orchestrated.get("discovery_result") or {}
        if orchestrated.get("error") and not discovery:
            payload = {
                "terminal": "FAILED_RETRYABLE",
                "execution_status": "FAILED_RETRYABLE",
                "error": orchestrated["error"],
                "traceback": "",
                "rounds": 0,
                "total_bugs": 0,
                "inconclusive_rate": 0.0,
            }
            exit_code = 1
        else:
            payload = dict(discovery)
            payload["orchestrator"] = {
                "active_policy_version": orchestrated.get("active_policy_version"),
                "evolution": orchestrated.get("evolution"),
            }
            exit_code = 0 if not str(payload.get("terminal", "")).startswith("FAILED") else 1
    except Exception as exc:
        payload = {
            "terminal": "FAILED_RETRYABLE",
            "execution_status": "FAILED_RETRYABLE",
            "error": str(exc),
            "traceback": traceback.format_exc(),
            "rounds": 0,
            "total_bugs": 0,
            "inconclusive_rate": 0.0,
        }
        exit_code = 1

    payload["worker_started_at"] = started
    payload["worker_finished_at"] = time.time()
    _atomic_write(RESULT_FILE, payload)
    # Reconcile the outer durable result with the human-readable heartbeat even
    # when a nested orchestration layer exited unexpectedly.
    try:
        from ai_test_asset_center.loop_runtime import LoopRuntimeSession
        LoopRuntimeSession.reconcile_terminal(
            PROJECT_ID,
            OUT,
            str(payload.get("terminal") or "FAILED_RETRYABLE"),
            error=str(payload.get("error") or ""),
            detail="run_loop_worker persisted final result",
        )
    except Exception:
        # The result file is already durable; never let terminal observability
        # repair hide the actual worker outcome.
        pass
    try:
        from ai_test_asset_center.console_output import safe_print
        safe_print(json.dumps({
            "terminal": payload.get("terminal"),
            "rounds": payload.get("rounds", 0),
            "total_bugs": payload.get("total_bugs", 0),
            "result_file": str(RESULT_FILE),
        }, ensure_ascii=False), flush=True)
    except Exception:
        pass
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
