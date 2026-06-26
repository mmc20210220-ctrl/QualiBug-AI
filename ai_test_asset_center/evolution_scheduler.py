"""Phase81: Evolution Scheduler — schedules evolution cycles.

Designed as a cron-friendly entry point. Called by cron or loop daemon.
Decides whether to trigger a new evolution cycle based on:
- Time since last cycle
- Signal accumulation
- Active job status
"""

from __future__ import annotations

import json, time, os
from pathlib import Path
from typing import Any


class EvolutionScheduler:
    """Decides when to trigger evolution cycles."""

    def __init__(self, project_id: str = "real_project_demo"):
        self.project_id = project_id
        self._state_dir = Path("platform_outputs") / project_id
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._schedule_path = self._state_dir / "evolution_schedule.json"

        # Default: don't evolve more than once per hour
        self.min_interval_seconds = int(os.environ.get("EVOLUTION_MIN_INTERVAL", "3600"))
        # Max evolution cycles per day
        self.max_cycles_per_day = int(os.environ.get("EVOLUTION_MAX_PER_DAY", "6"))

    def should_trigger(self) -> tuple[bool, str]:
        """Check if it's time to trigger an evolution cycle."""
        state = self._load_state()

        now = time.time()
        last_trigger = state.get("last_trigger_ts", 0)
        today_cycles = state.get("today_cycles", 0)
        today_date = state.get("today_date", "")

        today = time.strftime("%Y-%m-%d", time.gmtime(now))
        if today_date != today:
            today_cycles = 0
            state["today_date"] = today

        # Daily limit is a hard safety cap and should take precedence over a
        # shorter cooldown message so operators can see why no run will occur.
        if today_cycles >= self.max_cycles_per_day:
            return False, f"Daily limit reached ({today_cycles}/{self.max_cycles_per_day})"

        # Check min interval
        elapsed = now - last_trigger
        if elapsed < self.min_interval_seconds:
            remaining = self.min_interval_seconds - elapsed
            return False, f"Cooldown: {remaining:.0f}s remaining"

        return True, "Ready"

    def record_trigger(self) -> None:
        """Record that a cycle was triggered."""
        state = self._load_state()
        now = time.time()
        today = time.strftime("%Y-%m-%d", time.gmtime(now))

        if state.get("today_date") != today:
            state = {"today_date": today, "today_cycles": 0, "last_trigger_ts": 0, "history": state.get("history", [])}

        state["last_trigger_ts"] = now
        state["today_cycles"] = state.get("today_cycles", 0) + 1
        state.setdefault("history", []).append({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
            "cycle": state["today_cycles"],
        })
        # Keep last 30 days
        state["history"] = state["history"][-180:]

        self._save_state(state)

    def get_status(self) -> dict:
        """Get current scheduler status."""
        state = self._load_state()
        now = time.time()
        last = state.get("last_trigger_ts", 0)
        return {
            "last_trigger": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last)) if last else "never",
            "cooldown_remaining": max(0, self.min_interval_seconds - (now - last)),
            "today_cycles": state.get("today_cycles", 0),
            "max_per_day": self.max_cycles_per_day,
            "can_trigger": self.should_trigger()[0],
        }

    def _load_state(self) -> dict:
        if self._schedule_path.exists():
            try:
                return json.loads(self._schedule_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_state(self, state: dict) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._schedule_path.write_text(json.dumps(state, indent=2, ensure_ascii=False, default=str))


# Cron-friendly entry point
def run_scheduled_evolution(project_id: str = "real_project_demo") -> dict:
    """Entry point for cron: check schedule, trigger if ready.

    Returns {"triggered": bool, "reason": str, "result": dict|None}
    """
    scheduler = EvolutionScheduler(project_id)
    can_trigger, reason = scheduler.should_trigger()

    result = {
        "triggered": False,
        "reason": reason,
        "status": scheduler.get_status(),
        "result": None,
    }

    if not can_trigger:
        return result

    # Check if an evolution job is already running
    from .evolution_watchdog import get_evolution_watchdog
    wd = get_evolution_watchdog(project_id)
    wd_status = wd.tick()
    active_jobs = wd_status["total_jobs"] - wd_status["healthy"]
    if active_jobs > 0:
        return {**result, "triggered": False, "reason": f"{active_jobs} active evolution job(s) still running"}

    # Trigger
    scheduler.record_trigger()

    try:
        from .autonomous_evolution_orchestrator import run_evolution_orchestrated
        evo_result = run_evolution_orchestrated(project_id=project_id)
        result["triggered"] = True
        result["result"] = evo_result
    except Exception as e:
        result["triggered"] = False
        result["reason"] = f"Evolution failed: {e}"

    return result
