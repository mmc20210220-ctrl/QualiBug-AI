"""Ingest API doc as registered source."""
import requests
import json
import time
import base64
from pathlib import Path

PROJECT = "qb_ecommerce_single_retest"
INPUT_DIR = Path("platform_workspace/qb_ecommerce_single_retest/input")

# Read API_DOCS.md
api_file = INPUT_DIR / "API_DOCS.md"
if not api_file.exists():
    print(f"API file not found: {api_file}")
    exit(1)

content = api_file.read_bytes()
content_b64 = base64.b64encode(content).decode("ascii")
print(f"API doc size: {len(content)} bytes, base64: {len(content_b64)} chars")

# Ingest via knowledge API
print(f"Ingesting at {time.strftime('%H:%M:%S')}...")
r = requests.post(
    "http://127.0.0.1:8088/api/knowledge/ingest",
    json={
        "project": PROJECT,
        "filename": "API_DOCS.md",
        "type": "api_spec",
        "content": content_b64,
    },
    headers={"X-Project": PROJECT},
    timeout=300,
)
print(f"Status: {r.status_code}")
data = r.json()
print(f"ok: {data.get('ok')}")
print(f"Response: {json.dumps(data, indent=2, ensure_ascii=False)[:1000]}")
