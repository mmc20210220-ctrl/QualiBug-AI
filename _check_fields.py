# -*- coding: utf-8 -*-
"""Check actual field names in validation experiments' schemas."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from collections import Counter

d = json.load(open('_scan_result_latest.json', encoding='utf-8'))
v12 = d.get('v12') or {}
compile_data = v12.get('experiment_compile') or {}
all_exps = compile_data.get('all_experiments') or []

# Find validation experiments and check their control bodies
validation_exps = [e for e in all_exps if isinstance(e, dict) and e.get('risk_family') == 'validation']
print(f"validation experiments: {len(validation_exps)}")

# Collect control body field names
field_names = Counter()
control_bodies = []
for exp in validation_exps[:100]:
    control_plan = exp.get('control_plan') or []
    for step in control_plan:
        if not isinstance(step, dict):
            continue
        body = step.get('body')
        if isinstance(body, dict) and body:
            control_bodies.append(body)
            for k in body:
                field_names[k] += 1

print(f"\nControl bodies found: {len(control_bodies)}")
print(f"Field names (top 30):")
for name, cnt in field_names.most_common(30):
    print(f"  {name}: {cnt}")

# Show sample control bodies
print(f"\nSample control bodies:")
for body in control_bodies[:10]:
    print(f"  {json.dumps(body, ensure_ascii=False)[:200]}")

# Check what operations these target
op_refs = Counter()
for exp in validation_exps[:100]:
    ops = exp.get('operation_refs') or []
    for op in ops:
        op_refs[str(op)] += 1
print(f"\nTarget operations (top 10):")
for op, cnt in op_refs.most_common(10):
    print(f"  {op}: {cnt}")

# Check the behavior IR for operation details
bir = v12.get('behavior_ir') or {}
operations = bir.get('operations') or bir.get('nodes') or []
print(f"\nBehavior IR operations: {len(operations)}")
# Find operations with request schemas
for op in operations[:5]:
    if isinstance(op, dict):
        op_id = op.get('id', op.get('operation_id', '?'))
        path = op.get('path', '?')
        method = op.get('method', '?')
        schema = op.get('request_schema') or op.get('requestSchema') or {}
        props = (schema.get('properties') or {}) if isinstance(schema, dict) else {}
        if props:
            print(f"  {method} {path}: fields={list(props.keys())[:8]}")
