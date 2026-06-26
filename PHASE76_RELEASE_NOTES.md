# Phase76 Release Notes — Agent Business-Flow Orchestrator

## Core change

Phase76 extends the Agent Loop from single experiment packets into explicit,
reproducible multi-step business-flow experiments.

New module: `ai_test_asset_center/agent_business_flow_orchestrator.py`.

- Infers candidate stateful flow surfaces from API documentation without making target requests.
- Requires an explicit project flow mapping before any execution.
- Persists one flow scenario and its execution receipts in the existing Phase74 canonical SQLite ledger.
- Supports step outputs, response captures, explicit fixture dependencies, cross-step templates, snapshots and post-flow assertions.
- Treats positive precondition failures as blockers by default, not Bugs.
- Emits runtime evidence only when a mapped negative expectation is violated or a snapshot/quantity assertion fails.
- Reuses the existing disposable-sandbox contract gate; it does not alter `safety_boundary.py`, enable writes by default, or create direct-delete cleanup logic.

## Product boundary

- The runtime Loop does not know a target Bug count.
- PRD/API flow inference is candidate-only.
- Static and LLM text cannot confirm a Bug.
- Runtime evidence still requires human review before confirmation or regression guard creation.
- The delivery excludes runtime ledgers, target data, MES documents, truth files and credentials.
