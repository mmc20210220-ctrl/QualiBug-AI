# -*- coding: utf-8 -*-
"""Check if semantic invalid values are being used in compiled experiments."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

d = json.load(open('_scan_result_latest.json', encoding='utf-8'))
v12 = d.get('v12') or {}

# Check experiment_compile data
compile_data = v12.get('experiment_compile') or {}
print(f"experiment_compile keys: {sorted(compile_data.keys())[:10]}")

# Check experiment_execution data
exec_data = v12.get('experiment_execution') or {}
print(f"experiment_execution keys: {sorted(exec_data.keys())[:10]}")

# Look for obligation plan
plan = v12.get('obligation_plan') or {}
print(f"obligation_plan keys: {sorted(plan.keys())[:10]}")

# Check experiments in the plan
experiments = plan.get('experiments') or []
print(f"\nexperiments in plan: {len(experiments)}")

# Find validation experiments and check their treatment body
validation_exps = [e for e in experiments if isinstance(e, dict) and e.get('risk_family') == 'validation']
print(f"validation experiments: {len(validation_exps)}")

# Check treatment bodies for semantic invalid values
semantic_count = 0
old_count = 0
for exp in validation_exps[:20]:
    treatment_plan = exp.get('treatment_plan') or []
    for step in treatment_plan:
        if not isinstance(step, dict):
            continue
        mutation = step.get('mutation') or {}
        constraint = mutation.get('constraint', '')
        body = step.get('body')
        if 'semantic' in str(constraint):
            semantic_count += 1
            if semantic_count <= 5:
                print(f"  SEMANTIC: constraint={constraint}, body={json.dumps(body, ensure_ascii=False)[:150]}")
        elif constraint:
            old_count += 1
            if old_count <= 3:
                print(f"  OLD: constraint={constraint}, body={json.dumps(body, ensure_ascii=False)[:150]}")

print(f"\nSemantic treatment: {semantic_count}, Old treatment: {old_count}")

# Also check execution traces for actual HTTP requests
traces = v12.get('execution_trace_summaries') or []
print(f"\nexecution_trace_summaries: {len(traces)}")
if traces:
    # Find a validation trace
    for t in traces[:3]:
        if isinstance(t, dict):
            print(f"  trace keys: {sorted(t.keys())[:10]}")
            break
