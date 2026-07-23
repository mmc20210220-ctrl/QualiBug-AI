"""Ingest and scan Project C (ecommerce single)."""
import requests
import json
import time
from pathlib import Path

BASE = "http://127.0.0.1:8088"
PROJECT = "qb_ecommerce_single_retest"
ROOT = "d:/QualiBug-AI/QualiBug-AI-main/.tmp_single_ecommerce_suite"
INPUT_DIR = f"{ROOT}/projects/{PROJECT}/input"

# Step 1: Ingest project
print(f"Step 1: Ingesting project at {time.strftime('%H:%M:%S')}...")
ingest_files = {}
input_path = Path(INPUT_DIR)
if input_path.exists():
    for f in input_path.iterdir():
        if f.is_file() and f.suffix in ['.md', '.sql', '.csv', '.json', '.yaml', '.yml']:
            ingest_files[f.name] = f.read_text(encoding='utf-8', errors='replace')
    print(f"  Found {len(ingest_files)} input files")

r = requests.post(
    f"{BASE}/api/v1/ingest",
    json={
        "project": PROJECT,
        "root": ROOT,
        "files": ingest_files,
    },
    timeout=300,
)
print(f"  Ingest status: {r.status_code}")
if r.status_code == 200:
    ing = r.json()
    print(f"  Ingest ok: {ing.get('ok')}")
    print(f"  Ingest keys: {list(ing.keys())[:10]}")
else:
    print(f"  Ingest error: {r.text[:200]}")

# Step 2: Scan
print(f"\nStep 2: Scanning at {time.strftime('%H:%M:%S')}...")
r = requests.post(
    f"{BASE}/api/v1/scan",
    json={
        "project": PROJECT,
        "root": ROOT,
        "base_url": "http://localhost:8000",
    },
    timeout=1800,
)
print(f"  Scan status: {r.status_code}")
data = r.json()
print(f"  ok: {data.get('ok')}")
print(f"  total_findings: {data.get('total_findings')}")
print(f"  campaign_id: {data.get('campaign', {}).get('campaign_id', '')[:40]}")

with open("_ecommerce_scan_result.json", "w", encoding="utf-8") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
print("Saved to _ecommerce_scan_result.json")
