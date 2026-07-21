# -*- coding: utf-8 -*-
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path

d = json.loads(Path("scan_fresh_result.json").read_text(encoding="utf-8"))
findings = d.get("findings", [])

print("First 3 findings gate status:")
for i, f in enumerate(findings[:3]):
    print(f"\n[{i}] {f.get('title', '?')[:60]}")
    print(f"    gate_passed: {f.get('gate_passed')}")
    print(f"    confirmation_status: {f.get('confirmation_status')}")
    print(f"    customer_delivery_status: {f.get('customer_delivery_status')}")
    print(f"    category: {f.get('category')}")
