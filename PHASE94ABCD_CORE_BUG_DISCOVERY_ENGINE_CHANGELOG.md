# Phase94A-D Core Bug Discovery Engine Changelog

Goal: all optimization in this phase is judged by whether QualiBug can discover more high-value bugs, not by customer handoff/reporting features.

## Phase94A — Business state-machine auto-exploration + illegal path probes

Added `ai_test_asset_center/business_state_machine_explorer.py`.

Capability:
- Extracts state/status enums and terminal states from customer OpenAPI schemas.
- Infers stateful actions from document-grounded write endpoints (`submit`, `approve`, `pay`, `cancel`, `callback`, etc.).
- Generates illegal terminal-state transition probes such as `cancelled -> pay`, `rejected -> approve`, `completed -> submit`.
- Keeps all probes strict-document-grounded and requires runtime before/after evidence before findings are validated.

Proof of bug-discovery improvement:
- New tests verify the same baseline plan now generates additional high-value `state_transition_probe` candidates and records the state-machine count and added probe count.

## Phase94B — Multi-step business flow composition executor

Added `ai_test_asset_center/business_flow_combo_executor.py` and runtime execution support in `grounded_probe_executor.py`.

Capability:
- Composes single endpoint probes into ordered business chains.
- Generates illegal order inversion flows, e.g. `pay -> approve -> submit`.
- Adds an actual runtime multi-step executor path for `business_flow_sequence_probe`, not just a report artifact.
- Validates accepted illegal inversion flows as runtime candidates, still guarded by disposable sandbox approval.

Proof of bug-discovery improvement:
- New tests verify generated flow scenarios and runtime execution of a three-step illegal flow that produces a validated candidate when accepted.

## Phase94C — High-value business mutation probe generator

Added `ai_test_asset_center/high_value_business_mutation_probe_generator.py`.

Capability:
- Generates mutations for resource conservation, terminal object reuse, rejected object resume, illegal state jump, duplicate/blank/conflicting idempotency keys, cross-tenant object IDs, owner mismatch and boundary values.
- Mutations are derived from existing document-grounded write probes and preserve strict source refs.

Proof of bug-discovery improvement:
- New tests verify coverage across mutation kinds: terminal state reuse, duplicate idempotency, negative resource values, and multiple risk families.

## Phase94D — Concurrency/race runtime probe

Added `ai_test_asset_center/concurrency_race_probe_planner.py` and runtime parallel write execution support in `grounded_probe_executor.py`.

Capability:
- Identifies race surfaces: submit/pay/callback/approve/cancel/order/inventory/refund.
- Generates race families: idempotency race, stock oversell race, approval double-decision race, terminal transition race.
- Executes parallel write attempts using a barrier start and validates duplicate side-effect identifiers or before/after invariant failures.

Proof of bug-discovery improvement:
- New tests verify race probe generation and actual parallel runtime execution that detects multiple distinct resource IDs from concurrent submissions.

## Integrated Phase94 expander

Added `ai_test_asset_center/bug_discovery_probe_expander.py`.

Capability:
- Compounds Phase94A -> Phase94B -> Phase94C -> Phase94D so later phases expand on earlier generated probes.
- Emits `grounded_probe_phase94_bug_discovery_expansion.json` from the executor when explicitly enabled by `enable_phase94_bug_discovery_expansion`.
- Keeps legacy runtime validation stable unless Phase94 core bug-discovery expansion is explicitly enabled.

## Validation

Passed:

```bash
python -m pytest tests/test_phase94abcd_bug_discovery_engine.py -q
# 8 passed
```

```bash
python -m pytest tests/test_phase94abcd_bug_discovery_engine.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py tests/test_phase92r_observer_response_semantic_joiner.py tests/test_phase92s_cross_observer_conservation_reconciler.py tests/test_grounded_probe_executor.py -q
# 32 passed
```

```bash
python -m pytest tests/test_phase93w_external_tracker_closure_sync_policy.py tests/test_phase93x_external_tracker_sync_payload_builder.py tests/test_phase93y_external_tracker_sync_payload_gate.py tests/test_phase93z_external_tracker_sync_receipt_ledger.py tests/test_phase94abcd_bug_discovery_engine.py -q
# 22 passed
```

```bash
python -m compileall -q ai_test_asset_center tests/test_phase94abcd_bug_discovery_engine.py
# passed
```

## Next core bug-discovery refinement

Phase94A-D are now present and runtime-connected, but further polishing should keep pushing these directions:
- improve state-machine inference from prose transitions, not only enum/action hints;
- execute full Phase94B flows with per-step fixture handoff and observer snapshots between steps;
- mutate actual OpenAPI request bodies at field level rather than relying mostly on auto fixture body hints;
- add stronger concurrency oracles for inventory/ledger deltas beyond duplicate response IDs.
