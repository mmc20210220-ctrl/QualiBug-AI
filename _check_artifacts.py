# -*- coding: utf-8 -*-
import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Check sealed findings
sf_path = ROOT / "_eval_deep_sealed_findings.json"
d = json.loads(sf_path.read_text(encoding="utf-8"))
sf = d.get("sealed_findings", [])
print(f"sealed_findings: {len(sf)}")
for f in sf[:10]:
    if isinstance(f, dict):
        print(f"  {f.get('finding_id','')} rule={f.get('rule_id','')} mech={f.get('mechanism','')} op={f.get('operation','')}")

print(f"\nstatistics: {json.dumps(d.get('statistics', {}), indent=2)}")
print(f"gates: {json.dumps(d.get('gates', {}), indent=2)}")

# Find behavior IR - search in different locations
print("\n--- Searching for behavior_ir ---")
for pattern in ["**/behavior_ir*.json", "**/contractflow*behavior*"]:
    for p in ROOT.glob(pattern):
        if ".pytest_tmp" not in str(p) and ".worktrees" not in str(p):
            print(f"  {p} ({p.stat().st_size} bytes)")

# Find experiment results
print("\n--- Searching for experiment results ---")
for pattern in ["**/experiment*result*.json", "**/deep_experiment*.json", "**/execution_result*.json"]:
    for p in ROOT.glob(pattern):
        if ".pytest_tmp" not in str(p) and ".worktrees" not in str(p) and "node_modules" not in str(p):
            print(f"  {p} ({p.stat().st_size} bytes)")

# Check platform_workspace
pw = ROOT / "platform_workspace"
if pw.exists():
    print(f"\n--- platform_workspace contents ---")
    for p in sorted(pw.rglob("*.json"))[:20]:
        print(f"  {p.relative_to(ROOT)} ({p.stat().st_size} bytes)")

# Check _funnel_runs
fr = ROOT / "_funnel_runs"
if fr.exists():
    print(f"\n--- _funnel_runs (latest 5) ---")
    runs = sorted(fr.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True)[:5]
    for p in runs:
        print(f"  {p.name} ({p.stat().st_size} bytes)")
