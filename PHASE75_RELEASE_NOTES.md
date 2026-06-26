# Phase75 Release Notes — Agent Experiment Compiler & Safe Evidence Dispatch

## Core change

Phase75 extends the persistent Agent Loop from planning into safe experiment
compilation and evidence dispatch. It does not add a second orchestration
framework, a new business runtime, or any automatic production write path.

New module: `ai_test_asset_center/agent_experiment_runner.py`.

- Converts document-backed ledger items into reproducible scenario packets.
- Persists scenario identity and executor receipts in the Phase74 canonical
  SQLite ledger (`loop_experiments`).
- Represents preconditions, fixtures, mutation, verification and cleanup in
  one packet for each experiment.
- Supports explicit, project-owned fixture catalogs for dependencies such as
  material, customer, routing or warehouse references.
- Blocks unbound foreign references as `BLOCKED_BY_FIXTURE` instead of
  inventing data or emitting a false Bug.
- Delegates all target execution to the existing disposable-sandbox contract
  executor and re-checks the shared safety boundary before fixture creation.
- Routes deterministic runtime observations back to the parent loop item as
  `EVIDENCE_CAPTURED`; human review remains required for confirmation and
  regression-guard creation.

## Precision and safety improvement

Document mutations previously namespace any field ending in `code`, `no`,
`serial`, `ref` or similar. Phase75 adds an explicit preserve list for fixture
bound foreign references. This prevents a valid `materialCode` or comparable
fixture value from being modified into an invalid reference by the harness.

## Product boundary

- No known Bug total is stored or used at runtime.
- No benchmark truth, customer source, credential or runtime ledger is shipped.
- Static/LLM observations cannot become formal findings.
- Sandbox writes require project policy, disposable environment, approval ID,
  call-time execution approval and the shared safety verdict.
