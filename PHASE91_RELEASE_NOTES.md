# QualiBug Phase91 Release Notes

## Cognitive Memory Graph & Risk Frontier

Phase91 adds a private, typed SQLite cognitive memory layer for business Bug discovery. It does not bundle or depend on Obsidian. The product borrows only the underlying principles of atomic facts, explicit links, local context, and durable knowledge accumulation.

### Added

- Typed, project/environment-isolated Cognitive Memory Graph.
- Source-traceable business fact extraction and conflict handling.
- Bounded Graph Context Composer with an Evidence Pack and legacy fallback.
- Risk Frontier Planner for explainable, coverage-driven discovery selection.
- Human Fact Proposal review/approval/revocation gate.
- Graph integration in Discovery, Agent Loop planning, business-flow compilation/execution, replay evidence, and self-improving-loop reporting.
- Conservative A/B harness: graph context stays `shadow` without measured replay/shadow quality gates.
- Redacted, one-way Markdown knowledge-vault export.
- Phase91 release package audit and isolated full-suite release verification.

### Safety guarantees

- Graph data cannot authorize target writes or bypass existing safety/cleanup gates.
- Production HTTP request count remains zero.
- Findings still require the existing adversarial validation, evidence schema, and human-review path.
- Cleanup failure is persisted and blocks matching high-risk frontier work.
- Markdown is a read-only export, not a source of truth.

### Operator controls

```text
QUALIBUG_GRAPH_CONTEXT_MODE=off|shadow|active
qualibug graph-stats --project <project> --environment test
qualibug graph-context-ab --project <project> --environment test
qualibug export-knowledge-vault --project <project> --environment test --out <dir>
```

`shadow` remains the default. `active` is an explicit operator choice and must be supported by measured replay/shadow evidence; Phase91 never auto-promotes it.
