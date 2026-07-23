"""Check new scan conservation results."""
import json

r = json.load(open("project_c_post_tuning_result2.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check experiment_execution results
exp_exec = v12.get("experiment_execution", {})
results = exp_exec.get("results", [])
print(f"experiment_execution results: {len(results)}")

# Find conservation results
cons_results = [r for r in results if r.get("obligation_id", "").startswith("obl_") and "conservation" in str(r.get("risk_family", ""))]
# Also check by looking at the experiment_compile
exp_compile = v12.get("experiment_compile", {})
experiments = exp_compile.get("experiments", [])
cons_exps = [e for e in experiments if e.get("risk_family") == "conservation"]
cons_obl_ids = [e.get("obligation_id") for e in cons_exps]
print(f"Conservation experiments: {len(cons_exps)}")
print(f"Conservation obligation_ids: {cons_obl_ids[:5]}")

# Find conservation in results
cons_in_results = [r for r in results if r.get("obligation_id") in cons_obl_ids]
print(f"Conservation in results: {len(cons_in_results)}")

for res in cons_in_results[:3]:
    print(f"\n  obligation_id: {res.get('obligation_id')}")
    print(f"  status: {res.get('status')}")
    print(f"  reason_code: {res.get('reason_code')}")
    print(f"  detail: {res.get('detail')}")
    
    # Check oracle_verdict
    verdict = res.get("oracle_verdict", {})
    print(f"  oracle_verdict status: {verdict.get('status')}")
    print(f"  oracle_verdict missing_requirements: {verdict.get('missing_requirements')}")

# Check obligation_attempt_ledger for conservation
ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
cons_attempts = [a for a in attempts if a.get("risk_family") == "conservation"]
print(f"\nConservation attempts in ledger: {len(cons_attempts)}")
for a in cons_attempts[:3]:
    print(f"  - {a.get('obligation_id')}: status={a.get('terminal_status')}, reason={a.get('reason_code')}")
    stages = a.get("stages", [])
    if isinstance(stages, list):
        for s in stages:
            if isinstance(s, dict):
                print(f"      {s.get('stage')}: {s.get('status')} {s.get('reason_code')}")
