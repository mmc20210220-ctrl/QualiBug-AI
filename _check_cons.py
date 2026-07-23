"""Check conservation experiment details."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})
ec = v12.get("experiment_compile", {})
exps = ec.get("all_experiments", [])
cons_exps = [e for e in exps if e.get("risk_family") == "conservation"]
print(f"Conservation experiments: {len(cons_exps)}")

if cons_exps:
    e = cons_exps[0]
    print(f"Experiment ID: {e.get('experiment_id')}")
    assertions = e.get("assertions", [])
    if assertions:
        a = assertions[0]
        print(f"Assertion keys: {list(a.keys())[:20]}")
        print(f"Has observer_requirements: {bool(a.get('observer_requirements'))}")
        print(f"Has entity_bindings: {bool(a.get('entity_bindings'))}")
        print(f"Has structured_expression: {bool(a.get('structured_expression'))}")
        
        # Check observer_requirements content
        obs_reqs = a.get("observer_requirements", [])
        print(f"\nObserver requirements count: {len(obs_reqs)}")
        for req in obs_reqs[:3]:
            print(f"  - {req.get('entity_alias')}: {req.get('entity_name')} ({req.get('cardinality')})")
            if req.get("collection_requirements"):
                print(f"    collection_requirements: {req.get('collection_requirements')}")
        
        # Check entity_bindings
        bindings = a.get("entity_bindings", {})
        print(f"\nEntity bindings: {list(bindings.keys())}")
        
        # Check structured_expression
        se = a.get("structured_expression", {})
        print(f"\nStructured expression: operator={se.get('operator')}, node_type={se.get('node_type')}")
