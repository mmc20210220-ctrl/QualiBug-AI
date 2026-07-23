"""Ingest all required sources."""
import requests
import json
import time
import base64
from pathlib import Path

PROJECT = "qb_ecommerce_single_retest"
INPUT_DIR = Path("platform_workspace/qb_ecommerce_single_retest/input")
BASE = "http://127.0.0.1:8088"

# Files to ingest with their types
files_to_ingest = [
    ("PRD.md", "prd"),
    ("DATABASE_DESIGN.md", "database_schema"),
    ("schema.sql", "database_schema"),
    ("acceptance_scenarios.md", "prd"),
]

for filename, doc_type in files_to_ingest:
    filepath = INPUT_DIR / filename
    if not filepath.exists():
        print(f"SKIP: {filename} not found")
        continue
    
    content = filepath.read_bytes()
    content_b64 = base64.b64encode(content).decode("ascii")
    print(f"Ingesting {filename} ({len(content)} bytes) as {doc_type}...")
    
    r = requests.post(
        f"{BASE}/api/knowledge/ingest",
        json={
            "project": PROJECT,
            "filename": filename,
            "type": doc_type,
            "content": content_b64,
        },
        headers={"X-Project": PROJECT},
        timeout=300,
    )
    
    if r.status_code == 200:
        data = r.json()
        print(f"  ok: {data.get('ok')}, source_id: {data.get('source_id', '')[:30]}")
    else:
        print(f"  FAILED: {r.status_code} - {r.text[:100]}")

print("\nDone ingesting sources.")
