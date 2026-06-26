# Phase64 Verification Evidence

## Deterministic adversarial cases

- `sales_viewer` is explicitly configured with `expected_access=deny` for
  `/finance/reports`. The local test service intentionally returns HTTP 200.
  Expected result: `role_access_denied` with status-only, redacted evidence.
- `support_viewer` is allowed to read the same route but declares
  `gross_margin` and `bank_account` as forbidden. The local service returns a
  non-empty `gross_margin`. Expected result: `field_authorization_leak` with
  field path only, no value.
- `limited_viewer` declares `expected_access=empty` and receives an empty
  collection. Expected result: no permission finding.
- A production-declared target with a role-access contract is blocked before
  HTTP execution. Expected result: `blocked_by_safety_boundary` and zero
  target GETs.
- Mocked LLM output remains in `semantic_hypotheses` with
  `status=unverified_hypothesis`; it never appears in `findings`.

## Measured regression commands

```bash
PYTHONPATH=. pytest -q tests/test_consistency_isolation_reasoning.py
# 5 passed

PYTHONPATH=. pytest -q \
  tests/test_consistency_isolation_reasoning.py \
  tests/test_metamorphic_differential_reasoning.py \
  tests/test_business_reconciliation.py \
  tests/test_temporal_data_regression_reasoning.py \
  tests/test_product_ui.py \
  tests/test_release_verifier.py
# 26 passed

PYTHONPATH=. pytest -q \
  tests/test_business_invariant_mining.py \
  tests/test_multisource_reasoning.py \
  tests/test_business_lifecycle_reasoning.py \
  tests/test_business_causality_conservation.py \
  tests/test_business_population_constraints.py \
  tests/test_business_event_chain_reasoning.py \
  tests/test_business_saga_compensation_reasoning.py \
  tests/test_business_assurance_coverage.py \
  tests/test_confirmed_bug_flywheel.py \
  tests/test_bug_validation_queue.py
# 37 passed

python -m compileall -q ai_test_asset_center aitestops
```

The four non-overlapping groups above cover **92/92 collected tests** and all
passed. A canonical single-process `pytest -q` run in this container still
stalls after the self-dogfood audit begins (about 95% progress) without an
assertion failure; isolated and adjacent-module self-dogfood runs pass. The
clean-CI release verifier remains the GA gate.
