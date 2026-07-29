# Enterprise Business Understanding Model

## Purpose

QualiBug must first understand Chinese enterprise materials before any later
behavior modeling or validation work. Parsing files, extracting tables, or naming
entities is not sufficient. The formal product output is
`qualibug.enterprise-business-understanding-model.v1`.

This stage does not discover bugs and does not create executable tests.

## Mainline position

```text
Chinese enterprise materials
  -> document coverage ledger
  -> source-backed business fact ledger
  -> unresolved fact conflict reconciliation
  -> Enterprise Business Understanding Model
  -> understanding closure gate
  -> later Behavior IR / validation consumers
```

The model is installed on the existing enterprise knowledge build authority. It
is not a parallel pipeline.

## Fact authority

- Original Chinese source text is the formal fact authority.
- Translation may be used only as an explanatory view; it cannot promote facts.
- Every formal object, actor, operation, relation, lifecycle, or process requires
  source evidence or an existing source-backed asset reference.
- Token similarity, filename order, document order, model confidence, and generic
  industry assumptions cannot create formal business meaning.

## Model spaces

The model contains:

- `business_objects`: canonical enterprise objects, aliases, identity fields,
  attributes, operations, lifecycles, and relation references.
- `actors`: roles, responsibilities, permissions, restrictions, and evidence.
- `operations`: actor/object bindings, preconditions, effects, exceptions,
  temporal constraints, scopes, and modality contracts.
- `object_relations`: explicit source-backed `GENERATES`, `CONSUMES`,
  `DEPENDS_ON`, `BELONGS_TO`, `REFERENCES`, `AFFECTS`, `COMPENSATES`, and
  related relations.
- `lifecycles`: allowed/forbidden transitions, states, events, conditions, and
  graph completeness.
- `processes`: only a uniquely provable source-backed sequence. Document order is
  never treated as process order.
- `rules`: accepted Chinese fact projections.
- `unknowns`: unresolved subjects, objects, operations, relation endpoints,
  lifecycle states, disconnected lifecycle fragments, and missing business
  behavior.
- `conflicts`: unresolved source contradictions. They are never automatically
  resolved by recency, filename, order, or confidence.
- `evidence_index`: the reverse trace to original source spans.

## Fail-closed closure gate

The gate is `qualibug.enterprise-understanding-model-gate.v1`.

`PASS` requires:

1. The upstream Chinese comprehension gate passes.
2. All formal model entries have evidence.
3. No unresolved business fact conflict remains.
4. No critical unknown remains.
5. No non-critical unknown remains; otherwise the result is `PARTIAL`.
6. An active enterprise source set cannot pass with only fields, tables, or entity
   names and no accepted business behavior facts.

The internal `model_completeness_projection` is a closure diagnostic only. It is
not recall, accuracy, business-understanding quality, or a commercial claim.

## Current implemented boundary

Implemented in this phase:

- canonical schema and evidence contract;
- fact-to-object, actor, operation, and rule aggregation;
- source-backed object graph;
- lifecycle construction and lifecycle unknowns;
- unique-chain lifecycle process projection;
- conflict and unknown propagation;
- minimum business behavior closure;
- integration before downstream semantic binding;
- multi-condition combinator honesty (`AND`/`OR` only from explicit source wording;
  otherwise `UNRESOLVED`, never default `AND`);
- cross-document object identity merge only through ACCEPTED `TERM_ALIAS`
  evidence (conflicting alias mappings fail closed);
- section-scoped Chinese coreference / omitted-actor resolution (unique same-section
  context or explicit heading; TERM_ALIAS-aware uniqueness; fail-closed when ambiguous);
- source-backed structured quantity / time-window / formula / authorization-delegation
  fields when the original statement states them;
- conflict `authority_decision` marked `UNRESOLVED` (no automatic authority pick);
- EXCEPTION_SCOPE promotion only when the source uniquely names the exception actor.

Not yet complete:

- full Chinese document semantic tree at enterprise scale;
- cross-document coreference beyond source-backed TERM_ALIAS identity merge
  (proximity / filename / order remain forbidden);
- conditional branches, parallel branches, loops, compensation processes, and
  long-running multi-object process reconstruction (builder still projects only
  unique linear lifecycle chains);
- operator authority/version *workflow UI* for resolving conflicts (receipts stay
  UNRESOLVED until an explicit operator decision exists);
- externally labeled Chinese enterprise understanding benchmark and measured
  precision/recall.

Until those are implemented and measured, the product must not claim that it can
completely understand every Chinese enterprise document.
