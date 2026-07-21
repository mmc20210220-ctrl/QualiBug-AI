# -*- coding: utf-8 -*-
"""Check which endpoints the scan is probing."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path
from collections import Counter

# Load current scan result
d = json.loads(Path("scan_fresh_result.json").read_text(encoding="utf-8"))
findings = d.get("findings", [])

# Extract paths from findings
paths = []
for f in findings:
    evidence = f.get("evidence", {})
    if isinstance(evidence, dict):
        request = evidence.get("request", "")
        if request:
            parts = request.split(" ", 1)
            if len(parts) == 2:
                paths.append(parts[1].split("?")[0])

# Count by prefix
prefix_counts = Counter()
for p in paths:
    parts = p.strip("/").split("/")
    if parts:
        prefix_counts[parts[0]] += 1

print("Findings by endpoint prefix:")
for prefix, count in prefix_counts.most_common():
    print(f"  /{prefix}: {count}")

# Check candidate findings
candidates = d.get("candidate_findings", [])
print(f"\nCandidate findings: {len(candidates)}")

# Check obligation attempt ledger
ledger = d.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
print(f"Obligation attempts: {len(attempts)}")

# Count by status
status_counts = Counter(a.get("status") for a in attempts)
print("\nObligation status distribution:")
for status, count in status_counts.most_common():
    print(f"  {status}: {count}")

# Check which endpoints are being probed
probed_paths = set()
for a in attempts:
    exp = a.get("experiment", {})
    if isinstance(exp, dict):
        for step in exp.get("treatment_plan", []):
            if isinstance(step, dict):
                op_ref = step.get("operation_ref", "")
                if op_ref:
                    probed_paths.add(op_ref)

print(f"\nProbed operation refs: {len(probed_paths)}")
for p in sorted(probed_paths)[:20]:
    print(f"  - {p}")
