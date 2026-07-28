# Document Structure IR Contract

## Purpose

Enterprise materials must be understood from source-preserving structure, not from a flattened text approximation. `qualibug.document-structure-ir.v1` is the canonical format-neutral block contract used before Chinese document context and enterprise business understanding.

This phase is strongest for DOCX. It does not claim complete PDF, Excel, PPT, image, OCR, diagram, comment, footnote, endnote, revision or textbox comprehension.

## Mainline order

```text
immutable source bytes
  -> format-specific structure extraction
  -> Document Structure IR
  -> structure normalization
  -> IR-backed Chinese reference resolution
  -> lower-fidelity text context for formats not yet upgraded
  -> Chinese fact conflict reconciliation
  -> Enterprise Business Understanding Model
  -> understanding closure gate
```

## Canonical block fields

Each formal block may contain:

- `block_id`
- `type`: `HEADING`, `PARAGRAPH`, `LIST_ITEM`, `TABLE`, `TABLE_CELL`, `HEADER`, `FOOTER`
- `parent_id`
- `order`
- `region`
- `level`
- `text`
- `start_offset` / `end_offset`
- `source_locator`
- `style`
- `numbering`
- format-specific structural evidence

Block order preserves document order only. It is never a business-process or causal-flow assertion.

## DOCX fidelity in this phase

The DOCX extractor preserves:

- paragraph boundaries;
- Word heading styles and outline levels;
- paragraph and run style summaries;
- direct numbering properties;
- visible list markers and common list styles;
- body paragraphs and tables in original body order;
- table, row and cell identities;
- horizontal merged-cell evidence;
- body heading parentage for paragraphs, lists and tables;
- headers and footers as isolated non-main-flow regions;
- source locators for all formal blocks.

## Explicit non-authorities

The following must never create a business fact or business relation:

- filename;
- document order;
- style similarity alone;
- font size alone;
- block proximity across documents;
- table position alone;
- heading position alone;
- inferred industry templates.

A pending Chinese reference may be promoted only when:

1. its source statement maps to exactly one source block;
2. the block belongs to one source document;
3. explicit body headings or accepted prior facts in the same section identify one unique object and/or actor;
4. the resolution keeps block and heading evidence;
5. the promoted fact is reprocessed by the existing conflict authority.

## Unsupported-content contract

DOCX content that is present but not yet formally understood is emitted as visible structure gaps, including:

- inline images and shapes;
- comments;
- footnotes;
- endnotes;
- textboxes;
- tracked insertions;
- tracked deletions.

These gaps create `DOCUMENT_STRUCTURE_CONTENT_UNPARSED` unknowns and prevent a `PASS` understanding status. A DOCX structure parser failure or an empty DOCX structure creates a critical unknown and blocks formal understanding.

## Completion claim

`model_completeness_projection` is an internal closure projection. It is not document recall, extraction accuracy, semantic accuracy or proof that all enterprise materials were understood.

QualiBug may claim complete enterprise understanding only when:

- all active sources have traceable structure receipts;
- no critical structure parser failures exist;
- no unsupported source content remains unresolved;
- Chinese business facts and conflicts pass their own gates;
- the Enterprise Business Understanding Model has no unresolved unknowns or conflicts.
