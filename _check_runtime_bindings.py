"""Check conservation experiment runtime bindings."""
import json

r = json.load(open("project_c_post_tuning_result.json", "r", encoding="utf-8"))
v12 = r.get("v12", {})

# Check experiment_compile experiments
exp_compile = v12.get("experiment_compile", {})
experiments = exp_compile.get("experiments", [])

# Find the executed conservation experiment
cons_obl_id = "obl_0413ca640355cba0d746"
cons_exp = [e for e in experiments if e.get("obligation_id") == cons_obl_id]

if cons_exp:
    exp_data = cons_exp[0]
    print(f"Experiment: {exp_data.get('experiment_id')}")
    print(f"Risk family: {exp_data.get('risk_family')}")
    
    # Check binding_plan
    binding_plan = exp_data.get("binding_plan", {})
    print(f"\nbinding_plan: {json.dumps(binding_plan, indent=2, default=str)[:1000]}")
    
    # Check treatment_plan
    treatment_plan = exp_data.get("treatment_plan", [])
    print(f"\ntreatment_plan: {len(treatment_plan)}")
    for step in treatment_plan[:2]:
        print(f"  - operation_ref: {step.get('operation_ref')}")
        print(f"    body: {json.dumps(step.get('body', {}), default=str)[:300]}")
    
    # Check setup_plan
    setup_plan = exp_data.get("setup_plan", [])
    print(f"\nsetup_plan: {len(setup_plan)}")
    
    # Check fixture_dag
    fixture_dag = exp_data.get("fixture_dag", {})
    print(f"\nfixture_dag: {json.dumps(fixture_dag, indent=2, default=str)[:500]}")

# Check experiment_execution result for this conservation
exp_exec = v12.get("experiment_execution", {})
results = exp_exec.get("results", [])
cons_results = [r for r in results if r.get("obligation_id") == cons_obl_id]

if cons_results:
    res = cons_results[0]
    print(f"\n\nExecution result:")
    print(f"  status: {res.get('status')}")
    print(f"  reason_code: {res.get('reason_code')}")
    
    # Check steps for response body
    steps = res.get("steps", [])
    for step in steps[:2]:
        print(f"\n  Step: {step.get('phase')} {step.get('method')} {step.get('path')}")
        print(f"    status_code: {step.get('status_code')}")
        gov = step.get("governance_receipt", {})
        if gov:
            before = gov.get("before", {})
            after = gov.get("after", {})
            print(f"    before: {json.dumps(before, default=str)[:200] if before else 'None'}")
            print(f"    after: {json.dumps(after, default=str)[:200] if after else 'None'}")
