"""Check conservation experiment execution result."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check experiment_execution results
exp_exec = v12.get("experiment_execution", {})
results = exp_exec.get("results", [])

# Find conservation result
cons_results = [r for r in results if r.get("obligation_id") == "obl_0413ca640355cba0d746"]
print(f"Conservation results: {len(cons_results)}")

if cons_results:
    res = cons_results[0]
    print(f"\nstatus: {res.get('status')}")
    print(f"reason_code: {res.get('reason_code')}")
    print(f"detail: {res.get('detail')}")
    
    # Check oracle_verdict
    verdict = res.get("oracle_verdict", {})
    print(f"\noracle_verdict:")
    print(f"  status: {verdict.get('status')}")
    print(f"  verdict: {verdict.get('verdict')}")
    print(f"  missing_requirements: {verdict.get('missing_requirements')}")
    print(f"  failed_assertions: {len(verdict.get('failed_assertions', []))}")
    
    # Check observer_receipts
    observer_receipts = res.get("observer_receipts", [])
    print(f"\nobserver_receipts: {len(observer_receipts)}")
    for i, obs in enumerate(observer_receipts[:5]):
        print(f"  [{i}] type={obs.get('observer_type')}, status={obs.get('status')}")
    
    # Check steps
    steps = res.get("steps", [])
    print(f"\nsteps: {len(steps)}")
    for i, step in enumerate(steps[:3]):
        print(f"  [{i}] phase={step.get('phase')}, status={step.get('status')}, method={step.get('method')}, path={step.get('path', '')[:50]}")
    
    # Check execution_receipt
    exec_receipt = res.get("execution_receipt", {})
    print(f"\nexecution_receipt:")
    print(f"  status: {exec_receipt.get('status')}")
    print(f"  reason_code: {exec_receipt.get('reason_code')}")
    print(f"  detail: {exec_receipt.get('detail')}")
