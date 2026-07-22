"""Deep analysis of finding evidence structure."""
import json

with open("_scan_result_latest.json", "r", encoding="utf-8") as f:
    d = json.load(f)

findings = d.get("findings", [])

# Look at first finding in detail
if findings:
    f0 = findings[0]
    print("=== FINDING 0 FULL KEYS ===")
    for k in sorted(f0.keys()):
        v = f0[k]
        if isinstance(v, str) and len(v) > 200:
            print(f"  {k}: {v[:200]}...")
        elif isinstance(v, (dict, list)):
            print(f"  {k}: ({type(v).__name__}, len={len(v)})")
        else:
            print(f"  {k}: {v}")
    
    print("\n=== FINDING 0 EVIDENCE ===")
    ev = f0.get("evidence") or {}
    for k in sorted(ev.keys()):
        v = ev[k]
        if isinstance(v, str) and len(v) > 200:
            print(f"  {k}: {v[:200]}...")
        elif isinstance(v, (dict, list)):
            s = json.dumps(v, ensure_ascii=False, default=str)
            if len(s) > 300:
                print(f"  {k}: {s[:300]}...")
            else:
                print(f"  {k}: {s}")
        else:
            print(f"  {k}: {v}")
    
    # Check oracle/contract details
    print("\n=== FINDING 0 ORACLE/CONTRACT ===")
    oracle = f0.get("oracle_result") or f0.get("contract_oracle") or {}
    print(f"  oracle_result: {json.dumps(oracle, ensure_ascii=False, default=str)[:500]}")
    
    assertion = f0.get("assertion") or f0.get("assertion_result") or {}
    print(f"  assertion: {json.dumps(assertion, ensure_ascii=False, default=str)[:500]}")

# Check top-level keys
print("\n=== TOP-LEVEL KEYS ===")
for k in sorted(d.keys()):
    v = d[k]
    if isinstance(v, (int, float, str, bool, type(None))):
        print(f"  {k}: {v}")
    elif isinstance(v, list):
        print(f"  {k}: list[{len(v)}]")
    elif isinstance(v, dict):
        print(f"  {k}: dict[{len(v)}]")
