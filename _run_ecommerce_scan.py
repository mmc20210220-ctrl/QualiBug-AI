"""Run scan for Project C (ecommerce single) to test conservation observer."""
import requests
import json
import time

print(f"Starting Project C scan at {time.strftime('%H:%M:%S')}...")

r = requests.post(
    "http://127.0.0.1:8088/api/v1/scan",
    json={
        "project": "qb_ecommerce_single_retest",
        "root": "d:/QualiBug-AI/QualiBug-AI-main/.tmp_single_ecommerce_suite",
        "base_url": "http://localhost:8000",
    },
    timeout=1800,
)

print(f"Status: {r.status_code}")
data = r.json()
print(f"ok: {data.get('ok')}")
print(f"total_findings: {data.get('total_findings')}")
print(f"campaign_id: {data.get('campaign', {}).get('campaign_id', '')[:40]}")
print(f"report_path: {data.get('report_path', '')}")

# Save full response
with open("_ecommerce_scan_result.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print("Saved to _ecommerce_scan_result.json")
