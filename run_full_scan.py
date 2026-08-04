# -*- coding: utf-8 -*-
"""Run full scan with DB audit hook and evaluate."""
import sys, io, json, time, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")

# Let auto-scaling determine the budget from compiled pool size.
# Setting QUALIBUG_MAX_BEHAVIOR_SLICES_PER_ROUND bypasses auto-scaling
# and locks budget to campaign.slice_budget (capped at 150).
# Without it: budget = max(150, min(1200, compiled_pool_size)) = 1200.

from pathlib import Path

# The legacy ``private_pilot_db_audit_patch`` hook was retired (module-strangler
# cleanup 0ec57fd9): DB observation now flows through the governed
# persistence_observer -> assertion -> contract-oracle -> delivery-gate chain.

from ai_test_asset_center.scan_post_hooks import list_scan_post_hooks
print(f"Registered hooks: {list_scan_post_hooks()}")

# Run scan with proper parameters
from ai_test_asset_center.__main__ import scan

root = Path(r"d:\QualiBug-AI\QualiBug-AI-main")

campaign_context = {
    'target_id': 'benchmark_mall_v05',
    'environment_id': 'local_test',
    'scope_id': 'benchmark_mall_scope',
    'environment_ref': 'local_test_env',
    'environment_type': 'test',  # Required for write execution
    'execution_mode': 'approved_sandbox_write',
}

print("\nRunning scan...")
print(f"  base_url: http://localhost:8080")
print(f"  environment_type: test")
print(f"  execution_mode: approved_sandbox_write")

started = time.time()
result = scan(
    "benchmark_mall",
    root=root,
    base_url="http://localhost:8080",
    campaign_context=campaign_context,
    save_report=False,
)
elapsed = time.time() - started

# Check results
rc = result.get('runtime_contract', {})
print(f"\nScan completed in {elapsed:.1f}s")
print(f"  runtime_contract.status: {rc.get('status')}")
print(f"  runtime_contract.reason: {rc.get('reason', 'n/a')}")
print(f"  approved_base_url: {rc.get('approved_base_url', 'n/a')}")

findings = result.get('findings', [])
db_findings = result.get('db_findings', [])
confirmed = [f for f in findings if f.get('gate_passed') or f.get('confirmation_status') == 'confirmed']

print(f"\n  total findings: {len(findings)}")
print(f"  confirmed findings: {len(confirmed)}")
print(f"  db_findings: {len(db_findings)}")

# Show confirmed findings
if confirmed:
    print("\n  Confirmed findings:")
    for f in confirmed[:10]:
        print(f"    - {f.get('title', '?')[:70]}")

# Save for evaluation (repo-root hygiene: run artifacts live in .scratch/)
out_path = Path(".scratch/scan_fresh_result.json")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
print(f"\nSaved to {out_path}")

# Run evaluator
print("\n" + "=" * 60)
print("Running evaluator...")
print("=" * 60)

from benchmark_evaluator.benchmark_compute import compute_benchmark
gt_path = r"d:\QualiBug-AI\QualiBug-AI-main\_private_eval\_evaluator_private\benchmark_mall_131\bugs.json"

eval_result = compute_benchmark(
    project="benchmark_mall",
    findings=findings,
    root=root,
    ground_truth_path=gt_path,
)

print(f"\n=== EVALUATOR RESULTS ===")
print(f"  GT bugs: {eval_result.get('ground_truth_bug_count')}")
print(f"  Scan findings: {eval_result.get('scan_findings_total')}")
print(f"  TP: {eval_result.get('true_positives')}")
print(f"  FP: {eval_result.get('false_positives')}")
print(f"  Precision: {eval_result.get('precision')}")
print(f"  Recall: {eval_result.get('recall')}")

matched = eval_result.get("matched_bugs", [])
if matched:
    print(f"\n  Matched GT ({len(matched)}):")
    for m in matched:
        print(f"    - {m.get('gt_bug_id')}: {m.get('gt_title', '')[:50]} (score={m.get('match_score')})")
