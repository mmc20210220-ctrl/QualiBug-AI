# -*- coding: utf-8 -*-
"""Check Behavior IR compensation relations in v12 path."""
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

d = json.load(open('scan_fresh_result.json', 'r', encoding='utf-8'))
bir = d.get('v12', {}).get('behavior_ir', {})
ops_list = bir.get('operations', [])
rels = bir.get('relations', [])

print(f"Operations: {len(ops_list)}")
print(f"Relations: {len(rels)}")

# DELETE operations
delete_ops = [op for op in ops_list if op.get('method', '').upper() == 'DELETE']
print(f"\nDELETE operations: {len(delete_ops)}")
for op in delete_ops:
    print(f"  {op.get('id','?')}: {op.get('method')} {op.get('path', op.get('raw_path', '?'))}")

# POST operations (potential creates)
post_ops = [op for op in ops_list if op.get('method', '').upper() == 'POST']
print(f"\nPOST operations: {len(post_ops)}")
for op in post_ops[:15]:
    path = op.get('path', op.get('raw_path', '?'))
    print(f"  {op.get('id','?')}: POST {path}")

# Compensates relations
comp = [r for r in rels if r.get('relation_type') == 'compensates']
print(f"\nCompensates relations: {len(comp)}")
for r in comp[:20]:
    print(f"  {r.get('from_ref','?')} -> {r.get('to_ref','?')}")

# Relation types breakdown
rel_types = {}
for r in rels:
    rt = r.get('relation_type', '')
    rel_types[rt] = rel_types.get(rt, 0) + 1
print(f"\nRelation types: {json.dumps(rel_types, indent=2)}")
