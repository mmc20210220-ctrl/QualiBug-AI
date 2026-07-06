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
- remediation hints with concrete commands for common environment issues;
- readiness classification for handoff decisions;
- human-readable `summary_text` and `summary_lines` for customer-facing handoff notes;
- `support_bundle_manifest` with safe-to-share, review-required, and do-not-send artifact guidance.

## Human summary

`summary_text` is a newline-separated field-engineer summary that can be copied into a handoff note, support ticket, or customer acceptance record. `summary_lines` contains the same content as a list for UI rendering.

The summary includes:

- readiness label and level;
- product version and effective port;
- blocking issue codes when present;
- warning/action item codes when present;
- next action;
- up to three suggested commands.

Example summary shape:

```text
QualiBug private-pilot doctor: Warning - usable for diagnosis, not clean handoff.
Product version: 95.0.0; effective port: 8088; readiness: warning.
Warnings/action items: CREDENTIAL_KEY_MISSING, RUNTIME_PATCHES_NOT_INSTALLED_IN_READONLY_MODE
Next action: Review remediation_hints, apply recommended commands, then rerun qualibug-doctor --install-patches --output.
Suggested commands: qualibug-doctor --install-patches --output && qualibug-server
```

## Support bundle manifest

`support_bundle_manifest` tells field engineers what can be shared with support without exposing customer data.

The manifest has four sections:

| Section | Meaning |
|---------|---------|
| `safe_to_share` | Low-risk diagnostics, mainly `private_pilot_doctor_report.json` and the patched doctor report |
| `requires_review` | Logs, browser UI artifacts, HAR files, screenshots, and pipeline reports that may contain customer context |
| `do_not_send` | `.env*`, `.secrets`, `multi_service_config.json`, and enterprise source registry content |
| `redaction_rules` | Rules for removing keys, tokens, cookies, session IDs, screenshots, HAR headers, and customer identifiers |

Default safe files:

```text
platform_outputs/private_pilot_doctor_report.json
platform_outputs/private_pilot_doctor_patched_report.json
```

Do not send these paths without explicit customer approval and redaction:

```text
.env*
platform_workspace/.secrets/**
platform_workspace/**/multi_service_config.json
platform_workspace/**/source_registry/**
```

When in doubt, send only the doctor report and the `support_bundle_manifest` section.

## Readiness levels

The `readiness` object summarizes whether the current environment can move forward:

| Level | Meaning | Handoff decision |
|-------|---------|------------------|
| `ready` | Diagnostics are clean | Proceed with service startup and scenario smoke validation |
| `warning` | Usable for diagnosis but not a clean handoff | Apply `remediation_hints`, rerun `qualibug-doctor --install-patches --output`, then decide |
| `blocked` | A blocking error exists | Do not claim pilot readiness; fix blockers or send the doctor report to support |

`readiness.blockers` contains stable blocking codes. `readiness.warnings` contains non-blocking but actionable issues. `readiness.next_action` is the recommended next step for the field engineer.

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
