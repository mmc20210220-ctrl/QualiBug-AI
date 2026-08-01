# Discovery Funnel Closure: authenticated diagnostic comparison

Status: `NOT_MEASURED` for optimization promotion.

The baseline and candidate were run as authenticated evaluator-owned replay diagnostics against the same target, environment, input, fixture, context, and runtime fingerprints. The candidate is not a quality result: its campaign was degraded, so its precision and recall are intentionally absent.

| Metric | Baseline | Candidate | Interpretation |
|---|---:|---:|---|
| Quality claim | `MEASURED` | `NOT_MEASURED` | Candidate promotion is blocked |
| Precision | 0.3333 | `NOT_MEASURED` | No candidate quality delta |
| Recall | 0.0305 | `NOT_MEASURED` | No candidate quality delta |
| Selected / terminal | 512 / 512 | 507 / 507 | Funnel identity is complete |
| Compiled | 122 | 150 | Candidate reached more compileable obligations |
| Executed | 64 | 108 | Candidate reached more executions |
| Harness failures | 0 | 56 | Candidate degraded the campaign |
| Funnel conservation | `PASS` | `PASS` | No silent loss in either receipt |
| Cleanup failures | 0 | 0 | Target reset and cleanup were clean |
| Production requests | 0 | 0 | No production traffic |

The candidate’s observed reach increase is not a quality improvement. It introduced 36 `CONTRACT_ORACLE_HARNESS_FAILED` outcomes and 20 `CLEANUP_WRITE_COVERAGE_MISMATCH` outcomes; the evaluator therefore returned `NOT_MEASURED` and promotion remains blocked.

Top unresolved baseline causes:

1. `BLOCKED_NON_REVERSIBLE_WRITE` — 262. Source materials do not declare an exact reversible cleanup authority; these obligations must remain blocked.
2. `BLOCKED_MISSING_BINDING` — 53. Exact source-declared actor, fixture, body, or placeholder bindings are missing.
3. `BLOCKED_MISSING_OBSERVER` — 41. Required control-success or business-effect observations are not proven.
4. `FIELD_LEVEL_RULE_NOT_EXECUTABLE` — 22.
5. `BLOCKED_CONFLICTING_SOURCE` — 18.
6. `BLOCKED_INVALID_CLEANUP_PLAN` — 17.
7. `BLOCKED_MISSING_OPERATION` — 12.
8. `BLOCKED_CLEANUP_CONTRACT_DRIFT` — 11.
9. `STATE_RULE_PRECONDITION_NOT_ESTABLISHED` — 6.
10. `BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE` — 4.

The funnel reason registry now explicitly registers `CLEANUP_EVIDENCE_INCOMPLETE` and `CLEANUP_WRITE_COVERAGE_MISMATCH`. Candidate traces also bind the evaluator candidate policy identity from the immutable run context, not the product’s global active policy registry.

Evidence:

- Baseline: `D:\QF\d3\reports\policy-baseline-001.replay.json`
- Candidate: `D:\QF\d9\reports\policy-eval-8385734a62e5.replay.json`
- Machine-readable comparison: `discovery_funnel_external_diagnostic_comparison.json`

Commercial/generalization status remains blocked because this diagnostic has no held-out targets, no intentionally clean target, no three-industry held-out split, no cost baseline, and no paired replay/shadow promotion evidence.
