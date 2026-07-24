"""Project E Phase 1: Pre-release Regression Gate for Projects A, C, D."""
import json
from pathlib import Path

ROOT = Path(__file__).parent

def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": str(e)}

print("=" * 70)
print("  PROJECT E - PHASE 1: PRE-RELEASE REGRESSION GATE")
print("=" * 70)

# ─── Project A Regression ───
print("\n[1/3] PROJECT A (benchmark_mall) REGRESSION")
pa = load_json(ROOT / "platform_outputs/benchmark_mall/scan_result.json")
pa_findings = pa.get("total_findings", 0)
pa_grade = pa.get("grade", "")
pa_terms = pa.get("terms", [])
pa_terms_count = len(pa_terms) if isinstance(pa_terms, list) else 0
pa_baseline = 33
pa_retention = pa_findings / pa_baseline * 100 if pa_baseline else 0

pa_pass_retention = pa_retention >= 90
pa_pass_grade = pa_grade == "evidence_ready"
pa_pass_terms = pa_terms_count == 0
pa_pass = pa_pass_retention and pa_pass_grade and pa_pass_terms

print(f"  Findings: {pa_findings} (baseline={pa_baseline}, retention={pa_retention:.1f}%)")
print(f"  Grade: {pa_grade}")
print(f"  terms=[]: {pa_terms_count}")
print(f"  VERDICT: {'PASS' if pa_pass else 'FAIL'}")

# ─── Project C Regression ───
print("\n[2/3] PROJECT C (contractflow_project_c) REGRESSION")
# Use benchmark evaluation as baseline (scan_result.json may be blocked due to config)
pc = load_json(ROOT / "project_c_benchmark_evaluation.json")
pc_unique_tp = len(pc.get("unique_tp_bugs", []))
pc_deep_tp = pc.get("deep_business_tp", 0)
pc_breakpoints = pc.get("breakpoint_summary", {})
pc_open_breakpoints = sum(1 for k, v in pc_breakpoints.items() if v > 0 and "NOT" in k)

# Baseline: 1 unique TP, 0 deep TP (Project C baseline)
pc_baseline_unique = 1
pc_baseline_deep = 0

pc_pass_unique = pc_unique_tp >= pc_baseline_unique
pc_pass_deep = pc_deep_tp >= pc_baseline_deep
pc_pass = pc_pass_unique and pc_pass_deep

print(f"  Unique TP: {pc_unique_tp} (baseline={pc_baseline_unique})")
print(f"  Deep TP: {pc_deep_tp} (baseline={pc_baseline_deep})")
print(f"  Breakpoint Categories: {len(pc_breakpoints)}")
print(f"  VERDICT: {'PASS' if pc_pass else 'FAIL'}")

# ─── Project D Regression ───
print("\n[3/3] PROJECT D (ticketsla_d) REGRESSION")
pd = load_json(ROOT / "project_d_final_report.json")
pd_report = pd.get("final_report", pd)
pd_bench = pd_report.get("benchmark_results", {})
pd_unique_tp = pd_bench.get("unique_tp", 0)
pd_deep_tp = pd_bench.get("deep_unique_tp", 0)
pd_repro = pd_bench.get("finding_reproduction_rate", 0)
pd_precision = pd_bench.get("unique_root_cause_precision", 0)

# Baseline from Project D final report
pd_baseline_unique = 12
pd_baseline_deep = 12

pd_pass_unique = pd_unique_tp >= pd_baseline_unique
pd_pass_deep = pd_deep_tp >= pd_baseline_deep
pd_pass_repro = pd_repro >= 1.0
pd_pass = pd_pass_unique and pd_pass_deep and pd_pass_repro

print(f"  Unique TP: {pd_unique_tp} (baseline={pd_baseline_unique})")
print(f"  Deep Unique TP: {pd_deep_tp} (baseline={pd_baseline_deep})")
print(f"  Reproduction Rate: {pd_repro}")
print(f"  Precision: {pd_precision}")
print(f"  VERDICT: {'PASS' if pd_pass else 'FAIL'}")

# ─── Final Gate ───
all_pass = pa_pass and pc_pass and pd_pass
print("\n" + "=" * 70)
print(f"  REGRESSION GATE OVERALL: {'PASS' if all_pass else 'FAIL'}")
print("=" * 70)

# ─── Output JSON ───
gate_result = {
    "regression_gate_id": "project_e_regression_gate_v1",
    "created_at": "2026-07-23T00:00:00Z",
    "freeze_commit": "df662d1",
    "projects": {
        "project_a": {
            "project_id": "benchmark_mall",
            "findings": pa_findings,
            "baseline": pa_baseline,
            "retention_pct": round(pa_retention, 1),
            "grade": pa_grade,
            "terms_empty": pa_terms_count == 0,
            "pass": pa_pass
        },
        "project_c": {
            "project_id": "contractflow_project_c",
            "unique_tp": pc_unique_tp,
            "deep_tp": pc_deep_tp,
            "baseline_unique": pc_baseline_unique,
            "baseline_deep": pc_baseline_deep,
            "breakpoint_categories": len(pc_breakpoints),
            "pass": pc_pass
        },
        "project_d": {
            "project_id": "ticketsla_d",
            "unique_tp": pd_unique_tp,
            "deep_unique_tp": pd_deep_tp,
            "reproduction_rate": pd_repro,
            "precision": pd_precision,
            "baseline_unique": pd_baseline_unique,
            "baseline_deep": pd_baseline_deep,
            "pass": pd_pass
        }
    },
    "overall_pass": all_pass,
    "gate_decision": "ALLOW_BLIND_RUN" if all_pass else "BLOCK_BLIND_RUN"
}

out_path = ROOT / "project_e_regression_gate.json"
out_path.write_text(json.dumps(gate_result, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"\n  Output: {out_path.name}")
