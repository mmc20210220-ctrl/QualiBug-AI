# Phase71 Verification Evidence

## Reproducible pre-fix P0

An isolated local service stored harmless synthetic reports for
`alpha_project` and `bravo_project`. A legitimate `alpha_user` actor/role
context requested:

```text
GET /api/findings?project=bravo_project
```

Before remediation, the response was HTTP 200 and contained the synthetic
foreign-report marker. The Phase71 Oracle emitted exactly one P0
`project_scope_cross_access` finding with redacted/hashes-only evidence.

## Post-fix boundary

With `X-QualiBug-Project-Scopes: alpha_project` in public-bind mode:

```text
GET /api/findings?project=alpha_project  -> 200
GET /api/findings?project=bravo_project  -> 403
```

The same deterministic Oracle emitted no finding. The self-dogfood audit also
completed with zero findings and recorded `project_scope_isolation` coverage.

## Required regression

```text
python -m pytest tests/test_deep_bug_mining.py \
  tests/test_bug_validation_queue.py tests/test_product_ui.py -q --tb=short

11 passed
```

## Full release measurement

```text
95 passed in 37.36s
```

The Phase71 release verifier completed with `overall_status=passed` and
`release_ready=true`. It independently passed source compilation, product UI
tests, customer-visible text quality and private-service smoke checks.

No test file, safety boundary, environment configuration or pipeline
orchestration flow was changed for Phase71.