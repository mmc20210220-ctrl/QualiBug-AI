"""Phase81: Evolution Watchdog — monitors evolution jobs, auto-recovers stuck jobs.

Hooks into the existing loop_watchdog.py infrastructure.
Detects: zombie evolution jobs, stuck states, lease expiry.
Actions: auto-recover, re-queue, or escalate.
"""

from __future__ import annotations

import json, time, os
from pathlib import Path
from typing import Any


class EvolutionWatchdog:
    """Monitors EvolutionOrchestrator jobs for health issues.

    Integrates with loop_watchdog.py — call tick() from the main watchdog loop.
    """

    def __init__(self, project_id: str = "real_project_demo"):
        self.project_id = project_id
        self._state_dir = Path("platform_outputs") / project_id
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._jobs_path = self._state_dir / "evolution_jobs.json"
        self._alerts_path = self._state_dir / "evolution_watchdog_alerts.json"

    def tick(self) -> dict:
        """Single watchdog tick. Check all evolution jobs."""
        status = {
            "ts": time.time(),
            "total_jobs": 0,
            "healthy": 0,
            "stuck": 0,
            "zombie": 0,
            "recovered": 0,
            "alerts": [],
        }

        jobs = self._load_jobs()
        status["total_jobs"] = len(jobs)

        now = time.time()
        active_states = {
            "COLLECTING_SIGNALS", "DIAGNOSING", "CANDIDATE_GENERATED",
            "VALIDATING_CANDIDATE", "REPLAY_EVALUATING", "SHADOW_EVALUATING",
            "COMPARING", "ACTIVE_MONITORING",
        }
        terminal_states = {
            "PROMOTED", "ROLLED_BACK", "FAILED_TERMINAL", "CANCELLED",
            "BLOCKED_BY_SAFETY", "BLOCKED_BY_CONFIGURATION",
        }

        for job in jobs:
            state = job.get("state", "")
            if state in terminal_states:
                status["healthy"] += 1
                continue

            # Check lease expiry
            lease_expires = job.get("lease_expires_at", "")
            if lease_expires:
                try:
                    expiry = time.mktime(time.strptime(lease_expires, "%Y-%m-%dT%H:%M:%SZ"))
                    if now > expiry + 600:  # 10 min past lease = zombie
                        status["zombie"] += 1
                        status["alerts"].append({
                            "type": "zombie_lease",
                            "job_id": job.get("job_id"),
                            "state": state,
                            "lease_expired": lease_expires,
                        })
                        # Auto-recover: release lease, mark retryable
                        job["lease_owner"] = ""
                        job["lease_expires_at"] = ""
                        job["state"] = "FAILED_RETRYABLE"
                        job["retry_count"] = job.get("retry_count", 0) + 1
                        status["recovered"] += 1
                        continue
                except (ValueError, OSError):
                    pass

            # Check stuck in non-terminal state too long
            started = job.get("started_at", "")
            if started:
                try:
                    start_ts = time.mktime(time.strptime(started, "%Y-%m-%dT%H:%M:%SZ"))
                    elapsed = now - start_ts
                    if elapsed > 3600:  # 1 hour = stuck
                        status["stuck"] += 1
                        status["alerts"].append({
                            "type": "stuck_job",
                            "job_id": job.get("job_id"),
                            "state": state,
                            "elapsed_minutes": round(elapsed / 60, 1),
                        })
                        # Auto-recover stuck jobs
                        if job.get("retry_count", 0) < 3:
                            job["state"] = "FAILED_RETRYABLE"
                            job["retry_count"] = job.get("retry_count", 0) + 1
                            status["recovered"] += 1
                        else:
                            job["state"] = "FAILED_TERMINAL"
                except (ValueError, OSError):
                    pass

            status["healthy"] += 1

        self._save_jobs(jobs)

        if status["alerts"]:
            self._state_dir.mkdir(parents=True, exist_ok=True)
            self._alerts_path.write_text(json.dumps(status["alerts"], indent=2, default=str))

        return status

    def _load_jobs(self) -> list[dict]:
        if self._jobs_path.exists():
            try:
                return json.loads(self._jobs_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return []

    def _save_jobs(self, jobs: list[dict]) -> None:
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._jobs_path.write_text(json.dumps(jobs, indent=2, ensure_ascii=False, default=str))


# Singleton
_watchdog: EvolutionWatchdog | None = None


def get_evolution_watchdog(project_id: str = "real_project_demo") -> EvolutionWatchdog:
    global _watchdog
    if _watchdog is None:
        _watchdog = EvolutionWatchdog(project_id)
    return _watchdog


def tick_evolution_watchdog() -> dict:
    """Convenience function for loop_watchdog integration."""
    return get_evolution_watchdog().tick()
