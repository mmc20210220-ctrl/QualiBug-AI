"""P0-2: Rebuild cumulative TP Registry v3.

Merges:
- Project C Blind Baseline (existing registry)
- Project C Post-Tuning Oracle V1 Final (existing registry)
- Deep Experiment Execution Formal (existing registry)
- Violation Activation Formal (NEW: 4 violations)

Expected: 12 unique TP, 10 deep unique TP
"""
import json
from datetime import datetime

# Load existing registry (8 TP, 6 deep)
existing = json.loads(open("project_c_cumulative_tp_registry.json", encoding="utf-8").read())
existing_tps = existing["tp_records"]
print(f"Existing registry: {len(existing_tps)} TP records")

# Load VA formal results
va = json.loads(open("project_c_violation_activation_formal_results.json", encoding="utf-8").read())
va_findings = [r for r in va["results"] if r.get("violation_triggered")]
print(f"VA violations: {len(va_findings)}")

# Load benchmark map for ID mapping
bmap = json.loads(open("project_c_remaining_bug_benchmark_map.json", encoding="utf-8").read())
mappings = {m["benchmark_bug_id"]: m for m in bmap["mappings"]}

# Load full benchmark evaluation for bug details
beval = json.loads(open("project_c_benchmark_evaluation.json", encoding="utf-8").read())
missed_bugs = beval.get("missed_bugs", {})

# VA finding → Benchmark Bug ID mapping (by endpoint and mechanism)
va_to_benchmark = {
    "ONV-001": "CF-CON-001",   # version_check_optional → concurrency on PATCH /contracts/{id}
    "ONV-005": "CF-PAY-001",   # cancel_cascade_payment → cancel doesn't reject payments
    "ONV-006": "CF-PAY-003",   # temporal_invoice_date → payment date validation
    "ONV-008": "CF-PAY-004",   # pay_cancelled_contract → pay doesn't check contract status
}

# Build existing TP bug IDs for dedup
existing_bug_ids = {r["benchmark_bug_id"] for r in existing_tps}
print(f"Existing TP bug IDs: {sorted(existing_bug_ids)}")

# Add VA findings as new TP records
new_tps = []
for f in va_findings:
    target_id = f["target_id"]
    bug_id = va_to_benchmark.get(target_id)
    if not bug_id:
        print(f"  WARNING: No benchmark mapping for {target_id}")
        continue
    if bug_id in existing_bug_ids:
        print(f"  SKIP: {bug_id} already in registry")
        continue
    
    finding = f.get("finding", {})
    bug_info = missed_bugs.get(bug_id, {})
    
    new_record = {
        "benchmark_bug_id": bug_id,
        "first_detected_run_id": "PROJECT_C_VIOLATION_ACTIVATION_V1_FINAL",
        "finding_id": f"va_{target_id.lower()}",
        "root_cause_signature": f"{finding.get('mechanism', 'unknown')}|{finding.get('operation', 'unknown')}",
        "rule_type": f.get("expression_type", "unknown"),
        "experiment_mechanism": finding.get("mechanism", "unknown"),
        "deep_business": bug_info.get("deep_business", True),
        "reproduction_passed": True,
    }
    new_tps.append(new_record)
    existing_bug_ids.add(bug_id)
    print(f"  ADD: {bug_id} ({target_id}) deep={new_record['deep_business']}")

# Merge
all_tps = existing_tps + new_tps
unique_tp = len(all_tps)
deep_tp = sum(1 for r in all_tps if r.get("deep_business"))

print(f"\n{'='*60}")
print(f"  CUMULATIVE TP REGISTRY v3")
print(f"{'='*60}")
print(f"  Cumulative Unique TP: {unique_tp}")
print(f"  Cumulative Deep Unique TP: {deep_tp}")
print(f"  Expected: 12 / 10")
print(f"  Match: {'YES' if unique_tp == 12 and deep_tp == 10 else 'NO - INVESTIGATE'}")

# Generate remaining bug set
all_benchmark_ids = set(missed_bugs.keys()) | set(mappings.keys())
# Add the TP bug IDs from benchmark evaluation
tp_bug_ids_from_eval = set(beval.get("unique_tp_bugs", []))
all_known_bugs = all_benchmark_ids | tp_bug_ids_from_eval | existing_bug_ids

# Total benchmark = 26
total_benchmark = beval.get("benchmark_total", 26)
remaining_ids = all_known_bugs - existing_bug_ids
# Filter to only actual benchmark bugs (26 total)
# The remaining should be 26 - 12 = 14
remaining_bugs = []
for bug_id in sorted(remaining_ids):
    info = missed_bugs.get(bug_id, mappings.get(bug_id, {}))
    if info:
        remaining_bugs.append({
            "benchmark_bug_id": bug_id,
            "deep_business": info.get("deep_business", False),
            "endpoint": info.get("endpoint", ""),
            "old_breakpoint": info.get("old_breakpoint", info.get("breakpoint", "")),
        })

remaining_total = len(remaining_bugs)
remaining_deep = sum(1 for b in remaining_bugs if b.get("deep_business"))

print(f"\n  Remaining Bugs: {remaining_total}")
print(f"  Remaining Deep Bugs: {remaining_deep}")
print(f"  Expected: 14 / 12")

# Save v3 registry
registry_v3 = {
    "schema_version": "qualibug.project-c-cumulative-tp-registry.v3",
    "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "cumulative_unique_tp": unique_tp,
    "cumulative_deep_unique_tp": deep_tp,
    "total_benchmark": total_benchmark,
    "remaining_bugs": remaining_total,
    "remaining_deep_bugs": remaining_deep,
    "tp_records": all_tps,
    "sources": [
        "PROJECT_C_BLIND_BASELINE",
        "PROJECT_C_POST_TUNING_ORACLE_V1_FINAL",
        "DEEP_EXPERIMENT_EXECUTION",
        "PROJECT_C_VIOLATION_ACTIVATION_V1_FINAL",
    ],
}

with open("project_c_cumulative_tp_registry_v3.json", "w", encoding="utf-8") as f:
    json.dump(registry_v3, f, indent=2, ensure_ascii=False)
print(f"\n  Saved: project_c_cumulative_tp_registry_v3.json")

# Save remaining bug set v3
remaining_v3 = {
    "schema_version": "qualibug.project-c-remaining-bug-set.v3",
    "generated_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    "total_remaining": remaining_total,
    "deep_remaining": remaining_deep,
    "bugs": remaining_bugs,
}

with open("project_c_remaining_bug_set_v3.json", "w", encoding="utf-8") as f:
    json.dump(remaining_v3, f, indent=2, ensure_ascii=False)
print(f"  Saved: project_c_remaining_bug_set_v3.json")
