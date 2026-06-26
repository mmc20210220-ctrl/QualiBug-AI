# Phase11: Execution Time Profiler + Parallel Probe Runner

Phase11 adds execution-time governance for large benchmark runs. It does not add benchmark compatibility probes and does not read private ground truth.

## New capabilities

- Probe duration profiling
- Slow probe report
- Source / template / risk timing summaries
- Parallel execution plan
- Optional parallel probe runner via `PROBE_PARALLEL_WORKERS`
- Timeout metadata via `PROBE_TIMEOUT_MS`

## Commands

```cmd
RUN_EXECUTION_PROFILER.cmd
RUN_DEFECT_DISCOVERY_PARALLEL.cmd
RUN_PHASE11_VERIFY.cmd
```

## Outputs

```text
benchmark_outputs/execution_profiler/execution_time_scorecard.json
benchmark_outputs/execution_profiler/parallel_execution_plan.json
benchmark_outputs/execution_profiler/slow_probe_report.html
platform_workspace/enterprise_shop/defect_discovery/parallel_execution_plan.json
```

## Governance note

Parallel execution is opt-in. For a shared in-memory SUT, keep worker count low or run isolated SUT instances per worker. Use the profiler first to decide whether parallel execution is worth enabling.
