# Private Pilot Acceptance Smoke

`qualibug-acceptance-smoke` is the customer handoff command for private-pilot deployments. It wraps doctor diagnostics into an acceptance-oriented report and can optionally verify a running service through `/api/health`.

The command does not execute enterprise scans or read customer documents. It validates deployment readiness, runtime patch wiring, scan context contract, credential safety, support bundle guidance, and optional health reachability.

## Quick start

```bash
# Recommended customer-site acceptance smoke
qualibug-acceptance-smoke --output

# Also check a running service
qualibug-acceptance-smoke --server-url http://localhost:8088 --output

# Compact JSON for CI or scripts
qualibug-acceptance-smoke --compact

# Do not install runtime patches first; useful only for negative diagnostics
qualibug-acceptance-smoke --skip-install-patches --output
```

When `--output` is provided with no value, the report is written to:

```text
platform_outputs/private_pilot_acceptance_smoke_report.json
```

The companion doctor report is written to:

```text
platform_outputs/private_pilot_doctor_report.json
```

## Acceptance result

The report contains:

- `acceptance.level`: `accepted`, `warning`, or `blocked`;
- `acceptance.accepted`: boolean handoff result;
- `acceptance.blockers`: blocking reasons that must be fixed before claiming readiness;
- `checks.runtime_patches`: delivery gate, scan context, credential safety, browser UI smoke, customer report, and deployment health patches;
- `checks.scan_context_contract`: source manifest and campaign context helper completeness;
- `checks.credential_safety`: masked-ref policy and plaintext-return guard;
- `checks.http_health`: optional live `/api/health` result when `--server-url` is supplied;
- `support_bundle_manifest`: safe-to-share and do-not-send guidance inherited from doctor.

## Recommended handoff flow

```bash
pip install -e .
qualibug-doctor --output
qualibug-acceptance-smoke --output
python -m ai_test_asset_center.private_pilot_entrypoint
qualibug-acceptance-smoke --server-url http://localhost:8088 --output ./platform_outputs/private_pilot_acceptance_smoke_live_report.json
```

A clean handoff should have no blockers. If `acceptance.level` is `blocked`, fix the blockers or send the doctor and acceptance smoke reports to support.
