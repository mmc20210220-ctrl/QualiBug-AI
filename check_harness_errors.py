# -*- coding: utf-8 -*-
"""Check HARNESS_FAILED error details from trace ledger."""
import json, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

data = json.load(open('scan_fresh_result.json', encoding='utf-8'))

# Check trace ledger for HARNESS_FAILED details
trace = data.get('trace_ledger', {})
print(f"trace_ledger type: {type(trace).__name__}")
if isinstance(trace, dict):
    print(f"  keys: {sorted(trace.keys())[:15]}")
    entries = trace.get('entries', trace.get('traces', []))
    if isinstance(entries, list):
        print(f"  entries: {len(entries)}")
        # Find HARNESS_FAILED entries
        hf_traces = [e for e in entries if isinstance(e, dict) and 'HARNESS_FAILED' in str(e.get('status', '')) + str(e.get('reason', ''))]
        print(f"  HARNESS_FAILED traces: {len(hf_traces)}")
        for t in hf_traces[:3]:
            print(f"    {json.dumps(t, ensure_ascii=False, default=str)[:300]}")

# Check v12 execution trace
v12 = data.get('v12', {})
exec_trace = v12.get('execution_trace_summaries', [])
print(f"\nexecution_trace_summaries: {len(exec_trace) if isinstance(exec_trace, list) else type(exec_trace).__name__}")
if isinstance(exec_trace, list):
    for t in exec_trace[:3]:
        if isinstance(t, dict):
            print(f"  {json.dumps(t, ensure_ascii=False, default=str)[:200]}")

# Check experiment execution details
exec_data = v12.get('experiment_execution', {})
print(f"\nexperiment_execution keys: {sorted(exec_data.keys())[:15] if isinstance(exec_data, dict) else type(exec_data).__name__}")
if isinstance(exec_data, dict):
    harness_errors = exec_data.get('harness_errors', exec_data.get('errors', []))
    print(f"  harness_errors: {len(harness_errors) if isinstance(harness_errors, list) else type(harness_errors).__name__}")
    if isinstance(harness_errors, list):
        for e in harness_errors[:5]:
            print(f"    {json.dumps(e, ensure_ascii=False, default=str)[:200]}")
