# Phase92I — Grounded Probe Execution Bridge

This phase keeps the benchmark/enterprise workflow blind and input-only:

```text
projects/<project>/input/ -> grounded_candidates.json -> grounded_probe_plan.json -> safe runtime evidence
```

Guardrails:

- Only `projects/<project>/input/` is copied by `bug-engine-input-only`.
- `oracle`, `ground_truth`, `BUG_MATRIX`, `seed`, `answer` and solution files are refused.
- Document-derived candidates are not called confirmed bugs.
- Runtime validation requires observed HTTP evidence.
- Automatic execution is limited to `GET`/`HEAD` probes marked `read_only_safe` and only when `--execute-readonly` is explicitly passed.
- `POST`/`PUT`/`PATCH`/`DELETE` probes remain blocked unless an explicit disposable-sandbox approval path is later configured.

Commands:

```powershell
python -m aitestops.cli bug-engine-input-only `
  --input-dir C:\QB\QualiBug_Benchmark_Suite_v3\projects\01_ecommerce_order_payment_inventory\input `
  --project bench01
```

Generate execution/repro assets from an existing grounded plan without network traffic:

```powershell
python -m aitestops.cli bug-engine-grounded-execute `
  --probe-plan platform_outputs\bench01\input_only_run\grounded_probe_plan.json `
  --out-dir platform_outputs\bench01\input_only_run\grounded_execution
```

Execute eligible read-only probes against an approved live/disposable target:

```powershell
python -m aitestops.cli bug-engine-grounded-execute `
  --probe-plan platform_outputs\bench01\input_only_run\grounded_probe_plan.json `
  --out-dir platform_outputs\bench01\input_only_run\grounded_execution `
  --base-url http://127.0.0.1:8000 `
  --execute-readonly `
  --probe-config probe_config.json
```

`probe_config.json` may contain:

```json
{
  "default_headers": {"X-Tenant-Id": "tenant-a"},
  "bearer_token": "optional-token-for-authorized-read-probes",
  "path_params": {
    "*": {"id": "known-safe-object-id"},
    "GIC-0001": {"tenant_id": "tenant-a"}
  }
}
```

Outputs:

- `grounded_probe_execution_report.json`
- `grounded_probe_execution_report.md`
- `grounded_probe_repro.ps1`
- `grounded_probe_regression_pytest.py`
