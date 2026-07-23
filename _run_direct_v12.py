"""Direct v12 pipeline test for conservation observer."""
import json
import sys
import time
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from ai_test_asset_center.v12_pipeline import run_v12_pipeline

root = Path("d:/QualiBug-AI/QualiBug-AI-main/.tmp_single_ecommerce_suite")
project = "qb_ecommerce_single_retest"

print(f"Starting v12 pipeline at {time.strftime('%H:%M:%S')}...")
print(f"Root: {root}")
print(f"Project: {project}")
sys.stdout.flush()

start = time.time()
try:
    result = run_v12_pipeline(
        project=project,
        root=root,
        base_url="http://localhost:8000",
    )
    elapsed = time.time() - start
    print(f"\nPipeline completed in {elapsed:.1f}s")
    print(f"Result keys: {list(result.keys())[:15]}")
    
    # Check conservation experiments
    exp_exec = result.get("experiment_execution", {})
    results = exp_exec.get("results", [])
    print(f"\nexperiment_execution results: {len(results)}")
    
    # Find conservation results
    exp_compile = result.get("experiment_compile", {})
    experiments = exp_compile.get("experiments", [])
    cons_exps = [e for e in experiments if e.get("risk_family") == "conservation"]
    cons_obl_ids = [e.get("obligation_id") for e in cons_exps]
    print(f"Conservation experiments: {len(cons_exps)}")
    
    cons_in_results = [r for r in results if r.get("obligation_id") in cons_obl_ids]
    print(f"Conservation in results: {len(cons_in_results)}")
    
    for res in cons_in_results[:2]:
        print(f"\n  obligation_id: {res.get('obligation_id')}")
        print(f"  status: {res.get('status')}")
        print(f"  reason_code: {res.get('reason_code')}")
        verdict = res.get("oracle_verdict", {})
        print(f"  oracle_verdict: status={verdict.get('status')}, missing={verdict.get('missing_requirements')}")
    
    # Save result
    with open("project_c_direct_result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, default=str)
    print(f"\nSaved to project_c_direct_result.json")
    
except Exception as e:
    print(f"\nError: {e}")
    import traceback
    traceback.print_exc()
