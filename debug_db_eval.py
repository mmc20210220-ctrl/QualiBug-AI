# -*- coding: utf-8 -*-
"""Debug DB findings evaluation."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

data = json.load(open('scan_fresh_result.json', encoding='utf-8'))
db = data.get('db_findings', [])
print(f"DB findings: {len(db)}")
for f in db:
    gp = f.get('gate_passed')
    cds = f.get('customer_delivery_status')
    cs = f.get('confirmation_status')
    title = f.get('title', '?')[:50]
    print(f"  {title}")
    print(f"    gate_passed={gp} (type={type(gp).__name__})")
    print(f"    customer_delivery_status={cds}")
    print(f"    confirmation_status={cs}")

# Simulate the evaluator filter
findings = data.get('findings', []) + db
raw_confirmed = [
    f for f in findings
    if isinstance(f, dict) and (
        f.get("gate_passed") is True
        or str(f.get("customer_delivery_status") or "") == "defect"
        or str(f.get("confirmation_status") or "") == "confirmed"
    )
]
print(f"\nTotal findings: {len(findings)}")
print(f"Confirmed (evaluator filter): {len(raw_confirmed)}")

# Check which DB findings pass the filter
for f in db:
    passes = (
        f.get("gate_passed") is True
        or str(f.get("customer_delivery_status") or "") == "defect"
        or str(f.get("confirmation_status") or "") == "confirmed"
    )
    print(f"  DB '{f.get('title','?')[:40]}': passes={passes}")
