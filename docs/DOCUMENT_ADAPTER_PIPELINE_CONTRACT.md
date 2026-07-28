# QualiBug Document Adapter Pipeline Contract

## Purpose

QualiBug targets enterprises across industries.  It cannot scale by adding format
branches inside business understanding.  All source materials therefore enter one
stable pipeline:

```
immutable source bytes
  -> content fingerprint
  -> DocumentAdapter Registry
  -> capability-driven Parsing Planner
  -> one or more adapter executions
  -> evidence-preserving Document IR Merger
  -> structure completeness gate
  -> Business Fact Ledger
  -> Enterprise Understanding Model
```

## Adapter boundary

A `DocumentAdapter` may:

- inspect file signatures, MIME hints and container structure;
- extract text, page, coordinate, style, table, image or diagram structure;
- emit source locators and parser receipts;
- report unsupported or low-confidence content.

A `DocumentAdapter` must not:

- create business rules, objects, operations, lifecycles or processes;
- use the filename as business context;
- treat document order as business flow;
- hide unparsed content;
- silently replace conflicting output from another adapter.

## Stable capabilities

Adapters declare structural capabilities such as:

- `TEXT_EXTRACTION`
- `PAGE_LAYOUT`
- `TEXT_COORDINATES`
- `FONT_EVIDENCE`
- `HEADING_HIERARCHY`
- `LIST_HIERARCHY`
- `TABLE_STRUCTURE`
- `TABLE_REGION_DETECTION`
- `IMAGE_PRESENCE`
- `HEADER_FOOTER`
- `OCR`
- `DIAGRAM_STRUCTURE`
- `FORMULA_EXTRACTION`
- `COMMENT_EXTRACTION`
- `REVISION_EXTRACTION`
- `ATTACHMENT_EXTRACTION`
- `STYLE_SEMANTICS`

The planner compares required and provided capabilities. Missing capability coverage is
projected into `DOCUMENT_ADAPTER_CAPABILITY_GAP`; it cannot still report `COMPLETE`.

## Adapter modes

- `PRIMARY`: authoritative native/container parser for a source family.
- `SUPPLEMENTAL`: contributes capabilities not supplied by the primary adapter, such
  as OCR, table-cell reconstruction or diagram structure.
- `FALLBACK`: lower-fidelity generic text or fail-visible unknown-source handling.

Supplemental adapters may be added without changing enterprise understanding.

## Content detection

File extension is only a hint. Detection prioritizes source signatures and container
contents. Examples:

- `%PDF-` selects PDF even when the filename ends in `.bin`;
- an Office Open XML ZIP containing `word/document.xml` selects DOCX;
- an unknown extension containing reliable UTF-8 text uses generic text fallback;
- an unknown binary source becomes `UNKNOWN_BLOCK` and `UNSUPPORTED_SOURCE_FORMAT`.

## Merge and conflict authority

Multiple adapters may observe the same source. The merger:

- deduplicates identical blocks;
- preserves parser names and versions;
- retains all source locators;
- selects the highest-scoring text projection as plain-text authority;
- reports text-projection divergence;
- blocks formal understanding when the same source locator has contradictory block
  type or text (`DOCUMENT_ADAPTER_BLOCK_CONFLICT`).

No adapter silently overwrites another adapter's evidence.

## Fail-visible statuses

- `COMPLETE`: selected adapters cover required capabilities and expose no gap.
- `PARTIAL`: useful structure exists, but capability, visual, table or layout gaps
  remain.
- `BLOCKED`: source format is unsupported, a primary adapter failed, text authority is
  unavailable, or critical adapter evidence conflicts.

`UNKNOWN_BLOCK` and unsupported-content receipts are formal outputs, not exceptions to
be discarded.

## Extension rule

Adding Excel, PowerPoint, HTML DOM, OCR, table reconstruction, BPMN, UML, ER, Visio or
vendor-specific source support must follow this rule:

1. implement or register an adapter;
2. declare capabilities and mode;
3. emit the stable Document IR contract;
4. add adapter and merge tests;
5. do not add a new format branch to enterprise-understanding integration.

The business-fact and enterprise-understanding layers must remain format-agnostic.
