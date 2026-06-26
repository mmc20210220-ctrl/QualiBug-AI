# Phase92Q — Snapshot Observer Planner

## Goal

Phase92P introduced before/after business-invariant judgement, but its evidence quality depended on whether a customer or an earlier stage had already configured useful snapshots. Phase92Q makes the runtime loop more autonomous: for every approved disposable write probe, QualiBug now mines customer-provided OpenAPI input and automatically plans read-only snapshot observers around the affected business object.

This keeps the engine aligned with document-grounded discovery: invariant ideas such as “inventory must not be negative” or “terminal objects must not mutate” are not treated as static bug rules. They become runtime findings only when Phase92Q captures before/after evidence and Phase92P proves the invariant was broken.

## Added

- `ai_test_asset_center/snapshot_observer_planner.py`
  - Plans GET-only before/after observer requests from OpenAPI paths.
  - Scores candidate read endpoints by target-path relation, risk type, path/query parameters, and business keywords.
  - Produces observer kinds including:
    - `primary_resource_detail`
    - `inventory_projection`
    - `account_resource_projection`
    - `business_ledger_projection`
    - `workflow_history_projection`
    - `tenant_ownership_projection`
    - `idempotency_collection_projection`
  - Emits evidence goals explaining what each observer is meant to prove.
  - Preserves strict no-oracle behavior by using only OpenAPI under input materials.

- `ai_test_asset_center/auto_test_data_factory.py`
  - Integrates the Phase92Q planner into `build_auto_fixture_for_probe`.
  - Replaces the old single direct detail snapshot with a risk-aware observer set when OpenAPI exposes multiple useful GET endpoints.
  - Preserves direct-read fallback for backward compatibility.
  - Adds receipt fields:
    - `snapshot_observer_planner`
    - `snapshot_observer_coverage`

- `ai_test_asset_center/grounded_probe_executor.py`
  - Executes planned snapshot observer query parameters safely.
  - Carries `observer_kind`, `evidence_goal`, and `source` into runtime snapshot evidence.
  - Updates report engine to `grounded_probe_executor_v6_phase92q`.
  - Adds summary fields:
    - `auto_snapshot_observer_kinds`
    - `auto_snapshot_observer_kind_count`
  - Adds governance flags for Phase92Q multi-observer snapshots.

- `tests/test_phase92q_snapshot_observer_planner.py`
  - Verifies conservation probes get multiple observers: primary detail, inventory, account balance, and ledger.
  - Verifies planner can be used directly from OpenAPI input.
  - Verifies executor renders planned query parameters for idempotency collection observers.

## Commercial impact

With Phase92Q, a customer can provide only a staging/test URL, accounts, and OpenAPI input. QualiBug can now:

1. create disposable `qb_auto_*` data;
2. choose write probes grounded in input docs;
3. automatically discover before/after read observers;
4. execute the write/replay in sandbox;
5. compare multiple business projections;
6. promote only evidence-backed invariant failures to validated candidates.

This is a major step toward the “customer gives environment + accounts, engine finds high-value business bugs by itself” workflow.

## Validation

Targeted Phase92Q and runtime-loop regression:

```bash
python -m pytest tests/test_phase92q_snapshot_observer_planner.py -q
# 3 passed
```

```bash
python -m pytest tests/test_auto_test_data_factory.py tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py -q
# 19 passed
```

Broader grounding / gate / invariant / executor regression:

```bash
python -m pytest tests/test_strict_document_grounding.py tests/test_discovery_finding_gate.py tests/test_business_invariant_mining.py tests/test_grounded_probe_executor.py tests/test_phase92p_business_invariant_before_after.py tests/test_phase92q_snapshot_observer_planner.py -q
# 28 passed
```

Compile check:

```bash
python -m compileall -q ai_test_asset_center tests/test_phase92q_snapshot_observer_planner.py
# passed
```

Full `python -m pytest -q` was started and progressed through the early suite, but exceeded the current execution window before completion.

## Suggested next phase

Phase92R — Observer response semantic joiner:

- normalize multiple observer payloads into one object graph;
- map order/detail/ledger/inventory/account snapshots by inferred IDs and business keys;
- compute stronger deltas across heterogeneous projections;
- generate clearer evidence narratives for validated candidates.
