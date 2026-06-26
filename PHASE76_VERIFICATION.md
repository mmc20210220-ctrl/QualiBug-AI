# Phase76 Verification

## Isolated multi-step flow proof

An isolated disposable MES BugLab clone was used. The flow runner consumed PRD/API-derived context and explicit project flow mappings. It did not read the target `oracle/` truth directory or a known Bug total.

Two approved Sandbox flows executed:

1. **Later work-order start gate**
   - Created a production order, released it, captured the second work order, then tried to start it before the predecessor completed and while its machine was under maintenance.
   - The target accepted the action.
   - Phase76 wrote one `runtime_strong` state-transition observation to the parent ledger item.

2. **Failed stock-transfer rollback**
   - Captured source inventory, attempted a transfer to an explicitly unavailable destination, then captured source inventory again.
   - The target reported a business rejection but reduced source inventory.
   - Phase76 wrote one `runtime_strong` transactional-rollback observation based on a zero-delta snapshot assertion.

Both observations remained `EVIDENCE_CAPTURED`; no automated human confirmation or regression guard was created.

## Safety proof

- Compiling a flow made zero target requests.
- Executing without approval stayed blocked by the existing Sandbox gate.
- Execution required `sandbox`, `disposable_sandbox`, `execute`, `approved_sandbox_execution`, and an `approval_id`.
- Test evidence retained response hashes, step IDs, numeric deltas and status codes; credentials and raw payloads were redacted.
- No direct DELETE cleanup was attempted; the disposable target was destroyed after the isolated proof.

## Regression checks

- Required regression subset: 11/11 passed.
- Agent loop, lifecycle, Saga and conservation regression subset: 13/13 passed.
- Isolated Flow Orchestrator smoke: passed, with 2 deterministic runtime observations and a valid event hash chain.
- Full regression and release validation are recorded in `PHASE76_RELEASE_MANIFEST.json`.

## External boundary

No external LLM provider was needed for this proof. A configured Provider remains `offline` until the deployed environment receives a real, parseable health response.

## Full measured release verification

`python -m aitestops.cli verify-release --out PHASE76_RELEASE_MANIFEST.json` completed in an isolated process:

- `compileall`: passed;
- full regression suite: **99/99 passed**;
- product UI regression: passed;
- customer-visible text quality: passed;
- private service smoke: passed;
- `PHASE76_RELEASE_MANIFEST.json`: `overall_status=passed`, `release_ready=true`.
