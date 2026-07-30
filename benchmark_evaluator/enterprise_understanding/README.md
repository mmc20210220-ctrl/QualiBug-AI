# Enterprise Understanding Benchmark

This package is part of the existing `benchmark_evaluator` boundary. It measures the product's
persisted `enterprise_understanding_model` against evaluator-side, human-authored Ground Truth.

Ground Truth must never be copied into `ai_test_asset_center`, imported by product runtime, or used
to repair the product model during a benchmark run.

## One authority

```text
product sources
  -> existing product enterprise-understanding mainline
  -> persisted final enterprise knowledge asset
  -> immutable evaluator asset snapshot
  -> evaluator-only deterministic alignment
  -> recall / safety / root-cause receipts
```

The Benchmark creates no second business model and writes nothing back to the product asset.

## Capture one finalized product asset

First build the project through the existing product composition root. The evaluator does not own
or repeat that build.

Then capture the already-persisted final asset:

```bash
python -m benchmark_evaluator.enterprise_understanding.capture_product_asset \
  --project <project_id> \
  --root <qualibug-product-root> \
  --output <immutable-product-asset.json>
```

The capture command calls only:

```text
ai_test_asset_center.enterprise_knowledge_center.composition
  .load_enterprise_business_knowledge_asset
```

It never calls the builder, never loads Ground Truth and never enriches or rewrites the persisted
asset. Missing finalized assets block the capture instead of triggering an implicit rebuild.

## Run the evaluator

```bash
python -m benchmark_evaluator.enterprise_understanding \
  --project <project_id> \
  --ground-truth <evaluator-ground-truth.json> \
  --asset <immutable-product-asset.json> \
  --output <evaluator-output-directory>
```

Both Ground Truth and product asset are fingerprinted. The workflow receipt proves that hidden
Ground Truth did not enter product runtime.

## First source-backed baseline: TicketSLA

The first committed evaluator annotation set is:

```text
benchmark_evaluator/enterprise_understanding/fixtures/ticketsla_d/ground_truth.json
```

Its authority is limited to:

```text
projects/ticketsla_d/input/BUSINESS_RULES.md
projects/ticketsla_d/input/openapi.yaml
_private_eval/_evaluator_private/benchmark_ticketsla_d/ground_truth.json
```

The first annotation batch contains:

```text
10 business objects
4 actors
12 core operations
7 object relations
6 Ticket state transitions
25 explicit Business Behaviors
15 Bug-to-required-Behavior dependency mappings
```

It deliberately declares:

```text
scope_complete = false
```

Therefore it can rank real misses and calculate recall for the annotated scope, but it cannot
claim full TicketSLA business coverage or emit a false-confirmation rate. Cross-object propagation,
complex processes, source conflicts, Expected Unknowns and implicit rules remain future human
annotation work, not automatically generated truth.

Example:

```bash
python -m benchmark_evaluator.enterprise_understanding.capture_product_asset \
  --project ticketsla_d \
  --root . \
  --output evaluator_outputs/ticketsla_d/final_asset.json

python -m benchmark_evaluator.enterprise_understanding \
  --project ticketsla_d \
  --ground-truth benchmark_evaluator/enterprise_understanding/fixtures/ticketsla_d/ground_truth.json \
  --asset evaluator_outputs/ticketsla_d/final_asset.json \
  --output evaluator_outputs/ticketsla_d/understanding
```

## Ground Truth collections

```text
business_objects
actors
operations
object_relations
lifecycles
state_transitions
business_rules
business_behaviors
conflicts
expected_unknowns
bug_dependencies
```

Confirmed annotations require source evidence. `minimum_profile` shortfalls produce
`BENCHMARK_GROUND_TRUTH_INCOMPLETE`; the evaluator never fills missing annotations.

## Alignment authority

Only deterministic normalized names, explicit aliases, model identities and explicit structure
slots can confirm a match. Fuzzy matching, embeddings or LLM output may propose a review candidate
but cannot confirm Benchmark truth.

Operation recall requires the correct business object. Behavior recall evaluates actor, operation,
object, conditions, permission and effects. A candidate/incomplete Behavior is not an exact match.

## Metrics

```text
business-object recall
actor recall
operation recall
operation-object binding accuracy
business-rule recall
Business Behavior recall
state-transition recall
conflict exposure
Expected Unknown exposure
unexpected Unknown count
Behavior slot accuracy
source-evidence accuracy
P0/P1 weighted recall
Bug dependency rule coverage
false-confirmation rate
```

False-confirmation rate is emitted only when Ground Truth validation passes and
`scope_complete=true`.

## Earliest root causes

```text
SOURCE_NOT_PARSED
FACT_NOT_EXTRACTED
OBJECT_NOT_RESOLVED
ACTOR_NOT_RESOLVED
OPERATION_NOT_RESOLVED
CONDITION_NOT_PARSED
STATE_TRANSITION_MISSING
RULE_NOT_COMPILED
BEHAVIOR_NOT_CONFIRMED
```

The next repair target is the highest P0/P1-weighted earliest breakpoint. Repairs must modify the
existing product mainline module where the first loss occurs; downstream result fabrication is
forbidden.

## Outputs

```text
workflow_receipt.json
ground_truth_summary.json
understanding_alignment.json
metric_summary.json
missed_objects.json
missed_operations.json
missed_rules.json
false_confirmations.json
unknown_analysis.json
conflict_analysis.json
bug_dependency_analysis.json
root_cause_distribution.json
report.md
```
