#!/usr/bin/env python
"""Inspect direct execution results."""
import json
from pathlib import Path

data = json.loads(Path("_direct_exec_results.json").read_text(encoding="utf-8"))
print(f"Total results: {len(data)}")

for i, r in enumerate(data[:3]):
    print(f"\n{'='*50}")
    print(f"Result [{i}]: status={r.get('status')} reason={r.get('reason_code')}")
    print(f"  detail: {r.get('detail', '')[:100]}")
    print(f"  top keys: {list(r.keys())[:15]}")
    
    # Observations
    obs = r.get("observations", {})
    if obs:
        print(f"  observations keys: {list(obs.keys())[:10]}")
        oracle_trace = obs.get("oracle_trace", [])
        if oracle_trace:
            print(f"  oracle_trace: {len(oracle_trace)} entries")
            for t in oracle_trace[:2]:
                print(f"    {json.dumps(t, default=str, ensure_ascii=False)[:120]}")
    
    # Contract evidence receipts
    receipts = r.get("contract_evidence_receipts", [])
    if receipts:
        print(f"  evidence_receipts: {len(receipts)}")
        for rc in receipts[:5]:
            kind = rc.get("kind", "?")
            status = rc.get("status", "?")
            evidence = rc.get("evidence", {})
            print(f"    {kind}: {status} | {json.dumps(evidence, default=str, ensure_ascii=False)[:100]}")
    
    # Steps out
    steps_out = r.get("steps_out", [])
    if steps_out:
        print(f"  steps_out: {len(steps_out)}")
        for s in steps_out[:3]:
            if isinstance(s, dict):
                print(f"    {s.get('phase','?')} {s.get('method','?')} {str(s.get('path','?'))[:40]} -> {s.get('status_code','?')}")
    
    # Binding plan
    binding = r.get("binding_plan", r.get("runtime_bindings", {}))
    if binding:
        print(f"  bindings: {json.dumps(binding, default=str, ensure_ascii=False)[:200]}")
