"""P0-13: Project A regression verification."""
import json
from collections import Counter

print("=" * 60)
print("P0-13: Project A (benchmark_mall) Regression Verification")
print("=" * 60)

# Load Project A scan result
try:
    d = json.load(open("_scan_result_p13_v2.json", encoding="utf-8"))
    print("\nLoaded: _scan_result_p13_v2.json")
except Exception as e:
    print(f"Error loading: {e}")
    exit(1)

# Basic metrics
print(f"\n=== Basic Metrics ===")
print(f"  success: {d.get('success')}")
print(f"  grade: {d.get('grade')}")
print(f"  total_findings: {d.get('total_findings')}")
print(f"  total_candidates: {d.get('total_candidates')}")
print(f"  execution_status: {d.get('execution_status')}")
print(f"  total_ms: {d.get('total_ms')}")

# Obligation ledger
ledger = d.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
print(f"\n=== Obligation Ledger ({len(attempts)} attempts) ===")
status_counter = Counter(a.get("terminal_status", "?") for a in attempts)
print(f"  Terminal statuses: {dict(status_counter)}")

# Risk families
risk_families = Counter(a.get("risk_family", "unknown") for a in attempts)
print(f"\n=== Risk Families ===")
for rf, count in risk_families.most_common():
    print(f"  {rf}: {count}")

# DELIVERABLE obligations
deliverable = [a for a in attempts if a.get("terminal_status") == "DELIVERABLE"]
print(f"\n=== DELIVERABLE ({len(deliverable)}) ===")

# Findings
findings = d.get("findings", [])
print(f"\n=== Findings ({len(findings)}) ===")
for f in findings[:5]:
    title = f.get("title", "?")[:60]
    sev = f.get("severity", "?")
    print(f"  [{sev}] {title}")

# Discovery funnel
funnel = d.get("discovery_funnel", {})
print(f"\n=== Discovery Funnel ===")
print(f"  validated_bug_count: {funnel.get('validated_bug_count')}")
print(f"  canonical_defect_count: {funnel.get('canonical_defect_count')}")
print(f"  candidate_count: {funnel.get('candidate_count')}")

# Regression check
print("\n" + "=" * 60)
print("REGRESSION CHECK")
print("=" * 60)

# Compare with expected baseline
baseline_obligations = 500  # Expected ~500+ obligations
baseline_deliverable = 20   # Expected ~20+ DELIVERABLE

obligations_ok = len(attempts) >= baseline_obligations
deliverable_ok = len(deliverable) >= baseline_deliverable

print(f"  Obligations: {len(attempts)} (baseline: {baseline_obligations}+) [{'PASS' if obligations_ok else 'FAIL'}]")
print(f"  DELIVERABLE: {len(deliverable)} (baseline: {baseline_deliverable}+) [{'PASS' if deliverable_ok else 'FAIL'}]")
print(f"  Findings: {len(findings)}")

retention = min(100, len(attempts) / baseline_obligations * 100)
print(f"\n  Retention rate: {retention:.0f}%")
print(f"  P0-13 PASS: {retention >= 90}")
