# Phase104G Frontend Release Readiness Ledger

Phase104G adds a formal frontend release readiness ledger on top of the Phase104F frontend handoff bundle.

It generates:

- `handoff_bundle/`: the Phase104F frontend handoff bundle.
- `release/FRONTEND_RELEASE_NOTES.md`: frontend integration release notes.
- `release/FRONTEND_CUTOVER_PLAN.md`: cutover plan from static/demo flow to local V1 API.
- `release/FRONTEND_ROLLBACK_PLAN.md`: rollback plan when smoke, contract, checksum, or redaction gates fail.
- `release/FRONTEND_SIGNOFF_LEDGER.md`: Product, Backend, Frontend, QA, and Security signoff ledger.
- `release/FRONTEND_RELEASE_CHECKLIST.md`: release checklist.
- `phase104_frontend_release_manifest.json/.md`: machine and human-readable release manifest.
- `release_readiness_report.json/.md`: validation report.
- `CHECKSUMS.sha256`: SHA256 integrity ledger.
- `phase104_frontend_release_readiness_bundle.zip`: release archive.

## Build

```powershell
python -m ai_test_asset_center.phase104_frontend_release_readiness --output-dir .\outputs\phase104_frontend_release_readiness
```

## Use an existing Phase104F handoff bundle

```powershell
python -m ai_test_asset_center.phase104_frontend_release_readiness --no-build-handoff --handoff-dir .\outputs\phase104_frontend_handoff_bundle --output-dir .\outputs\phase104_frontend_release_readiness
```

## Validate only

```powershell
python -m ai_test_asset_center.phase104_frontend_release_readiness --validate-only --output-dir .\outputs\phase104_frontend_release_readiness
```

## Safety

The release gate scans generated JSON, Markdown, TypeScript, JavaScript, env examples, and checksum files for unsafe raw credential examples or Python traceback leakage. Generated outputs are passed through the shared redaction helper.
