"""Check experiment execution results."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check experiment_execution results
exp_exec = v12.get("experiment_execution", {})
print(f"selected_count: {exp_exec.get('selected_count')}")
print(f"scheduled_count: {exp_exec.get('scheduled_count')}")
print(f"executed_count: {exp_exec.get('executed_count')}")
print(f"blocked_count: {exp_exec.get('blocked_count')}")
print(f"harness_failure_count: {exp_exec.get('harness_failure_count')}")

results = exp_exec.get("results", [])
print(f"\nresults count: {len(results)}")

# Check risk family distribution
from collections import Counter
families = Counter(r.get("risk_family", "?") for r in results)
print(f"Risk families in results: {dict(families)}")

# Check if any conservation experiments were executed
cons_results = [r for r in results if r.get("risk_family") == "conservation"]
print(f"\nConservation results: {len(cons_results)}")

# Check obligation_attempt_ledger for conservation
ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
cons_attempts = [a for a in attempts if a.get("risk_family") == "conservation"]
print(f"\nConservation attempts in ledger: {len(cons_attempts)}")

# Check the first conservation attempt details
if cons_attempts:
    a = cons_attempts[0]
    print(f"\nFirst conservation attempt:")
    print(f"  obligation_id: {a.get('obligation_id')}")
    print(f"  experiment_id: {a.get('experiment_id')}")
    print(f"  terminal_status: {a.get('terminal_status')}")
    print(f"  reason_code: {a.get('reason_code')}")
    print(f"  keys: {list(a.keys())[:20]}")
