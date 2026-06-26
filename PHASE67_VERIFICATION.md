# Phase67 Verification Evidence

## Focused regression suite

```text
python -m pytest \
  tests/test_deep_bug_mining.py \
  tests/test_bug_validation_queue.py \
  tests/test_product_ui.py \
  tests/test_env_loader.py \
  tests/test_enterprise_pilot_runtime.py \
  tests/test_self_dogfood_audit.py \
  -q --tb=short

19 passed
```

Source compilation passed:

```text
python -m compileall -q ai_test_asset_center aitestops
```

## Oracle compiler contract check

A controlled in-process response contained one valid candidate and one
candidate referring to `/invented`. The compiler accepted only the valid
candidate and verified all safety invariants:

- exact declared endpoints only;
- `GET` only;
- `unverified_hypothesis` status;
- `candidate_only` execution policy;
- deterministic replay required;
- confidence capped at `0.60`.

A pipeline integration check confirmed that one accepted hypothesis appears in
`stage2_discovery.llm_oracle_hypotheses` while `executive_summary.total_bugs_found`
continues to equal only the detailed deterministic finding list.

## Self-dogfood repair proof

An isolated real private-service scan completed with no audit findings after
the aggregation repair:

```text
self_dogfood_audit.ok = true
self_dogfood_audit.finding_count = 0
stage2.total_findings = 18
detailed_findings = 18
executive_summary.total_bugs_found = 18
sum(stage2.by_severity) = 18
stage3.total_analyses = 18
detailed_analyses = 18
executive_summary.impact_analyses = 18
stage3.llm_powered = 0
executive_summary.llm_powered_analyses = 0
```

## LLM provider proof boundary

The build environment refused outbound DNS/network access before an
authentication request could reach the configured provider. No model result is
recorded as evidence. The package is therefore engineering-validated for
configuration, isolation, and fallback behavior, but not provider-verified or
GA-ready until a private deployment's `/health` probe returns online.

## Release smoke

`python -m aitestops.cli verify-release --skip-full-tests --out PHASE67_RELEASE_MANIFEST.json`
completed all executed checks successfully:

- compileall
- product UI tests
- customer-visible text quality
- private service smoke

The manifest status remains `incomplete` because the known one-process full
pytest exit-stage hang was not reclassified as a passing full-suite gate.
