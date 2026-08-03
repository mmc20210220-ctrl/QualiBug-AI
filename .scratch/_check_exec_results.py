# -*- coding: utf-8 -*-
"""Check what experiments were actually executed for the 9 ORACLE_NOT_VIOLATED targets."""
import json, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Load deep execution results (1.3MB)
der_path = ROOT / "deep_experiment_execution_results.json"
der = json.loads(der_path.read_text(encoding="utf-8"))
print(f"Top keys: {list(der.keys())[:20]}")

# Check structure
if isinstance(der, dict):
    results = der.get("results", der.get("experiments", der.get("execution_results", [])))
    if isinstance(results, list):
        print(f"Results count: {len(results)}")
    elif isinstance(results, dict):
        print(f"Results keys: {list(results.keys())[:10]}")
    
    # Check for experiments
    exps = der.get("experiments", [])
    print(f"Experiments: {len(exps) if isinstance(exps, list) else 'dict'}")
    
    # Check findings
    findings = der.get("findings", [])
    print(f"Findings: {len(findings) if isinstance(findings, list) else 'dict'}")
    
    # Check oracle results
    oracle = der.get("oracle_stats", der.get("oracle_results", {}))
    print(f"Oracle: {json.dumps(oracle, indent=2)[:500] if oracle else 'none'}")

# Look for target operations
target_ops = [
    "PATCH /api/v1/contracts/{id}",       # CF-CON-001
    "POST /api/v1/contracts/{id}/submit",  # CF-CON-003
    "POST /api/v1/contracts/{id}/activate",# CF-BUD-001
    "POST /api/v1/contracts/{id}/cancel",  # CF-BUD-002, CF-PAY-001
    "POST /api/v1/payment-requests",       # CF-TIME-001, CF-PAY-004
    "POST /api/v1/payment-requests/{id}/pay", # CF-STATE-004, CF-BUD-003
]

# Search through all results for these operations
print("\n--- Searching for target operations in results ---")
all_text = json.dumps(der, ensure_ascii=False)
for op in target_ops:
    count = all_text.count(op.replace("{id}", ""))
    print(f"  {op}: {count} mentions")

# Check the mock server implementation
print("\n--- Mock server files ---")
mock_paths = list(ROOT.glob("**/mock_server*")) + list(ROOT.glob("**/contractflow*server*"))
for p in mock_paths[:10]:
    if ".pytest_tmp" not in str(p) and ".worktrees" not in str(p):
        print(f"  {p.relative_to(ROOT)} ({p.stat().st_size} bytes)")

# Check project_c_blind_baseline_seal for mock server
seal_dir = ROOT / "project_c_blind_baseline_seal"
if seal_dir.exists():
    print(f"\n--- project_c_blind_baseline_seal ---")
    for f in sorted(seal_dir.glob("*"))[:15]:
        print(f"  {f.name} ({f.stat().st_size} bytes)")
