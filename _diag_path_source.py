"""Find who sets /api/orders path on pathless findings."""
import json, os

# Read intelligence_report findings
data = json.loads(open("platform_outputs/第一个真实项目测试/intelligence_report.json", encoding="utf-8").read())
findings = data.get("real_findings") or data.get("bug_scores") or []
print(f"intelligence_report findings: {len(findings)}")

# Show first 3 findings in detail
for i, f in enumerate(findings[:3]):
    if not isinstance(f, dict): continue
    print(f"\n=== Finding {i} ===")
    for k, v in f.items():
        print(f"  {k}: {str(v)[:120]}")

# Check scan_result.json for HAR entries
scan = json.loads(open("platform_outputs/第一个真实项目测试/scan_result.json", encoding="utf-8").read())
har = scan.get("auto_har", {})
entries = har.get("entries", [])
print(f"\n\n=== HAR entries: {len(entries)} ===")
for e in entries[:10]:
    req = e.get("request", {})
    resp = e.get("response", {})
    print(f"  {req.get('method','')} {req.get('url','')[:60]} → {resp.get('status','')}")
