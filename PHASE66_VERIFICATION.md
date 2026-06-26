# Phase66 Verification Evidence

## Runtime health

A fresh isolated private-service `/health` request returned `ok=true` and
`llm_available=false` because no real provider was configured. The self-dogfood
scan therefore relied on deterministic local engines; it produced zero
LLM-powered analyses.

## Required regression tests

```text
python -m pytest tests/test_deep_bug_mining.py tests/test_bug_validation_queue.py tests/test_product_ui.py -q --tb=short
11 passed

python -m pytest tests/test_self_dogfood_audit.py -q --tb=short
1 passed

python -m compileall -q ai_test_asset_center aitestops
passed
```

## Self-dogfood rescan

The final run found the new strong-evidence P1 cross-view reconciliation defect:

```text
executive_summary.total_bugs_found=13
stage2.total_findings=18
detailed_findings=18
```

The audit command exits non-zero because a P1 defect was deliberately detected;
that is expected evidence, not a test failure.

## Release limitation

The package retains the Phase65 note that the environment may hang at the end of
one-process full-suite pytest. This iteration ran the mandated focused tests,
self-dogfood audit test, source compilation, and release smoke only. A clean CI
environment must still pass the repository's full release gate before GA.
