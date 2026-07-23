"""Check conservation experiment results from scan."""
import json
from pathlib import Path

# Check intelligence report
report_path = Path("platform_outputs/benchmark_mall_131/intelligence_report.json")
if report_path.exists():
    d = json.load(open(report_path, "r", encoding="utf-8"))
    print("Top keys:", list(d.keys())[:20])
    
    # Look for experiment execution
    exp_exec = d.get("experiment_execution", {})
    if exp_exec:
        results = exp_exec.get("results", [])
        print(f"\nexperiment_execution.results: {len(results)}")
        for r in results[:5]:
            obl = r.get("obligation_id", "")[:35]
            status = r.get("status")
            reason = r.get("reason_code")
            print(f"  obl={obl}, status={status}, reason={reason}")
    else:
        print("No experiment_execution in report")
    
    # Check findings
    findings = d.get("findings", [])
    print(f"\nfindings: {len(findings)}")
    for f in findings[:5]:
        print(f"  {f.get('title', '')[:60]}")
else:
    print(f"Report not found: {report_path}")

# Also check scan_result.json
scan_path = Path("platform_outputs/benchmark_mall_131/scan_result.json")
if scan_path.exists():
    s = json.load(open(scan_path, "r", encoding="utf-8"))
    print(f"\nscan_result keys: {list(s.keys())[:15]}")
