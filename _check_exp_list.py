"""Check conservation experiment in experiments list."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check experiment_compile experiments
exp_compile = v12.get("experiment_compile", {})
experiments = exp_compile.get("experiments", [])
all_experiments = exp_compile.get("all_experiments", [])
print(f"experiments count: {len(experiments)}")
print(f"all_experiments count: {len(all_experiments)}")

# Get conservation experiment_id
ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
cons_attempts = [a for a in attempts if a.get("risk_family") == "conservation"]

if cons_attempts:
    cons_exp_id = cons_attempts[0].get("experiment_id")
    cons_obl_id = cons_attempts[0].get("obligation_id")
    print(f"\nConservation experiment_id: {cons_exp_id}")
    
    # Search in experiments
    found = [e for e in experiments if e.get("experiment_id") == cons_exp_id]
    print(f"Found in experiments: {len(found)}")
    
    # Search in all_experiments
    found_all = [e for e in all_experiments if e.get("experiment_id") == cons_exp_id]
    print(f"Found in all_experiments: {len(found_all)}")
    
    if found_all:
        exp_data = found_all[0]
        print(f"\nExperiment keys: {list(exp_data.keys())[:25]}")
        
        # Check assertion vs assertions
        assertion = exp_data.get("assertion")
        assertions = exp_data.get("assertions")
        print(f"\nassertion (singular): {type(assertion).__name__ if assertion else 'None'}")
        print(f"assertions (plural): {type(assertions).__name__ if assertions else 'None'}")
        
        if assertion:
            print(f"\nassertion keys: {list(assertion.keys())[:20]}")
            print(f"assertion kind: {assertion.get('kind')}")
            print(f"has structured_expression: {bool(assertion.get('structured_expression'))}")
            print(f"has observer_requirements: {bool(assertion.get('observer_requirements'))}")
            obs_reqs = assertion.get("observer_requirements", [])
            print(f"observer_requirements count: {len(obs_reqs)}")
            if obs_reqs:
                for i, req in enumerate(obs_reqs[:2]):
                    print(f"\n  observer_requirement[{i}]:")
                    print(f"    entity_alias: {req.get('entity_alias')}")
                    print(f"    cardinality: {req.get('cardinality')}")
                    print(f"    fields: {req.get('fields', [])[:5]}")
                    print(f"    collection_requirements: {req.get('collection_requirements')}")
