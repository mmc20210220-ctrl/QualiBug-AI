"""Check conservation experiment assertions content."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check experiment_compile experiments
exp_compile = v12.get("experiment_compile", {})
experiments = exp_compile.get("experiments", [])

# Get conservation experiment_id
ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
cons_attempts = [a for a in attempts if a.get("risk_family") == "conservation"]

if cons_attempts:
    cons_exp_id = cons_attempts[0].get("experiment_id")
    
    # Search in experiments
    found = [e for e in experiments if e.get("experiment_id") == cons_exp_id]
    
    if found:
        exp_data = found[0]
        assertions = exp_data.get("assertions", [])
        print(f"assertions count: {len(assertions)}")
        
        for i, assertion in enumerate(assertions):
            print(f"\nassertion[{i}]:")
            print(f"  kind: {assertion.get('kind')}")
            print(f"  has structured_expression: {bool(assertion.get('structured_expression'))}")
            print(f"  has observer_requirements: {bool(assertion.get('observer_requirements'))}")
            
            obs_reqs = assertion.get("observer_requirements", [])
            print(f"  observer_requirements count: {len(obs_reqs)}")
            
            if obs_reqs:
                for j, req in enumerate(obs_reqs[:2]):
                    print(f"\n    observer_requirement[{j}]:")
                    print(f"      entity_alias: {req.get('entity_alias')}")
                    print(f"      entity_name: {req.get('entity_name')}")
                    print(f"      cardinality: {req.get('cardinality')}")
                    print(f"      fields: {req.get('fields', [])[:5]}")
                    print(f"      collection_requirements: {req.get('collection_requirements')}")
            
            # Check structured_expression
            se = assertion.get("structured_expression", {})
            if se:
                print(f"\n  structured_expression:")
                print(f"    operator: {se.get('operator')}")
                print(f"    node_type: {se.get('node_type')}")
                left = se.get("left", {})
                right = se.get("right", {})
                print(f"    left: entity={left.get('entity')}, aggregate={left.get('aggregate')}, field={left.get('field')}")
                print(f"    right: entity={right.get('entity')}, aggregate={right.get('aggregate')}, field={right.get('field')}")
