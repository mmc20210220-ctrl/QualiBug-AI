# Phase5 Adaptive Probe Optimizer

Phase5 adds a safe feedback-learning loop on top of Phase4.

Goal:

- Read evaluator feedback such as `probe_improvement_plan.json`, `training_samples.jsonl`, and scorecards.
- Convert missed-template feedback into a sanitized `learned_probe_policy.json`.
- Generate `adaptive_policy` probes in blind mode without reading private ground truth, bug sets, enabled bugs, or bug instance answers.

## Why this matters

Phase4 already converted common misses into reusable probes. Phase5 starts to make that process systematic:

1. Evaluator identifies missed templates and false-positive candidates.
2. Adaptive optimizer assigns template priority scores.
3. Discovery engine generates adaptive probes from template-level policy.
4. Benchmark report shows whether recall improves while precision and clean-mode false positive rate stay controlled.

## Files

- `ai_test_asset_center/adaptive_probe_optimizer.py`
- `platform_workspace/enterprise_shop/defect_discovery/learned_probe_policy.json`
- `platform_workspace/enterprise_shop/defect_discovery/adaptive_policy_probes.json`
- `RUN_ADAPTIVE_OPTIMIZER.cmd`
- `RUN_PHASE5_VERIFY.cmd`

## Safety rules

The adaptive policy must not contain:

- ground truth bug files
- enabled bug set
- bug instance ids
- concrete private trigger answers
- random seed

It only stores reusable template-level strategies, such as `REFUND_OVER_AMOUNT` or `PAYMENT_DUPLICATE_CALLBACK`.

## Recommended flow

Terminal A:

```bat
set BUG_SET=bug_set_200
.\RUN_BUG_FACTORY.cmd
```

Terminal B:

```bat
.\RUN_ADAPTIVE_OPTIMIZER.cmd
.\RUN_DEFECT_DISCOVERY_BLIND.cmd
.\RUN_BENCHMARK_EVALUATION.cmd
```

Reports:

- `benchmark_outputs/benchmark_report.html`
- `platform_workspace/enterprise_shop/defect_discovery/learned_probe_policy.json`
- `platform_workspace/enterprise_shop/defect_discovery/adaptive_policy_probes.json`
- `platform_outputs/enterprise_shop/defect_discovery/discovered_bugs.json`

## Target metrics

- `bug_set_200 template_recall`: move toward 0.70+
- `bug_set_200 instance_recall`: move toward 0.25+
- `precision`: keep >= 0.90
- `clean mode false_positive_rate`: keep <= 0.05

Do not optimize by enabling `benchmark_compat` in blind mode.
