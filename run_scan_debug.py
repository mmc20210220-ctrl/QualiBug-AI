# -*- coding: utf-8 -*-
"""Run scan with full traceback."""
import sys, io, json, time, os, traceback
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")

from pathlib import Path

# Install DB audit hook
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

try:
    result = scan(
        "benchmark_mall",
        root=root,
        base_url="http://localhost:8080",
        campaign_context=campaign_context,
        save_report=False,
    )
    print(f"Scan success: {result.get('success')}")
    print(f"Findings: {len(result.get('findings', []))}")
except Exception as e:
    print(f"ERROR: {e}")
    traceback.print_exc()
