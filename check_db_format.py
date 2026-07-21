# -*- coding: utf-8 -*-
"""Check DB findings format and evaluator compatibility."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

data = json.load(open('scan_fresh_result.json', encoding='utf-8'))
db_findings = data.get('db_findings', [])
print(f"DB findings: {len(db_findings)}")
for f in db_findings:
    print(f"\n--- DB Finding ---")
    for k, v in sorted(f.items()):
        val = str(v)
        if len(val) > 150:
            val = val[:150] + '...'
        print(f"  {k}: {val}")

# Check what the evaluator expects
print("\n\n=== Evaluator input format ===")
findings = data.get('findings', [])
if findings:
    f = findings[0]
    print("Scan finding keys:", sorted(f.keys())[:20])
