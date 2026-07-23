"""Check conservation experiment assertion structure."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check experiment_compile for conservation
exp_compile = v12.get("experiment_compile", {})
print(f"experiment_compile keys: {list(exp_compile.keys())}")

# Check by_obligation
by_obligation = exp_compile.get("by_obligation", {})
print(f"by_obligation count: {len(by_obligation)}")

# Get conservation obligation_id
ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
cons_attempts = [a for a in attempts if a.get("risk_family") == "conservation"]

if cons_attempts:
    cons_obl_id = cons_attempts[0].get("obligation_id")
    cons_exp_id = cons_attempts[0].get("experiment_id")
    print(f"\nConservation obligation_id: {cons_obl_id}")
    print(f"Conservation experiment_id: {cons_exp_id}")
    
    # Check by_obligation for this obligation
    obl_data = by_obligation.get(cons_obl_id, {})
    print(f"\nby_obligation[{cons_obl_id}]: {bool(obl_data)}")
    if obl_data:
        print(f"  keys: {list(obl_data.keys())[:15]}")
        exp_data = obl_data.get("experiment", {})
        print(f"  experiment keys: {list(exp_data.keys())[:20]}")
        
        # Check assertion vs assertions
        assertion = exp_data.get("assertion")
        assertions = exp_data.get("assertions")
        print(f"\n  assertion (singular): {type(assertion).__name__ if assertion else 'None'}")
        print(f"  assertions (plural): {type(assertions).__name__ if assertions else 'None'}")
        
        if assertion:
            print(f"  assertion keys: {list(assertion.keys())[:15]}")
            print(f"  assertion kind: {assertion.get('kind')}")
            print(f"  has structured_expression: {bool(assertion.get('structured_expression'))}")
            print(f"  has observer_requirements: {bool(assertion.get('observer_requirements'))}")
            obs_reqs = assertion.get("observer_requirements", [])
            print(f"  observer_requirements count: {len(obs_reqs)}")
            if obs_reqs:
                print(f"  first observer_requirement: {json.dumps(obs_reqs[0], indent=2)[:500]}")
