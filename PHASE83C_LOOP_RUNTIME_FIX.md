# Phase83C — Loop Runtime Supervisor Fix

## Scope

This patch stabilizes the long-running QualiBug discovery loop. It does **not** change customer production access rules, business Bug confirmation thresholds, or customer-side product code.

## Fixed P0 failures

1. Replaced the undefined `HEARTBEAT_FILE` write path with a durable runtime heartbeat.
2. Added a SQLite-backed, per-project lease so only one loop worker can run at a time.
3. Added a 15-second background heartbeat pump so slow Reader/Reasoner API calls do not look like a stalled process.
4. Changed watchdog behavior: a live process with a stale heartbeat is diagnosed, never killed solely due to API latency.
5. Changed discovery retry semantics: two failed `discover()` attempts now return `FAILED_RETRYABLE`; they can no longer become `CONVERGED` with zero findings.
6. Isolated malformed hypotheses, including `entity: null`, so one LLM output cannot abort the entire executor stage.
7. Preserved child stdout/stderr in `platform_outputs/<project>/cron_worker.log` instead of discarding it.
8. Routed legacy Loop1, Loop2, continuous, cron and daemon launches through one supervised worker.
9. Blocked policy mutation after failed/skip runs and removed automatic policy promotion from runtime signals.
10. Fixed policy rollback parent lookup and scheduler daily-limit precedence.

## Canonical entry points

- Manual foreground worker: `python run_loop_worker.py`
- Cron scheduler tick: `python run_cron_loop.py`
- Continuous supervisor: `python loop_daemon.py`
- Watchdog: `python -m ai_test_asset_center.loop_watchdog --once`

Legacy `run_loop1_sweep.py`, `run_loop2_improve.py`, `run_continuous_loop.py`, and `run_self_improving.py` remain compatibility shims. They all route to the supervised worker and are protected by the same lease.

## Runtime artifacts

Per project under `platform_outputs/<project>/`:

- `.loop_runtime.sqlite` — authoritative lease state
- `.loop_heartbeat.json` — human-readable heartbeat and terminal status
- `.discovery_result.json` — worker result passed to the next scheduler tick
- `cron_worker.log` — child stdout and traceback
- `.last_loop_failure.json` — retained failure report; failed runs do not train memory or mutate policy

## Verified behavior

- Heartbeat keeps updating during a slow stage.
- A second owner cannot acquire the same project lease.
- Repeated discovery failure returns `FAILED_RETRYABLE`, never `CONVERGED`.
- A `None` entity is isolated to its own hypothesis rather than terminating Stage 3.
- A live slow Reasoner is never killed by the watchdog.
- Policy rollback restores the parent policy.

## Remaining operational requirement

Disable duplicate OS scheduled tasks for old launchers when possible. The lease makes duplicates safe, but one scheduler is still operationally cleaner. Use either the cron tick or the daemon—not both—as the normal long-running control plane.
