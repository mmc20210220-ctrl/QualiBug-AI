"""Check latest scan results from real_project_demo."""
from pathlib import Path
import json

# Check intelligence_report.json
ir_file = Path("platform_outputs/real_project_demo/intelligence_report.json")
if ir_file.exists():
    d = json.load(open(ir_file, "r", encoding="utf-8"))
    print("intelligence_report.json:")
    print(f"  generated_at_utc: {d.get('generated_at_utc')}")
    print(f"  total_findings: {d.get('total_findings')}")
    
    # Check findings
    findings = d.get("findings", [])
    print(f"  findings: {len(findings)}")
    
    # Find conservation findings
    cons_findings = [f for f in findings if f.get("category") == "conservation"]
    print(f"  conservation findings: {len(cons_findings)}")
    for f in cons_findings[:3]:
        print(f"\n    title: {f.get('title', '')[:80]}")
        print(f"    severity: {f.get('severity')}")
        evidence = f.get("evidence", {})
        obs = evidence.get("observations", [])
        print(f"    observations: {len(obs)}")
        for o in obs[:3]:
            if isinstance(o, dict):
                otype = o.get("type", "")
                print(f"      type: {otype}")
                mes = o.get("multi_entity_state")
                if mes:
                    print(f"      multi_entity_state keys: {list(mes.keys())[:5]}")

# Check scan_result.json
sr_file = Path("platform_outputs/real_project_demo/scan_result.json")
if sr_file.exists():
    s = json.load(open(sr_file, "r", encoding="utf-8"))
    print(f"\nscan_result.json:")
    print(f"  success: {s.get('success')}")
    print(f"  total_findings: {s.get('total_findings')}")
    print(f"  total_candidates: {s.get('total_candidates')}")
    
    # Check for conservation findings
    findings = s.get("findings", [])
    cons = [f for f in findings if f.get("category") == "conservation"]
    print(f"  conservation findings: {len(cons)}")
