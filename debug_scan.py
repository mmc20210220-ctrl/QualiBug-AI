# -*- coding: utf-8 -*-
"""Debug scan to understand early return."""
import sys, io, json, time
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

print("Running scan with debug...")
started = time.time()

# Call _scan_impl directly to see what happens
from ai_test_asset_center.__main__ import _scan_impl
result = _scan_impl(
    "benchmark_mall",
    root=root,
    base_url="http://localhost:8080",
    campaign_context=campaign_context,
    save_report=False,
)
elapsed = time.time() - started

print(f"\nScan completed in {elapsed:.1f}s")
print(f"Result keys: {list(result.keys())[:20]}")
print(f"success: {result.get('success')}")
print(f"error: {result.get('error')}")

rc = result.get('runtime_contract', {})
print(f"runtime_contract.status: {rc.get('status')}")
print(f"runtime_contract.reason: {rc.get('reason', 'n/a')}")

findings = result.get('findings', [])
print(f"findings: {len(findings)}")
