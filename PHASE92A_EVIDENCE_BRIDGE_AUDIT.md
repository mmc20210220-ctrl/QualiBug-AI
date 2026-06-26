# Phase92A Evidence Bridge Audit Report

## Executive Summary

This audit documents the fix for the critical evidence contract bridge between Runtime Probe Evidence and Business Evidence Contract in the QualiBug Discovery Engine.

### Problem Statement

The original gate implementation had a critical flaw:
- **Stage_verify** produced correct semantic verdicts (e.g., `SEMANTIC_CONFIRMED`)
- **Gate** only accepted business contract evidence (entity_binding, before_snapshot_ref, cleanup, etc.)
- When business evidence was incomplete, semantic verdicts were incorrectly downgraded to `REJECTED`, `FALSIFIED`, or `INCONCLUSIVE`
- This caused valid semantic findings to be silently dropped

### Solution

Implemented a four-layer state preservation system:

```
Raw Runtime Verdict (Stage_verify original)
    ↓ preserved
Semantic Verdict (mapped to SEMANTIC_VERDICTS)
    ↓ preserved
Business Evidence Status (PENDING_* if incomplete)
    ↓ never rejected
Final Review Status (NEEDS_MORE_EVIDENCE if incomplete)
```

---

## 1. Original Runtime Evidence → Business Contract Field Mapping

| Runtime Probe Field | Business Contract Field | Extraction Source |
|---------------------|------------------------|-------------------|
| `call.method` | `method` | First meaningful call |
| `call.path` | `resource_path` | First meaningful call |
| `response.body.data.id` | `entity_binding.entity_id` | Response body extraction |
| `response.body.data.code` | `entity_binding.entity_alias` | Response body extraction |
| `path[0]` (e.g., /api/materials) | `entity_binding.entity_type` | Path segment extraction |
| `calls[0].results.admin.body` | `before_snapshot_ref` | First call body digest |
| `calls[1].call` | `action_evidence_ref` | Second call reference |
| `calls[-1].results.admin.body` | `after_snapshot_ref` | Last call body digest |
| `calls[*].results.{admin,viewer,no_auth}` | `observer_refs` | All observer results |
| `calls[1].method` (POST/PUT/DELETE) | `cleanup.status = PENDING` | Action method detection |

---

## 2. Why Old Gate Swallowed Stage_verify Verdict

### Root Cause Analysis

**File**: `ai_test_asset_center/discovery_finding_gate.py` (original)

```python
# OLD CODE (BUGGY):
if not contract:
    finding.verdict = "NEEDS_MORE_EVIDENCE"  # Overwrites semantic verdict!
    
# ...
gate_verdict = contract.get("verdict", "NEEDS_MORE_EVIDENCE").upper()
gated_mapped = GATED_VERDICTS.get(gate_verdict, "needs_more_evidence")
finding.verdict = gated_mapped  # BLINDLY OVERWRITES SEMANTIC VERDICT
```

### Problems Identified

1. **No distinction between semantic verdict and business evidence status**
2. **Gate verdict directly overwrote finding verdict**
3. **Missing business evidence → automatic downgrade to `needs_more_evidence` or `rejected`**
4. **No preservation of original Stage_verify truth**

---

## 3. Fixed State Machine

### Before Fix
```
Stage_verify = SEMANTIC_CONFIRMED
    ↓
Gate checks business evidence
    ↓
Business evidence incomplete
    ↓
finding.verdict = "needs_more_evidence"  ← SEMANTIC TRUTH LOST
```

### After Fix
```
Stage_verify = confirmed
    ↓
EvidenceNormalizer extracts semantic_verdict = SEMANTIC_CONFIRMED
    ↓
BusinessEvidenceEnricher computes business_evidence_status = PENDING_ENTITY_BINDING
    ↓
RuntimeEvidenceGate checks traceability → PASSED
    ↓
BusinessEvidenceGate checks completeness → PENDING (missing entity_binding)
    ↓
Four-layer state preserved:
  - raw_runtime_verdict: "confirmed"
  - semantic_verdict: "SEMANTIC_CONFIRMED"
  - business_evidence_status: "PENDING_ENTITY_BINDING"
  - final_review_status: "NEEDS_MORE_EVIDENCE"
    ↓
Finding is NOT rejected, awaits evidence supplementation
```

---

## 4. All Finding Entry Points Integration Matrix

| Entry Point | File | Integration Status | Evidence Bridge Used |
|-------------|------|-------------------|---------------------|
| Discovery Engine Main | `discovery_engine.py` | ✅ Fixed | `normalize_finding_evidence` + `enrich_finding_evidence` + `gate_discovery_findings` |
| Stage_execute | `discovery_engine.py` | ✅ Fixed | Same pipeline |
| Stage_verify | `discovery_engine.py` | ✅ Fixed | Same pipeline |
| Agent Business Flow Orchestrator | `agent_business_flow_orchestrator.py` | ✅ Delegates to discovery_engine | Inherited |
| Risk Based Probe Planner | `risk_based_probe_planner.py` | ✅ Enhanced | Added `prioritize_evidence_supplementation` |
| Regression Replay | `replay_evidence_sandbox.py` | ✅ Uses gate | Inherited |
| Self-Improving Loop | `self_improving_loop.py` | ✅ Uses discovery_engine | Inherited |
| Manual/CLI Triggered | `aitestops/cli.py` | ✅ Uses gate | Inherited |

---

## 5. Real Case: Semantic Confirmed → Pending Evidence → Validated Candidate

### Test Case: `test_missing_entity_binding_becomes_pending_evidence_not_rejected`

**Input**:
- Finding with `verdict = "confirmed"`
- Single GET call with no extractable entity ID

**Processing**:
1. `normalize_finding_evidence()` extracts semantic verdict
2. `enrich_finding_evidence()` computes missing requirements
3. Gate determines `business_evidence_status = PENDING_ENTITY_BINDING`

**Output**:
```json
{
  "raw_runtime_verdict": "confirmed",
  "semantic_verdict": "SEMANTIC_CONFIRMED",
  "business_evidence_status": "PENDING_ENTITY_BINDING",
  "final_review_status": "NEEDS_MORE_EVIDENCE",
  "compound_status": "SEMANTIC_CONFIRMED_PENDING_EVIDENCE",
  "missing_requirements": ["ENTITY_BINDING_MISSING", "BEFORE_SNAPSHOT_MISSING", "AFTER_SNAPSHOT_MISSING"]
}
```

**Result**: Semantic truth preserved, NOT rejected.

---

## 6. Real Case: Missing Cleanup Blocks High-Risk but Candidate Preserved

### Test Case: `test_missing_cleanup_blocks_high_risk_but_preserves_candidate`

**Input**:
- Finding with `verdict = "confirmed"`
- 3-step call chain: GET → POST → GET
- Action is POST (write operation)

**Processing**:
1. Normalizer detects POST as action method
2. Enricher sets `cleanup_status = PENDING`
3. Gate adds `CLEANUP_PENDING` to missing requirements

**Output**:
```json
{
  "semantic_verdict": "SEMANTIC_CONFIRMED",
  "cleanup_status": "PENDING",
  "final_review_status": "NEEDS_MORE_EVIDENCE",
  "missing_requirements": ["CLEANUP_PENDING"]
}
```

**Result**: Write operation blocked from confirmation, but finding preserved for evidence supplementation.

---

## 7. Test Results

```
tests/test_phase92a_evidence_bridge.py

24 tests collected, 24 passed in 0.29s

PASSED test_semantic_confirmed_runtime_evidence_not_dropped
PASSED test_probe_calls_normalize_to_business_evidence_refs
PASSED test_stage_verify_verdict_preserved_after_gate
PASSED test_missing_entity_binding_becomes_pending_evidence_not_rejected
PASSED test_missing_before_after_snapshot_becomes_pending_not_falsified
PASSED test_missing_cleanup_blocks_high_risk_but_preserves_candidate
PASSED test_runtime_gate_rejects_untraceable_probe_evidence
PASSED test_business_gate_requires_contract_for_validated_candidate
PASSED test_valid_enriched_evidence_reaches_adversarial_gate
PASSED test_all_discovery_paths_use_evidence_bridge
PASSED test_registry_records_raw_semantic_business_final_states
PASSED test_legacy_registry_migration_preserves_findings
PASSED test_pending_evidence_creates_frontier_follow_up
PASSED test_observer_conflict_blocks_confirmation_not_candidate_retention
PASSED test_async_window_pending_not_false_positive
PASSED test_entity_tenant_owner_binding_isolation
PASSED test_evidence_enricher_never_fabricates_snapshot_or_cleanup
PASSED test_production_zero_http_requests_with_evidence_bridge
PASSED test_cleanup_failure_blocks_replay_of_high_risk_flow
PASSED test_raw_probe_evidence_preserves_original_data
PASSED test_business_finding_contract_state_transitions
PASSED test_sourced_value_tracking
PASSED test_normalizer_handles_none_entity_safely
PASSED test_semantic_verdict_mapping
```

---

## 8. Production Gate Results

- **HTTP Request Count**: 0 (all operations are in-memory transformations)
- **No external API calls** in evidence bridge pipeline
- **No LLM calls** without explicit enable_llm_disprover flag

---

## 9. Known External Limitations

1. **LLM Disprover**: Requires explicit `enable_llm_disprover=True` and valid LLM configuration
2. **Sandbox Execution**: Real MES BugLab execution requires running sandbox environment
3. **Human Review**: Validated candidates require manual human review for final confirmation

---

## 10. Files Modified

| File | Changes |
|------|---------|
| `ai_test_asset_center/evidence_models.py` | **NEW** - Created with all Phase92A models |
| `ai_test_asset_center/evidence_normalizer.py` | Updated to preserve semantic verdict |
| `ai_test_asset_center/business_evidence_enricher.py` | Updated for four-layer state and cleanup detection |
| `ai_test_asset_center/discovery_finding_gate.py` | **REWRITTEN** with Runtime Gate + Business Gate split |
| `ai_test_asset_center/discovery_engine.py` | Updated evidence bridge integration |
| `ai_test_asset_center/business_finding_registry.py` | Updated for four-layer state preservation |
| `ai_test_asset_center/risk_based_probe_planner.py` | Added evidence supplementation strategies |
| `tests/test_phase92a_evidence_bridge.py` | **NEW** - 24 comprehensive tests |

---

## Conclusion

Phase92A successfully fixes the evidence bridge断层 by:

1. ✅ Preserving Stage_verify semantic verdict through all gates
2. ✅ Separating Runtime Gate (traceability) from Business Gate (completeness)
3. ✅ Converting missing business evidence to PENDING_* states, not REJECTED
4. ✅ Adding evidence supplementation strategy to Risk Frontier
5. ✅ Maintaining all security boundaries (no production HTTP, cleanup blocking)
6. ✅ Supporting legacy finding migration

**COMPLETED_PHASE92A**
