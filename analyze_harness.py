# -*- coding: utf-8 -*-
"""Analyze HARNESS_FAILED and ORACLE_NOT_VIOLATED experiments."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from collections import Counter

data = json.load(open('scan_fresh_result.json', encoding='utf-8'))
ledger = data.get('obligation_attempt_ledger', {})
attempts = ledger.get('attempts', [])

# HARNESS_FAILED details
hf = [a for a in attempts if a.get('terminal_status') == 'HARNESS_FAILED']
print(f"=== HARNESS_FAILED ({len(hf)}) ===")
for a in hf:
    oid = a.get('obligation_id', '?')
    fam = a.get('risk_family', '?')
    reason = a.get('reason_code', '?')
    detail = str(a.get('detail', ''))[:120]
    print(f"  {oid[:25]} family={fam} reason={reason}")
    if detail:
        print(f"    detail: {detail}")

# ORACLE_NOT_VIOLATED - check assertion types
print(f"\n=== ORACLE_NOT_VIOLATED sample ===")
onv = [a for a in attempts if a.get('reason_code') == 'ORACLE_NOT_VIOLATED']
print(f"Total: {len(onv)}")
# Check what assertion types were used
assertion_kinds = Counter()
for a in onv:
    kind = a.get('assertion_kind', a.get('assertion_type', '?'))
    assertion_kinds[kind] += 1
print(f"Assertion kinds: {dict(assertion_kinds)}")

# Check findings - what assertion types produced findings
findings = data.get('findings', [])
print(f"\n=== FINDINGS ({len(findings)}) ===")
for f in findings:
    fid = f.get('finding_id', '?')
    title = f.get('title', '?')[:80]
    assertion = f.get('assertion_kind', f.get('assertion_type', '?'))
    risk = f.get('risk_family', '?')
    print(f"  {fid}: [{risk}] [{assertion}] {title}")

# Check what risk families are in GT but not in findings
print(f"\n=== Coverage gap analysis ===")
finding_families = Counter(f.get('risk_family', '?') for f in findings)
print(f"Finding families: {dict(finding_families)}")
obligation_families = Counter(a.get('risk_family', '?') for a in attempts)
print(f"Obligation families: {dict(obligation_families)}")
