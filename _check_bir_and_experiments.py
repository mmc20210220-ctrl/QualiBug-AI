# -*- coding: utf-8 -*-
import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Check behavior IR
bir_path = ROOT / "platform_outputs/contractflow_project_c/behavior_ir.json"
print(f"behavior_ir exists: {bir_path.exists()}")
if bir_path.exists():
    bir = json.loads(bir_path.read_text(encoding="utf-8"))
    print(f"  keys: {list(bir.keys())[:20]}")
    print(f"  operations: {len(bir.get('operations', []))}")
    print(f"  states: {len(bir.get('states', []))}")
    print(f"  relations: {len(bir.get('relations', []))}")
    # Show first few operations
    for op in bir.get("operations", [])[:10]:
        if isinstance(op, dict):
            print(f"    op: {op.get('id','')} {op.get('method','')} {op.get('path', op.get('path_template',''))}")

# Check existing experiments from deep exec
deep_path = ROOT / "platform_outputs/contractflow_project_c/deep_experiments.json"
print(f"\ndeep_experiments exists: {deep_path.exists()}")
if deep_path.exists():
    de = json.loads(deep_path.read_text(encoding="utf-8"))
    if isinstance(de, list):
        print(f"  count: {len(de)}")
        for exp in de[:5]:
            if isinstance(exp, dict):
                print(f"    {exp.get('experiment_id','')} obl={exp.get('obligation_id','')} mechanism={exp.get('mechanism','')}")
    elif isinstance(de, dict):
        print(f"  keys: {list(de.keys())[:10]}")
        exps = de.get("deep_experiments", de.get("experiments", []))
        print(f"  experiments: {len(exps)}")

# Check post-tuning experiments
pt_path = ROOT / "platform_outputs/contractflow_project_c"
print(f"\nProject C output files:")
for f in sorted(pt_path.glob("*.json"))[:30]:
    print(f"  {f.name} ({f.stat().st_size} bytes)")

# Check mock server status
import urllib.request
try:
    req = urllib.request.Request("http://localhost:8000/api/v1/contracts", method="GET")
    resp = urllib.request.urlopen(req, timeout=3)
    print(f"\nMock server: HTTP {resp.status}")
except Exception as e:
    print(f"\nMock server: {e}")

# Check existing run results
run_dirs = list((ROOT / "platform_outputs/contractflow_project_c").glob("*run*"))
print(f"\nRun directories: {[d.name for d in run_dirs]}")

# Check the deep execution results
deep_exec_path = ROOT / "_eval_deep_sealed_findings.json"
print(f"\n_eval_deep_sealed_findings.json exists: {deep_exec_path.exists()}")
if deep_exec_path.exists():
    df = json.loads(deep_exec_path.read_text(encoding="utf-8"))
    print(f"  keys: {list(df.keys())[:15]}")
    findings = df.get("findings", [])
    print(f"  findings: {len(findings)}")
    for f in findings[:5]:
        if isinstance(f, dict):
            print(f"    {f.get('finding_id','')} rule={f.get('rule_id','')} mechanism={f.get('mechanism','')} op={f.get('operation','')}")
