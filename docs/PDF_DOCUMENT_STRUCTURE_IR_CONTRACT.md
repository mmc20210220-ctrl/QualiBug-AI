# PDF Document Structure IR Contract

## Purpose

QualiBug must not treat a PDF as one flat string. PDF structure is part of the
source evidence used to understand Chinese enterprise materials.

The PDF structure stage runs before document-context resolution and before the
Enterprise Business Understanding Model is compiled.

```text
immutable PDF bytes
  -> page/layout extraction
  -> Document Structure IR
  -> document-context resolution
  -> Chinese fact conflict reconciliation
  -> Enterprise Business Understanding Model
```

## Current authority

The implementation uses the project runtime dependency `pypdf` and records:

- page number, width, height and rotation;
- text fragments and text-block bounding boxes;
- font name, font size and bold evidence;
- projected page reading order;
- heading hierarchy inferred from explicit font/layout evidence;
- repeated headers and footers;
- image and Form XObject counts;
- scanned-page status;
- table-like regions whose cell structure is not yet parsed;
- multi-column pages whose order is only a projection.

Coordinates use PDF bottom-left point units and are evidence from the PDF text
matrix. They are not a claim that the visual reading order is perfect.

## Fail-closed rules

The following are formal blockers:

- `SCANNED_PAGE_REQUIRES_OCR`;
- `PDF_TEXT_COORDINATES_UNAVAILABLE`;
- `PDF_DOCUMENT_STRUCTURE_IR_FAILED`;
- `PDF_STRUCTURE_EMPTY`;
- a non-empty PDF with no verifiable textual page content.

A blocked PDF cannot produce a complete enterprise-understanding result.

The following keep understanding at `PARTIAL` until resolved:

- `PDF_IMAGE_CONTENT_UNPARSED`;
- `PDF_FORM_XOBJECT_CONTENT_UNVERIFIED`;
- `PDF_TABLE_REGION_NOT_CELL_PARSED`;
- `PDF_MULTI_COLUMN_READING_ORDER_HEURISTIC`.

Text visible inside a detected table region does not prove that merged cells,
headers, row inheritance or decision-matrix semantics were understood.

## Reading-order contract

- Single-column pages use top-to-bottom, then left-to-right layout order.
- A two-column order may be projected only when both left and right column evidence
  are present.
- Projected multi-column order is marked with reduced confidence.
- PDF order is never promoted directly into business-process order.
- Page proximity and block proximity cannot create a business relationship.

## Header and footer contract

A header or footer is excluded from business-fact flow only when the same normalized
text repeats on enough pages. The filename is never used as a business heading or
as a reference-resolution candidate.

## Heading contract

A PDF text block may become `HEADING` only when it is short and has layout evidence,
such as a materially larger font or bold weight relative to the document body.
Chinese numbering patterns may refine the level only after layout evidence exists.
A numbered sentence is not a heading merely because it begins with `1.` or `（一）`.

## Current boundary

This phase does not yet provide:

- OCR or visual understanding of scanned pages;
- semantic understanding of figures, BPMN, UML, ER diagrams or screenshots;
- authoritative table-cell extraction from arbitrary PDFs;
- guaranteed reading order for mixed, nested or more-than-two-column layouts;
- reliable bounding boxes for images exposed only as indirect XObjects;
- recovery of content hidden in unsupported encrypted PDFs.

These gaps must remain visible in the structure receipt and enterprise-understanding
unknowns. They must never be silently replaced by model guesses.
