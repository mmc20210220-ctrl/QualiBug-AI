# QualiBug Advanced Visual Table Providers Contract

## Purpose

This contract extends the shared visual-table pipeline without introducing source-format
branches. Borderless tables and merged cells must still produce the canonical:

```text
TABLE
  -> TABLE_ROW
      -> TABLE_CELL
```

The providers recover visual structure only. They do not infer business meaning, process
order, authoritative headers or domain semantics.

## Mainline

```text
source bytes
  -> native adapter / visual gap
  -> shared RenderedPage
  -> CompositeVisualTableProvider
      -> MergedCellRuledGridVisualTableProvider
      -> TextAlignedVisualTableProvider
  -> duplicate observation reconciliation
  -> VisualTableSupplementalAdapter
  -> cell-level OCR and formal gates
  -> Document IR merger
  -> Chinese business fact ledger
```

Adding these providers must not add DOCX, PDF, PPT or image branches to enterprise
understanding.

## Merged-cell ruled-grid provider

The ruled-grid provider first projects atomic cells from visible horizontal and vertical
lines. The merged-cell provider may combine adjacent atomic cells only when:

- their shared internal boundary has low visual support;
- surrounding outer boundaries remain strongly supported;
- the resulting component is a complete rectangle;
- the merged rectangle has a complete top, bottom, left and right boundary.

A valid merged cell records:

- `row_index` and `column_index`;
- `row_span` and `column_span`;
- the union bounding box;
- outer-border support;
- the list of atomic cells that formed the span;
- `RULED_GRID_MISSING_INTERNAL_BOUNDARY_MERGE` evidence.

An L-shaped, disconnected or otherwise non-rectangular component is not promoted. It must
remain unresolved and keep formal geometry false.

## Borderless text-alignment provider

A borderless table may be projected only from word-level text boxes. Plain line-level text
without reliable word coordinates is insufficient.

The default provider requires:

- at least three structural rows;
- at least two repeatedly supported column anchors;
- repeated x-alignment across rows;
- sufficient occupied-cell coverage;
- acceptable OCR word confidence;
- acceptable row-spacing regularity.

A single centered heading may span multiple columns only after column anchors were
established by repeated multi-column rows. This is structural span recovery, not semantic
header classification.

The provider must reject ordinary paragraphs, two-line lists and layouts without repeated
column evidence.

## Composite provider

The default visual-table adapter uses a composite provider. It runs every available
structural provider independently and reconciles overlapping observations.

When two providers describe substantially the same region:

- a formally supported observation outranks a non-formal one;
- higher confidence outranks lower confidence;
- equal observations prefer visible ruled-grid evidence over alignment-only evidence;
- the non-selected observation remains recorded as an alternative provider observation.

An unavailable optional borderless provider must not disable ruled-grid recovery.

## Formal table gate

Advanced providers do not bypass the existing table gate. A table becomes formal only when:

- structural geometry reaches the configured threshold;
- every final cell has a complete structural boundary projection;
- cell text is recovered;
- mean cell-text confidence reaches the configured threshold;
- the safe cell OCR limit is not exceeded;
- every targeted native table region on the page is formally recovered.

For borderless cells, `border_complete` means the inferred alignment boundaries are
structurally complete. It does not claim that visible borders exist. The provider retains
`TEXT_ALIGNMENT_NOT_VISIBLE_BORDERS` evidence.

## Text authority

When a visual table is formal:

- `TABLE_CELL` text becomes the merged text authority for the table region;
- overlapping native PDF text and page-level OCR remain as evidence;
- superseded text blocks are excluded only from the fact projection;
- no evidence block is deleted.

Merged cells remain one authoritative `TABLE_CELL` with explicit spans. Their text must not
be duplicated into every covered atomic coordinate.

## Runtime metrics

The final structure receipt distinguishes:

- total visual tables;
- formal visual tables;
- ruled visual tables;
- borderless visual tables;
- visual table cells;
- tables containing merged cells;
- merged visual table cells;
- unresolved merged structures;
- unresolved native table regions.

These are structural recovery metrics, not business-understanding accuracy metrics.

## Fail-visible reason codes

The existing adapter continues to expose at least:

```text
VISUAL_TABLE_STRUCTURE_NOT_RECOVERED
VISUAL_TABLE_MERGED_CELL_OR_BORDER_UNRESOLVED
VISUAL_TABLE_GRID_LOW_CONFIDENCE
VISUAL_TABLE_CELL_TEXT_UNAVAILABLE
VISUAL_TABLE_CELL_TEXT_NOT_RECOVERED
VISUAL_TABLE_CELL_TEXT_LOW_CONFIDENCE
VISUAL_TABLE_CELL_LIMIT_EXCEEDED
PAGE_RENDERING_TARGET_PAGES_INCOMPLETE
```

Irregular merges and weak borderless alignment must never be converted into empty successful
tables.

## Current limitations

This phase does not claim complete support for:

- nested tables;
- arbitrary L-shaped visual cells;
- diagonal headers;
- perspective-skewed photographs;
- handwritten tables;
- color-only state matrices;
- semantic multi-level header inheritance;
- tables continued across pages;
- row-span inference in borderless tables without reliable word geometry;
- business meaning derived from spatial order alone.

These limitations remain visible through structure receipts and the enterprise understanding
gate.
