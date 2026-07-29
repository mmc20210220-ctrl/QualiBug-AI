# QualiBug Visual Table Continuation and Header Contract

## Purpose

This contract links formally recovered visual table fragments across pages without introducing
PDF-, image- or office-format business logic.  It operates only on canonical Document IR:

```text
TABLE
  -> TABLE_ROW
      -> TABLE_CELL
```

The stage recovers structural relationships only.  It does not infer process order, business
causality or rule priority from page order.

## Mainline

```text
source adapters
  -> shared RenderedPage
  -> visual table providers
  -> TABLE / ROW / CELL Document IR
  -> multi-adapter merger
  -> cross-page continuation resolver
  -> repeated-header suppression and header-path projection
  -> table-cell text authority
  -> Chinese business fact ledger
```

## Continuation evidence

Two fragments may be linked only when all baseline requirements hold:

- both fragments are formally recovered tables;
- the fragments occur on immediately adjacent pages;
- the prior fragment reaches the lower part of its page;
- the following fragment begins in the upper part of its page;
- both fragments expose the same effective column count;
- normalized column boundaries are sufficiently similar;
- either exact repeated header rows or an explicit continuation marker is present.

Accepted marker examples include `续表`, `表（续）`, `continued` and `cont.`.  A filename,
folder name or document-order assumption is never continuation evidence.

## Exact repeated headers

Repeated headers are matched by exact normalized structural signatures:

- starting column;
- column span;
- row span;
- source text after whitespace normalization.

Token similarity, embeddings and fuzzy semantic matching are forbidden for formal header
identity.  A repeated header row remains in Document IR as evidence but is excluded from the
merged business-text projection after the first canonical occurrence.

## Multi-level headers

A multi-level header is formal when one or more of these source-backed conditions hold:

- multiple top rows repeat exactly on a continuation page;
- the canonical top rows contain explicit rectangular spans and complete text;
- an explicit continuation marker links the fragment to a previously confirmed header chain.

The resolver emits:

- `header_row_count`;
- `table_header_role` (`CANONICAL_HEADER` or `REPEATED_HEADER`);
- `table_header_level`;
- `column_header_paths` on data cells;
- `header_source_table_id`;
- `header_inheritance_evidence`.

A header path is structural context.  It does not assign domain meaning or decide which column
is a status, action, actor, amount or identifier.

## Logical table groups

Confirmed fragments share a stable `logical_table_id`.  Each fragment records:

- fragment index and count;
- previous and next fragment table IDs;
- continuation status;
- canonical header source;
- repeated header count;
- explicit `document_order_is_business_flow = false`.

The final IR also contains `table_groups` and `table_continuations` receipts.

## Fail-visible ambiguity

Adjacent edge tables with compatible geometry but without an exact repeated header or explicit
continuation marker are not linked.  They produce:

```text
VISUAL_TABLE_CONTINUATION_AMBIGUOUS
```

This is a visible P1 structural uncertainty.

When an explicit continuation marker is present but the candidate repeats the same header layout
with conflicting non-empty text, the resolver emits:

```text
VISUAL_TABLE_CONTINUATION_HEADER_CONFLICT
```

This is P0 and blocks formal understanding because column authority cannot be established safely.

## Text authority

For a confirmed logical table:

- the first fragment's header cells remain the canonical text authority;
- repeated headers on later fragments remain as evidence;
- repeated headers are excluded only from the merged fact projection;
- data cells inherit exact column-header paths;
- original cell text is never rewritten;
- no source evidence is deleted.

## Runtime metrics

The structure receipt exposes:

- logical visual table group count;
- continued fragment count;
- multi-level header group count;
- repeated header cell count;
- data cells with inherited header paths;
- ambiguous continuation count;
- continuation header conflict count.

These are structural recovery metrics, not business-understanding recall or accuracy.

## Current limitations

This phase does not claim complete support for:

- a continuation that skips pages;
- continuation without page dimensions;
- rotated tables with incompatible coordinate projections;
- multiple indistinguishable candidate tables at the same page edge;
- fuzzy or translated header matching;
- cross-document continuation;
- semantic header roles;
- business flow inferred from page sequence;
- continuation whose column geometry changes materially between pages.

These cases remain separate, ambiguous or blocked rather than being silently joined.
