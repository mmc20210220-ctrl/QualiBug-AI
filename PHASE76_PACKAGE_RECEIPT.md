# Phase76 Package Receipt

## Included

- QualiBug Phase76 source, tests, examples, deployment templates, documentation and CI workflow;
- Agent Discovery Ledger integration and the Phase76 multi-step business-flow orchestrator;
- Phase76 release notes, verification evidence and the measured release manifest.

## Excluded

- runtime SQLite ledgers, compiled flow packs, execution receipts and generated CSV projections;
- MES target data, document inputs, benchmark truth/oracle files and any externally supplied fixture data;
- real API keys, credentials, tokens, `.env`, `.env.local`, logs, caches and bytecode.

## Verification before archive

- Full source release manifest: `passed`, `release_ready=true`.
- Full regression suite: 99/99 passed.
- Required product regressions: 11/11 passed.
- Controlled local business-flow smoke verification: a mapped illegal state transition and a failed-transfer rollback drift both produced runtime evidence without auto-confirming a Bug.
- Archive release process verifies ZIP integrity, SHA-256, exclusion rules, extraction and core regressions before delivery.

## Operational boundary

Phase76 does not auto-execute inferred PRD/API flows. Candidate flows require explicit project-owned mappings, disposable sandbox approval, fixture bindings and the existing shared safety gate before any write-capable execution.
