# Phase92C Bug Engine Autorun + Auth Evidence Bridge

## What changed

- Added `bug-engine-auto` CLI command for reboot recovery and one-command supervised autorun.
- Added `ai_test_asset_center/bug_engine_autorun.py`:
  - auto-starts bundled MES BugLab when needed,
  - checks `/api/health`,
  - reconciles stale loop runtime state after reboot/crash,
  - persists timestamped autorun reports and `bug_engine_autorun_latest.json`.
- Added read-only authorization-boundary evidence bridge:
  - anonymous GET -> HTTP 200 with non-empty business data can become a `VALIDATED_CANDIDATE`,
  - requires auth boundary matrix, response sensitivity summary, invariant evidence, reproduction input and entity binding,
  - does not require mutation before/after snapshots for read-only probes.
- Added customer-safe redaction for auth-boundary contracts:
  - raw password/token-like response fields are not copied into business finding contracts,
  - evidence retains status, route, role and redacted body summary.
- Fixed auth verifier false positives:
  - auth/anonymous hypotheses no longer become confirmed merely because authenticated admin requests return success.
  - unauthenticated response is now the relevant signal for anonymous auth-boundary hypotheses.
- Hardened automatic execution safety:
  - unauthenticated mutating probes (`POST/PUT/PATCH/DELETE` no-auth) are skipped by default,
  - opt-in only with `QUALIBUG_ALLOW_UNAUTH_WRITE_PROBES=1` for disposable sandboxes.
- Fixed local loop accounting:
  - repeated self-improvement rounds no longer sum duplicate findings,
  - successful runs with validated candidates terminate as `COMPLETED_WITH_FINDINGS`.

## Verified local autorun result

Command:

```bash
python -m aitestops.cli bug-engine-auto --project real_project_demo --cycles 1 --out-dir platform_outputs/real_project_demo
```

Result:

```json
{
  "terminal": "COMPLETED_WITH_FINDINGS",
  "total_bugs": 9,
  "validated_candidates": 9,
  "raw_confirmed_signals": 11,
  "needs_more_evidence": 6,
  "inconclusive_rate": 0.4
}
```

Latest report:

```text
platform_outputs/real_project_demo/bug_engine_autorun_latest.json
```

## Test verification

```text
56 passed
```

Covered suites:

- `tests/test_phase92a_evidence_bridge.py`
- `tests/test_discovery_finding_gate.py`
- `tests/test_business_finding_schema.py`
- `tests/test_discovery_engine_verifier_quality.py`
- `tests/test_production_safety_gate.py`
- `tests/test_loop_runtime_supervisor.py`
