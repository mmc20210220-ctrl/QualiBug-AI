# Benchmark Runtime Validation

This package converts Benchmark Suite v3 from an offline oracle-alignment suite
into a runnable HTTP target.

Important boundary:

- QualiBug probe generation still uses only `projects/<project>/input`.
- The runtime target reads oracle files only to seed flawed behavior in the fake
  customer service.
- Runtime-confirmed bugs are counted only from HTTP evidence emitted by
  `bug-engine-grounded-execute`.

Run:

```powershell
powershell -ExecutionPolicy Bypass -File .\RUN_BENCHMARK_RUNTIME_VALIDATION.ps1
```

Current script scope:

- Starts FastAPI target on `http://127.0.0.1:8011`.
- Uses project `03_mes_work_order_quality_trace`.
- Executes up to 120 grounded probes against the live target.
- Writes evidence to `platform_outputs/benchmark_runtime_suite_v3_mes`.

This is deliberately separate from the offline `2400/2400` score.  The offline
score measures candidate recall against hidden ground truth; this runtime run
measures reproducible HTTP evidence.
