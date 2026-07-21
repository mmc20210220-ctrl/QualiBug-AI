# -*- coding: utf-8 -*-
"""Check Behavior IR compensates relations and operations."""
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

d = json.load(open('scan_fresh_result.json', 'r', encoding='utf-8'))
mr = d.get('mainline_run', {})
bir = mr.get('behavior_ir', {})
rels = bir.get('relations', [])
ops = bir.get('operations', {})

# Compensates relations
comp = [r for r in rels if r.get('relation_type') == 'compensates']
print(f"Total relations: {len(rels)}")
print(f"Compensates relations: {len(comp)}")
for r in comp[:20]:
    print(f"  {r.get('from_ref','?')} -> {r.get('to_ref','?')}")

# DELETE operations in IR
delete_ops = {k: v for k, v in ops.items() if v.get('method', '').upper() == 'DELETE'}
print(f"\nDELETE operations in IR: {len(delete_ops)}")
for k, v in delete_ops.items():
    print(f"  {k}: {v.get('method')} {v.get('path', v.get('raw_path', '?'))}")

# Write operations without cleanup
write_ops = {k: v for k, v in ops.items() if v.get('method', '').upper() in ('POST', 'PUT', 'PATCH')}
print(f"\nWrite operations (POST/PUT/PATCH): {len(write_ops)}")
for k, v in list(write_ops.items())[:20]:
    print(f"  {k}: {v.get('method')} {v.get('path', v.get('raw_path', '?'))}")

# Check relation types
rel_types = {}
for r in rels:
    rt = r.get('relation_type', '')
    rel_types[rt] = rel_types.get(rt, 0) + 1
print(f"\nRelation types: {json.dumps(rel_types, indent=2)}")
