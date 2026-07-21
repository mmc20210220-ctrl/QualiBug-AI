# -*- coding: utf-8 -*-
"""Show actual invariant content from Behavior IR."""
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

path = r"d:\QualiBug-AI\QualiBug-AI-main\platform_outputs\benchmark_mall\scan_result.json"
with open(path, 'r', encoding='utf-8') as f:
    result = json.load(f)

bir = result.get('v12', result).get('behavior_ir', {})
invariants = bir.get('invariants', [])

# Show ALL keys present in invariants
all_keys = set()
for inv in invariants:
    all_keys.update(inv.keys())
print(f"Invariant keys: {sorted(all_keys)}")

# Show first 10 invariants with all their content
for i, inv in enumerate(invariants[:10]):
    print(f"\n--- Invariant {i} ---")
    for k, v in inv.items():
        val_str = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
        if len(val_str) > 200:
            val_str = val_str[:200] + "..."
        print(f"  {k}: {val_str}")

# Check states too
states = bir.get('states', [])
print(f"\n\n=== States ({len(states)}) ===")
for i, s in enumerate(states[:5]):
    print(f"\n--- State {i} ---")
    for k, v in s.items():
        val_str = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
        if len(val_str) > 200:
            val_str = val_str[:200] + "..."
        print(f"  {k}: {val_str}")

# Check entities
entities = bir.get('entities', [])
print(f"\n\n=== Entities ({len(entities)}) ===")
for i, e in enumerate(entities[:3]):
    print(f"\n--- Entity {i} ---")
    for k, v in e.items():
        val_str = json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else str(v)
        if len(val_str) > 300:
            val_str = val_str[:300] + "..."
        print(f"  {k}: {val_str}")
