# -*- coding: utf-8 -*-
"""Get full HARNESS_FAILED attempt details."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

data = json.load(open('scan_fresh_result.json', encoding='utf-8'))
trace = data.get('trace_ledger', {})
attempts = trace.get('attempts', [])

hf = [a for a in attempts if isinstance(a, dict) and a.get('terminal_status') == 'HARNESS_FAILED']
print(f"HARNESS_FAILED: {len(hf)}")

for a in hf[:5]:
    print(f"\n--- Attempt ---")
    for k, v in sorted(a.items()):
        val = str(v)
        if len(val) > 200:
            val = val[:200] + '...'
        print(f"  {k}: {val}")

# Also check failure_signature_counts
fsc = trace.get('failure_signature_counts', {})
print(f"\nfailure_signature_counts: {json.dumps(fsc, ensure_ascii=False, default=str)[:500]}")

# Check outcome_counts
oc = trace.get('outcome_counts', {})
print(f"\noutcome_counts: {json.dumps(oc, ensure_ascii=False, default=str)[:500]}")
