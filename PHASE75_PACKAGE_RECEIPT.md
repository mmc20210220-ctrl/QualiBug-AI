# Phase75 Package Receipt

## Included

- QualiBug Phase75 source code, tests, examples, documentation and CI workflow;
- persistent Agent Loop control-plane code and the Phase75 experiment compiler;
- Phase75 release notes, verification evidence and measured release manifest.

## Excluded

- project-local SQLite ledgers, compiled experiment packs and execution receipts;
- target data, fixture data, MES benchmark inputs and benchmark truth files;
- real API keys, credentials, tokens, `.env`, `.env.local`, logs, caches and bytecode.

## Verification

The external release process verifies archive integrity, SHA-256, excluded
runtime/private material, extraction, Python compilation and the Agent Loop
core regression suite. The measured source release manifest records full
99/99 regression success before packaging.
