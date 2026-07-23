"""Check the selected conservation experiment."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check experiment_compile
exp_compile = v12.get("experiment_compile", {})
agent_intent_plan = exp_compile.get("agent_intent_plan", {})
intents = agent_intent_plan.get("intents", [])
obligation_plan = exp_compile.get("obligation_plan", {})
selected = obligation_plan.get("selected", [])

# Get conservation obligation_ids
ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
cons_attempts = [a for a in attempts if a.get("risk_family") == "conservation"]
cons_obl_ids = [a.get("obligation_id") for a in cons_attempts]

# Find conservation in intents
cons_in_intents = [i for i in intents if i.get("obligation_id") in cons_obl_ids]
print(f"Conservation in intents: {len(cons_in_intents)}")
if cons_in_intents:
    intent = cons_in_intents[0]
    print(f"  obligation_id: {intent.get('obligation_id')}")
    print(f"  intent_id: {intent.get('intent_id')}")
    print(f"  execution_adapters: {intent.get('execution_adapters')}")

# Find conservation in selected
cons_in_selected = [s for s in selected if s.get("obligation_id") in cons_obl_ids]
print(f"\nConservation in selected: {len(cons_in_selected)}")
if cons_in_selected:
    sel = cons_in_selected[0]
    print(f"  obligation_id: {sel.get('obligation_id')}")

# Check experiment_execution results for this conservation
exp_exec = v12.get("experiment_execution", {})
results = exp_exec.get("results", [])
if cons_in_intents:
    cons_obl_id = cons_in_intents[0].get("obligation_id")
    cons_results = [r for r in results if r.get("obligation_id") == cons_obl_id]
    print(f"\nConservation in experiment_execution.results: {len(cons_results)}")
    if cons_results:
        res = cons_results[0]
        print(f"  status: {res.get('status')}")
        print(f"  reason_code: {res.get('reason_code')}")

# Check execution_results in v12
exec_results = v12.get("execution_results", {})
print(f"\nexecution_results keys count: {len(exec_results)}")
if cons_in_intents:
    cons_obl_id = cons_in_intents[0].get("obligation_id")
    cons_exec = exec_results.get(cons_obl_id, {})
    print(f"Conservation execution_results: {bool(cons_exec)}")
    if cons_exec:
        print(f"  status: {cons_exec.get('status')}")
        print(f"  keys: {list(cons_exec.keys())[:15]}")
        # Check observations
        obs = cons_exec.get("observations", {})
        print(f"  observations keys: {list(obs.keys())[:20]}")
        # Check multi_entity_state
        mes = obs.get("multi_entity_state", {})
        print(f"  multi_entity_state keys: {list(mes.keys())}")
        # Check related_entity_observations
        rel_obs = obs.get("related_entity_observations", [])
        print(f"  related_entity_observations: {len(rel_obs)}")
