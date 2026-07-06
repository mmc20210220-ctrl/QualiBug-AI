# Private Pilot Doctor Diagnostics

`qualibug-doctor` is the first command to run during private-pilot installation, smoke acceptance, and support handoff. It prints a JSON diagnostic report and can also write the same report to disk for delivery archives or support tickets.

## Quick checks

```bash
# Read-only diagnostics, printed to stdout
qualibug-doctor

# Compact JSON for CI or scripts
qualibug-doctor --compact

# Write the default delivery report under the private-pilot root
qualibug-doctor --output

# Write a custom report path
qualibug-doctor --output ./artifacts/private-pilot-doctor.json

# Install runtime patches first, then report active patch status and write the report
qualibug-doctor --install-patches --output
```

When `--output` is provided with no value, the report is written to:

```text
platform_outputs/private_pilot_doctor_report.json
```

Relative output paths are resolved under `--root`. Absolute output paths are used as-is.

## What the report covers

The JSON report includes:

- product name, version, phase, channel, port, and health-path contract;
- import status for the private-pilot patch modules;
- runtime patch status for delivery gate, scan campaign context, credential safety, browser UI smoke, customer report, and deployment health patch;
- credential-safety posture, including key source and masked-ref frontend policy;
- Browser UI Smoke readiness, Playwright availability, and configured target URL environment variables;
- scan context contract status, including source manifest, scan body preparation, and campaign context helpers;
- a local `/api/health` payload preview;
- remediation hints with concrete commands for common environment issues.

## Remediation hints

The `remediation_hints` array is machine-readable and field-engineer-friendly. Each item includes:

- `code`: stable issue code;
- `severity`: `info`, `warning`, or `error`;
- `title`: short problem summary;
- `action`: what to do next;
- `commands`: suggested commands when the fix can be executed locally.

Common codes include:

| Code | Meaning | Typical fix |
|------|---------|-------------|
| `INVALID_QUALIBUG_PORT` | `QUALIBUG_PORT` is not a valid TCP port | `unset QUALIBUG_PORT` or `export QUALIBUG_PORT=8088` |
| `CREDENTIAL_KEY_MISSING` | Local credential encryption key has not been created yet | `qualibug-doctor --install-patches --output` |
| `BROWSER_UI_PLAYWRIGHT_MISSING` | Browser UI Smoke is enabled but Playwright is not installed | `pip install -e '.[browser]'` and `python -m playwright install chromium` |
| `RUNTIME_PATCHES_NOT_INSTALLED_IN_READONLY_MODE` | Doctor ran in read-only mode, so active patch status is not installed yet | `qualibug-doctor --install-patches --output` |
| `RUNTIME_PATCH_INSTALL_INCOMPLETE` | Runtime patches are still missing after install-patches mode | send the doctor report to support before claiming readiness |
| `SCAN_CONTEXT_CONTRACT_INCOMPLETE` | Scan context helpers are missing or incomplete | `pip install -e .` then rerun doctor |

## Recommended handoff flow

```bash
pip install -e .
qualibug-doctor --output
qualibug-doctor --install-patches --output ./platform_outputs/private_pilot_doctor_patched_report.json
python -m ai_test_asset_center.private_pilot_entrypoint
```

Share the generated JSON report when reporting environment or deployment problems. It avoids sharing service credentials because credential values are reported as masked refs only.
