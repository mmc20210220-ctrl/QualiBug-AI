# -*- coding: utf-8 -*-
"""Check semantic treatment in experiment_compile."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

d = json.load(open('_scan_result_latest.json', encoding='utf-8'))
v12 = d.get('v12') or {}
compile_data = v12.get('experiment_compile') or {}

# Get all experiments
all_exps = compile_data.get('all_experiments') or compile_data.get('experiments') or []
print(f"all_experiments: {len(all_exps)}")

# Find validation experiments
validation_exps = [e for e in all_exps if isinstance(e, dict) and e.get('risk_family') == 'validation']
print(f"validation experiments: {len(validation_exps)}")

# Check treatment bodies
semantic_count = 0
old_count = 0
no_mutation = 0
for exp in validation_exps:
    treatment_plan = exp.get('treatment_plan') or []
    for step in treatment_plan:
        if not isinstance(step, dict):
            continue
        mutation = step.get('mutation') or {}
        constraint = str(mutation.get('constraint', ''))
        body = step.get('body')
        if 'semantic' in constraint:
            semantic_count += 1
            if semantic_count <= 8:
                op_ref = exp.get('operation_refs', ['?'])[0] if exp.get('operation_refs') else '?'
                print(f"  SEMANTIC [{op_ref}]: {constraint}")
                print(f"    body: {json.dumps(body, ensure_ascii=False)[:200]}")
        elif constraint:
            old_count += 1
            if old_count <= 3:
                print(f"  OLD: {constraint}, body={json.dumps(body, ensure_ascii=False)[:100]}")
        else:
            no_mutation += 1

print(f"\nResults: semantic={semantic_count}, old={old_count}, no_mutation={no_mutation}")

# Check execution results for validation
exec_data = v12.get('experiment_execution') or {}
results = exec_data.get('results') or []
print(f"\nexecution results: {len(results)}")
val_results = [r for r in results if isinstance(r, dict) and r.get('risk_family') == 'validation']
print(f"validation execution results: {len(val_results)}")

# Check oracle status for validation results
from collections import Counter
oracle_statuses = Counter(r.get('oracle_status', r.get('contract_oracle_status', '?')) for r in val_results)
print(f"Validation oracle statuses: {dict(oracle_statuses.most_common())}")

# Sample a validation result to see actual HTTP response
for r in val_results[:3]:
    obs = r.get('observations') or r.get('evidence') or {}
    status_code = obs.get('status_code', obs.get('treatment_status_code', '?'))
    print(f"  status_code={status_code}, oracle={r.get('oracle_status', '?')}")
