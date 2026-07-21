# -*- coding: utf-8 -*-
"""Inspect Behavior IR from latest scan result."""
import sys, io, json, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# Try multiple scan result locations
candidates = [
    r"d:\QualiBug-AI\QualiBug-AI-main\platform_outputs\benchmark_mall\scan_result.json",
    r"d:\QualiBug-AI\QualiBug-AI-main\scan_result.json",
    r"d:\QualiBug-AI\QualiBug-AI-main\_scan_result.json",
]
result = None
for path in candidates:
    if os.path.exists(path):
        mtime = os.path.getmtime(path)
        print(f"Loading: {path} (mtime={mtime})")
        with open(path, 'r', encoding='utf-8') as f:
            result = json.load(f)
        break

if not result:
    print("No scan result found!")
    sys.exit(1)

# Navigate to behavior IR
v12 = result.get('v12', result)
bir = v12.get('behavior_ir', v12.get('behaviorir', {}))
if not bir:
    # Try top-level
    bir = result.get('behavior_ir', {})
if not bir:
    print("Keys in result:", list(result.keys())[:20])
    print("Keys in v12:", list(v12.keys())[:20])
    sys.exit(1)

print(f"Behavior IR keys: {list(bir.keys())}")

# 1. Invariants
invariants = bir.get('invariants', [])
print(f"\n=== Invariants: {len(invariants)} ===")
from collections import Counter
inv_types = Counter(i.get('type','') for i in invariants)
print(f"Types: {dict(inv_types)}")

for itype in sorted(inv_types.keys()):
    samples = [i for i in invariants if i.get('type') == itype][:2]
    for s in samples:
        print(f"\n  [{itype}] id={s.get('id','')}")
        print(f"    desc: {str(s.get('description',''))[:150]}")
        expr = s.get('expression', '')
        if expr:
            print(f"    expr: {json.dumps(expr, ensure_ascii=False)[:200]}")
        ops = s.get('operands', [])
        if ops:
            print(f"    operands: {json.dumps(ops, ensure_ascii=False)[:200]}")
        print(f"    op_refs: {s.get('operation_refs',[])}")
        print(f"    entity: {s.get('entity','')}")

# 2. States
states = bir.get('states', [])
print(f"\n=== States: {len(states)} ===")
for s in states[:6]:
    print(f"  id={s.get('id','')} entity={s.get('entity','')} field={s.get('field','')}")
    vals = s.get('values', s.get('valid_values', []))
    print(f"    values({len(vals)}): {vals[:8]}")
    trans = s.get('transitions', [])
    if trans:
        print(f"    transitions({len(trans)}): {json.dumps(trans[:3], ensure_ascii=False)[:200]}")

# 3. Entities
entities = bir.get('entities', [])
print(f"\n=== Entities: {len(entities)} ===")
for e in entities[:10]:
    print(f"  id={e.get('id','')} name={e.get('name','')} fields={e.get('fields',[])[:8]}")
# -*- coding: utf-8 -*-
"""Inspect Behavior IR structure for IR-driven DB audit design."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")

# Load the latest scan result to get behavior IR
import glob
scan_files = sorted(glob.glob(r"d:\QualiBug-AI\QualiBug-AI-main\scan_results\*.json"), key=lambda f: __import__('os').path.getmtime(f), reverse=True)
if not scan_files:
    print("No scan results found")
    sys.exit(1)

print(f"Latest scan: {scan_files[0]}")
with open(scan_files[0], 'r', encoding='utf-8') as f:
    result = json.load(f)

v12 = result.get('v12', {})
bir = v12.get('behavior_ir', {})

# 1. Invariants structure
invariants = bir.get('invariants', [])
print(f"\n=== Invariants: {len(invariants)} ===")
# Group by type
from collections import Counter
inv_types = Counter(i.get('type','') for i in invariants)
print(f"Types: {dict(inv_types)}")

# Show sample of each type
for itype in inv_types:
    samples = [i for i in invariants if i.get('type') == itype][:2]
    for s in samples:
        print(f"\n  [{itype}] id={s.get('id','')}")
        print(f"    description: {s.get('description','')[:120]}")
        print(f"    expression: {json.dumps(s.get('expression',''), ensure_ascii=False)[:200]}")
        print(f"    operands: {json.dumps(s.get('operands',''), ensure_ascii=False)[:200]}")
        print(f"    operation_refs: {s.get('operation_refs',[])}")
        print(f"    entity: {s.get('entity','')}")

# 2. States structure
states = bir.get('states', [])
print(f"\n=== States: {len(states)} ===")
for s in states[:5]:
    print(f"  id={s.get('id','')} entity={s.get('entity','')} field={s.get('field','')}")
    print(f"    values: {s.get('values', s.get('valid_values', []))}")
    print(f"    transitions: {json.dumps(s.get('transitions', []), ensure_ascii=False)[:200]}")

# 3. Entities structure
entities = bir.get('entities', [])
print(f"\n=== Entities: {len(entities)} ===")
for e in entities[:8]:
    print(f"  id={e.get('id','')} name={e.get('name','')} fields={e.get('fields',[])}")

# 4. Operations with DB-relevant info
ops = bir.get('operations', [])
print(f"\n=== Operations: {len(ops)} (sample GET ops) ===")
get_ops = [o for o in ops if o.get('method','').upper() == 'GET']
for o in get_ops[:5]:
    print(f"  {o.get('method','')} {o.get('path','')} id={o.get('id','')}")
