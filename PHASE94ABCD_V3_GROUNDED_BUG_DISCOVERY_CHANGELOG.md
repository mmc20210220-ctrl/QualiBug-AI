# Phase94A-D V3 Grounded Bug Discovery Changelog

## Goal

Converge all Phase94A-D optimization on discovering more high-value bugs while removing dead-rule style generation.  V3 treats examples like `rejected -> pay` only as examples: the engine may generate such a probe only if the customer's own OpenAPI / source refs expose both the `rejected` state and the `pay` action for the same business resource.

## Completed

### Phase94A V3: grounded state-machine exploration

- Removed default fallback states such as `created/submitted/approved/paid/cancelled/completed/rejected`.
- Removed default terminal fallback such as `cancelled/rejected/completed`.
- State-path probes now require:
  - customer-grounded state values from OpenAPI enum or source quote;
  - customer-grounded write action endpoint from probe plan or OpenAPI operation;
  - same normalized business resource grouping.
- `attempt_target_state` is marked as grounded only when that target state exists in the customer state enum. Otherwise it remains a hint, not a claimed business state.

### Phase94C V3: grounded state mutations

- Removed hard-coded state mutation values like `completed` and `rejected`.
- State mutations now use terminal/current states observed in:
  - Phase94A illegal transition payloads;
  - customer-grounded terminal_states/customer_grounded_states;
  - customer source quotes.
- State mutation probes are skipped when the source probe has no customer-grounded state value.

### Regression and bug-discovery proof

New tests prove:

- no `rejected -> pay` style probe is generated when customer input has no `rejected` or `pay`;
- no state-machine probe is generated if an endpoint exists but no customer-grounded state enum/quote exists;
- Phase94C state mutation values come from grounded customer states such as `closed`, not global templates like `rejected` or `completed`.

## Validation

- `pytest -q tests/test_phase94abcd_bug_discovery_engine.py` → 11 passed
- `pytest -q tests/test_phase94abcd_bug_discovery_engine.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py tests/test_phase92r_observer_response_semantic_joiner.py tests/test_phase92s_cross_observer_conservation_reconciler.py tests/test_grounded_probe_executor.py` → 35 passed

## Commercial bug-discovery impact

V3 improves trust and signal quality: Phase94 now expands bug discovery only from customer-specific business states/actions, so generated probes remain high-value without becoming static, cross-industry dead rules.
