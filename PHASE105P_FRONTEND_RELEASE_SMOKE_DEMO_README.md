# Phase105P Frontend Release Smoke Demo

Phase105P adds the last-mile demo and smoke gate for the Phase105 frontend preview release package.

## What it adds

- Builds the Phase105O frontend preview release package.
- Runs a socket-free smoke test through the Phase105M pure preview router.
- Validates the Hub V2 entry page and all 8 core pages.
- Validates read-only APIs: `/health`, `/manifest`, `/pages`, `/acceptance`, `/delivery`, `/handoff`, `/checksums`.
- Validates write protection, OPTIONS preflight, and directory traversal protection.
- Generates one-click demo scripts: `RUN_FRONTEND_DEMO.ps1` and `RUN_FRONTEND_DEMO.cmd`.
- Generates one-click smoke scripts: `SMOKE_FRONTEND_RELEASE.ps1` and `SMOKE_FRONTEND_RELEASE.cmd`.
- Generates `DEMO_QUICKSTART.md`, smoke reports, manifest, checksums, and a zip archive.
- Scans for raw token/cookie/session/client_secret/traceback leakage.

## Build

```powershell
python -m ai_test_asset_center.phase105_frontend_release_smoke_demo --output-dir .\outputs\phase105_frontend_release_smoke_demo
```

## One-click demo

```powershell
powershell -ExecutionPolicy Bypass -File .\outputs\phase105_frontend_release_smoke_demo\RUN_FRONTEND_DEMO.ps1
```

Open:

```text
http://127.0.0.1:8795/
```

## One-click smoke

```powershell
powershell -ExecutionPolicy Bypass -File .\outputs\phase105_frontend_release_smoke_demo\SMOKE_FRONTEND_RELEASE.ps1
```

## Validate existing package

```powershell
python -m ai_test_asset_center.phase105_frontend_release_smoke_demo --validate-only --package-dir .\outputs\phase105_frontend_release_smoke_demo --output-dir .\outputs\phase105_frontend_release_smoke_demo_recheck
```
