# Phase4 Feedback Probe Learning

Phase4 adds the first real learning loop on top of Phase3 template benchmarking.
It does not let the AI discovery platform read hidden ground truth, bug sets, or enabled bug switches.
Instead, it converts previous benchmark feedback such as `missed_bug_analysis.md` and `probe_improvement_plan.json` into reusable high-value probe templates.

## What changed

- Added `feedback_learning` probe source.
- Added learned probes for repeated high-value misses: stock negative quantity, duplicate submit, stock rollback, IDOR, tenant isolation, cancelled-order payment, payment callback idempotency, refund abuse, and money consistency.
- Added `feedback_learning_probes.json` in the workspace for auditability.
- Kept `benchmark_compat` disabled in blind mode.
- Kept clean-mode false positive protection by excluding unstable journey findings from promoted discovered bugs.
- Added tests for learned probe generation and dedup behavior.

## Why this matters

Phase3 showed template-level metrics and missed-template rankings. Phase4 closes the loop:

1. Run blind discovery.
2. Run evaluator.
3. Review missed templates.
4. Convert repeated missed templates into reusable probes.
5. Re-run blind discovery and compare instance/template recall.

This is the first step toward a trainable high-quality bug discovery engine.

## Expected benchmark direction

On the bundled 200-bug benchmark, Phase4 should improve template recall and instance recall while keeping clean mode at zero or near-zero false positives.

Do not treat demo mode as the real score. The real score is blind mode + clean baseline.
