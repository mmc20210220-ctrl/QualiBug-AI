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
- Captures before/after snapshots from the runtime target `GET /__state`
  observer.

This is deliberately separate from the offline `2400/2400` score.  The offline
score measures candidate recall against hidden ground truth; this runtime run
measures reproducible HTTP evidence.

Runtime target policy:

- `list` / `search` / report-style query surfaces are modeled as GET read
  surfaces in this benchmark target.
- Mutating business commands remain POST/PUT/PATCH/DELETE surfaces.
- Query endpoints are matched with their business domain prefix so evidence from
  one project cannot satisfy another project's probe.

Latest local validation:

- loaded runtime surfaces: 1095
- executed probes: 120
- runtime confirmed: 82
- protected: 6
- needs more evidence: 0
- before/after snapshot requests: 228
- write probes with before/after evidence: 114 / 114
- strong evidence findings: 82
- high/P1 findings: 25

Full-suite bounded run:

```powershell
powershell -ExecutionPolicy Bypass -File .\RUN_BENCHMARK_RUNTIME_SUITE_VALIDATION.ps1 -MaxProbesPerProject 40
```

The suite runner starts the same live target once, iterates all 15 Benchmark
Suite v3 projects, executes each project's generated `grounded_probe_plan.json`,
and writes an aggregate report to
`platform_outputs/benchmark_runtime_suite_v3_full/suite_runtime_validation_summary.json`.

Latest bounded full-suite validation (`-MaxProbesPerProject 20`, high-priority smoke):

- projects: 15
- probes: 300
- runtime confirmed: 270
- strong evidence findings: 270
- high/P1 findings: 95
- protected: 0
- needs more evidence: 0
- before/after snapshot requests: 570

Latest expanded full-suite validation (`-MaxProbesPerProject 80`, deeper queue):

- projects: 15
- probes: 1200
- runtime confirmed: 1005
- strong evidence findings: 1005
- high/P1 findings: 797
- protected: 105
- needs more evidence: 0
- before/after snapshot requests: 2220
- confirmed by risk type: ownership_scope_probe 480, auth_boundary_probe 480, state_transition_probe 45
- observed non-confirmed outcomes: protected 105, observed_no_finding 90
- runtime execution errors: 0

The 20-probe smoke run is intentionally top-of-queue and measures priority
quality. The 80-probe run measures deeper queue behavior and is the more honest
commercial readiness indicator.

The expanded result improved after fixing runtime URL percent-encoding for
non-ASCII business paths. Before the fix, 300 probes were inconclusive due to
`UnicodeEncodeError`; after the fix, those probes execute normally and the full
suite confirmation rate is 1005 / 1200.
