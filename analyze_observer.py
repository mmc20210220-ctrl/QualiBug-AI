# -*- coding: utf-8 -*-
"""Analyze MISSING_OBSERVER blocks."""
import json, io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

d = json.load(open('scan_fresh_result.json', 'r', encoding='utf-8'))
ledger = d.get('obligation_attempt_ledger', {})
attempts = ledger.get('attempts', [])

mo = [a for a in attempts if a.get('reason_code') == 'BLOCKED_MISSING_OBSERVER']
print(f"MISSING_OBSERVER: {len(mo)}")

# Group by risk_family
by_family = {}
for a in mo:
    fam = a.get('risk_family', '')
    by_family[fam] = by_family.get(fam, 0) + 1
print(f"\nBy risk_family: {json.dumps(by_family, indent=2)}")

# Check reason_detail
details = {}
for a in mo:
    detail = a.get('reason_detail', '')
    details[detail] = details.get(detail, 0) + 1
print(f"\nBy reason_detail: {json.dumps(details, indent=2)}")

# Sample entries
print(f"\nSample entries:")
for a in mo[:5]:
    print(f"  family={a.get('risk_family')} detail={a.get('reason_detail')} ops={a.get('operation_refs', [])[:1]}")
