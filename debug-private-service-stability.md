# [OPEN] Debug Session: private-service-stability

## Problem
- Symptom: local page verification for `benchmark_mall_v05_p0probe` shows unstable values across refreshes (`30/30`, `11/11`, `3/19`, and empty state).
- Expected: `Dashboard` and `EvidenceChain` should consistently reflect the same `command-center` payload for the same project and point in time.

## Constraints
- Do not modify business logic before runtime evidence is collected.
- First code change in existing logic must be instrumentation only.

## Hypotheses
1. Multiple backend processes or mixed versions are serving `8088`, so the frontend occasionally reaches stale code.
2. `private_pilot_entrypoint` starts successfully but crashes on first request or background work, causing aborted `command-center` responses.
3. Background recomputation mutates benchmark output files during page verification, so payload values legitimately change over time.
4. Frontend requests are interrupted or proxied inconsistently, causing `Dashboard` and `EvidenceChain` to observe different payload snapshots.

## Evidence Plan
- Inspect the current startup chain and request handlers without changing logic.
- Add minimal instrumentation around service startup, health checks, and `command-center` request handling.
- Reproduce with one clean backend process and compare pre-fix logs against browser/network observations.

## Status
- Session opened.

## Evidence
- Pre-fix local verification was polluted by stale listeners and mixed process state; browser runs observed `30/30`, `11/11`, `3/19`, and empty-state responses across different attempts.
- Instrumentation now proves the active backend is a single process:
  - `private-pilot entrypoint starting server` -> `pid=1968`
  - `private-pilot service bound` -> `host=127.0.0.1`, `port=8088`
- Repeated `GET /api/v1/projects/benchmark_mall_v05_p0probe/command-center` requests on that same process are stable:
  - `scan_id=scan_benchmark_mall_v05_p0probe_1783466097521`
  - `current_report_customer_ready_defect_count=4`
  - `family_customer_ready_defect_count=22`
  - `defect_count=22`
  - `clue_count=21`
- The service remains healthy after command-center requests (`/api/health` -> `200`), so the first request does not crash the process.

## Hypothesis Status
1. Confirmed: stale/mixed local backend listeners were a real source of inconsistent browser verification.
2. Rejected: the current backend does not crash on first request; health remains `200` after repeated command-center reads.
3. Partially confirmed: benchmark output can legitimately differ over time across runs, but under a single clean process the payload is stable.
4. Partially confirmed: frontend verification was polluted by inconsistent service state rather than a stable proxy bug.

## Fix Applied
- Added minimal runtime instrumentation only to startup and command-center response assembly.
- Added `scripts/start_private_pilot_acceptance.ps1` to enforce a clean acceptance environment:
  - kills stale listeners on `7777` and `8088`
  - starts the Debug Server with the active debug session
  - starts the private pilot backend with debug reporting enabled
  - waits for `/api/health`
  - prints the active PIDs, URLs, and log file paths

## Post-Fix Verification
- Running `scripts/start_private_pilot_acceptance.ps1` produces a single clean backend process and `backend_status=200`.
- Subsequent command-center requests keep returning the same `scan_id` and the same `4 / 22 / 22 / 21` counts, with matching debug log entries.
