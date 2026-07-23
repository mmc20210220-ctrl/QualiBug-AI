"""Check original result file."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
print(f"scan_id: {r.get('scan_id')}")
print(f"total_findings: {r.get('total_findings')}")
print(f"total_candidates: {r.get('total_candidates')}")

v12 = r.get("v12", {})
exp_exec = v12.get("experiment_execution", {})
print(f"experiment_execution results: {len(exp_exec.get('results', []))}")

ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
cons = [a for a in attempts if a.get("risk_family") == "conservation"]
print(f"conservation attempts: {len(cons)}")

# Check conservation attempt details
for a in cons[:3]:
    print(f"\n  {a.get('obligation_id')}: status={a.get('terminal_status')}, reason={a.get('reason_code')}")
    stages = a.get("stages", [])
    if isinstance(stages, list):
        for s in stages:
            if isinstance(s, dict):
                print(f"    {s.get('stage')}: {s.get('status')} {s.get('reason_code')}")

# Check experiment_execution results for conservation
exp_compile = v12.get("experiment_compile", {})
experiments = exp_compile.get("experiments", [])
cons_exps = [e for e in experiments if e.get("risk_family") == "conservation"]
cons_obl_ids = [e.get("obligation_id") for e in cons_exps]

results = exp_exec.get("results", [])
cons_in_results = [r for r in results if r.get("obligation_id") in cons_obl_ids]
print(f"\nConservation in experiment_execution.results: {len(cons_in_results)}")

for res in cons_in_results[:2]:
    print(f"\n  obligation_id: {res.get('obligation_id')}")
    print(f"  status: {res.get('status')}")
    print(f"  reason_code: {res.get('reason_code')}")
    print(f"  detail: {res.get('detail')}")
    verdict = res.get("oracle_verdict", {})
    print(f"  oracle_verdict: status={verdict.get('status')}, missing={verdict.get('missing_requirements')}")
