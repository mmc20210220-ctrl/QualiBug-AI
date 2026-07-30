# QualiBug Enterprise Understanding Benchmark v1

## Purpose

This Benchmark measures how much of a human-authored, source-backed enterprise Ground Truth is
present in the existing QualiBug enterprise-understanding asset.

It does **not** build a second enterprise model. It does not repair or confirm business facts. It
does not write benchmark alignments back into:

```text
business_facts
business_objects
actors
operations
object_relations
lifecycles
processes
rules
business_behaviors
unknowns
conflicts
```

The one existing enterprise-understanding model remains the only business authority.

## Mainline contract

```text
enterprise source material
  -> existing document_structure_assets
  -> existing governed business facts
  -> existing enterprise_understanding_model
  -> Benchmark read-only alignment
  -> recall / safety / root-cause receipts
```

Benchmark output is a measurement receipt, never an input to formal business understanding.

## Ground Truth authority

Ground Truth must be authored or confirmed by people with source evidence. It must not be generated
from the current model output.

Schema:

```text
qualibug.enterprise-understanding-ground-truth.v1
```

Supported collections:

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

Every confirmed annotation requires:

```text
ground_truth_id
criticality
annotation_status = CONFIRMED
source_refs or source_locators
```

Entity annotations also require a canonical name. Rules and Behaviors require an operation and at
least one object. Bug dependencies require a `bug_id` and explicit `required_ground_truth_ids`.

Ground Truth may declare a `minimum_profile`. Shortfalls produce:

```text
BENCHMARK_GROUND_TRUTH_INCOMPLETE
```

The Benchmark must not fill missing annotations automatically.

## Deterministic alignment

Formal alignment authority is limited to deterministic exact normalized names, explicit aliases,
model identities and source-backed structure slots.

Alignment states:

```text
EXACT_MATCH
PARTIAL_MATCH
MISSING
WRONG_BINDING
CONFLICTED
UNKNOWN_CORRECTLY_EXPOSED
UNKNOWN_SHOULD_HAVE_BEEN_RESOLVED
```

Embedding, fuzzy matching or an LLM may later propose candidates for human review, but may not
confirm a Benchmark match.

Operation alignment requires both the operation identity and its business object. The same action
name bound to a different object is `WRONG_BINDING`, not recall.

Behavior alignment evaluates:

```text
actor
operation
object
preconditions
permission decision
state effects
data effects
exceptions
compensations
```

A candidate or incomplete Behavior is not credited as an exact confirmed Behavior.

## Core metrics

The Benchmark reports:

```text
business object recall
actor recall
operation recall
operation-object binding accuracy
business rule recall
Business Behavior recall
state-transition recall
conflict exposure rate
Expected Unknown exposure rate
Behavior slot accuracy
source-evidence accuracy
P0/P1 critical-rule weighted recall
Bug dependency rule coverage
false-confirmation risk
```

Weights:

```text
P0 = 5
P1 = 3
P2 = 1
P3 = 0.5
```

False-confirmation rate is measurable only when:

```text
Ground Truth validation = PASS
scope_complete = true
```

Otherwise the metric is explicitly:

```text
NOT_MEASURABLE_INCOMPLETE_GROUND_TRUTH_SCOPE
```

An unannotated CONFIRMED model entry cannot be called false merely because the Ground Truth scope is
incomplete.

## Earliest root-cause analysis

Each miss is assigned to the earliest visible break in the existing mainline:

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
IMPLEMENTATION_NOT_BOUND
SCENARIO_NOT_GENERATED
ORACLE_NOT_AVAILABLE
EXECUTION_NOT_REACHED
```

Current enterprise-understanding work should focus on the first nine categories. Root causes are
ranked by P0/P1-weighted impact.

Repair rule:

> Fix the earliest existing mainline module. Never create the expected result in a downstream
> Behavior, Scenario or execution layer to hide an upstream understanding failure.

Examples:

```text
SOURCE_NOT_PARSED       -> repair the existing structure/source parser
FACT_NOT_EXTRACTED      -> repair the existing governed fact ledger
OBJECT_NOT_RESOLVED     -> repair existing object identity resolution
OPERATION_NOT_RESOLVED  -> repair existing operation identity and object binding
CONDITION_NOT_PARSED    -> repair existing fact / Behavior condition representation
STATE_TRANSITION_MISSING-> repair existing lifecycle transition construction
RULE_NOT_COMPILED       -> repair existing rule-to-Behavior compilation
BEHAVIOR_NOT_CONFIRMED  -> repair existing evidence / conflict / completeness governance
```

## Bug dependency analysis

Each known Bug annotation explicitly lists the Ground Truth objects, operations, rules, Behaviors or
state transitions needed to expose it.

The Benchmark reports:

```text
which dependencies are understood
which dependencies are missing
coverage per Bug
earliest understanding root cause per Bug
```

A Bug is considered understanding-ready only when every required dependency is an exact match.

## CLI

```bash
python -m ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding_benchmark \
  --project <project_id> \
  --ground-truth <ground_truth.json> \
  --asset <enterprise_asset.json> \
  --output <result_directory>
```

`--asset` is optional. When omitted, the CLI loads the existing persisted enterprise knowledge
asset for the project.

The Ground Truth `project_id` must equal `--project`; the Benchmark cannot silently switch scope.

## Outputs

```text
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

## Prohibited behavior

The Benchmark must never:

- generate Ground Truth from the current model;
- modify Ground Truth to improve scores;
- write alignments back to the enterprise model;
- add a second fact, object, process or Behavior authority;
- use fuzzy or LLM similarity as final truth;
- count an action with the wrong object as recall;
- claim false-confirmation rate with incomplete Ground Truth scope;
- lower `CONFIRMED` evidence requirements to improve recall;
- recommend a downstream patch when an earlier root cause is visible.
