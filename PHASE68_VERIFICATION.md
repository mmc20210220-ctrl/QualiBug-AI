# Phase68 Verification Evidence

## Required focused regression

```text
python -m pytest \
  tests/test_deep_bug_mining.py \
  tests/test_bug_validation_queue.py \
  tests/test_product_ui.py \
  -q --tb=short

11 passed
```

## Private-service and audit regression

```text
python -m pytest \
  tests/test_enterprise_pilot_runtime.py \
  tests/test_release_verifier.py \
  tests/test_self_dogfood_audit.py \
  -q --tb=short

10 passed
```

## Full-suite proof

The full suite was executed as a detached process because the interactive build
wrapper has an approximately 35-second command ceiling. The process completed
normally:

```text
95 passed in 36.88s
```

This corrects the prior assumption that the suite had an exit-stage hang. The
observed interruption was the wrapper timeout, not a test failure or deadlock.

## Self-dogfood precision and security proof

The same isolated self-dogfood inputs were run before and after the change.

```text
before: total=18, P1=10, missing_auth=5
after:  total=13, P1=5,  missing_auth=0
self_dogfood_audit.ok=true
self_dogfood_audit.finding_count=0
```

A direct route proof demonstrated both intended outcomes:

```text
GET /api/pilot/overview without actor headers
X-QualiBug-No-Local-Dev: 1
=> 401

GET /api/pilot/overview on localhost development without opt-out header
=> 200
```

## Build and release smoke

```text
python -m compileall -q \
  ai_test_asset_center aitestops benchmark_evaluator demo_system enterprise_bug_factory

python -m aitestops.cli verify-release --skip-full-tests --out PHASE68_RELEASE_MANIFEST.json
```

Compilation passed. Product UI, customer-visible text and private-service
smoke checks passed. The skip-full-tests smoke manifest is intentionally
`incomplete`; the separately measured full suite above passed.

## Full release manifest

The complete verifier was then run in a detached process so its internal full
pytest subprocess could finish beyond the interactive wrapper's command limit:

```text
python -m aitestops.cli verify-release --out PHASE68_RELEASE_MANIFEST.json

overall_status = passed
release_ready = true
full_test_suite = 95/95 passed
```

The generated manifest also records passing compilation, product UI,
customer-visible text, private-service view and read-only API checks.

## Remaining release boundary

Real LLM-provider health is deployment-specific and was not proven in this
isolated runtime because external DNS/network access was unavailable before
authentication. The package is engineering-validated, but provider online
status must be checked from the private deployment using `/health`.
