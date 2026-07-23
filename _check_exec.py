"""Check experiment execution structure."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check experiment_execution structure
exp_exec = v12.get("experiment_execution", {})
print(f"experiment_execution keys: {list(exp_exec.keys())[:15]}")

# Check all_experiments in experiment_compile
ec = v12.get("experiment_compile", {})
exps = ec.get("all_experiments", [])
print(f"\nall_experiments count: {len(exps)}")

# Check experiment status distribution
from collections import Counter
statuses = Counter(e.get("status", "?") for e in exps)
print(f"Experiment statuses: {dict(statuses)}")

# Check conservation experiments
cons_exps = [e for e in exps if e.get("risk_family") == "conservation"]
print(f"\nConservation experiments: {len(cons_exps)}")
for e in cons_exps[:3]:
    print(f"  - {e.get('experiment_id')}: status={e.get('status')}, blocked_reason={e.get('blocked_reason', '')}")

# Check if there's a separate execution results section
exec_results = v12.get("execution_results", [])
print(f"\nexecution_results count: {len(exec_results)}")

# Check mainline_run
mainline = v12.get("mainline_run", {})
print(f"\nmainline_run keys: {list(mainline.keys())[:15]}")
