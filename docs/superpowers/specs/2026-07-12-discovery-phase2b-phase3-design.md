# QualiBug Discovery Phase 2B and Phase 3 Design

**Status:** Approved by the user's instruction to finish the remaining
architecture gaps and continue into Phase 3 on 2026-07-12.
**Parent architecture:**
`docs/AUTONOMOUS_BUG_DISCOVERY_CAPABILITY_BREAKTHROUGH_SPEC.md`
**Metric and promotion SSOT:** `docs/DISCOVERY_HARNESS_EVOLUTION_GOAL.md`

## 1. Outcome

This migration closes the remaining Phase 2 execution gaps before activating
Phase 3 formal Oracle and deduplication authority. The only product discovery
flow remains:

```text
Visible source facts
  -> Behavior IR
  -> Test Obligations
  -> Executable Experiments
  -> Governed Execution
  -> Typed Observation
  -> Tri-state Assertions
  -> Contract Oracle
  -> Delivery Gate
  -> Canonical Defect Registry
  -> Quality Projection
```

The migration must improve epistemic correctness before increasing execution
volume. A response that does not prove the asserted business fact is
`INDETERMINATE`, never a defect. A missing capability is `BLOCKED`, never a
nominally successful observer.

## 2. Root Causes Being Closed

### 2.1 Source semantics stop before Behavior IR

The knowledge asset already contains typed `rule_to_interface` relationships,
but Behavior IR currently derives invariant-operation relations only from an
invariant's embedded `operation_refs`. The production planner therefore loses
most conservation, state, validation, idempotency, and concurrency obligations
before compilation.

**Decision:** Behavior IR consumes typed knowledge relationships as a first
class input. Exact source edges produce exact IR relations. Ambiguous,
conflicting, missing, or dangling edges produce typed coverage gaps. Path or
name similarity must not guess a business relation.

### 2.2 Permission absence is treated as a denial

The current permission derivation collapses `permit`, `deny`, and `unknown`
into a Boolean. A method that is not recognized by an allow-list is treated as
an explicit deny. This generates unsupported authorization obligations.

**Decision:** permission knowledge is three-state:

```text
PERMIT  = an explicit source fact permits the operation
DENY    = an explicit source fact denies the operation
UNKNOWN = no source fact proves either outcome
```

Only explicit `DENY` relations may generate negative authorization
obligations. `UNKNOWN` remains visible as a coverage gap. Generic source
actions such as `manage` are normalized through a documented, industry-neutral
action lattice rather than target-specific endpoint names.

### 2.3 HTTP success is mistaken for business evidence

The current executor marks all declared observers satisfied after any HTTP
response and treats a treatment 2xx as protected-resource visibility. Empty
objects, empty arrays, HEAD responses, 204 responses, login pages, and unrelated
success payloads can therefore become formal authorization defects.

**Decision:** every observer emits a typed receipt with an explicit evidence
state:

```text
OBSERVED | INDETERMINATE | FAILED | UNSUPPORTED
```

Authorization requires a proven control resource identity and treatment
evidence for that same resource identity or a source-grounded protected
business effect. Status alone is insufficient. Empty/equivalent payloads are
`INDETERMINATE`. Unsupported surfaces block compilation or execution with
`BLOCKED_MISSING_OBSERVER` or `BLOCKED_UNSUPPORTED_ADAPTER`.

### 2.4 Experiments do not yet encode family semantics

The current compiler emits nearly identical control/treatment request pairs
for every risk family. Idempotency lacks a repeated-write/effect observation;
concurrency lacks a barrier/final invariant; conservation lacks typed before
and after values; state lacks a proven precondition.

**Decision:** each supported family has a protocol contract declaring required
steps, fixtures, observers, activation requirements, and evidence sufficiency.
A family is either compiled faithfully or blocked. It is never downgraded to a
single endpoint/status probe.

Initial truthful support is delivered in this order:

1. HTTP authorization and isolation with same-resource comparison;
2. HTTP validation with a source-derived valid control and one-dimensional
   mutation;
3. idempotency with governed repeated writes and a source-grounded effect
   observer;
4. state and conservation with explicit before/after read observations;
5. concurrency only when a real barrier and final-state observer are available.

### 2.5 Oracle and gate accept missing evidence as assertion failure

The current Boolean assertion model cannot distinguish a proven violation from
unobservable evidence. The generic Oracle can then turn an evidence gap into a
customer candidate, while the Delivery Gate mostly validates self-declared
status fields.

**Decision:** assertion results are tri-state:

```text
PASS | VIOLATION | INDETERMINATE
```

Only `VIOLATION` with all activation requirements satisfied may enter the
Contract Oracle. `INDETERMINATE`, observer failure, fixture failure, actor
failure, and cleanup uncertainty are non-defect terminal outcomes. The
Delivery Gate independently verifies the typed receipt chain and cannot rely
on executor-authored `validated` or `reproduced` flags.

### 2.6 Formal deduplication lacks an outcome-semantic identity

Title or text similarity is not a stable defect identity and can both merge
different defects and multiply the same root cause.

**Decision:** the canonical signature is content-addressed from:

```text
target identity
+ normalized operation identity
+ property/invariant identity
+ actor relation
+ resource identity class
+ observed outcome signature
```

Multiple reproductions attach evidence receipts to one canonical defect.
Different mutations or materially different business outcomes remain separate.

## 3. One-Mainline Module Strategy

The roughly 418 Python modules and 233K lines are not reduced through a bulk
rewrite or an arbitrary line-count target. They are reduced with an in-place
strangler and evidence-backed deletion:

1. Declare the supported product roots and architectural owners.
2. Route every product entry point to the single mainline coordinator.
3. Keep legacy capability behind explicit adapters while it remains the
   externally measured champion.
4. Record runtime module/import traces for product, evaluation, and focused
   tests.
5. Classify modules as `core`, `adapter`, `compatibility`, `diagnostic`, or
   `retirement_candidate` with an owner and removal gate.
6. Delete a retirement candidate only after static reachability, dynamic trace,
   public-entrypoint, plugin/dynamic-import, and regression checks agree.
7. Split oversized modules only at existing responsibility boundaries; never
   clone logic into a second implementation.

The first concrete reductions are the dead candidate vertical slice inside the
legacy V12 domain, the post-projection scan-result repair monkeypatch, and the
independent public `qualibug discover` product path. Broad deletion of the
legacy champion is forbidden until paired external evaluation permits
promotion.

## 4. Component Boundaries

### 4.1 Behavior IR normalization

`behavior_ir.py` owns conversion from source graph facts to typed IR nodes,
relations, conflicts, and coverage gaps. `obligation_compiler.py` consumes only
IR. Neither module may perform HTTP requests, infer hidden evaluator facts, or
guess relations from benchmark/customer names.

### 4.2 Observer registry

A focused observer registry owns capability discovery, activation validation,
typed receipt schemas, and evidence sufficiency. `experiment_compiler.py`
checks declared capabilities. `experiment_executor.py` performs governed steps
and delegates observations; it does not synthesize observer success booleans.

HTTP, UI, DB, log, event, and job observers remain distinct. A configured but
unverified adapter is unavailable.

### 4.3 Assertion and Oracle authority

`assertion_dsl.py` evaluates typed receipts and emits tri-state results.
`contract_oracles.py` owns source-contract resolution only. Protocol/harness
verification occurs before business Oracle resolution. `customer_delivery_gate.py`
is the sole `DELIVERABLE` authority.

### 4.4 Canonical defect registry

One registry computes canonical signatures and joins reproductions. Product
quality projection and evaluator submissions consume registry output; no UI,
service, patch, or CLI independently recomputes formal defects.

## 5. Operational and Evaluation Truth

Known request, execution, safety, and cleanup metrics remain measurable even
when provider cost is unknown. Missing cost continues to block promotion but
does not erase independently observed metrics. Cleanup counts come only from
accepted-write audit receipts and terminal compensation receipts, not recursive
JSON traversal.

Candidate shadow output has one evaluator-only projection. It never enters
product formal scope. External feedback returned to planning is aggregate,
pattern-level, policy-scoped yield and cost data; hidden ground-truth instances,
Bug IDs, match labels, endpoint answers, and keywords never enter runtime.

## 6. Failure Semantics

- Missing or ambiguous source relation: coverage gap.
- Missing actor, fixture, binding, observer, adapter, or reversible cleanup:
  typed `BLOCKED` terminal attempt.
- Invalid response parsing or adapter failure: harness failure with trace.
- Assertion evidence unavailable: `INDETERMINATE`, not violation.
- Oracle exception: harness failure, not defect.
- Persistence or projection mismatch: fail the run; never repair lists after
  formal projection.
- Unknown or production environment: writes denied before the request.

## 7. Phase 2B Acceptance

1. Exact source `rule_to_interface` edges survive into Behavior IR and
   obligations; ambiguous edges are gaps.
2. Permission relations distinguish permit, deny, and unknown; only explicit
   deny generates a negative authorization obligation.
3. Empty/equivalent HTTP 2xx control/treatment evidence cannot emit a finding.
4. Every satisfied observer requirement has a typed receipt; nominal Boolean
   observer success no longer exists.
5. Each supported family compiles its required protocol; unsupported semantics
   block visibly.
6. Fixture ownership/state claims are backed by setup and read receipts.
7. Operational request/safety/cleanup metrics are derived from actual receipts.
8. Dead/alternate product execution paths listed in Section 3 no longer hold
   runtime or formal authority.
9. Focused and full tests pass, configuration floors remain intact, and no
   hidden GT enters runtime.

## 8. Phase 3 Acceptance

1. Assertions emit `PASS`, `VIOLATION`, or `INDETERMINATE` with expected,
   actual, observer receipt IDs, and source refs.
2. Contract Oracle activation fails closed when any required control, fixture,
   observer, or cleanup receipt is absent.
3. Harness failures and indeterminate evidence cannot enter customer delivery.
4. The Delivery Gate independently validates the complete receipt chain.
5. Canonical defect signatures and evidence aggregation are deterministic and
   shared by product projection and evaluator submission.
6. Repeated evidence for one root cause is deduplicated; materially different
   outcomes remain distinct.
7. An actual clean champion/candidate external 131-Bug evaluation determines
   promotion. Internal counts never substitute for TP, Recall, or Precision.

## 9. Frozen Constraints

- Frontend port `5174`; backend port `8088`.
- Discovery timeout `>= 300` seconds and `max_tokens >= 32768`.
- `MAX_HYPOTHESES = 15`; default `max_workers = 4`.
- All reusable behavior is industry-neutral and source-grounded.
- Every non-production write is governed and auditable; production and unknown
  environments fail closed.
- Hidden GT and evaluator-private labels remain isolated.
- Current unrelated worktree changes and artifacts are preserved.

