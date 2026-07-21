# -*- coding: utf-8 -*-
"""Check experiment execution results for HARNESS_FAILED details."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

data = json.load(open('scan_fresh_result.json', encoding='utf-8'))
v12 = data.get('v12', {})
exec_data = v12.get('experiment_execution', {})

print(f"executed_count: {exec_data.get('executed_count')}")
print(f"scheduled_count: {exec_data.get('scheduled_count')}")
print(f"selected_count: {exec_data.get('selected_count')}")
print(f"blocked_count: {exec_data.get('blocked_count')}")
print(f"harness_failure_count: {exec_data.get('harness_failure_count')}")

results = exec_data.get('results', [])
print(f"\nresults: {len(results) if isinstance(results, list) else type(results).__name__}")

if isinstance(results, list):
    # Find harness failures
    hf = [r for r in results if isinstance(r, dict) and r.get('status') == 'HARNESS_FAILED']
    print(f"HARNESS_FAILED results: {len(hf)}")
    for r in hf[:5]:
        print(f"\n  obligation: {r.get('obligation_id', '?')}")
        print(f"  experiment: {r.get('experiment_id', '?')}")
        print(f"  error: {str(r.get('error', r.get('detail', r.get('reason', '?'))))[:200]}")
        # Check all keys
        print(f"  keys: {sorted(r.keys())[:15]}")
elif isinstance(results, dict):
    print(f"results keys: {sorted(results.keys())[:15]}")
    # Check by_obligation
    by_obl = results.get('by_obligation', {})
    print(f"by_obligation: {len(by_obl)}")
    hf = {k: v for k, v in by_obl.items() if isinstance(v, dict) and v.get('status') == 'HARNESS_FAILED'}
    print(f"HARNESS_FAILED: {len(hf)}")
    for k, v in list(hf.items())[:3]:
        print(f"\n  {k}:")
        print(f"    {json.dumps(v, ensure_ascii=False, default=str)[:300]}")

# Also check trace ledger attempts
trace = data.get('trace_ledger', {})
attempts = trace.get('attempts', [])
print(f"\ntrace_ledger attempts: {len(attempts)}")
hf_attempts = [a for a in attempts if isinstance(a, dict) and a.get('terminal_status') == 'HARNESS_FAILED']
print(f"HARNESS_FAILED in trace: {len(hf_attempts)}")
for a in hf_attempts[:3]:
    print(f"  {json.dumps(a, ensure_ascii=False, default=str)[:300]}")
