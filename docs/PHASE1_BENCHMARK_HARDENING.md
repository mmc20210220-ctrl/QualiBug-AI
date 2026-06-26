# Phase 1 Benchmark Hardening

This patch makes the defect benchmark more credible by separating demo scoring from blind scoring.

## What changed

- Added `blind` and `demo` discovery modes.
- `blind` mode disables `benchmark_compat` probes and is the default for `RUN_DEFECT_DISCOVERY.cmd`.
- `demo` mode enables compatibility probes for repeatable product demonstrations only.
- Added Bug Factory clean mode through `BUG_SET=clean` and `RUN_BUG_FACTORY_CLEAN.cmd`.
- Added private bug-set switching through `BUG_SET=<bug_set_name>`.
- Added random hidden bug-set generation through `RUN_GENERATE_RANDOM_BUG_SET.cmd`.
- Reworked Bug Factory behavior so routes can run correctly in clean mode and only expose buggy behavior when the related private bug template is enabled.
- Strengthened Benchmark Evaluator matching from broad `risk_type + API` matching to multi-factor matching with `exact_match` and `partial_match`.
- `discovered_bugs.json` now records `discovery_mode` and `benchmark_compat_enabled`.
- `benchmark_report.html` now shows discovery mode, clean false-positive rate, exact matches and partial matches.

## Recommended verification flow

### 1. Clean baseline

```bat
RUN_BUG_FACTORY_CLEAN.cmd
```

In another terminal:

```bat
set DEFECT_DISCOVERY_MODE=blind
RUN_DEFECT_DISCOVERY.cmd
RUN_BENCHMARK_EVALUATION.cmd
```

Expected: known bugs = 0 and false positives close to 0.

### 2. Blind benchmark

```bat
set BUG_SET=bug_set_50
RUN_BUG_FACTORY.cmd
```

In another terminal:

```bat
set DEFECT_DISCOVERY_MODE=blind
RUN_DEFECT_DISCOVERY.cmd
RUN_BENCHMARK_EVALUATION.cmd
```

Expected: realistic non-100% recall. This is the credible metric.

### 3. Demo benchmark

```bat
RUN_DEMO.cmd
```

Expected: high discovery rate for demonstration. Do not treat this as the formal blind benchmark score.

### 4. Random hidden set

```bat
set BUG_COUNT=50
set BUG_SEED=20260619
RUN_GENERATE_RANDOM_BUG_SET.cmd
set BUG_SET=random_50_seed_20260619
RUN_BUG_FACTORY.cmd
```

Then run blind discovery and evaluation.

## Principle

AI Test Asset Center must never read `private_ground_truth`, `bug_sets`, `enabled_bugs`, `current_bug_set`, or hidden bug IDs. Only Bug Factory and Benchmark Evaluator may use hidden truth.
