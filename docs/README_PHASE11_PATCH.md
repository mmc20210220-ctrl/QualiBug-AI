# Phase11 Execution Time Profiler + Parallel Probe Runner

Phase11 adds execution-time governance for large Bug Benchmark runs.

## What changed

- `ai_test_asset_center/execution_profiler.py`
  - reads `probe_execution_result.json`, `discovered_bugs.json`, and optional ROI scorecard
  - builds per-probe execution time profile
  - identifies slow probes
  - generates affinity-bucketed parallel execution plan
  - generates ROI-per-second scheduler policy
- `RUN_EXECUTION_PROFILER.cmd`
- `RUN_DEFECT_DISCOVERY_PARALLEL.cmd`
- `RUN_PHASE11_VERIFY.cmd`
- `tests/test_phase11_execution_profiler.py`

## Outputs

- `benchmark_outputs/execution_profiler/execution_time_profile.json`
- `benchmark_outputs/execution_profiler/parallel_execution_plan.json`
- `benchmark_outputs/execution_profiler/execution_budget_scheduler_policy.json`
- `benchmark_outputs/execution_profiler/phase11_execution_profiler_scorecard.json`
- `benchmark_outputs/execution_profiler/slow_probe_report.html`
- `platform_workspace/enterprise_shop/defect_discovery/execution_budget_scheduler_policy.json`

## How to run

```cmd
RUN_EXECUTION_PROFILER.cmd
```

Optional parallel discovery:

```cmd
set PROBE_PARALLEL_WORKERS=4
RUN_DEFECT_DISCOVERY_PARALLEL.cmd
```

Default production flow should still use sequential discovery unless the SUT is isolated or can tolerate parallel resets. The Phase11 profiler always produces a safe affinity-bucketed parallel plan first.
