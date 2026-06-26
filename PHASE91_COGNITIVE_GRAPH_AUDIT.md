# Phase91 Cognitive Memory Graph Audit

## Scope

Phase91 implements a private, SQLite-backed cognitive memory graph and risk frontier for QualiBug. It does not use or bundle Obsidian; Markdown is a redacted, read-only export only.

## Mainline integration

```text
Reader/Project Context
  → CognitiveMemoryGraph.sync_context
  → RiskFrontierPlanner
  → GraphContextComposer
  → Reasoner (shadow by default; explicit active mode supported)
  → Executor / Semantic Verifier
  → Adversarial + Schema Gate
  → Finding / Evidence / Cleanup graph updates
  → next Risk Frontier
```

The same graph is read or updated by Discovery, Agent Loop planning, business-flow compilation/execution, replay evidence, and the self-improving-loop report.

## System of record and safety

- System of record: `platform_workspace/<project>/cognitive_memory_graph.sqlite3`.
- Scope boundary: `project_id + environment_id`; cross-scope edges are rejected.
- Inferred/disputed facts are excluded from high-risk write context.
- Graph context cannot authorize writes, bypass production zero-HTTP protection, lower verifier standards, bypass the adversarial/schema gate, or auto-confirm a finding.
- Cleanup failure persists a `CleanupRecord`, marks matching frontier items `BLOCKED_BY_CLEANUP`, and prevents high-risk scheduling.

## Controlled A/B measurement

The controlled local contract fixture measured **6600** baseline document-context characters against **1844** graph-context characters, a measured reduction of **72.1%**. It made **0 network requests**.

The result remains **shadow** because no external replay/shadow quality metrics were supplied; Phase91 intentionally does not fabricate LLM latency, false-positive rate, or customer finding-quality claims.

## Validation

- Release Verifier: `passed`.
- Full suite: `356 passed`, `1 skipped`, `52/52 test files`.
- Compileall, UI smoke, customer-visible text quality, private service smoke: passed.

See `PHASE91_COGNITIVE_GRAPH_VERIFICATION.md`, `PHASE91_CONTEXT_AB_REPORT.json`, `PHASE91_RISK_FRONTIER_REPORT.json`, and `PHASE91_GRAPH_SCORECARD.json`.
