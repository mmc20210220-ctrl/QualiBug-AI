"""Run scan with explicit api_doc."""
import requests
import json
import time
from pathlib import Path

PROJECT = "qb_ecommerce_single_retest"
INPUT_DIR = Path("platform_workspace/qb_ecommerce_single_retest/input")

# Read API_DOCS.md as api_doc
api_doc = ""
api_file = INPUT_DIR / "API_DOCS.md"
if api_file.exists():
    api_doc = api_file.read_text(encoding="utf-8")
    print(f"Loaded API doc: {len(api_doc)} chars")

# Read PRD.md as prd
prd = ""
prd_file = INPUT_DIR / "PRD.md"
if prd_file.exists():
    prd = prd_file.read_text(encoding="utf-8")
    print(f"Loaded PRD: {len(prd)} chars")

print(f"Starting scan at {time.strftime('%H:%M:%S')}...")
r = requests.post(
    "http://127.0.0.1:8088/api/v1/scan",
    json={
        "project": PROJECT,
        "base_url": "http://localhost:8000",
        "api_doc": api_doc,
        "prd": prd,
        "environment_type": "test",
    },
    headers={"X-Project": PROJECT},
    timeout=1800,
)
print(f"Status: {r.status_code}")
data = r.json()
print(f"ok: {data.get('ok')}")
print(f"total_findings: {data.get('total_findings')}")
campaign = data.get("campaign", {})
print(f"campaign_id: {campaign.get('campaign_id', '')[:40] if isinstance(campaign, dict) else campaign}")
print(f"execution_status: {data.get('execution_status')}")
print(f"total_ms: {data.get('total_ms')}")

with open("_ecommerce_scan_result.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print("Saved to _ecommerce_scan_result.json")
