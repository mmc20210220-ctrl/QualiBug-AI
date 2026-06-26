# Phase66 Release Notes — Evidence-First Self-Optimization

## Scope

Phase66 strengthens QualiBug's own bug-detection quality without adding a new
product subsystem or changing safety boundaries. It applies three measured,
reversible improvements to the existing self-dogfood and PRD/OpenAPI mining
paths.

## Improvements kept

1. **Scan-report cross-view reconciliation Oracle**
   - The self-dogfood audit now verifies that `executive_summary.total_bugs_found`,
     `stage2_discovery.total_findings`, and the detailed finding list have the
     same count.
   - It detected a real P1 issue in the current product: the executive summary
     reports 13 findings while the calibrated detail list reports 18 after
     health and validation enrichment.
   - Evidence is direct runtime payload comparison, not LLM inference.

2. **Operation-local idempotency evidence calibration**
   - A global PRD mention of retry or duplication no longer marks every POST,
     PUT, or PATCH route as a P1 idempotency risk.
   - A route must now contain its own create/pay/import/submit/idempotency
     signal. The PRD strengthens confidence only after that local signal exists.
   - This removed ungrounded settings-save and scan-run candidates while
     retaining the knowledge-ingest replay candidate.

3. **Explicit asynchronous-work vocabulary**
   - `scan`, `import`, `export`, and `report` alone no longer prove background
     execution. Async progress checks now require terms such as `job`, `queue`,
     `async`, `task`, or their Chinese equivalents.
   - This removed two synchronous-route false positives while preserving
     explicit job/batch detection.

## Boundaries preserved

- `safety_boundary.py` was not changed.
- No destructive test mode, production target, or production-blocking logic was changed.
- No test files or `__init__.py` files were changed.
- LLM output did not create any finding. The real isolated `/health` probe
  reported the provider as unconfigured/offline; self-dogfood test doubles are
  recorded separately and are not treated as provider availability.

## Measured result

| Metric | Baseline | Final |
| --- | ---: | ---: |
| Pipeline candidates | 22 | 18 |
| P1 / P2 / P3 | 12 / 3 / 7 | 10 / 1 / 7 |
| Strong-evidence candidates | 12 | 12 |
| Strong-evidence share | 54.5% | 66.7% |
| Confirmed false positives in manual audit | 3 | 0 |
| Newly detected real self-dogfood defect | 0 | 1 P1 |

The current P1 is intentionally left visible as a verified product defect for
repair and replay. Phase66 is an engineering-validated iteration, not GA.
