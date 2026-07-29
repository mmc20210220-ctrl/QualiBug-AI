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
- `processes`: source-backed lifecycle sequences. Unique linear chains remain
  `LIFECYCLE_UNIQUE_CHAIN`. Explicit multi-outcome conditions or distinct
  operations project `LIFECYCLE_CONDITIONAL` / nonlinear types; underdetermined
  branches stay `LIFECYCLE_PARTIAL` with unknowns. Unique object-relation chains
  project `MULTI_OBJECT_LINKED`; source-backed joins / message waits / timed waits /
  cross-system markers project `MULTI_OBJECT_ORCHESTRATION`. Document order is
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
- nested `若…且…否则…` / `除…外` / exception-overlay framing with explicit
  `condition_frame` combinators (THEN/ELSE/ELSE_IF branches, nested EXCEPT overlays,
  and chained `除外` scopes); underdetermined nesting stays `UNRESOLVED` with a
  visible unknown instead of silent drop or default `AND`; frames project through
  Behavior IR / rule library / operations without silent slot loss;
- decision-matrix row slot completeness into Behavior IR (actor / object / operation /
  condition / permission / effect); empty cells stay incomplete, never wildcard `any`;
- cross-document object identity merge only through ACCEPTED `TERM_ALIAS`
  evidence, including source-backed synonym markers (又称/也称/又名/简称/即/
  等同于/是指/aka…) and glossary/definition tables (conflicting alias mappings
  fail closed);
- section-scoped Chinese coreference / omitted-actor resolution (unique same-section
  context or explicit heading; TERM_ALIAS-aware uniqueness; fail-closed when ambiguous);
- source-backed structured quantity / time-window / formula / authorization-delegation
  fields when the original statement states them;
- source-backed temporal trigger conditions, postconditions, data effects, and
  compensations projected into facts / operations / behaviors (no silent drop);
- ONLY_IF / MUST modalities mapped to explicit permission decisions instead of
  false `UNSPECIFIED` when the source is unambiguous;
- conflict `authority_decision` marked `UNRESOLVED` (no automatic authority pick);
  opposing conflict fact spans project into standard `evidence` / `message` /
  `operator_action` fields and into command-center / settings receipts so
  UNRESOLVED conflicts remain visible with quotes (display only — never auto-pick);
- understanding-model `rules` retain source-backed structured slots
  (`condition_frame`, postconditions, data effects, compensations, quantity /
  time-window / formula constraints, authorization delegation, exception scope)
  instead of thin shells that silently drop them;
- EXCEPTION_SCOPE promotion only when the source uniquely names the exception actor;
- source-backed non-linear process projection: conditional multi-outcome branches,
  parallel groups (only with explicit parallel markers), lifecycle loops, and
  withdraw / return / compensation exception paths; underdetermined branches emit
  `LIFECYCLE_PARTIAL` + visible unknowns instead of silent omission;
- unique source-backed multi-object process linking along GENERATES / CREATES /
  COMPENSATES / DEPENDS_ON / NOTIFIES / AWAITS / TRIGGERS / AFFECTS relation edges
  (branching relation graphs stay unresolved unless the source states an explicit
  join or parallel marker);
- source-backed multi-object orchestration projection: message/async waits,
  timed waits from temporal / time-window evidence, explicit joins, and
  cross-system markers; underdetermined choreography emits PARTIAL + visible
  unknowns instead of inventing order from document appearance;
- full Chinese document semantic tree at enterprise scale from Document IR
  headings / lists / tables / continued-table groups when present (text-heading
  fallback only when IR body blocks are absent); structure-preserving
  span → fact attachment with fail-closed ambiguous/unattached receipts;
  same-section priors cannot join across heading spans from document order;
  span attachment projects into rule library / understanding-model rules without
  silent drop of document_block_id.

Not yet complete:

- cross-document coreference beyond broader source-backed TERM_ALIAS /
  synonym / glossary-table identity merge (proximity / filename / order remain
  forbidden);
- operator authority/version *decision workflow* that can change conflict
  `authority_decision` from UNRESOLVED to an explicit operator-selected authority
  (product surfaces already **display** UNRESOLVED conflicts with opposing
  evidence and forbid auto-pick; they still cannot close a conflict without an
  explicit authority/version decision);
- externally labeled Chinese enterprise understanding benchmark and measured
  precision/recall.

Until those are implemented and measured, the product must not claim that it can
completely understand every Chinese enterprise document.
