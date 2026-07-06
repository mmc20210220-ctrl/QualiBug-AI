# Private Pilot Acceptance Smoke

`qualibug-acceptance-smoke` is the customer handoff command for private-pilot deployments. It wraps doctor diagnostics into an acceptance-oriented report and can optionally verify a running service through `/api/health`.

The command does not execute enterprise scans or read customer documents. It validates deployment readiness, runtime patch wiring, scan context contract, credential safety, support bundle guidance, scenario-readiness metadata, and optional health reachability.

## Quick start

```bash
# Recommended customer-site acceptance smoke
qualibug-acceptance-smoke --output

# Also check a running service
qualibug-acceptance-smoke --server-url http://localhost:8088 --output

# Check whether a real customer scenario is ready to run
qualibug-acceptance-smoke \
  --project demo \
  --scan-base-url http://staging.example.internal \
  --scope-id checkout-scope \
  --environment-ref staging \
  --test-data-strategy synthetic_only \
  --require-scenario-ready \
  --output

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
- `checks.scenario_readiness`: source registry asset count and required scan metadata readiness;
- `checks.http_health`: optional live `/api/health` result when `--server-url` is supplied;
- `customer_acceptance_summary`: bilingual copyable customer handoff summary;
- `acceptance_artifact_manifest`: archive index for handoff artifacts;
- `support_bundle_manifest`: safe-to-share and do-not-send guidance inherited from doctor.

## Customer acceptance summary

`customer_acceptance_summary` is designed for customer acceptance forms, support tickets, and handoff emails. It contains:

- `zh_text` / `zh_lines`: Chinese copyable summary;
- `en_text` / `en_lines`: English copyable summary;
- `safe_report_paths`: reports that can be sent back to support;
- `next_commands`: suggested next commands;
- `accepted` and `level`: the same decision status used by `acceptance`.

Example Chinese summary shape:

```text
客户验收结果：warning（通过）。
产品版本：95.0.0。
可安全发回支持的报告：platform_outputs/private_pilot_acceptance_smoke_report.json；platform_outputs/private_pilot_doctor_report.json
待处理事项：scenario_readiness_missing:source_registry_asset,base_url,scope_id,environment_ref,test_data_strategy
真实扫描前置缺失：source_registry_asset，base_url，scope_id，environment_ref，test_data_strategy
下一步：Review warnings and scenario_readiness before claiming a clean handoff.
建议命令：qualibug-acceptance-smoke --project <project> --scan-base-url <url> --scope-id <scope> --environment-ref <env> --test-data-strategy <strategy> --require-scenario-ready --output
```

## Acceptance artifact manifest

`acceptance_artifact_manifest` is the handoff archive index. It tells the delivery manager which artifacts must be kept together and which embedded fields can be copied into an acceptance record.

Required handoff artifacts include:

| Artifact id | Kind | Purpose |
|-------------|------|---------|
| `acceptance_smoke_report` | file | Primary customer acceptance JSON report |
| `private_pilot_doctor_report` | file | Companion diagnostics report with masked credential refs |
| `customer_acceptance_summary_zh` | embedded field | Chinese handoff note for acceptance forms or support tickets |
| `support_bundle_manifest` | embedded field | Safety policy for what can and cannot be shared |

Optional but safe artifact:

| Artifact id | Kind | Purpose |
|-------------|------|---------|
| `customer_acceptance_summary_en` | embedded field | English handoff note |

Archive recommendations:

- archive the acceptance smoke report and companion doctor report together;
- use `customer_acceptance_summary.zh_text` or `customer_acceptance_summary.en_text` as the handoff note;
- do not add logs, HAR files, screenshots, `.env` files, secrets, or enterprise source registry contents unless explicitly approved;
- follow inherited `support_bundle_manifest.do_not_send` rules.

## Scenario-readiness preflight

Scenario readiness checks only metadata. It does not read customer source contents or execute scans.

The check verifies:

- project id;
- at least one immutable source registry asset for the project;
- base URL for the target environment;
- approved scope id;
- environment reference;
- test data strategy.

By default, missing scenario metadata creates a warning so infrastructure handoff can still complete. Use `--require-scenario-ready` when the handoff must prove that a real customer scenario is ready to run; missing metadata then blocks acceptance.

Useful parameters:

```bash
--project demo
--scan-base-url http://staging.example.internal
--scope-id checkout-scope
--environment-ref staging
--test-data-strategy synthetic_only
--require-scenario-ready
```

Environment variable fallbacks are supported for automation:

```text
QUALIBUG_PROJECT
QUALIBUG_TARGET_BASE_URL
QUALIBUG_TARGET_UI_BASE_URL
QUALIBUG_BROWSER_UI_BASE_URL
QUALIBUG_SCOPE_ID
QUALIBUG_ENVIRONMENT_REF
QUALIBUG_TARGET_ENVIRONMENT
QUALIBUG_TEST_DATA_STRATEGY
```

## Recommended handoff flow

```bash
pip install -e .
qualibug-doctor --output
qualibug-acceptance-smoke --output
python -m ai_test_asset_center.private_pilot_entrypoint
qualibug-acceptance-smoke --server-url http://localhost:8088 --output ./platform_outputs/private_pilot_acceptance_smoke_live_report.json
qualibug-acceptance-smoke --project demo --scan-base-url http://staging.example.internal --scope-id checkout-scope --environment-ref staging --test-data-strategy synthetic_only --require-scenario-ready --output ./platform_outputs/private_pilot_acceptance_smoke_scenario_report.json
```

A clean handoff should have no blockers. If `acceptance.level` is `blocked`, fix the blockers or send the doctor and acceptance smoke reports to support.
