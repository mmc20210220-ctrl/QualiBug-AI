# Product Quality Audit — 2026-09-03

Status: active validation phase

## Decision

QualiBug is under a product feature freeze for the current validation round.

No new capability layer is justified until the existing Requirement Intelligence and Test Intelligence outputs demonstrate useful, repeatable value on enterprise-shaped materials.

The product boundary under evaluation is:

```text
enterprise materials
        -> Enterprise Understanding
        -> Requirement Intelligence
           -> Conflict / Missing / Ambiguity
           -> Evidence
           -> Requirement Readiness
        -> exact Requirement Finding linkage
        -> Test Intelligence
           -> Test Obligation
           -> structured Test Design
           -> STOP
```

The following are explicitly out of scope for this audit and must not be implemented merely to improve the report:

- Grounding Assessment;
- API binding;
- UI binding;
- test-data materialization;
- Observer binding;
- Oracle runtime binding;
- environment selection;
- safety/runtime approval automation;
- Executable Experiment generation;
- new Bug Discovery runtime capability;
- new Requirement Finding kinds;
- new IR layers or synthetic quality scores.

Allowed engineering work during the freeze is limited to P0 correctness, output-quality fixes demonstrated by audit evidence, and UX defects that block review of existing outputs.

## Audit question

The audit is not asking whether the architecture can be extended.

It asks whether the product that already exists is useful:

1. Does Requirement Intelligence surface business problems a product/engineering/QA owner would actually resolve before development?
2. Does every delivered Finding have evidence that makes the conclusion reviewable?
3. Does Test Intelligence identify things a QA/test owner would actually consider mandatory to verify?
4. Does Test Design save meaningful analysis work without inventing runtime details?
5. What obvious source-backed business rules are missed?
6. What delivered items are duplicate, generic, irrelevant, or misleading?

## Frozen first-round samples

Only three existing repository samples are used in the first round.

### 1. `object_source_conflict`

Purpose: Requirement Intelligence conflict/evidence/readiness behavior.

Frozen sources:

- `benchmark/multi_source_object_conflict/PRD_LEGACY.md`
- `benchmark/multi_source_object_conflict/PRD_CURRENT.md`

The historical PRD declares `CustomerProfile（客户）`; the current approved PRD declares `CustomerAccount（客户）`. The existing enterprise-understanding baseline already requires this source-authority conflict to fail closed until an explicit operator authority decision is recorded.

Audit expectation: Requirement Intelligence should expose the unresolved conflict with both source sides and must not present the project as READY before resolution.

### 2. `benchmark_mall`

Purpose: multi-source Test Obligation and Test Design usefulness.

Frozen sources: 7 documents covering PRD, API, business rules, database schema, roles, test accounts, and historical bugs.

Review anchors intentionally sample high-value semantics rather than attempting an exhaustive test-case count:

- payment requires `PENDING_PAYMENT`;
- payment success moves the order to `PAID`;
- paid orders cannot be directly cancelled;
- cancellation releases inventory;
- refund approval belongs to finance/admin;
- buyers may operate only their own business data;
- cancellation restores `locked_qty` to `available_qty`.

### 3. `warehouse_e`

Purpose: richer enterprise rules with explicit business-rule IDs.

Frozen sources: business rules, data dictionary, test accounts, and OpenAPI.

Review anchors sample:

- role authorization;
- warehouse scope/ownership;
- allowed and forbidden state transitions;
- inventory side effects;
- compensation rules;
- idempotency;
- atomic bulk rollback.

The last two are deliberately retained even if the current product misses them. A miss is an audit result, not an invitation to immediately add another inference engine.

## Evaluation independence

The audit must not use product output as Ground Truth.

`benchmark_evaluator/product_quality/current_product_audit.py` runs in two phases:

1. Copy frozen source files into an isolated temporary product workspace, run the existing production ingestion/understanding/Requirement/Test Intelligence stack, and persist product outputs.
2. Only after all selected product captures complete, load `fixtures/review_anchors.json` and generate human review worksheets.

The review anchor file declares:

```text
ground_truth_generated_from_product_output = false
```

Exact source quotes are used only to narrow which captured output rows a human should inspect. Quote matching does not assign semantic correctness, usefulness, precision, recall, or quality scores.

## Human verdicts

Each review anchor starts as:

```text
PENDING_REVIEW
```

The human reviewer should classify the relevant product output using one of these audit labels:

- `USEFUL` — materially saves review/test-analysis effort and is source-correct;
- `TOO_GENERIC` — directionally related but not actionable enough to save work;
- `NOISY` — irrelevant, redundant, misleading, or not worth surfacing;
- `MISSED` — the source-backed review anchor has no adequate product output;
- `UNSUPPORTED_CLAIM` — output goes beyond source evidence;
- `NEEDS_SOURCE_CLARIFICATION` — source itself is insufficient/ambiguous and the product correctly refuses to invent truth.

Do not convert these labels into a synthetic percentage until the review universe and denominator are intentionally frozen.

## Running the capture

From repository root in a working environment with the project dependencies installed:

```bash
python -m benchmark_evaluator.product_quality.current_product_audit --root .
```

Optional sample selection:

```bash
python -m benchmark_evaluator.product_quality.current_product_audit \
  --root . \
  --sample object_source_conflict \
  --sample benchmark_mall \
  --sample warehouse_e
```

Default output:

```text
evaluator_outputs/product_quality/current/
  audit_summary.json
  object_source_conflict/
    requirement_analysis.json
    test_intelligence_analysis.json
    review_worksheet.json
  benchmark_mall/
    ...
  warehouse_e/
    ...
```

## Current execution status

As of 2026-09-03, this audit harness has been added against the current product boundary, but the full repository run is not being claimed as measured in this document.

The connected GitHub Actions environment has repeatedly failed before allocating a runner (`steps=[]`), and the isolated assistant container cannot resolve `github.com` to clone the complete repository. Therefore no product-quality numbers are recorded here until the production chain actually runs.

This is intentional: an unavailable execution environment is `NOT_MEASURED`, not a reason to manufacture a score from static inspection.

## Stop rule

After a successful capture and human review, the next decision is binary:

- If current Findings / Obligations / Designs are repeatedly useful, focus on packaging, workflow, customer trials, and only audit-backed quality fixes.
- If they are mostly generic, noisy, or miss obvious source-backed rules, stop architecture expansion and fix only the demonstrated intelligence-quality bottlenecks.

No Grounding or Runtime work should begin merely because it is the next box in an architecture diagram.
