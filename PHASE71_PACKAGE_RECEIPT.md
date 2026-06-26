# Phase71 Package Receipt

## Included evidence

- `PHASE71_DEEP_ANALYSIS.md`: architecture, finding provenance, endpoint
  coverage matrix, adversarial analysis and gap ranking.
- `PHASE71_RELEASE_NOTES.md`: scope and implementation boundary.
- `PHASE71_VERIFICATION.md`: pre-fix P0 reproduction and post-fix proof.
- `PHASE71_RELEASE_MANIFEST.json`: measured release-verifier result.
- `docs/PHASE71_PROJECT_SCOPE_ISOLATION.md`: reusable Oracle contract guide.
- `examples/project_scope_contracts_config.example.json`: safe explicit contract
  configuration example.

## Exclusions

The delivery archive intentionally excludes `.env`, `.env.local`, real API
keys, credentials, runtime workspaces, self-dogfood artifacts, logs, caches,
bytecode and private benchmark ground truth. The separately delivered
`.sha256` file is the authoritative archive-integrity checksum.