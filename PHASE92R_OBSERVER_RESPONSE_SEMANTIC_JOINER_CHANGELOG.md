# Phase92R — Observer Response Semantic Joiner

## Goal

Phase92Q expanded before/after evidence collection from a single resource detail endpoint to multiple OpenAPI-derived observer endpoints. Phase92R turns those heterogeneous observer responses into a deterministic business-object graph so the invariant adjudicator can evaluate all observed projections together.

This keeps QualiBug away from static rule matching: a business invariant is upgraded only when runtime before/after evidence from the joined observer graph proves an actual mutation, negative value, duplicate side effect, or boundary violation.

## Added

- `ai_test_asset_center/observer_response_semantic_joiner.py`
  - Extracts records from object, list, and common API envelopes (`data`, `records`, `items`, `rows`, etc.).
  - Links records by observed business identifiers such as `id`, `order_id`, `order_no`, `sku_id`, `business_key`, `idempotency_key`, `event_id`, `tenant_id`, `owner_user_id`, and `user_id`.
  - Builds a serializable before/after business object graph with joined records, clusters, join-key coverage, added/removed/changed entity fingerprints, and observer kind coverage.

## Changed

- `ai_test_asset_center/business_invariant_before_after.py`
  - Upgraded engine marker to `business_invariant_before_after_v2_phase92r`.
  - Uses the Phase92R joined observer graph when snapshot evidence is present.
  - Non-negative, terminal immutability, rejected-non-mutation, tenant/owner non-mutation, and idempotency checks now evaluate the joined graph rather than only the first snapshot payload.
  - Embeds `semantic_observer_graph` in the invariant evaluation for evidence traceability.

- `ai_test_asset_center/grounded_probe_executor.py`
  - Upgraded engine marker to `grounded_probe_executor_v7_phase92r`.
  - Adds governance flag `phase92r_observer_response_semantic_joiner`.
  - Adds summary count for semantic joined observer graphs.
  - Adds report text explaining that observer responses are joined into a before/after business object graph.

## Added tests

- `tests/test_phase92r_observer_response_semantic_joiner.py`
  - Verifies semantic joining across order detail, inventory projection, and ledger projection by shared business keys.
  - Verifies Phase92P/P92R evaluator detects a negative stock value from a secondary observer even when the primary resource detail is unchanged.
  - Verifies a rejected cross-tenant write is still validated when only a secondary ledger observer proves side effects.

## Verification

```bash
pytest -q tests/test_phase92r_observer_response_semantic_joiner.py
# 3 passed
```

```bash
pytest -q tests/test_auto_test_data_factory.py tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py tests/test_phase92r_observer_response_semantic_joiner.py
# 22 passed
```

```bash
pytest -q tests/test_strict_document_grounding.py tests/test_discovery_finding_gate.py tests/test_business_invariant_mining.py tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py tests/test_phase92r_observer_response_semantic_joiner.py
# 31 passed
```

```bash
python -m compileall -q ai_test_asset_center tests/test_phase92r_observer_response_semantic_joiner.py
# passed
```

Full `pytest -q` was started and progressed through the early suite without failures in the current execution window, then timed out before completion.

## Next

Phase92S should add a cross-observer conservation reconciler on top of the joined graph: derive expected resource deltas from the write request and ledger entries, then compare order amount, inventory stock, account balance, points/quota, and workflow history as a single reconciled evidence pack.
