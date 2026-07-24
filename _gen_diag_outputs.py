# -*- coding: utf-8 -*-
"""Generate final diagnostic output files per SPEC §5, §12, §19"""
import json, sys, time
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(__file__).resolve().parent

# Load diagnosis results
diag = json.loads((ROOT / "project_c_remaining_bug_diagnosis.json").read_text(encoding="utf-8"))

# ═══════════════════════════════════════════
# §5: project_c_cumulative_tp_registry.json
# ═══════════════════════════════════════════
tp_registry = {
    "schema_version": "qualibug.project-c-cumulative-tp-registry.v1",
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "cumulative_unique_tp": 8,
    "cumulative_deep_unique_tp": 6,
    "tp_records": [
        {
            "benchmark_bug_id": "CF-DATA-002",
            "first_detected_run_id": "PROJECT_C_BLIND_BASELINE",
            "finding_id": "finding_e7dc78be4fd4983dbddf",
            "root_cause_signature": "data_visibility|GET /api/v1/reference/vendors",
            "rule_type": "permission",
            "experiment_mechanism": "data_visibility",
            "deep_business": False,
            "reproduction_passed": True,
        },
        {
            "benchmark_bug_id": "CF-PAY-005",
            "first_detected_run_id": "PROJECT_C_POST_TUNING_ORACLE_V1_FINAL",
            "finding_id": "F-dc1dfb49d31f",
            "root_cause_signature": "cross_entity_consistency|POST /api/v1/payment-requests",
            "rule_type": "LIMIT_CONSTRAINT",
            "experiment_mechanism": "limit_constraint",
            "deep_business": True,
            "reproduction_passed": True,
        },
        {
            "benchmark_bug_id": "CF-MIL-001",
            "first_detected_run_id": "PROJECT_C_POST_TUNING_ORACLE_V1_FINAL",
            "finding_id": "F-b8ccc0d230f7",
            "root_cause_signature": "temporal_constraint|POST /api/v1/contracts/{id}/milestones",
            "rule_type": "TEMPORAL",
            "experiment_mechanism": "temporal_constraint",
            "deep_business": True,
            "reproduction_passed": True,
        },
        {
            "benchmark_bug_id": "CF-CON-002",
            "first_detected_run_id": "DEEP_EXPERIMENT_EXECUTION",
            "finding_id": "finding_aed3dac2cca0f8fbbec3",
            "root_cause_signature": "validation|GET /api/v1/contracts/{id}",
            "rule_type": "precondition",
            "experiment_mechanism": "validation",
            "deep_business": True,
            "reproduction_passed": True,
        },
        {
            "benchmark_bug_id": "CF-DATA-001",
            "first_detected_run_id": "DEEP_EXPERIMENT_EXECUTION",
            "finding_id": "finding_9a5621d1805783f3b6fd",
            "root_cause_signature": "state|GET /api/v1/contracts/{id}/vendor-view",
            "rule_type": "data_visibility",
            "experiment_mechanism": "state",
            "deep_business": False,
            "reproduction_passed": True,
        },
        {
            "benchmark_bug_id": "CF-MIL-002",
            "first_detected_run_id": "DEEP_EXPERIMENT_EXECUTION",
            "finding_id": "finding_5dd2e357bfc554a56e58",
            "root_cause_signature": "idempotency|POST /api/v1/milestones/{id}/accept",
            "rule_type": "idempotency",
            "experiment_mechanism": "idempotency",
            "deep_business": True,
            "reproduction_passed": True,
        },
        {
            "benchmark_bug_id": "CF-STATE-003",
            "first_detected_run_id": "DEEP_EXPERIMENT_EXECUTION",
            "finding_id": "finding_87ce499efd1c50d93f2e",
            "root_cause_signature": "idempotency|POST /api/v1/payment-requests/{id}/finance-approve",
            "rule_type": "state_transition",
            "experiment_mechanism": "idempotency",
            "deep_business": True,
            "reproduction_passed": True,
        },
        {
            "benchmark_bug_id": "CF-IDEM-001",
            "first_detected_run_id": "DEEP_EXPERIMENT_EXECUTION",
            "finding_id": "finding_19f959e119e0b9cc3aed",
            "root_cause_signature": "idempotency|POST /api/v1/payment-requests/{id}/pay",
            "rule_type": "idempotency",
            "experiment_mechanism": "idempotency",
            "deep_business": True,
            "reproduction_passed": True,
        },
    ],
}
(ROOT / "project_c_cumulative_tp_registry.json").write_text(
    json.dumps(tp_registry, indent=2, ensure_ascii=False), encoding="utf-8")
print("[SAVED] project_c_cumulative_tp_registry.json")

# ═══════════════════════════════════════════
# §5: project_c_remaining_bug_set.json
# ═══════════════════════════════════════════
remaining_set = {
    "schema_version": "qualibug.project-c-remaining-bug-set.v1",
    "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "total_remaining": 18,
    "deep_remaining": 16,
    "bugs": [d for d in diag["diagnoses"]],
}
(ROOT / "project_c_remaining_bug_set.json").write_text(
    json.dumps(remaining_set, indent=2, ensure_ascii=False), encoding="utf-8")
print("[SAVED] project_c_remaining_bug_set.json")

# ═══════════════════════════════════════════
# §12: project_c_remaining_bug_trace_map.json (production side only)
# ═══════════════════════════════════════════
trace_map = {
    "schema_version": "qualibug.remaining-bug-trace-map.v1",
    "note": "Production-side trace only. Benchmark bug IDs NOT included here.",
    "traces": []
}
for d in diag["diagnoses"]:
    trace_map["traces"].append({
        "endpoint": d["endpoint"],
        "category": d["benchmark_category"],
        "rule_generated": d["pipeline_status"]["rule_generated"],
        "rule_count": d["pipeline_status"]["rule_count"],
        "oracle_type": d["pipeline_status"]["oracle_type"],
        "oracle_compiled": d["pipeline_status"]["oracle_compiled"],
        "oracle_violated": d["pipeline_status"]["oracle_violated"],
        "experiment_executed": d["pipeline_status"]["experiment_executed"],
        "findings_on_endpoint": d["pipeline_status"]["findings_on_endpoint"],
        "primary_breakpoint": d["primary_breakpoint"],
    })
(ROOT / "project_c_remaining_bug_trace_map.json").write_text(
    json.dumps(trace_map, indent=2, ensure_ascii=False), encoding="utf-8")
print("[SAVED] project_c_remaining_bug_trace_map.json")

# ═══════════════════════════════════════════
# §12: project_c_remaining_bug_benchmark_map.json (evaluator-private)
# ═══════════════════════════════════════════
benchmark_map = {
    "schema_version": "qualibug.remaining-bug-benchmark-map.v1",
    "note": "Evaluator-private. Benchmark bug IDs mapped to production trace. NEVER feed back into production.",
    "mappings": []
}
for d in diag["diagnoses"]:
    benchmark_map["mappings"].append({
        "benchmark_bug_id": d["benchmark_bug_id"],
        "title": d["title"],
        "deep_business": d["deep_business"],
        "endpoint": d["endpoint"],
        "old_breakpoint": d["old_breakpoint"],
        "new_primary_breakpoint": d["primary_breakpoint"],
        "secondary_breakpoint": d["secondary_breakpoint"],
        "confidence": d["confidence"],
    })
(ROOT / "project_c_remaining_bug_benchmark_map.json").write_text(
    json.dumps(benchmark_map, indent=2, ensure_ascii=False), encoding="utf-8")
print("[SAVED] project_c_remaining_bug_benchmark_map.json")

print("\n[DONE] All output files generated.")
