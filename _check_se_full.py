"""Check conservation structured_expression in detail."""
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

# Find the one that was executed
cons_obl_id = "obl_0413ca640355cba0d746"
cons_exp = [e for e in experiments if e.get("obligation_id") == cons_obl_id]

if cons_exp:
    exp_data = cons_exp[0]
    assertions = exp_data.get("assertions", [])
    
    if assertions:
        assertion = assertions[0]
        se = assertion.get("structured_expression", {})
        print("structured_expression (full):")
        print(json.dumps(se, indent=2, default=str))
        
        print("\n\nentity_bindings:")
        eb = assertion.get("entity_bindings", {})
        print(json.dumps(eb, indent=2, default=str))
        
        print("\n\nobserver_requirements:")
        obs_reqs = assertion.get("observer_requirements", [])
        print(json.dumps(obs_reqs, indent=2, default=str))
