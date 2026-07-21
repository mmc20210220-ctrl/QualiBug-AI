# -*- coding: utf-8 -*-
"""Run scan and print full result."""
import sys, io, json, time, os
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")

from pathlib import Path

from ai_test_asset_center.private_pilot_db_audit_patch import install
install()

from ai_test_asset_center.__main__ import scan

root = Path(r"d:\QualiBug-AI\QualiBug-AI-main")

campaign_context = {
    'target_id': 'benchmark_mall_v05',
    'environment_id': 'local_test',
    'scope_id': 'benchmark_mall_scope',
    'environment_ref': 'local_test_env',
    'environment_type': 'test',
    'execution_mode': 'approved_sandbox_write',
}

result = scan(
    "benchmark_mall",
    root=root,
    base_url="http://localhost:8080",
    campaign_context=campaign_context,
    save_report=False,
)
print(f"success: {result.get('success')}")
print(f"error: {result.get('error', 'n/a')}")
# Print v12 error if present
v12 = result.get('v12', {})
if v12.get('error'):
    print(f"v12.error: {v12.get('error')}")
# Check mainline_run
mr = result.get('mainline_run', {})
print(f"mainline_run keys: {list(mr.keys())[:10]}")
