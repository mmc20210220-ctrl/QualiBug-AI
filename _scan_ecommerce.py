"""Run scan for ecommerce project."""
import requests
import json
import time

print(f"Starting scan at {time.strftime('%H:%M:%S')}...")
r = requests.post(
    "http://127.0.0.1:8088/api/v1/scan",
    json={
        "project": "qb_ecommerce_single_retest",
        "base_url": "http://localhost:8000",
    },
    headers={"X-Project": "qb_ecommerce_single_retest"},
    timeout=1800,
)
print(f"Status: {r.status_code}")
data = r.json()
print(f"ok: {data.get('ok')}")
print(f"total_findings: {data.get('total_findings')}")
campaign = data.get("campaign", {})
print(f"campaign_id: {campaign.get('campaign_id', '')[:40] if campaign else ''}")
print(f"execution_status: {data.get('execution_status')}")

with open("_ecommerce_scan_result.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print("Saved to _ecommerce_scan_result.json")
