# -*- coding: utf-8 -*-
"""Debug compensation relation derivation."""
import json, io, sys, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from ai_test_asset_center.behavior_ir import _path_shape

d = json.load(open('scan_fresh_result.json', 'r', encoding='utf-8'))
bir = d.get('v12', {}).get('behavior_ir', {})
ops_list = bir.get('operations', [])

# Simulate _derive_compensation_relations logic
print("=== POST create operations (no {} in path) ===")
for op in ops_list:
    method = op.get('method', '').upper()
    if method != 'POST':
        continue
    path = op.get('path', op.get('raw_path', ''))
    create_shape = _path_shape(path).rstrip('/')
    if not create_shape or '{}' in create_shape:
        print(f"  SKIP {op.get('id')}: POST {path} -> shape={create_shape} (has placeholder)")
        continue
    print(f"  CREATE {op.get('id')}: POST {path} -> shape={create_shape}")
    
    # Find DELETE candidates
    candidates = []
    for cand in ops_list:
        cand_method = cand.get('method', '').upper()
        if cand_method not in {'DELETE', 'POST', 'PATCH', 'PUT'}:
            continue
        cand_path = cand.get('path', cand.get('raw_path', ''))
        compensation_shape = _path_shape(cand_path).rstrip('/')
        segments = compensation_shape.split('/')
        if not segments or segments[-1] != '{}':
            continue
        collection_shape = '/'.join(segments[:-1]).rstrip('/')
        if cand_method == 'DELETE' and collection_shape == create_shape:
            candidates.append(cand)
            print(f"    MATCH DELETE {cand.get('id')}: {cand_path} -> collection={collection_shape}")
    
    if len(candidates) == 1:
        print(f"    => WOULD CREATE compensates relation")
    elif len(candidates) > 1:
        print(f"    => AMBIGUOUS: {len(candidates)} candidates, no relation")
    else:
        print(f"    => NO DELETE candidate found")
