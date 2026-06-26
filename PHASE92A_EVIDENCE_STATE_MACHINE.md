# Phase92A Evidence State Machine

## Overview

This document describes the state machine for the evidence bridge between Runtime Probe Evidence and Business Evidence Contract in QualiBug Discovery Engine.

---

## Four-Layer State Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    FINAL REVIEW                                │
│  (VALIDATED_CANDIDATE | NEEDS_MORE_EVIDENCE | BLOCKED)     │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                BUSINESS EVIDENCE STATUS                       │
│  (VALIDATED | PENDING_ENTITY_BINDING | PENDING_SNAPSHOT...) │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│                  SEMANTIC VERDICT                             │
│  (SEMANTIC_CONFIRMED | SEMANTIC_FALSIFIED | ...)           │
└─────────────────────────┬───────────────────────────────────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│              RAW RUNTIME VERDICT                              │
│        (confirmed | falsified | execution_error | ...)      │
└─────────────────────────────────────────────────────────────┘
```

---

## State Definitions

### Layer 1: Raw Runtime Verdict
**Source**: Stage_verify original verdict

| Value | Meaning |
|-------|---------|
| `confirmed` | Finding confirmed by verifier |
| `falsified` | Finding proven false |
| `inconclusive` | Insufficient evidence |
| `execution_error` | Execution failed |
| `duplicate_mutation_observed` | Duplicate mutation detected |
| `auth_bypass_observed` | Auth bypass observed |

### Layer 2: Semantic Verdict
**Mapping**: Raw verdict → SEMANTIC_VERDICTS namespace

| Raw Verdict | Semantic Verdict |
|-------------|------------------|
| `confirmed` | `SEMANTIC_CONFIRMED` |
| `falsified` | `SEMANTIC_FALSIFIED` |
| `inconclusive` | `SEMANTIC_INCONCLUSIVE` |
| `execution_error` | `SEMANTIC_ERROR` |
| `needs_more_evidence` | `SEMANTIC_PENDING` |

**CRITICAL**: This layer is NEVER modified by gates. It preserves the truth of Stage_verify.

### Layer 3: Business Evidence Status
**Derived from**: Enrichment + Gate results

| Status | Conditions |
|--------|------------|
| `VALIDATED` | All required fields present |
| `PENDING_ENTITY_BINDING` | Missing entity_binding |
| `PENDING_BEFORE_SNAPSHOT` | Missing before_snapshot_ref |
| `PENDING_AFTER_SNAPSHOT` | Missing after_snapshot_ref |
| `PENDING_CLEANUP_EVIDENCE` | Cleanup status = PENDING |
| `PENDING_OBSERVER_CONSENSUS` | Observer conflict detected |
| `PENDING_ASYNC_WINDOW` | Async window open |
| `PENDING_REPRODUCTION` | Reproduction flow incomplete |
| `NOT_ENRICHED` | No enrichment performed |
| `BLOCKED_BY_RUNTIME_EVIDENCE` | Runtime gate failed |

### Layer 4: Final Review Status
**Derived from**: Business evidence status + semantic verdict

| Status | Conditions |
|--------|------------|
| `VALIDATED_CANDIDATE` | Business evidence validated, awaiting adversarial/schema |
| `NEEDS_MORE_EVIDENCE` | Business evidence incomplete |
| `PENDING_REVIEW` | Awaiting human review |
| `REJECTED_BY_COUNTERFACTUAL` | Adversarial validation rejected |
| `REJECTED_BY_SCHEMA` | Schema validation failed |
| `CONFIRMED_BY_HUMAN` | Human review approved |
| `BLOCKED` | Blocked by safety or cleanup failure |

---

## State Transition Rules

### Rule 1: Semantic Verdict Immutability
```
SEMANTIC_* → ANY other value is FORBIDDEN
```
Gates may NOT modify semantic_verdict. They can only check it.

### Rule 2: Pending Evidence Preserves Semantic Truth
```
If semantic_verdict == "SEMANTIC_CONFIRMED" AND business_evidence_status starts with "PENDING":
    final_review_status MUST be "NEEDS_MORE_EVIDENCE"
    NEVER "REJECTED", "FALSIFIED", or "INCONCLUSIVE"
```

### Rule 3: Runtime Gate Failure Does Not Reject
```
If runtime_gate_status == "FAILED":
    finding is BLOCKED_BY_RUNTIME_EVIDENCE
    semantic_verdict IS preserved
    not automatically rejected
```

### Rule 4: Business Gate Completeness Check
```
If business_gate_status == "PASSED":
    candidate can proceed to adversarial validation
Else:
    candidate enters NEEDS_MORE_EVIDENCE state
```

### Rule 5: Cleanup Blocks High-Risk Operations
```
If cleanup_status == "PENDING" OR cleanup_status == "FAILED":
    high-risk write operations are blocked
    but finding is NOT rejected
    remains as NEEDS_MORE_EVIDENCE
```

---

## Compound Status

When semantic confirmed but business evidence incomplete, a compound status is computed:

```python
if semantic_verdict == "SEMANTIC_CONFIRMED" and business_evidence_status.startswith("PENDING"):
    compound_status = "SEMANTIC_CONFIRMED_PENDING_EVIDENCE"
elif semantic_verdict == "SEMANTIC_CONFIRMED" and business_evidence_status == "VALIDATED":
    compound_status = "VALIDATED_CANDIDATE"
else:
    compound_status = final_review_status
```

---

## Example State Flow

### Case 1: Semantic Confirmed, Missing Entity Binding

```
Initial State:
  raw_runtime_verdict: "confirmed"
  semantic_verdict: "SEMANTIC_CONFIRMED"
  business_evidence_status: "PENDING_ENTITY_BINDING"
  final_review_status: "NEEDS_MORE_EVIDENCE"
  compound_status: "SEMANTIC_CONFIRMED_PENDING_EVIDENCE"

After Evidence Supplementation:
  raw_runtime_verdict: "confirmed"
  semantic_verdict: "SEMANTIC_CONFIRMED"
  business_evidence_status: "VALIDATED"
  final_review_status: "PENDING_REVIEW"
  compound_status: "VALIDATED_CANDIDATE"
```

### Case 2: Runtime Gate Failed

```
Initial State:
  raw_runtime_verdict: "confirmed"
  semantic_verdict: "SEMANTIC_CONFIRMED"
  business_evidence_status: "BLOCKED_BY_RUNTIME_EVIDENCE"
  final_review_status: "BLOCKED"
  compound_status: "BLOCKED"

Note: Semantic truth preserved, not rejected
```

### Case 3: Cleanup Failure on Write Operation

```
Initial State:
  raw_runtime_verdict: "confirmed"
  semantic_verdict: "SEMANTIC_CONFIRMED"
  cleanup_status: "PENDING"
  missing_requirements: ["CLEANUP_PENDING"]
  final_review_status: "NEEDS_MORE_EVIDENCE"

Result: Write operation blocked, finding preserved for supplementation
```

---

## State History Tracking

Each finding maintains a `state_history` list recording all transitions:

```json
{
  "state_history": [
    {
      "from": "PENDING_ENTITY_BINDING",
      "to": "VALIDATED",
      "reason": "entity_binding_completed",
      "timestamp": 1704067200.0
    },
    {
      "from": "VALIDATED",
      "to": "PENDING_REVIEW",
      "reason": "awaiting_human_review",
      "timestamp": 1704067260.0
    }
  ]
}
```

---

## Security Boundary States

| State | Action |
|-------|--------|
| `cleanup_status == "PENDING"` | Block high-risk replay |
| `cleanup_status == "FAILED"` | Block replay entirely |
| `raw_runtime_verdict == "confirmed"` | Still requires business evidence |
| `business_evidence_status == "VALIDATED"` | Requires adversarial + schema approval |

---

## Migration Path

Legacy findings without four-layer state are migrated:

```python
def migrate_legacy_finding(finding: dict) -> dict:
    """Convert old-format finding to four-layer state."""
    legacy = finding.get("evidence", {})
    return {
        "raw_runtime_verdict": legacy.get("verdict", "inconclusive"),
        "semantic_verdict": map_to_semantic(legacy.get("verdict")),
        "business_evidence_status": "LEGACY_EVIDENCE_INCOMPLETE",
        "final_review_status": "NEEDS_MORE_EVIDENCE",
        "missing_requirements": [],
    }
```

Marked as `LEGACY_EVIDENCE_INCOMPLETE` if migration fails.
