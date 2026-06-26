# Phase7 Policy Memory + Regression Trend

Phase7 adds a policy memory layer on top of Phase6 policy A/B evaluation.

## Goal

Record each A/B evaluation run, detect strategy regressions, track long-term weak bug templates, and prepare reliable data for future probe optimization and training.

## New outputs

- `benchmark_outputs/policy_history/policy_history.json`
- `benchmark_outputs/policy_history/strategy_regression_report.html`
- `benchmark_outputs/policy_history/weak_template_trends.json`
- `benchmark_outputs/policy_history/policy_regression_alerts.json`
- `benchmark_outputs/policy_history/policy_memory_summary.json`

## Run

```cmd
RUN_POLICY_AB_EVALUATION.cmd
RUN_POLICY_MEMORY_UPDATE.cmd
```

Or verify:

```cmd
RUN_PHASE7_VERIFY.cmd
```

## Anti-cheat boundary

The policy memory layer reads benchmark outputs only. It does not read `enterprise_bug_factory/private_ground_truth`, `bug_sets`, or enabled bug files.

## Product value

Phase7 helps answer:

- Which probe policy improves high-value bug discovery over time?
- Which policy increased false positives?
- Which templates are consistently missed?
- Did a new strategy regress compared with the previous run?
