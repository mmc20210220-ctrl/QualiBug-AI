# -*- coding: utf-8 -*-
"""Test DSN discovery and run full scan with DB audit hook."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")

from pathlib import Path
from ai_test_asset_center.private_pilot_db_audit_patch import _discover_dsn, install

# Test DSN discovery
root = Path(r"d:\QualiBug-AI\QualiBug-AI-main")
dsn = _discover_dsn("benchmark_mall", root)
print(f"DSN discovered: {dsn[:50]}..." if dsn else "DSN NOT FOUND")

if not dsn:
    # Try parent directory (benchmark_mall is at D:\QualiBug-AI\benchmark_mall)
    root2 = Path(r"D:\QualiBug-AI")
    dsn = _discover_dsn("benchmark_mall", root2)
    print(f"DSN from parent: {dsn[:50]}..." if dsn else "DSN NOT FOUND from parent")

# Install hook and run scan
install()

from ai_test_asset_center.scan_post_hooks import list_scan_post_hooks
print(f"Registered hooks: {list_scan_post_hooks()}")

# Run scan
from ai_test_asset_center.__main__ import scan

campaign_context = {
    'target_id': 'benchmark_mall_v05',
    'environment_id': 'local_test',
    'scope_id': 'benchmark_mall_scope',
    'environment_ref': 'local_test_env',
    'execution_mode': 'approved_sandbox_write',
}

print("\nRunning scan with DB audit hook...")
result = scan(
    "benchmark_mall",
    root=root,
    base_url="http://localhost:8080",
    campaign_context=campaign_context,
    save_report=False,
)

# Check results
db_findings = result.get('db_findings', [])
all_findings = result.get('findings', [])
print(f"\nScan complete!")
print(f"  db_findings: {len(db_findings)}")
print(f"  total findings: {len(all_findings)}")
print(f"  total_findings field: {result.get('total_findings')}")

for f in db_findings:
    print(f"  [DB] {f['title'][:70]}")

# Save for evaluation
with open("scan_with_db_audit.json", 'w', encoding='utf-8') as fp:
    json.dump(result, fp, ensure_ascii=False, indent=2, default=str)
print("\nSaved to scan_with_db_audit.json")
