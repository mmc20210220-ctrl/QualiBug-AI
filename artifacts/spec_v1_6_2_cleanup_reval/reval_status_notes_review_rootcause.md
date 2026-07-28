# V1.6.2 cleanup review-rootcause reval status

- scan_id: scan_benchmark_mall_131_1785197078245
- campaign_id: CMP_e85f8b58ec3346a5a6c68796
- rerun_key: v162_cleanup_review_rootcause_reval_v1
- elapsed_ms: 490687
- Canonical selected/terminal: 1317/1317
- Unlock N=61 seen: **61**
- Unlock terminal: {'REJECTED': 61}
- Unlock reason: {'ORACLE_NOT_VIOLATED': 61}
- Cleanup contract: **{'COMPLETED': 54}** / {'<none>': 54}
- Finalization / TRUE_COMPLETED: **61 / 7**
- EQUIVALENT: **0** (mix={'NOT_EQUIVALENT': 54} reasons={'ENTITY_STILL_PRESENT_AFTER_CLEANUP': 54})
- vs V14: D=61 EQ=54 delta={'TRUE_COMPLETED': -54, 'EQUIVALENT': -54} verdict=**REGRESSION**
- Retained Unlock exec rows: **61/61** (missing_retention=0)

## Next single breakpoint

UNLOCK_CLEANUP_EQUIVALENCE_NOT_EQUIVALENT
