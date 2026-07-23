"""Run scan with registered source manifest."""
import requests
import json
import time

PROJECT = "qb_ecommerce_single_retest"

print(f"Starting scan at {time.strftime('%H:%M:%S')}...")
r = requests.post(
    "http://127.0.0.1:8088/api/v1/scan",
    json={
        "project": PROJECT,
        "base_url": "http://localhost:8000",
        "environment_type": "test",
        "source_manifest": {
            "source_id": "src_b818d92199a29ff7",
            "source_hash": "94a66d9e458e9f102b8d06c6d2b4603d3f609fbef3305edf1fa9f4063ba305f6",
        },
    },
    headers={"X-Project": PROJECT},
    timeout=1800,
)
print(f"Status: {r.status_code}")
data = r.json()
print(f"ok: {data.get('ok')}")
print(f"total_findings: {data.get('total_findings')}")
campaign = data.get("campaign", {})
if isinstance(campaign, dict):
    print(f"campaign_id: {campaign.get('campaign_id', '')[:40]}")
    print(f"campaign_status: {campaign.get('campaign_status')}")
print(f"execution_status: {data.get('execution_status')}")
print(f"total_ms: {data.get('total_ms')}")

with open("_ecommerce_scan_result.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print("Saved to _ecommerce_scan_result.json")
