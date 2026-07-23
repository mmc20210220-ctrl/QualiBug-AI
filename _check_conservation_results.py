"""Check latest scan results for conservation experiments."""
from pathlib import Path
import json

# Check real_project_demo (where ingest saved files)
demo_dir = Path("platform_outputs/real_project_demo")
if demo_dir.exists():
    print(f"Checking: {demo_dir}")
    
    # Check intelligence_report.json
    ir_file = demo_dir / "intelligence_report.json"
    if ir_file.exists():
        d = json.load(open(ir_file, "r", encoding="utf-8"))
        print(f"intelligence_report.json:")
        print(f"  total_findings: {d.get('total_findings')}")
        print(f"  generated_at_utc: {d.get('generated_at_utc')}")
        
        # Check findings
        findings = d.get("findings", [])
        print(f"  findings: {len(findings)}")
        
        # Find conservation findings
        cons_findings = [f for f in findings if f.get("category") == "conservation"]
        print(f"  conservation findings: {len(cons_findings)}")
        for f in cons_findings[:3]:
            print(f"    title: {f.get('title', '')[:80]}")
            print(f"    severity: {f.get('severity')}")
            evidence = f.get("evidence", {})
            print(f"    evidence keys: {list(evidence.keys())[:10]}")
    
    # Check scan_result.json
    sr_file = demo_dir / "scan_result.json"
    if sr_file.exists():
        s = json.load(open(sr_file, "r", encoding="utf-8"))
        print(f"\nscan_result.json:")
        print(f"  total_findings: {s.get('total_findings')}")
        print(f"  success: {s.get('success')}")

# Check for experiment files in workspace
ws_dir = Path("platform_workspace/real_project_demo")
if ws_dir.exists():
    print(f"\nWorkspace: {ws_dir}")
    exp_files = list(ws_dir.rglob("*experiment*.json"))
    print(f"  Experiment files: {len(exp_files)}")
    for f in exp_files[:5]:
        print(f"    {f.relative_to(ws_dir)}")
    
    # Check for agent_intent_plan
    intent_files = list(ws_dir.rglob("*intent*.json"))
    print(f"  Intent files: {len(intent_files)}")
    for f in intent_files[:3]:
        print(f"    {f.relative_to(ws_dir)}")
