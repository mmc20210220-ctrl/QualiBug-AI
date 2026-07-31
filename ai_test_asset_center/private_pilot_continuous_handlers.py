"""Continuous discovery lifecycle handlers."""
from __future__ import annotations

import threading
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

_CONTINUOUS_ROLES = {"project_owner", "qa_lead", "testops_admin", "admin"}
_CONTINUOUS_LIFECYCLE_LOCK = threading.RLock()


class ContinuousHandlersMixin:
    def _handle_continuous_start(
        self,
        project: str,
        root: Path,
        actor: dict[str, str],
        body: dict[str, Any],
    ) -> None:
        """Start one authorized continuous scan loop for a tenant project."""

        if not self._require_role(
            actor,
            _CONTINUOUS_ROLES,
            "continuous scan start",
        ):
            return None
        key = (str(root.resolve()), project)
        prepared = prepare_scan_body_for_campaign(project, root, dict(body))
        campaign_context = build_campaign_context_from_scan_body(prepared)
        try:
            interval_seconds = int(
                prepared.get("interval_s", body.get("interval_s", 60))
            )
        except (TypeError, ValueError):
            return self._json(
                {
                    "ok": False,
                    "error": "CONTINUOUS_INTERVAL_INVALID",
                },
                400,
            )
        interval_seconds = max(10, min(interval_seconds, 600))
        tenant_id = self._request_tenant()

        with _CONTINUOUS_LIFECYCLE_LOCK:
            current = _continuous_threads.get(key)
            if current and not current.get("stop"):
                if current.get("tenant_id") != tenant_id:
                    return self._json(
                        {
                            "ok": False,
                            "error": "CONTINUOUS_OWNER_MISMATCH",
                        },
                        403,
                    )
                return self._json(
                    {
                        "ok": True,
                        "message": "持续检测已在运行中。",
                        "round": current.get("round", 0),
                    }
                )
            if campaign_context:
                CONTINUOUS_CAMPAIGN_CONTEXTS[
                    continuous_context_key(root, project)
                ] = dict(campaign_context)
            state_file = _continuous_state_path(root, project)
            if state_file.exists():
                state = _read_json_object(state_file)
                state["status"] = "scanning"
                state["converged"] = False
                state.pop("converge_reason", None)
                state.pop("last_failure", None)
                state.pop("termination", None)
                _write_json_object_atomic(state_file, state)
            entry: dict[str, Any] = {
                "stop": False,
                "round": 0,
                "converged": False,
                "started_at": time.time(),
                "tenant_id": tenant_id,
                "actor": {
                    "name": str(actor.get("name") or "")[:120],
                    "role": str(actor.get("role") or "")[:64],
                },
            }
            _continuous_threads[key] = entry
            thread = threading.Thread(
                target=_continuous_scan_loop,
                args=(root, project, tenant_id, interval_seconds),
                name=f"qualibug-continuous-{project}",
                daemon=True,
            )
            entry["thread"] = thread
            thread.start()
        return self._json(
            {
                "ok": True,
                "message": (
                    f"持续检测已启动，每 {interval_seconds} 秒一轮，直到覆盖收敛。"
                ),
                "interval_s": interval_seconds,
            }
        )

    def _handle_continuous_stop(self, project: str, root: Path) -> None:
        """Stop a loop only for its authenticated tenant owner."""

        actor = self._require_actor()
        if actor is None:
            return None
        if not self._require_role(
            actor,
            _CONTINUOUS_ROLES,
            "continuous scan stop",
        ):
            return None
        tenant_id = self._request_tenant()
        key = (str(root.resolve()), project)
        with _CONTINUOUS_LIFECYCLE_LOCK:
            entry = _continuous_threads.get(key)
            if not entry:
                return self._json(
                    {"ok": True, "message": "持续检测未在运行。"}
                )
            if entry.get("tenant_id") != tenant_id:
                return self._json(
                    {
                        "ok": False,
                        "error": "CONTINUOUS_OWNER_MISMATCH",
                    },
                    403,
                )
            entry["stop"] = True
            state_file = _continuous_state_path(root, project)
            if state_file.exists():
                state = _read_json_object(state_file)
                state["status"] = "stopped"
                state["converged"] = False
                state["stopped_by"] = {
                    "name": str(actor.get("name") or "")[:120],
                    "role": str(actor.get("role") or "")[:64],
                }
                _write_json_object_atomic(state_file, state)
        return self._json({"ok": True, "message": "持续检测已手动停止。"})
