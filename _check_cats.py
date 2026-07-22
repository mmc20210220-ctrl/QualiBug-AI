"""Check candidate categories in latest scan."""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

raw = Path("_scan_result_latest.json").read_bytes()
d = json.loads(raw)
del raw

candidates = d.get("candidate_findings", [])
findings = d.get("findings", [])
print(f"Findings: {len(findings)}, Candidates: {len(candidates)}")

# Candidate categories
cats = {}
for c in candidates:
    if isinstance(c, dict):
        cat = c.get("category") or "unknown"
        cats[cat] = cats.get(cat, 0) + 1
print(f"\nCandidate categories: {cats}")

# Finding categories
fcats = {}
for f in findings:
    if isinstance(f, dict):
        cat = f.get("category") or "unknown"
        fcats[cat] = fcats.get(cat, 0) + 1
print(f"Finding categories: {fcats}")

# Check if OTV is in findings
otv_findings = [f for f in findings if f.get("category") == "owner_tenant_visibility"]
print(f"\nOTV in findings: {len(otv_findings)}")
for f in otv_findings:
    print(f"  {f.get('title','')[:70]}")
    print(f"    gate_passed={f.get('gate_passed')} status={f.get('customer_delivery_status')}")

# Check pipeline health terminal reasons
ph = d.get("pipeline_health") or {}
note = ph.get("operator_note", "")
# Extract terminal reasons
import re
m = re.search(r"Terminal reasons: (.+?)\.", note)
if m:
    print(f"\nTerminal reasons: {m.group(1)}")
