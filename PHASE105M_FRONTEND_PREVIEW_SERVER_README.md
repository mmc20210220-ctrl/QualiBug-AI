# Phase105M Frontend Preview Server

Phase105M adds a dependency-free local preview server for the Phase105 frontend delivery bundle.

## What it does

- Builds or loads the Phase105L frontend delivery bundle.
- Serves the Hub V2 site from one local URL.
- Serves page aliases for `pages/`, `assets/`, and `data/` under `hub_v2`.
- Exposes read-only metadata APIs:
  - `/api/v1/frontend-preview/health`
  - `/api/v1/frontend-preview/manifest`
  - `/api/v1/frontend-preview/pages`
  - `/api/v1/frontend-preview/acceptance`
  - `/api/v1/frontend-preview/delivery`
  - `/api/v1/frontend-preview/handoff`
  - `/api/v1/frontend-preview/checksums`
- Blocks write methods and directory traversal.
- Keeps demo payloads redacted and scans for raw token/cookie/session/password/client_secret/traceback leaks.

## Build and run

```powershell
python -m ai_test_asset_center.phase105_frontend_preview_server --bundle-dir .\outputs\phase105_frontend_delivery_bundle --port 8795
```

Then open:

```text
http://127.0.0.1:8795/
```

## Check only

```powershell
python -m ai_test_asset_center.phase105_frontend_preview_server --check --bundle-dir .\outputs\phase105_frontend_delivery_bundle
```

## Use an existing bundle

```powershell
python -m ai_test_asset_center.phase105_frontend_preview_server --no-build-bundle --bundle-dir .\outputs\phase105_frontend_delivery_bundle --port 8795
```

## Static routes

- `/` -> `hub_v2/index.html`
- `/pages/test_execution/test_execution.html`
- `/pages/risk_evidence/risk_evidence.html`
- `/pages/report_roi/report_roi.html`
- `/handoff/DEMO_RUNBOOK.md`
- `/interaction_acceptance/frontend_interaction_acceptance_report.md`

## Output files added to the bundle

- `frontend_preview_server_manifest.json`
- `frontend_preview_server_manifest.md`

## Validation

```powershell
python -m pytest -q tests/test_phase105m_frontend_preview_server.py
```
