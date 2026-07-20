"""Trigger a scan and report results."""
import requests
import json

r = requests.post("http://localhost:8088/api/v1/scan", json={
    "project_id": "benchmark_mall_131",
    "base_url": "http://localhost:8080",
    "source_manifest": {
        "source_id": "src_6efcb7cd38ce74d3",
        "source_hash": "99a0209c562b736d13a2bd35a81ba6d2b2a5b2f00941a806f1d7ed3118b57703",
    },
    "environment_type": "test",
    "environment_ref": "benchmark_local",
    "scope_id": "benchmark_mall_131",
    "target_id": "benchmark_mall_131",
}, headers={"X-Project": "benchmark_mall_131"}, timeout=600)
data = r.json()
print(f"status={r.status_code}")
print(f"response keys: {list(data.keys())[:20]}")
print(f"success={data.get('success')}")
print(f"ok={data.get('ok')}")
print(f"findings={data.get('total_findings')}")
print(f"error={str(data.get('error', ''))[:200]}")
# Print campaign info if available
campaign = data.get("campaign") or {}
if campaign:
    print(f"campaign_id={campaign.get('campaign_id', '')[:40]}")
# Save full response for debugging
with open('_scan_response.json', 'w', encoding='utf-8') as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print('Full response saved to _scan_response.json')
