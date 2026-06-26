# Phase65 Verification Evidence

## Deterministic adversarial cases

- `/journal-lines` returns a complete, two-line voucher whose debit total is
  `50.00` and credit total is `40.00`. Expected result:
  `journal_double_entry_unbalanced` with a hashed voucher identity only.
- `/trial-balances` returns a prior-period closing balance of `130`, followed
  by an opening balance of `125` for the same account/currency. Expected
  result: `period_opening_balance_mismatch`.
- The same current period reports opening `125`, debit `10`, credit `5` and
  closing `125`. Expected result: `period_balance_formula_mismatch`.
- A production-declared target has a local base URL but is still blocked by the
  environment safety boundary before any HTTP GET. Expected result:
  `blocked_by_safety_boundary` and zero target requests.
- Any LLM semantic output is returned only in `semantic_hypotheses` with
  `status=unverified_hypothesis`; it cannot appear in deterministic findings.

## Measured regression commands

```bash
# Group A: accounting and cross-view safety/consistency
PYTHONPATH=. pytest -q \
  tests/test_financial_ledger_conservation.py \
  tests/test_consistency_isolation_reasoning.py \
  tests/test_metamorphic_differential_reasoning.py \
  tests/test_business_reconciliation.py \
  tests/test_temporal_data_regression_reasoning.py \
  tests/test_product_ui.py \
  tests/test_release_verifier.py
# 29 passed

# Group B: business causality and event-driven business facts
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

# Group C: private runtime, control plane and self-dogfood
PYTHONPATH=. pytest -q \
  tests/test_deep_bug_mining.py \
  tests/test_enterprise_knowledge_center.py \
  tests/test_enterprise_pilot_runtime.py \
  tests/test_enterprise_testops_control_plane.py \
  tests/test_env_loader.py \
  tests/test_multi_industry_business_reasoning.py \
  tests/test_self_dogfood_audit.py
# 22 passed

PYTHONPATH=. pytest -q tests/test_business_outcome_validation.py
# 7 passed

python -m compileall -q ai_test_asset_center aitestops
# passed
```

The groups are non-overlapping and cover the collected **95/95** tests. A
direct single-process `PYTHONPATH=. pytest -q` run reached roughly 95% progress
without an assertion failure, then stalled during late-suite shutdown in this
container and required termination. It remains a clean-CI release blocker; it
is not counted as a successful canonical suite.
