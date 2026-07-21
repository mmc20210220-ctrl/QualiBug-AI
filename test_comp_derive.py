# -*- coding: utf-8 -*-
"""Test compensation derivation with actual IR operations."""
import json, io, sys, logging
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
logging.basicConfig(level=logging.DEBUG)

from ai_test_asset_center.behavior_ir import _path_shape, _derive_compensation_relations

d = json.load(open('scan_fresh_result.json', 'r', encoding='utf-8'))
bir = d.get('v12', {}).get('behavior_ir', {})
ops_list = bir.get('operations', [])

# Build model dict
model = {"operations": ops_list, "relations": []}

# Check DELETE operations and their shapes
print("=== DELETE operations and shapes ===")
for op in ops_list:
    if op.get('method', '').upper() == 'DELETE':
        path = op.get('path', '')
        shape = _path_shape(path)
        print(f"  {op.get('id')}: {path} -> shape={shape}")

# Check POST /api/orders candidates
print("\n=== POST /api/orders DELETE candidates ===")
create_shape = '/api/orders'
for op in ops_list:
    method = op.get('method', '').upper()
    if method != 'DELETE':
        continue
    path = op.get('path', '')
    shape = _path_shape(path).rstrip('/')
    segments = shape.split('/')
    if segments and segments[-1] == '{}':
        collection = '/'.join(segments[:-1]).rstrip('/')
        if collection == create_shape:
            print(f"  CANDIDATE: {op.get('id')} path={path} shape={shape} collection={collection}")

# Run derivation
print("\n=== Running _derive_compensation_relations ===")
import os
os.environ["QUALIBUG_DEBUG_COMPENSATION"] = "1"
relations = _derive_compensation_relations(model)
comp = [r for r in relations if r.get('relation_type') == 'compensates']
print(f"\nTotal compensates relations: {len(comp)}")
for r in comp:
    print(f"  {r.get('from_ref')} -> {r.get('to_ref')}")
