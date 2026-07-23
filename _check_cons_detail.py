"""Check conservation finding details."""
from pathlib import Path
import json
import time

f = Path("_scan_result_latest.json")
if f.exists():
    mtime = f.stat().st_mtime
    age = int(time.time() - mtime)
    print(f"File age: {age}s ({age//60}m {age%60}s)")
    
    d = json.load(open(f, "r", encoding="utf-8"))
    print(f"success: {d.get('success')}")
    print(f"total_findings: {d.get('total_findings')}")
    
    # Check for conservation findings
    findings = d.get("findings", [])
    cons = [x for x in findings if x.get("category") == "conservation"]
    print(f"\nconservation findings: {len(cons)}")
    for c in cons[:2]:
        print(f"\n  title: {c.get('title', '')[:80]}")
        print(f"  severity: {c.get('severity')}")
        # Check evidence for multi_entity_state
        evidence = c.get("evidence", {})
        obs = evidence.get("observations", [])
        print(f"  observations: {len(obs)}")
        for o in obs[:3]:
            if isinstance(o, dict):
                otype = o.get("type", "")
                print(f"    type: {otype}")
                mes = o.get("multi_entity_state")
                if mes:
                    print(f"    multi_entity_state keys: {list(mes.keys())[:5]}")
                    for entity, data in list(mes.items())[:2]:
                        if isinstance(data, dict):
                            print(f"      {entity}: phases={list(data.keys())[:5]}")
else:
    print(f"File not found: {f}")
