"""Find the actual source of the 82 /api/orders findings."""
import json, os
from collections import Counter

root = "."
project = "第一个真实项目测试"

# Check all report files
candidates = [
    f"platform_outputs/{project}/intelligence_report.json",
    f"platform_outputs/{project}/v12_report.json",
    f"platform_outputs/{project}/scan_result.json",
    f"platform_workspace/{project}/intelligence_report.json",
    f"platform_workspace/{project}/v12_report.json",
]

for path in candidates:
    if not os.path.exists(path):
        continue
    data = json.loads(open(path, encoding="utf-8").read())
    findings = data.get("real_findings") or data.get("findings") or data.get("bug_scores") or []
    if not isinstance(findings, list):
        findings = []
    print(f"\n=== {path} ===")
    print(f"  findings count: {len(findings)}")
    if findings:
        # Count by path
        path_counts = Counter()
        for f in findings:
            if not isinstance(f, dict): continue
            p = f.get("path") or f.get("_api_path") or ""
            ev = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
            if not p:
                p = ev.get("path", "")
            path_counts[p or "(empty)"] += 1
        print("  Top paths:")
        for p, c in path_counts.most_common(10):
            print(f"    {c:3d}  {p}")
        
        # Show first few /api/orders findings
        orders = [f for f in findings if isinstance(f, dict) and 
                  (f.get("path") or f.get("_api_path") or (f.get("evidence") or {}).get("path") or "") == "/api/orders"]
        if orders:
            print(f"\n  /api/orders findings: {len(orders)}")
            for f in orders[:5]:
                title = f.get("title","")[:80]
                path = f.get("path") or f.get("_api_path") or ""
                print(f"    path='{path}' title='{title}'")

# Also check deep_findings, e2e_findings, db_verification
for path in candidates:
    if not os.path.exists(path):
        continue
    data = json.loads(open(path, encoding="utf-8").read())
    for key in ("deep_findings", "e2e_findings", "ui_findings"):
        items = data.get(key, [])
        if isinstance(items, list) and items:
            print(f"\n=== {path} → {key}: {len(items)} ===")
            for f in items[:3]:
                if isinstance(f, dict):
                    print(f"    {f.get('title','')[:80]}")
