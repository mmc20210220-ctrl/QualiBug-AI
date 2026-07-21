# -*- coding: utf-8 -*-
"""Analyze remaining NON_REVERSIBLE_WRITE blocks."""
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

d = json.load(open('scan_fresh_result.json', 'r', encoding='utf-8'))
ledger = d.get('obligation_attempt_ledger', {})
attempts = ledger.get('attempts', [])
bir = d.get('v12', {}).get('behavior_ir', {})
ops_list = bir.get('operations', [])

# Build op lookup
ops_by_id = {op.get('id'): op for op in ops_list}

nrw = [a for a in attempts if a.get('reason_code') == 'BLOCKED_NON_REVERSIBLE_WRITE']
print(f"NON_REVERSIBLE_WRITE: {len(nrw)}")

# Group by primary operation
by_op = {}
for a in nrw:
    op_refs = a.get('operation_refs', [])
    primary = op_refs[0] if op_refs else 'none'
    by_op[primary] = by_op.get(primary, 0) + 1

sorted_ops = sorted(by_op.items(), key=lambda x: -x[1])[:20]
print(f"\nTop blocked operations:")
for op_id, count in sorted_ops:
    op = ops_by_id.get(op_id, {})
    method = op.get('method', '?')
    path = op.get('path', op.get('raw_path', '?'))
    print(f"  {op_id}: {method} {path} ({count} obligations)")

# Check which blocked ops have matching DELETE
print(f"\n=== DELETE coverage analysis ===")
delete_shapes = set()
for op in ops_list:
    if op.get('method', '').upper() == 'DELETE':
        from ai_test_asset_center.behavior_ir import _path_shape
        shape = _path_shape(op.get('path', '')).rstrip('/')
        delete_shapes.add(shape)
        print(f"  DELETE shape: {shape}")

print(f"\nBlocked POST ops without DELETE coverage:")
for op_id, count in sorted_ops:
    op = ops_by_id.get(op_id, {})
    if op.get('method', '').upper() != 'POST':
        continue
    path = op.get('path', '')
    from ai_test_asset_center.behavior_ir import _path_shape
    shape = _path_shape(path).rstrip('/')
    # Check if there's a DELETE for this collection
    has_delete = any(ds.startswith(shape + '/') or ds == shape + '/{}' for ds in delete_shapes)
    if not has_delete:
        print(f"  {op_id}: POST {path} -> NO DELETE (shape={shape})")
