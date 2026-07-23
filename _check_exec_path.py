"""Check experiment execution path for conservation."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check experiment_execution results
exp_exec = v12.get("experiment_execution", {})
results = exp_exec.get("results", [])
print(f"experiment_execution results: {len(results)}")
for i, x in enumerate(results[:5]):
    print(f"  {i}: exp_id={x.get('experiment_id', '?')[:25]}, status={x.get('status')}, keys={list(x.keys())[:8]}")

# Check conservation attempt experiment_id
ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
cons_attempts = [a for a in attempts if a.get("risk_family") == "conservation"]

if cons_attempts:
    cons_exp_id = cons_attempts[0].get("experiment_id")
    print(f"\nConservation experiment_id: {cons_exp_id}")
    
    # Check if this experiment is in results
    found = [x for x in results if x.get("experiment_id") == cons_exp_id]
    print(f"Found in experiment_execution.results: {len(found)}")
    
    # Check experiment_compile for conservation
    exp_compile = v12.get("experiment_compile", {})
    compiled = exp_compile.get("compiled", [])
    print(f"\nexperiment_compile.compiled: {len(compiled)}")
    cons_compiled = [c for c in compiled if c.get("experiment_id") == cons_exp_id]
    print(f"Conservation in compiled: {len(cons_compiled)}")
    if cons_compiled:
        c = cons_compiled[0]
        print(f"  status: {c.get('status')}")
        print(f"  keys: {list(c.keys())[:15]}")
        # Check if it has execution_plan
        exec_plan = c.get("execution_plan", {})
        print(f"  execution_plan: {bool(exec_plan)}")
        if exec_plan:
            print(f"    protocol: {exec_plan.get('protocol')}")
            print(f"    steps: {len(exec_plan.get('steps', []))}")

# Check stages in conservation attempt
if cons_attempts:
    a = cons_attempts[0]
    stages = a.get("stages", [])
    print(f"\nConservation attempt stages type: {type(stages).__name__}")
    if isinstance(stages, list):
        print(f"  stages count: {len(stages)}")
        for s in stages[:5]:
            if isinstance(s, dict):
                print(f"    - {s.get('stage')}: status={s.get('status')}, reason={s.get('reason_code')}")
    elif isinstance(stages, dict):
        for stage_name, stage_data in stages.items():
            if isinstance(stage_data, dict):
                print(f"  {stage_name}: status={stage_data.get('status')}, reason={stage_data.get('reason_code')}")

# Check scenario execution for conservation
scenario_exec = v12.get("scenario_execution", {})
print(f"\nscenario_execution keys: {list(scenario_exec.keys())[:10]}")
scenarios = scenario_exec.get("scenarios", [])
print(f"scenarios count: {len(scenarios)}")
cons_scenarios = [s for s in scenarios if s.get("risk_family") == "conservation" or "conservation" in str(s.get("obligation_id", ""))]
print(f"conservation scenarios: {len(cons_scenarios)}")

# Check v12_legacy_scenario_exec path
legacy_exec = v12.get("legacy_scenario_execution", {})
print(f"\nlegacy_scenario_execution: {bool(legacy_exec)}")
if legacy_exec:
    print(f"  keys: {list(legacy_exec.keys())[:10]}")
