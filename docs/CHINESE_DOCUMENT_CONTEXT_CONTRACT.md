# Chinese Document Context Contract

## Purpose

QualiBug must understand Chinese enterprise documents as hierarchical business materials rather than as a flat bag of sentences. This stage improves document comprehension only. It does not generate tests, findings, probes, or industry assumptions.

## Source authority

The original Chinese source text and its heading/source spans are the only formal authority.

- Chinese terms remain in the original language.
- Translation is never a business-fact authority.
- Every resolved reference records the source document, heading node, section path, character range, and resolution method.
- A fact without traceable source and heading evidence cannot be promoted through document context.

## Document semantic tree

Each source is represented as a hierarchy of:

- document root;
- chapter/part;
- section;
- subsection/item;
- source character range.

Supported heading evidence includes Markdown headings, Chinese chapter/section headings, numbered headings, and Chinese ordered headings.

The tree is structural context only. Document order is not business process order.

## Reference resolution

A pending Chinese fact may be resolved only when one of these conditions is satisfied:

1. The deepest containing heading path contains exactly one known business object or actor.
2. The same document section contains prior accepted facts that identify exactly one object or actor.
3. Heading context and prior same-section context identify the same canonical reference.

If multiple candidates exist, or heading and prior facts disagree, the fact remains `PENDING` and the ambiguity remains visible.

## Forbidden inference

The following must never resolve a formal business reference:

- filename similarity alone;
- token overlap alone;
- paragraph proximity across sections;
- source upload order;
- document order interpreted as process order;
- proximity between different documents;
- model confidence without source evidence;
- industry convention or generic business common sense.

Cross-document resolution requires a separate explicit identity/alias contract. It is not performed by this stage.

## Mainline order

The authoritative knowledge-center order is:

```text
Chinese source comprehension
→ initial fact conflict reconciliation
→ document semantic tree and same-document context resolution
→ conflict reconciliation refresh
→ enterprise business understanding model
→ understanding closure gate
→ downstream binding
```

Newly promoted context-resolved facts must pass the existing conflict authority before entering the enterprise understanding model.

## Outputs

The asset emits:

- `document_semantic_trees`;
- `document_context_resolution_receipt`;
- updated `business_fact_ledger`;
- updated `document_coverage_ledger`;
- updated `enterprise_comprehension_gate`;
- rebuilt Chinese-derived rules.

## Fail-closed status

Unresolved or contradictory context remains visible through ambiguity codes such as:

- `DOCUMENT_CONTEXT_HEADING_AMBIGUOUS`;
- `DOCUMENT_CONTEXT_PRIOR_FACT_AMBIGUOUS`;
- `DOCUMENT_CONTEXT_CONFLICT`;
- `DOCUMENT_CONTEXT_NO_UNIQUE_REFERENCE`;
- `DOCUMENT_CONTEXT_SOURCE_RANGE_UNAVAILABLE`.

A critical unresolved reference prevents the Chinese comprehension gate from passing.
