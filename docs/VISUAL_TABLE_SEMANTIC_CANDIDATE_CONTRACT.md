# QualiBug Visual Table Semantic Candidate Contract

## Purpose

This stage projects auditable structural-semantic candidates from formally recovered tables.
It does not create business rules, process steps, test cases or findings.

The stage consumes canonical Document IR:

```text
TABLE
  -> TABLE_ROW
      -> TABLE_CELL
```

and may emit:

```text
Header nodes
Header parent-child relationships
Row-header candidates
Condition-column candidates
Result-column candidates
Decision-matrix candidates
Explicit legend candidates
```

Every output remains a candidate until later business comprehension validates it against
additional source evidence.

## Mainline

```text
formal visual table
  -> cross-page continuation and repeated-header resolution
  -> header tree projection
  -> logical-table candidate normalization
  -> candidate safety validation
  -> decision-matrix separation gate
  -> table-cell text authority
  -> Chinese business fact ledger
```

The stage must not add format-specific branches to enterprise understanding.

## Header tree

Header nodes are projected only from cells inside a source-backed header boundary. A node records:

- source table and logical-table identity;
- source cell block and locator;
- header level;
- start and end columns;
- row and column spans;
- parent and child header-node IDs;
- `HEADER_GROUP` or `HEADER_LEAF` structural kind.

A parent-child relationship requires exact column containment between adjacent header levels.
It does not assign domain meaning.

## Row-header candidates

A leftmost body column may become a `ROW_HEADER_CANDIDATE` only when:

- a reliable header boundary separates header rows from body rows;
- the table has at least two effective columns;
- at least two body rows exist;
- the leftmost non-spanning body cell is non-empty in at least 60% of body rows.

Without a reliable header boundary, leftmost-column candidates are rejected.
A row-spanning leftmost cell may become a `ROW_HEADER_GROUP_CANDIDATE`, but it remains structural.

## Condition and result column candidates

Condition/result roles require explicit source header vocabulary. The default generic vocabulary
includes examples such as:

```text
条件 / 前提 / 输入 / 状态 / 场景
结果 / 输出 / 动作 / 处理 / 结论 / 决策
condition / input / result / output / action / decision
```

The vocabulary is not an industry ontology. It only creates candidates.

Rules:

- exact normalized matches receive stronger candidate evidence;
- contained Chinese or sufficiently long English matches may create weaker candidates;
- short English control words such as `if` and `then` require exact matching;
- fuzzy embeddings, filename hints and document order are forbidden;
- one column matching both roles produces `DECISION_COLUMN_ROLE_AMBIGUOUS`;
- condition and result columns must be distinct before a decision-matrix candidate is accepted.

## Decision-matrix candidates

A table may become a decision-matrix candidate only when validated explicit headers support:

- at least one condition-column candidate;
- at least one result-column candidate;
- no overlap between the accepted condition and result column sets.

The candidate explicitly records:

```text
candidate_only = true
formal_business_rule = false
business_semantics_added = false
```

No table row becomes a business rule in this stage.

## Cross-page logical tables

Continuation fragments share one candidate owner. The canonical first fragment owns:

- header nodes;
- condition/result column candidates;
- decision-matrix candidate identity.

Later fragments inherit the same IDs. Repeated fragment headers remain source evidence but do not
create duplicate candidate sets.

## Explicit legends

Legends are accepted only from explicit source declarations such as:

```text
√ = 允许
×：禁止
红色表示异常
```

A legend records token, source text, candidate meaning and exact source locator.

Important boundaries:

- symbol text is not interpreted without a unique explicit legend;
- one token with multiple meanings produces `TABLE_LEGEND_TOKEN_AMBIGUOUS`;
- symbols without a unique legend produce `TABLE_SYMBOL_LEGEND_MISSING`;
- color legend text remains unverified until a real cell color sample is bound;
- color text alone produces `TABLE_COLOR_LEGEND_VISUAL_SAMPLE_UNVERIFIED`;
- original cell text is never rewritten by a candidate meaning.

## Fail-visible reason codes

```text
DECISION_COLUMN_ROLE_AMBIGUOUS
TABLE_LEGEND_TOKEN_AMBIGUOUS
TABLE_SYMBOL_LEGEND_MISSING
TABLE_COLOR_LEGEND_VISUAL_SAMPLE_UNVERIFIED
```

These are currently non-blocking structural unknowns unless a later contract raises their
criticality for a specific formal use.

## Runtime metrics

The structure receipt exposes at least:

- header node count;
- group and leaf header-node counts;
- row-header candidate count;
- condition and result column candidate counts;
- decision-matrix candidate count;
- legend candidate counts by type;
- legend-mapped cell count;
- unsafe role candidates rejected;
- row-header candidates rejected;
- overlapping decision-matrix candidates rejected;
- legend ambiguity, missing-symbol and unverified-color counts;
- inherited fragment and cell candidate counts.

These are structural candidate metrics, not business-understanding accuracy.

## Non-claims

This phase does not claim to determine:

- authoritative business conditions or outcomes;
- semantic row types;
- rule priority;
- exception handling;
- truth-table completeness;
- color meaning without sample binding;
- symbol meaning without explicit legends;
- formulas or calculated results;
- business flow from row or page order;
- industry meaning from column names;
- test cases or Bugs.

Later stages must validate candidates against source-backed enterprise facts before promotion.
