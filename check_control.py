# -*- coding: utf-8 -*-
"""Check control evidence in findings."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path

d = json.loads(Path("scan_fresh_result.json").read_text(encoding="utf-8"))
findings = d.get("findings", [])

# Check first few findings for control evidence
for i, f in enumerate(findings[:5]):
    title = f.get("title", "?")
    print(f"\n[{i}] {title[:70]}")
    
    # Check oracle info
    oracle = f.get("oracle", {})
    print(f"    oracle.verdict: {oracle.get('verdict')}")
    print(f"    oracle.status: {oracle.get('status')}")
    
    # Check evidence
    evidence = f.get("evidence", {})
    print(f"    evidence.control_succeeded: {evidence.get('control_succeeded')}")
    print(f"    evidence.actor: {evidence.get('actor')}")
    
    # Check raw_evidence
    raw = f.get("raw_evidence", {})
    print(f"    raw.control_actor: {raw.get('control_actor')}")
    print(f"    raw.treatment_actor: {raw.get('treatment_actor')}")
    
    # Check observations
    obs = raw.get("observations", {})
    print(f"    observations.control_succeeded: {obs.get('control_succeeded')}")
