"""Ingest project sources via knowledge API."""
import requests
import json
import time
from pathlib import Path

BASE = "http://127.0.0.1:8088"
PROJECT = "qb_ecommerce_single_retest"
INPUT_DIR = Path("d:/QualiBug-AI/QualiBug-AI-main/.tmp_single_ecommerce_suite/projects/qb_ecommerce_single_retest/input")

print(f"Ingesting sources for {PROJECT} at {time.strftime('%H:%M:%S')}...")

# Read all input files
sources = []
if INPUT_DIR.exists():
    for f in sorted(INPUT_DIR.iterdir()):
        if f.is_file() and f.suffix in ['.md', '.sql', '.csv', '.json', '.yaml', '.yml']:
            content = f.read_text(encoding='utf-8', errors='replace')
            sources.append({
                "filename": f.name,
                "content": content,
                "source_type": "api_spec" if "api" in f.name.lower() else "prd",
            })
    print(f"Found {len(sources)} source files")

# Try knowledge ingest endpoint
r = requests.post(
    f"{BASE}/api/knowledge/ingest",
    json={
        "project": PROJECT,
        "sources": sources,
    },
    headers={"X-Project": PROJECT},
    timeout=300,
)
print(f"Ingest status: {r.status_code}")
print(f"Response: {r.text[:500]}")
