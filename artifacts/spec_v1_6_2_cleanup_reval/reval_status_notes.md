# V1.6.2 cleanup-equivalence reval status

- scan_id: scan_benchmark_mall_131_1785165636907
- campaign_id: CMP_88f1834f5884dbb974238430
- rerun_key: v162_cleanup_equivalence_reval_v14
- elapsed_ms: 163246
- Canonical selected/terminal: 1317/1317
- Unlock N=61 seen: **61**
- Unlock terminal: {'REJECTED': 61}
- Unlock reason: {'ORACLE_NOT_VIOLATED': 61}
- Conservation assertions: **{'PASS': 54}** / {'<none>': 54}
- entity_state: **{'OBSERVED:': 54}**
- Cleanup contract: **{'COMPLETED': 54}** / {'<none>': 54}
- Finalization / TRUE_COMPLETED (retained exec results): **61 / 61**
- EQUIVALENT: **54** (mix={'EQUIVALENT': 54} reasons={'<none>': 54})
- Retained Unlock exec rows: **61/61** (missing_retention=0)
- D>=49: **True**

## UNLOCK_FINALIZER_FOLLOW_ON_RESULT_DROPPED — CLOSED

experiment_execution.results merged only primary+round_two batches while executed_count/ledger included follow-on rounds. Finalizer TRUE_COMPLETED/EQUIVALENT lived on follow-on outcomes but were dropped from the retained projection, so Unlock D stayed at 8 despite cleanup COMPLETED 54. TRUE_COMPLETED is Spec §31 execution completeness (not a finding); PROPERTY_HELD may still be TRUE_COMPLETED with oracle_property_held.

## Next single breakpoint

UNLOCK_COVERAGE_D_GE_49
