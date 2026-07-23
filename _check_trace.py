"""Check related entity observer execution trace."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check obligation attempt ledger for conservation attempts
ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
cons_attempts = [a for a in attempts if a.get("risk_family") == "conservation"]

print(f"Conservation attempts: {len(cons_attempts)}")

for i, attempt in enumerate(cons_attempts[:3]):
    print(f"\n--- Attempt {i+1}: {attempt.get('obligation_id')} ---")
    print(f"  Status: {attempt.get('terminal_status')}")
    print(f"  Reason: {attempt.get('reason_code')}")
    
    # Check execution trace
    exec_trace = attempt.get("execution_trace", {})
    if exec_trace:
        print(f"  Execution trace keys: {list(exec_trace.keys())[:10]}")
        
        # Check for related entity observations
        rel_obs = exec_trace.get("related_entity_observations", [])
        print(f"  Related entity observations: {len(rel_obs)}")
        for obs in rel_obs[:2]:
            print(f"    - {obs.get('entity_alias')}: status={obs.get('status')}, records={len(obs.get('records', []))}")
        
        # Check for related entity blockers
        rel_blockers = exec_trace.get("related_entity_blockers", [])
        print(f"  Related entity blockers: {len(rel_blockers)}")
        for b in rel_blockers[:2]:
            print(f"    - {b.get('entity_alias')}: {b.get('reason')}")
        
        # Check for related entity trace
        rel_trace = exec_trace.get("related_entity_trace", [])
        print(f"  Related entity trace: {len(rel_trace)}")
        for t in rel_trace[:2]:
            print(f"    - {t.get('entity_alias')}: status={t.get('status')}, candidates={t.get('candidates_found')}")

# Also check experiment execution results
exp_exec = v12.get("experiment_execution", {})
if exp_exec:
    results = exp_exec.get("results", [])
    cons_results = [r for r in results if r.get("risk_family") == "conservation"]
    print(f"\n\nExperiment execution results (conservation): {len(cons_results)}")
    for res in cons_results[:2]:
        print(f"  - {res.get('experiment_id')}: status={res.get('status')}")
        obs = res.get("observations", {})
        if obs:
            print(f"    observations keys: {list(obs.keys())[:10]}")
            rel_obs = obs.get("related_entity_observations", [])
            print(f"    related_entity_observations: {len(rel_obs)}")
