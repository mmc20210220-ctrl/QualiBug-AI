# QualiBug Visual Table Structure Adapter Contract

## Purpose

The visual table adapter recovers source-preserving table geometry and cell text from the
shared `RenderedPage` contract. It serves scanned PDF pages, standalone images, visual
PowerPoint/office fallbacks and future page-renderable sources without adding file-format
branches to enterprise business understanding.

It is a structural adapter. It must not infer business meaning, process order, field
semantics or table intent.

## Mainline

```text
source bytes
  -> native Document Adapter when available
  -> concrete scanned-page or table-region gap
  -> PageRendererRegistry
  -> RenderedPage
  -> VisualTableProvider
  -> TABLE -> TABLE_ROW -> TABLE_CELL
  -> Document IR merger
  -> visual-table text authority projection
  -> Chinese business fact ledger
  -> Enterprise Understanding Model
```

Standalone visual sources may run OCR and visual-table adapters together. Each adapter
contributes independent capabilities through the planner.

## Stable table IR

A recovered table must emit:

- one `TABLE` block;
- one `TABLE_ROW` block for every recovered row;
- one `TABLE_CELL` block for every recovered cell;
- parent links from cell to row and row to table;
- page-local pixel coordinates;
- renderer and provider evidence;
- source locators for every block;
- row/column indexes and row/column spans;
- geometry confidence and cell-text confidence;
- an explicit `formal_table_structure` decision.

`TABLE` and `TABLE_ROW` are container evidence. `TABLE_CELL` blocks are the text-bearing
business-source projection.

## Built-in provider

The built-in `ruled-grid-visual-table-provider` detects bordered grids from pixel line
intersections. It is intended for:

- approval matrices;
- status/action tables;
- field dictionaries;
- bordered decision tables;
- conventional ruled report tables.

It does not claim authoritative support for:

- borderless tables;
- merged cells with missing interior borders;
- nested tables;
- rotated or perspective-distorted photographs;
- handwritten tables;
- semantic header hierarchy;
- color-only row/column boundaries.

Those cases require another `VisualTableProvider` implementation and remain fail-visible
until such a provider succeeds.

## Three formal gates

A native `PDF_TABLE_REGION_NOT_CELL_PARSED` gap may be resolved only when all three gates
pass.

### 1. Geometry gate

- table confidence meets the configured threshold;
- required borders are present;
- cell boxes are non-empty;
- merged-cell or border ambiguity is absent.

### 2. Target-completeness gate

- every native table-region target on a page is recovered;
- a partial success on one region never clears another region on the same page;
- embedded-image PDF fallback cannot resolve a coordinate-targeted native table region
  unless full-page coordinate mapping is available.

### 3. Cell-text gate

- a cell OCR provider is available;
- table cell text is recovered;
- mean cell-text confidence meets the formal threshold;
- configured cell-count safety limits are not exceeded.

Failure of any gate preserves the original native gap.

## Gap codes

The adapter may emit:

```text
VISUAL_TABLE_FULL_PAGE_RENDER_REQUIRED
VISUAL_TABLE_PROVIDER_EXECUTION_FAILED
VISUAL_TABLE_STRUCTURE_NOT_RECOVERED
VISUAL_TABLE_GRID_LOW_CONFIDENCE
VISUAL_TABLE_MERGED_CELL_OR_BORDER_UNRESOLVED
VISUAL_TABLE_CELL_LIMIT_EXCEEDED
VISUAL_TABLE_CELL_TEXT_UNAVAILABLE
VISUAL_TABLE_CELL_TEXT_NOT_RECOVERED
VISUAL_TABLE_CELL_TEXT_LOW_CONFIDENCE
PAGE_RENDERER_UNAVAILABLE_OR_FAILED
PAGE_RENDERING_TARGET_PAGES_INCOMPLETE
```

Critical text or rendering failures block formal enterprise understanding. Geometry
limitations that leave the original native table-region gap unresolved remain explicit
`PARTIAL` evidence.

## Text authority

A page-level OCR paragraph can overlap a formally recovered visual table. The paragraph
must remain in Document IR as evidence, but it is excluded from the merged business-text
projection inside that table region.

Formal `TABLE_CELL` blocks become text authority because they preserve row and column
structure. Fact-evidence alignment must ignore blocks marked
`excluded_from_plain_text_projection` so a table fact resolves to one exact cell locator.

## Spreadsheet policy

XLS, XLSX, XLSM, XLSB and ODS sources must not use this adapter as a substitute for native
spreadsheet structure. They require native:

```text
TABLE_STRUCTURE
FORMULA_EXTRACTION
STYLE_SEMANTICS
```

Rendering a workbook screenshot cannot provide formal formula, merged-range, hidden-row,
style or cross-sheet authority.

## Extension rule

A more capable visual-table engine implements the `VisualTableProvider` protocol and
returns the same geometry contract. It may use a local model, a licensed commercial engine
or a company-specific provider, but it must:

- consume `RenderedPage` rather than add new PDF/PPT/image rendering branches;
- preserve provider/version evidence;
- expose confidence and unresolved structures;
- never infer business semantics;
- never clear native gaps without target-complete evidence.

## Current thresholds

Default formal thresholds:

```text
minimum table confidence: 0.72
minimum cell text confidence: 0.55
maximum cells OCR'd per table: 240
```

These are engineering gates, not claims of recall or accuracy. They must be calibrated on
the enterprise-material benchmark before production claims are made.
