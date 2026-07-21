# -*- coding: utf-8 -*-
"""Check if DB findings are already in findings list."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

data = json.load(open('scan_fresh_result.json', encoding='utf-8'))
findings = data.get('findings', [])
db = data.get('db_findings', [])

db_titles = set(f.get('title', '') for f in db)
scan_db = [f for f in findings if f.get('title', '') in db_titles]

print(f"findings: {len(findings)}")
print(f"db_findings: {len(db)}")
print(f"DB findings already in findings: {len(scan_db)}")
for f in scan_db:
    print(f"  {f.get('title', '?')[:60]} evidence_source={f.get('evidence_source', '?')}")

# Show all findings titles
print(f"\nAll findings:")
for f in findings:
    src = f.get('evidence_source', 'scan')
    print(f"  [{src}] {f.get('title', '?')[:70]}")
