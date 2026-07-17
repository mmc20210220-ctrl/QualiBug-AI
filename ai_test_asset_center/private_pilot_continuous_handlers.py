"""Continuous discovery start/stop handlers for PrivatePilotHandler."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from .private_pilot_continuous import (
    _continuous_scan_loop,
    _continuous_state_path,
    _continuous_threads,
)
from .private_pilot_json_io import _read_json_object, _write_json_object_atomic
from .private_pilot_scan_context_contract import (
    CONTINUOUS_CAMPAIGN_CONTEXTS,
    build_campaign_context_from_scan_body,
    continuous_context_key,
    prepare_scan_body_for_campaign,
)


class ContinuousHandlersMixin:
    def _handle_continuous_start(self, project: str, root: Path, actor: dict[str, str], body: dict[str, Any]) -> None:
        """Start a continuous auto-scan loop for a project.

        The loop runs scans at intervals until convergence (no new findings
        for N consecutive rounds + coverage threshold) or explicit stop.
        Manual scans remain available in parallel — they do not conflict.
        """
        import threading as _threading
        key = (str(root), project)

        # Already running?
        if key in _continuous_threads and not _continuous_threads[key].get("stop"):
            return self._json({
                "ok": True,
                "message": "持续检测已在运行中。",
                "round": _continuous_threads[key].get("round", 0),
            })

        prepared_body = prepare_scan_body_for_campaign(project, root, body)
        campaign_context = build_campaign_context_from_scan_body(prepared_body)
        if campaign_context:
            CONTINUOUS_CAMPAIGN_CONTEXTS[continuous_context_key(root, project)] = campaign_context

        interval_s = int(prepared_body.get("interval_s", body.get("interval_s", 60)))  # default 60s between rounds
        interval_s = max(10, min(interval_s, 600))  # clamp 10s–10min

        # Reset converged flag
        state_file = _continuous_state_path(root, project)
        if state_file.exists():
            state = _read_json_object(state_file)
            state["status"] = "scanning"
            state["converged"] = False
            state.pop("converge_reason", None)
            state.pop("last_failure", None)
            state.pop("termination", None)
            _write_json_object_atomic(state_file, state)

        tenant_id = self._request_tenant()
        thread_entry = {"stop": False, "round": 0, "converged": False, "started_at": time.time()}
        _continuous_threads[key] = thread_entry

        t = _threading.Thread(
            target=_continuous_scan_loop,
            args=(root, project, tenant_id, interval_s),
            daemon=True,
        )
        t.start()
        _continuous_threads[key]["thread"] = t

        return self._json({
            "ok": True,
            "message": f"持续检测已启动，每 {interval_s} 秒一轮，直到覆盖收敛。",
            "interval_s": interval_s,
        })

    def _handle_continuous_stop(self, project: str, root: Path) -> None:
        """Stop the continuous auto-scan loop for a project."""
        key = (str(root), project)
        entry = _continuous_threads.get(key)
        if entry:
            entry["stop"] = True
            # Mark state
            state_file = _continuous_state_path(root, project)
            if state_file.exists():
                state = _read_json_object(state_file)
                state["status"] = "stopped"
                state["converged"] = False
                _write_json_object_atomic(state_file, state)
            return self._json({"ok": True, "message": "持续检测已手动停止。"})
        return self._json({"ok": True, "message": "持续检测未在运行。"})
