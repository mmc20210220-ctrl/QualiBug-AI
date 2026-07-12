# Discovery Phase 2B and Phase 3 Implementation Plan

> Execute continuously with root-cause fixes, test-first changes, immediate
> Python AST checks, focused verification per task, and external evidence before
> any quality or promotion claim.

**Goal:** Finish the source semantics, experiment protocol, fixture, observer,
operational-truth, and single-mainline gaps from Phase 2; then implement the
Phase 3 tri-state Contract Oracle, independent Delivery Gate verification, and
canonical formal deduplication.

**Design:**
`docs/superpowers/specs/2026-07-12-discovery-phase2b-phase3-design.md`

## Global Constraints

- Preserve frontend `5174`, backend `8088`, model timeout `>=300`, model
  `max_tokens >=32768`, `MAX_HYPOTHESES=15`, and default `max_workers=4`.
- No benchmark/customer/industry hardcoding and no hidden-GT data in runtime.
- Production and unknown environment writes fail closed. Every accepted write
  has one governed audit receipt and reverse compensation.
- Never turn missing evidence, adapter failure, or harness failure into a Bug.
- Preserve unrelated dirty worktree files and benchmark artifacts.
- Add a failing test before each behavior change; observe the expected failure.
- After every Python edit, immediately AST-parse that file.
- Commit only task-owned paths or exact task-owned hunks.

## Task 1: Source Graph and Three-State Permission Semantics

**Primary files:** `behavior_ir.py`, `obligation_compiler.py`, relevant Behavior
IR/obligation tests.

- Add failing tests for exact `rule_to_interface` propagation, dangling and
  ambiguous relationship gaps, explicit permit/deny, unknown permission, and
  generic source action normalization.
- Normalize source relationship identifiers without path/name guessing.
- Represent `UNKNOWN` explicitly and prevent it from generating negative
  authorization obligations.
- Verify stable IDs/source refs and no hidden/private fields in IR.

## Task 2: Typed Observer Contracts and Authorization Evidence

**Primary files:** a focused observer contract/registry module,
`experiment_compiler.py`, `experiment_executor.py`, `assertion_dsl.py`, tests.

- Add failing tests proving `200 {}`, `200 []`, `204`, HEAD, and equivalent
  control/treatment payloads are indeterminate and never findings.
- Add typed observer receipts and verified capability preflight.
- Implement same-resource HTTP authorization/isolation evidence.
- Remove nominal observer booleans; unsupported observers block visibly.

## Task 3: Family-Specific Experiment Protocols and Fixture Proof

**Primary files:** `experiment_contract.py`, `experiment_compiler.py`,
`fixture_dag.py`, `runtime_binding_graph.py`, `runtime_binding_materializer.py`,
`experiment_executor.py`, focused tests.

- Encode activation requirements for authorization/isolation, validation,
  idempotency, state, conservation, and concurrency.
- Compile only faithful control/treatment/observation/cleanup steps.
- Require setup plus read receipts for ownership and state claims.
- Block unsupported barriers/effect observers rather than synthesizing values.

## Task 4: Operational Receipt Truth and Evaluator Projection

**Primary files:** `scan_operational_metrics.py`, `discovery_runtime.py`,
`discovery_evaluation_contract.py`, benchmark/evaluator submission boundaries,
focused tests.

- Derive HTTP attempts, production requests, scenario attempts, accepted
  writes, and cleanup outcomes from actual execution/audit receipts.
- Preserve known metrics when cost is unknown; retain the cost promotion block.
- Use attempt-ledger terminal receipts as the cleanup SSOT.
- Create one authority-scoped projection for evaluator-only shadow findings and
  pass Trace Ledger V2 consistently.

## Task 5: Remove Known Runtime Side Paths

**Primary files:** exact owned hunks in `v12_pipeline.py`, `__main__.py`,
`private_pilot_entrypoint.py`, the scan repair patch and tests, `aitestops/cli.py`.

- Delete the dead candidate vertical slice from the legacy domain while
  preserving the champion.
- Fix scan scope/order at the canonical source and stop installing the
  post-projection repair monkeypatch.
- Route the public product discovery command through the mainline or explicitly
  reject deprecated invocation; it must not execute an independent product
  finding/gate path.
- Verify one scheduler, one formal projection, and no exception fallback.

## Task 6: Evidence-Backed Module Strangler

**Primary files:** a small architecture inventory tool, its tests, and living
documentation.

- Declare supported product/evaluation roots and module responsibility classes.
- Build deterministic static reachability plus optional runtime import-trace
  comparison with explicit dynamic-import uncertainty.
- Produce retirement candidates; never auto-delete.
- Remove only candidates separately proven unused by product roots, tests,
  entrypoints, plugins, and runtime trace.
- Record module count, duplicate entrypoint count, monkeypatch authority count,
  and oversized-boundary count as architecture diagnostics, not quality.

## Task 7: Tri-State Assertion DSL and Contract Oracle Activation

**Primary files:** `assertion_dsl.py`, `contract_oracles.py`,
`runtime_verifier.py`, `oracle_engine.py`, focused tests.

- Add tri-state assertion receipts with expected, actual, source refs, and
  observer receipt IDs.
- Separate `VIOLATION` from `INDETERMINATE` throughout batch resolution.
- Require all family activation receipts before business Oracle execution.
- Classify parsing, actor, fixture, observer, and cleanup problems as harness or
  blocked outcomes, never defects.

## Task 8: Independent Delivery Gate Verification

**Primary files:** `customer_delivery_gate.py`, `discovery_finding_gate.py`,
`obligation_attempt_ledger.py`, quality projection tests.

- Add failing tests showing self-declared executor flags cannot pass the gate.
- Validate execution, control, treatment, typed observation, assertion
  violation, Oracle, reproduction, cleanup, lineage, and identity receipts.
- Make gate rejection reason codes stable and observable.
- Prove formal IDs/counts match attempt ledger, quality projection, API, and
  evaluator submission exactly.

## Task 9: Canonical Defect Signature and Registry

**Primary files:** the existing dedupe SSOT or one focused registry module,
quality projection, evaluator submission, tests.

- Compute deterministic signatures from target, normalized operation,
  property/invariant, actor relation, resource identity class, and outcome.
- Aggregate multiple reproduction receipts for one signature.
- Keep different mutations or materially different outcomes separate.
- Remove title-similarity and service/UI recomputation from formal authority.

## Task 10: Architecture, Safety, and External Verification

- Run all touched-file AST checks, focused suites, then the full Python suite.
- Verify configuration floors and product ports.
- Verify hidden-GT redaction/isolation, target policy, audit coverage, cleanup,
  attempt-ledger terminality, Trace V2, and formal ID consistency.
- Create a clean candidate worktree from the exact commit without altering the
  user's dirty original worktree.
- Run paired champion/candidate replay and shadow envelopes on the frozen
  131-Bug evaluator-private manifest.
- Keep `legacy_champion` selected unless candidate external TP/Recall/Precision/
  F1 and all health/safety gates satisfy the promotion rule.
- Update `AGENTS.md` and authoritative architecture documentation only with
  verified implemented state; do not document aspirations as completed facts.

## Completion Rule

Phase 2B or Phase 3 may be called complete only when its acceptance tests pass
and required runtime receipts exist. External quality remains `NOT_MEASURED`
until an evaluator-private receipt is produced. Module/line reduction is never
reported as discovery-quality improvement without external evidence.

