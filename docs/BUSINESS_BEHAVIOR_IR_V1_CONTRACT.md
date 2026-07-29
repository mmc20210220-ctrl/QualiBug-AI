# QualiBug Business Behavior IR v1 Contract

## Purpose

Business Behavior IR connects enterprise knowledge components into source-backed behavior units:

```text
actor
  + operation
  + business object
  + trigger and preconditions
  + expected permission or effect
  + state/data effects
  + exceptions and compensation
  + source evidence
```

This layer remains inside enterprise understanding. It does not create test cases, execute a
system, report a bug, or infer industry policy.

## Inputs

The compiler consumes only governed inputs:

- accepted Chinese business facts;
- formal business objects and object-bound operations;
- decision-matrix candidates recovered from Document IR;
- exact table rows, cells, header paths and explicit legends;
- source locators and evidence spans.

A filename, folder path, row order, column position, visual color or model guess is not business
fact authority.

## Decision Matrix Row Ledger

Every candidate matrix row is first projected into a row ledger containing:

- source document, page, table and row identity;
- condition slots;
- result slots;
- actor candidates backed by explicit role headers;
- empty or unresolved slots;
- exact cell evidence;
- candidate status.

A matrix row is never a formal rule by itself.

## Condition normalization

Version 1 recognizes source-explicit operators:

```text
EQUALS
NOT_EQUALS
GREATER_THAN
GREATER_THAN_OR_EQUAL
LESS_THAN
LESS_THAN_OR_EQUAL
```

Chinese numeric scales such as `千`, `万` and `亿` may be normalized while preserving the raw
cell value and unit. No fuzzy field binding is performed.

An empty condition cell produces:

```text
EMPTY_CELL_SEMANTICS_UNRESOLVED
```

It is not automatically treated as wildcard, inherited value, not applicable or missing data.

## Permission precedence

Permission candidates use deterministic precedence:

1. deny or prohibition;
2. approval requirement;
3. confirmation requirement;
4. allow or permit;
5. unspecified.

Therefore `不允许发货` is `DENY`, not a conflict caused by the substring `允许`.

## Behavior status

### CONFIRMED

A behavior can be confirmed only when it comes from an accepted source fact, has an explicit
operation, object binding and evidence, and has no unresolved semantics or conflicts.

### CANDIDATE

A complete decision-matrix row remains a candidate until corroborated by accepted source facts or
explicit governance. Candidate behaviors cannot be executed as formal validation rules.

### INCOMPLETE

A behavior is incomplete when required meaning remains unresolved, including:

- missing operation;
- missing object binding;
- empty condition or result semantics;
- missing evidence;
- unresolved condition combinator.

### CONFLICTED

A behavior is conflicted when source-backed alternatives cannot coexist safely, including:

- the same behavior family and same condition signature allow and deny the same operation;
- an explicit `AND` requires one field to equal incompatible values;
- one result row contains incompatible permission decisions.

No automatic conflict resolution is allowed.

## Multiple conditions

Multiple condition slots are not implicitly `AND`.

- one condition uses `SINGLE_CONDITION`;
- explicit source `AND` or `OR` is preserved;
- otherwise the behavior receives `BEHAVIOR_CONDITION_COMBINATOR_UNRESOLVED` and remains
  incomplete.

Different equality values are considered contradictory only under explicit `AND`. Under explicit
`OR` they are valid alternatives. With no explicit combinator, no contradiction is asserted.

## Cross-source corroboration

Exact behavior identities may merge evidence across:

- accepted prose rules;
- state-transition facts;
- decision-matrix rows.

The merge key includes operation, object refs, actor refs, permission decision and canonical
condition signature. Different condition signatures remain separate behavior variants.

A candidate can inherit confirmed status only when an accepted fact matches the exact governed
behavior identity. The original source refs and evidence remain attached.

## Gate

The Behavior IR gate reports:

- row-ledger count;
- total behavior count;
- confirmed, candidate, incomplete and conflicted counts;
- conflict count;
- unresolved condition-combinator count;
- source traceability rate.

Gate statuses:

```text
PASS
PARTIAL_BUSINESS_BEHAVIOR_IR
BLOCKED_BUSINESS_BEHAVIOR_CONFLICT
NO_BUSINESS_BEHAVIOR_EVIDENCE
```

The quality claim is internal closure only. It is not recall, precision or business-understanding
accuracy.

## Current limitations

Version 1 does not yet claim complete support for:

- explicit row-span inheritance across matrix rows;
- blank-cell wildcard semantics;
- default rows and hit policies;
- rule priority or salience;
- formula-derived conditions;
- visual color sample binding;
- temporal windows and calendar semantics;
- nested Boolean expressions;
- cross-object transaction boundaries;
- compensation synthesis;
- executable endpoint, UI or database binding;
- automatic test generation or bug discovery.

These remain visible gaps rather than inferred behavior.
