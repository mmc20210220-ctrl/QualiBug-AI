# -*- coding: utf-8 -*-
"""Test IR-driven DB audit against benchmark."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")

from ai_test_asset_center.db_state_audit import run_db_state_audit

# Load behavior IR from scan result
path = r"d:\QualiBug-AI\QualiBug-AI-main\platform_outputs\benchmark_mall\scan_result.json"
with open(path, 'r', encoding='utf-8') as f:
    result = json.load(f)
bir = result.get('v12', result).get('behavior_ir', {})

DSN = "postgresql://benchmark_user:benchmark_pass@localhost:5432/benchmark_mall"
findings = run_db_state_audit(bir, DSN)

print(f"IR驱动DB审计: {len(findings)}个findings\n")
for i, f in enumerate(findings):
    print(f"--- Finding {i+1} ---")
    print(f"  title: {f['title']}")
    print(f"  category: {f['category']}")
    print(f"  family: {f.get('defect_family','')}")
    print(f"  severity: {f['severity']}")
    print(f"  actual: {f.get('actual','')[:120]}")
    print()

# Save for evaluator
with open("db_audit_findings.json", 'w', encoding='utf-8') as fp:
    json.dump(findings, fp, ensure_ascii=False, indent=2)
print(f"已保存到 db_audit_findings.json")
