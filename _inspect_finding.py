#!/usr/bin/env python
"""Inspect the conservation finding in detail."""
import json
from pathlib import Path

data = json.loads(Path("_direct_exec_results.json").read_text(encoding="utf-8"))

# Find the conservation finding
for i, r in enumerate(data):
    finding = r.get("finding")
    if finding:
        print(f"{'='*60}")
        print(f"FINDING at result[{i}]")
        print(f"{'='*60}")
        print(f"\n--- Finding ---")
        print(json.dumps(finding, indent=2, default=str, ensure_ascii=False)[:2000])
        
        print(f"\n--- Oracle Verdict ---")
        verdict = r.get("oracle_verdict", {})
        print(json.dumps(verdict, indent=2, default=str, ensure_ascii=False)[:2000])
        
        print(f"\n--- Observer Receipts ---")
        for rc in r.get("observer_receipts", []):
            print(f"  {json.dumps(rc, default=str, ensure_ascii=False)[:200]}")
        
        print(f"\n--- Steps ---")
        for s in r.get("steps", []):
            if isinstance(s, dict):
                print(f"  {s.get('phase','?')} {s.get('method','?')} {str(s.get('path','?'))[:50]} -> {s.get('status_code','?')}")
                body = s.get("response_body", s.get("body"))
                if body:
                    print(f"    response: {json.dumps(body, default=str, ensure_ascii=False)[:200]}")
        
        print(f"\n--- Contract Evidence Receipts ---")
        for rc in r.get("contract_evidence_receipts", []):
            print(f"  {rc.get('kind')}: {rc.get('status')} | {json.dumps(rc.get('evidence',{}), default=str, ensure_ascii=False)[:150]}")
        break

# Also check the other conservation experiment (no finding)
print(f"\n\n{'='*60}")
print("CONSERVATION without finding (obl_95ea015c3729d457d46a):")
print(f"{'='*60}")
for i, r in enumerate(data):
    if r.get("obligation_id", "").startswith("obl_95ea"):
        print(f"  status: {r.get('status')}")
        print(f"  reason: {r.get('reason_code')}")
        print(f"  detail: {r.get('detail', '')[:200]}")
        verdict = r.get("oracle_verdict", {})
        print(f"  oracle_verdict: {json.dumps(verdict, default=str, ensure_ascii=False)[:500]}")
        for s in r.get("steps", []):
            if isinstance(s, dict):
                print(f"  step: {s.get('phase','?')} {s.get('method','?')} {str(s.get('path','?'))[:50]} -> {s.get('status_code','?')}")
        break
