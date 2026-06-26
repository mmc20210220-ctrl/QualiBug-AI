# Phase6: Probe Policy A/B Evaluation

Phase6 adds a governance layer for probe-generation policies. It compares multiple blind-mode policies against the hidden benchmark and recommends the safest next policy.

## Why this phase matters

Phase5 introduced adaptive probe learning. The next risk is uncontrolled probe growth: adding more probes may increase recall but also increase false positives and execution cost. Phase6 solves this by measuring policies side by side.

## Policy profiles

- `baseline`: generic + high-value pattern + journey probes.
- `feedback`: baseline + feedback-learning probes.
- `adaptive`: feedback + learned adaptive-policy probes.
- `conservative`: pattern-library only.
- `full_blind`: all blind-safe probe sources.

Blind mode never allows `benchmark_compat` probes.

## Outputs

`benchmark_outputs/policy_ab/`

- `policy_ab_scorecard.json`
- `policy_ab_report.html`
- `recommended_probe_policy.json`
- one subdirectory per policy profile with its own benchmark report

## Run

Start the SUT first:

```bat
set BUG_SET=bug_set_200
RUN_BUG_FACTORY.cmd
```

In another terminal:

```bat
RUN_POLICY_AB_EVALUATION.cmd
```

Open:

```text
benchmark_outputs\policy_ab\policy_ab_report.html
```

## Ranking score

The ranking score combines:

- instance recall
- template recall
- P0/P1 template recall
- precision
- false positive rate penalty
- probe-count penalty

The score is a governance metric for selecting the next strategy, not a marketing claim.

## Anti-cheat guarantees

- Evaluation runs in blind mode.
- `benchmark_compat_probe_count` must remain 0.
- AI discovery still reads only PRD, OpenAPI, SUT config, accounts and runtime responses.
- Ground truth is read only by the evaluator.
