# Phase92L — Probe Config Template Builder

This phase keeps the bug discovery path document-grounded and input-only, then lowers the manual work needed to execute sandbox write probes safely.

## What changed

- Added `ai_test_asset_center/probe_config_template_builder.py`.
- Added CLI command `bug-engine-probe-config-template`.
- Generates:
  - `probe_config.template.json`
  - `probe_config_template_report.json`
  - `probe_config_template_report.md`
- The template is intentionally non-executable:
  - `template_not_executable=true`
  - `disposable_sandbox.enabled=false`
  - request bodies, path params, headers and snapshots contain `<FILL:...>` placeholders.
- `grounded_probe_executor` now blocks unresolved placeholders before any HTTP execution.

## Intended flow

```powershell
python -m aitestops.cli bug-engine-input-only `
  --input-dir C:\QB\QualiBug_Benchmark_Suite_v3\projects\01_ecommerce_order_payment_inventory\input `
  --project bench01

python -m aitestops.cli bug-engine-probe-config-template `
  --probe-plan platform_outputs\bench01\input_only_run\grounded_probe_plan.json `
  --input-dir C:\QB\QualiBug_Benchmark_Suite_v3\projects\01_ecommerce_order_payment_inventory\input `
  --out-dir platform_outputs\bench01\input_only_run\probe_config_template
```

The customer then copies `probe_config.template.json` to `probe_config.local.json`, fills only disposable sandbox values, and runs `bug-engine-grounded-execute` with explicit approval.

## Guardrail

The builder does not read `oracle`, `ground_truth`, `BUG_MATRIX`, `seed`, `answer`, or `solution` files. It uses only `grounded_probe_plan.json` and optional `projects/<project>/input` documents for OpenAPI/schema hints.
