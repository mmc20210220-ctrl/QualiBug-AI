# Phase105L Frontend Experience Delivery Bundle

Phase105L packages the Phase105J frontend experience hub v2 and the Phase105K interaction acceptance gate into a customer-demo-ready frontend delivery bundle.

## What it produces

- `hub_v2/index.html`: unified frontend entrypoint.
- `hub_v2/pages/...`: product shell, dashboard, customer intake, environment diagnosis, business flow map, test execution, risk evidence, report ROI.
- `interaction_acceptance/frontend_interaction_acceptance_report.md`: Phase105K acceptance report.
- `handoff/README_FRONTEND_DELIVERY.md`: delivery overview.
- `handoff/DEMO_RUNBOOK.md`: local demo runbook.
- `handoff/CUSTOMER_WALKTHROUGH_SCRIPT.md`: customer-facing walkthrough script.
- `handoff/FRONTEND_DELIVERY_CHECKLIST.md`: delivery checklist.
- `phase105_frontend_delivery_manifest.json/.md`: delivery manifest.
- `CHECKSUMS.sha256`: integrity ledger.
- `frontend_delivery_acceptance_report.json/.md`: delivery acceptance report.
- `phase105_frontend_delivery_bundle.zip`: distributable archive.

## Build

```powershell
python -m ai_test_asset_center.phase105_frontend_delivery_bundle --output-dir .\outputs\phase105_frontend_delivery_bundle
Start-Process .\outputs\phase105_frontend_delivery_bundle\hub_v2\index.html
```

## Validate only

```powershell
python -m ai_test_asset_center.phase105_frontend_delivery_bundle --validate-only --bundle-dir .\outputs\phase105_frontend_delivery_bundle --output-dir .\outputs\phase105_frontend_delivery_bundle_recheck
```

## Acceptance focus

Phase105L checks required frontend files, Hub V2 completeness, Phase105K interaction acceptance, handoff copy, checksums, zip archive completeness, and secret redaction.
