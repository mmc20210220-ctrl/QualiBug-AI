# QualiBug Discovery Single-Mainline Phase 1 Design

**Status:** Approved for Phase 1 implementation on 2026-07-12  
**Scope:** Architecture simplification, authoritative execution semantics, and
stage observability. Phase 1 requires non-regression but does not require a new
external true positive.  
**Metric SSOT:** `docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md`  
**Architecture parent:**
`docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md`

## 1. Goal

Converge QualiBug on one traceable discovery mainline:

```text
Visible Sources
  -> Behavior IR
  -> Test Obligations
  -> Executable Experiments
  -> Governed Execution
  -> Typed Observers and Assertions
  -> Delivery Gate
  -> Quality Projection
  -> External Evaluator
```

Phase 1 removes duplicate scheduling, completion, execution-status, and formal
finding authority. It also makes every selected obligation explainable through
an immutable terminal attempt receipt. Later phases use this foundation to
convert binding, fixture, observer, and cleanup blockers into real external
true positives.

Phase 1 is successful only when a clean, reproducible candidate demonstrates
that the single-mainline architecture does not regress the eligible champion's
external TP, Recall, Precision, F1, execution health, safety, or cleanup.

## 2. Why Phase 1 Exists

The latest inspected diagnostic run has 22 TP, 63 FP, 16.79% Recall, and
25.88% Precision, but pipeline health is `DEGRADED`. It is diagnostic evidence,
not a promotable clean champion.

The current obligation path reports:

- 114 Test Obligations;
- 7 compiled experiments;
- 107 blocked experiments;
- 68 `BLOCKED_MISSING_BINDING`;
- 39 `BLOCKED_NON_REVERSIBLE_WRITE`;
- all 7 executed experiments in the authorization family.

The code also exposes conflicting authority:

- `discovery_funnel.effective_execution_status` reconciles legacy scenario
  execution with Experiment Executor state;
- `EnterpriseCampaign` completion is derived from selected and attempted
  behavior-slice IDs;
- `discovery_trace_ledger.v1` is keyed by behavior slices and joins legacy
  phase objects after execution;
- `run_v12_pipeline` owns orchestration, scheduling, execution, projection,
  and persistence responsibilities in one large function;
- the legacy behavior-slice path still provides most diagnostic TP while the
  Experiment path has not yet achieved equivalent coverage.

This makes local changes difficult to attribute. More hypotheses, probes, or
rounds can increase execution volume without increasing externally scored TP.

## 3. Decisions

### 3.1 Use an in-place strangler migration

Phase 1 extends the existing Behavior IR, Obligation, Experiment, governed
executor, delivery gate, quality projection, and evaluator contracts. It does
not create a V13 rewrite or a parallel product pipeline.

The existing V12 entry point remains temporarily for compatibility, but it
becomes a thin caller of a focused mainline coordinator. Domain logic moves
behind existing contract modules rather than being copied into a new stack.

### 3.2 Select authority before a run

Each run freezes exactly one `mainline_authority` in its run envelope:

```text
legacy_champion | experiment_candidate
```

This value is selected before planning or execution and is immutable during
the run. Exceptions never switch authority. A rollback selects the previous
frozen policy before a later run.

During migration, the non-authoritative path may execute only in a separate,
explicitly labelled shadow run envelope. A single run never invokes both paths.
Shadow findings:

- cannot enter the Delivery Gate's customer track;
- cannot affect `formal_customer_deliverable_count`;
- cannot satisfy campaign completion;
- cannot appear in product-created evaluation submissions;
- remain available as redacted engineering diagnostics.

The evaluator-owned champion/challenger runner may send the isolated shadow
scope directly to the private evaluator and receive a shadow comparison
receipt. That evaluator-private channel does not publish product or customer
output and is distinct from the product evaluation-submission API.

Promotion atomically changes the pre-run authority only after paired
champion/candidate replay proves non-regression. After promotion, legacy inputs
must enter through the Obligation adapter; legacy execution is disabled.

### 3.3 Make Obligation attempts the execution SSOT

Campaign identity and target governance remain in `EnterpriseCampaign`, but
campaign completion is no longer inferred from behavior-slice traffic.

The authoritative run ledger contains one attempt for every selected
obligation. Each attempt binds the continuity chain:

```text
candidate_id
  -> source_ref
  -> behavior_ir_refs
  -> obligation_id
  -> experiment_id
  -> execution_id
  -> observation_receipt_ids
  -> oracle_receipt_id
  -> gate_receipt_id
  -> finding_id (only when deliverable)
```

Every selected obligation ends in exactly one terminal outcome:

```text
DELIVERABLE
REJECTED
BLOCKED(reason_code)
DEFERRED(policy_code)
HARNESS_FAILED(error_code)
```

`EXECUTED` and `VERIFIED` are lifecycle stages, not terminal customer
outcomes. A selected obligation without a terminal outcome makes the campaign
incomplete and pipeline health `DEGRADED`.

### 3.4 Evolve existing receipts instead of inventing a second ledger

Phase 1 evolves the existing contracts:

- `qualibug.experiment.v1` remains the compiled experiment contract;
- `qualibug.experiment-execution.v1` remains the governed execution receipt;
- a new obligation-attempt projection joins compile, execution, observation,
  Oracle, and gate receipts without duplicating their payloads;
- `qualibug.discovery-trace-ledger.v2` becomes obligation-attempt keyed;
- `qualibug.discovery-quality-projection.v1` remains the only formal product
  count and classification projection.

V1 Trace Ledger input is not silently interpreted as V2. A deterministic,
explicit migration function may convert stored diagnostic artifacts. Runtime
emits V2 directly after cutover.

## 4. Target Component Boundaries

### 4.1 Mainline coordinator

Create a focused coordinator whose only responsibilities are:

1. validate immutable run identity and `TargetPolicyDecision`;
2. call stages in the authoritative order;
3. persist stage and terminal receipts;
4. fail the run when stage contracts disagree;
5. return the canonical run result.

The coordinator contains no endpoint heuristics, fixture synthesis, HTTP
details, Oracle rules, or customer-delivery classification.

### 4.2 Legacy input adapter

The adapter converts existing behavior slices and LLM hypotheses into
source-referenced Test Obligations. It may preserve lineage fields, but it may
not:

- choose budgets or rounds;
- send requests;
- invoke an Oracle;
- confirm a finding;
- persist a formal count;
- manufacture missing actors, operations, fixtures, observers, or assertions.

Incomplete conversion emits a typed compile blocker.

### 4.3 Experiment compiler and binding

`obligation_compiler.py`, `experiment_compiler.py`,
`runtime_binding_graph.py`, and `fixture_dag.py` own compilation readiness.
They return compiled experiments or explicit blockers. They never downgrade an
unresolved requirement into a best-effort scenario.

### 4.4 Governed executor

`experiment_executor.py` remains the only experiment HTTP execution path.
Every write uses the governed sandbox executor and emits one audit receipt per
actual write. Partial setup is compensated in reverse accepted-write order;
the original failure remains visible.

### 4.5 Typed observation and verification

An observer requirement is satisfied only by a typed receipt from an available
surface. HTTP, DB, UI, event, log, job, and trace observations are distinct.
An unrelated successful response cannot satisfy a missing observer.

Protocol Oracle results and source-derived business assertions remain separate.
Oracle exceptions are harness failures, never defects.

### 4.6 Delivery and quality

The Delivery Gate is the only component that may emit `DELIVERABLE`.
`discovery_quality_projection.py` is the only product-facing defect-count
projection. External Recall, Precision, and F1 continue to come only from the
evaluator-owned receipt.

## 5. Observability Contract

Each attempt exposes stage records with the following required fields:

```text
run_id
campaign_id
target_id
environment_id
obligation_id
experiment_id (when compiled)
stage
status
reason_code
started_at_utc
finished_at_utc
elapsed_ms
input_fingerprint
output_fingerprint
source_refs
receipt_refs
cost_usage_status
```

Required stages are:

```text
obligation_generation
experiment_compile
binding_materialization
fixture_setup
governed_execution
observation
assertion
oracle_resolution
delivery_gate
cleanup
formal_projection
```

The funnel is derived only from these records. No counter may be independently
incremented by a second path. For every stage, the system reports input,
success, blocked, failed, and elapsed-time distributions by source, risk family,
operation, actor, adapter, reason code, and round.

Errors are versioned data and remain visible. Missing fields, parse failures,
receipt mismatches, and persistence failures fail fast; they are not converted
to empty success objects.

## 6. Migration Sequence

### Step 1 — Freeze a reproducible champion

- preserve all current user changes;
- produce a clean committed worktree before an eligible benchmark;
- bind run, source, policy, target, fixture, reset, and Git fingerprints;
- require healthy preflight, execution, cleanup, and post-run reset;
- keep current dirty/degraded receipts diagnostic only.

### Step 2 — Add authority and shadow isolation

- add immutable `mainline_authority` to run identity;
- prove only the authoritative path can reach formal projection or submission;
- label all candidate shadow outputs and exclude them from campaign completion;
- forbid exception-triggered fallback.

### Step 3 — Add obligation-attempt terminal accounting

- create one attempt record per selected obligation;
- derive campaign completion from terminal attempt coverage;
- retain behavior-slice IDs only as optional lineage;
- make missing attempts and duplicate terminal receipts hard failures.

### Step 4 — Upgrade Trace Ledger and funnel projection

- emit an obligation-attempt keyed V2 Trace Ledger;
- derive stage loss and funnel counts from receipt joins;
- assert count consistency across Delivery Gate, quality projection, evaluator
  submission, Trace Ledger, and API/UI projection;
- retain an explicit offline V1-to-V2 migration for old diagnostics.

### Step 5 — Extract the coordinator

- move orchestration from `run_v12_pipeline` into the focused coordinator;
- keep the existing entry point as a compatibility wrapper;
- delete legacy execution authority after the experiment candidate proves
  paired non-regression;
- keep legacy generation only behind the Obligation adapter.

### Step 6 — Promote or reject

- run champion replay and candidate replay on identical frozen inputs;
- run corresponding shadow comparisons;
- reject on any quality, safety, cleanup, identity, or pipeline-health
  regression;
- promote by selecting `experiment_candidate` before the next run;
- never switch engines inside a running campaign.

## 7. Phase 1 Acceptance Gates

Phase 1 does not change commercial Gate D, controlled-pilot, or GA thresholds.
It adds an engineering checkpoint subordinate to the existing Goal SSOT.

All of the following are required:

1. Exactly one authoritative scheduler, execution path, terminal-attempt ledger,
   Delivery Gate input, and formal finding projection exists per run.
2. Legacy inputs enter the candidate mainline only through the Obligation
   adapter after cutover.
3. Runtime exceptions cannot activate the legacy path.
4. Every selected obligation has exactly one terminal attempt receipt.
5. Executable experiments contain zero unresolved placeholders.
6. Execution and engine success rates are each at least 95%.
7. Governed write receipt coverage and cleanup success are each 100%.
8. Delivery Gate, formal projection, evaluator submission, Trace Ledger, and
   product API/UI formal IDs and counts agree exactly.
9. Pipeline health is not `DEGRADED`.
10. Hidden GT and evaluator miss labels are absent from runtime inputs, traces,
    prompts, policies, fixtures, and product artifacts.
11. Candidate external TP, Recall, Precision, F1, reproduction, duplicate rate,
    safety, and cleanup do not regress from the eligible clean champion.
12. Stage-local single-variable verification p50 wall time improves by at least
    60% from a frozen measured engineering baseline. Baseline and candidate
    each use five warm executions of the same focused contract/integration
    command set, code/input/environment fingerprints, and timing receipt.
13. Full benchmark wall time and unit cost do not regress; missing cost remains
    `UNKNOWN`, never zero.

The existing benchmark P1 compile-rate target of at least 90% remains required
by the authoritative benchmark P0/P1 design. This Phase 1 architecture
checkpoint does not claim benchmark P1 exit. If the single-mainline checkpoint
passes while compile rate remains below 90%, reporting must state
`single_mainline_phase1=COMPLETE` and `benchmark_p1=INCOMPLETE`; the following
binding-conversion phase must close the remaining gap. The 90% gate is not
weakened or redefined.

## 8. Testing Strategy

Implementation follows TDD at contract seams:

- authority isolation tests prove shadow output cannot enter formal scopes;
- attempt-ledger tests prove one terminal receipt per selected obligation;
- count-consistency tests compare exact finding IDs, not only counts;
- V1-to-V2 migration tests prove explicit deterministic conversion;
- pipeline-health tests fail on missing, duplicate, or mismatched receipts;
- governed-write tests prove one audit receipt per write and reverse cleanup;
- integration tests exercise the real mainline with redacted runtime receipts;
- external quality evidence comes only from actual target execution and the
  evaluator-private scorer.

Constructed contract fixtures may test code behavior but never count as an
executed Bug, TP, Recall, or commercial capability result.

Every edited Python file receives an immediate AST syntax check. Focused tests
run after each change, relevant integration tests run at each migration step,
and the full suite runs before the candidate benchmark.

## 9. Frozen Constraints

Phase 1 must preserve:

- QualiBug frontend port `5174` and backend port `8088`;
- discovery model timeout `>= 300` seconds;
- discovery model `max_tokens >= 32768`;
- `MAX_HYPOTHESES = 15`;
- default reasoner `max_workers = 4`;
- production and unknown-environment write denial;
- one governed audit receipt per actual write;
- hidden-GT isolation and artifact redaction;
- cross-industry, source-grounded behavior with no benchmark endpoint, Bug ID,
  title, keyword, reproduction answer, or customer-specific rule hardcoding.

## 10. Documentation Ownership

- Gate metrics and promotion thresholds remain in
  `docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md`.
- Cross-phase architecture remains in
  `docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md`.
- This document owns only the Phase 1 single-mainline migration design.
- Implementation must update `AGENTS.md` when the runtime authority changes.
- DOCX files remain generated release exports and are never edited as an
  independent specification.
