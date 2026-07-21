# -*- coding: utf-8 -*-
"""Check Behavior IR actors and operations."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

data = json.load(open('scan_fresh_result.json', encoding='utf-8'))
bir = data.get('v12', {}).get('behavior_ir', {})

actors = bir.get('actors', [])
print(f"Actors: {len(actors)}")
for a in actors[:15]:
    aid = a.get('id', '?')
    role = a.get('role', '?')
    uname = a.get('username', '?')
    print(f"  {aid}: role={role}, username={uname}")

ops = bir.get('operations', [])
print(f"\nOperations: {len(ops)}")
from collections import Counter
methods = Counter(op.get('method', '?').upper() for op in ops if isinstance(op, dict))
print(f"  Methods: {dict(methods)}")

# Show all operations grouped by method
for method in ['GET', 'POST', 'PUT', 'PATCH', 'DELETE']:
    group = [op for op in ops if isinstance(op, dict) and op.get('method', '').upper() == method]
    if group:
        print(f"\n  {method} ({len(group)}):")
        for op in group:
            print(f"    {op.get('id', '?')}: {op.get('path', '?')}")

# Check invariants
invariants = bir.get('invariants', [])
print(f"\nInvariants: {len(invariants)}")
kinds = Counter(inv.get('kind', '?') for inv in invariants if isinstance(inv, dict))
print(f"  Kinds: {dict(kinds)}")
