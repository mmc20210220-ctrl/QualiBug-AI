# -*- coding: utf-8 -*-
"""Run DB audit + merge with scan findings → evaluator measurement."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")

from pathlib import Path

# 1. Load existing scan result
scan_path = Path("scan_with_db_audit.json")
data = json.loads(scan_path.read_text(encoding="utf-8"))

findings = data.get("findings", [])
db_findings_old = data.get("db_findings", [])
v12 = data.get("v12", {})
behavior_ir = v12.get("behavior_ir", data.get("behavior_ir", {}))

print(f"Existing scan findings: {len(findings)}")
print(f"Old DB findings in file: {len(db_findings_old)}")
print(f"Has behavior_ir: {bool(behavior_ir)}")
print(f"runtime_contract: {data.get('runtime_contract', {}).get('status', '?')}")

# 2. Run fresh DB audit with multi-DB support
from ai_test_asset_center.db_state_audit import run_db_state_audit

dsn = "postgresql://postgres:postgres@localhost:5432/benchmark_mall"
fresh_db_findings = run_db_state_audit(behavior_ir, dsn)
print(f"\nFresh DB audit findings: {len(fresh_db_findings)}")
for f in fresh_db_findings:
    print(f"  - {f['title'][:80]}")

# 3. Merge: scan findings (without old db_findings) + fresh DB findings
# Remove old db_findings from the findings list to avoid duplicates
scan_only = [f for f in findings if f.get("evidence_source") != "db_state_audit"]
combined = scan_only + fresh_db_findings
print(f"\nScan-only findings: {len(scan_only)}")
print(f"Combined (scan + DB): {len(combined)}")

# 4. Save combined result for evaluator
result = dict(data)
result["findings"] = combined
result["db_findings"] = fresh_db_findings
result["total_findings"] = len(combined)

out_path = Path("eval_combined_result.json")
out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"\nSaved to {out_path}")

# 5. Run evaluator
print("\n" + "=" * 60)
print("Running evaluator...")
print("=" * 60)

try:
    from benchmark_evaluator.benchmark_compute import compute_benchmark
    gt_path = r"d:\QualiBug-AI\QualiBug-AI-main\_private_eval\_evaluator_private\benchmark_mall_131\bugs.json"
    eval_result = compute_benchmark(
        project="benchmark_mall",
        findings=combined,
        root=Path(r"d:\QualiBug-AI\QualiBug-AI-main"),
        ground_truth_path=gt_path,
    )
    print(f"\n=== EVALUATOR RESULTS ===")
    print(json.dumps({k: v for k, v in eval_result.items() if k != 'matches'}, ensure_ascii=False, indent=2, default=str))
    
    # Show matched GT IDs
    matches = eval_result.get("matches", [])
    if matches:
        print(f"\n  Matched GT ({len(matches)}):")
        for m in matches:
            gt_id = m.get('gt_id', m.get('id', '?'))
            score = m.get('score', '?')
            print(f"    - {gt_id} (score={score})")
except Exception as e:
    print(f"Evaluator error: {e}")
    import traceback
    traceback.print_exc()
