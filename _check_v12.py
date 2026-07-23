"""Check new scan result v12 data."""
import json
from pathlib import Path

r = json.loads(Path("_scan_project_c_result.json").read_text(encoding="utf-8"))
v12 = r.get("v12", {})
print("v12 keys:", list(v12.keys())[:15])

bir = v12.get("behavior_ir", {})
entities = bir.get("entities", [])
invariants = bir.get("invariants", [])
print(f"behavior_ir: {len(entities)} entities, {len(invariants)} invariants")

# Check obligation attempts
ledger = v12.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
print(f"attempts: {len(attempts)}")

if attempts:
    from collections import Counter
    reasons = Counter(a.get("reason_code", "?") for a in attempts)
    print("Reason codes:")
    for rc, c in reasons.most_common(15):
        print(f"  {rc}: {c}")
    
    # Check ASSERTION_INDETERMINATE
    indeterminate = [a for a in attempts if a.get("reason_code") == "ASSERTION_INDETERMINATE"]
    print(f"\nASSERTION_INDETERMINATE: {len(indeterminate)}")
    
    # Check for conservation attempts with structured_expression
    conservation = [a for a in attempts if a.get("risk_family") == "conservation"]
    print(f"Conservation attempts: {len(conservation)}")

# Check findings
findings = r.get("findings", [])
candidates = r.get("candidate_findings", [])
print(f"\nFindings: {len(findings)}")
print(f"Candidates: {len(candidates)}")
for f in findings[:5]:
    print(f"  [F] {f.get('title', 'N/A')[:80]}")
for c in candidates[:5]:
    print(f"  [C] {c.get('title', 'N/A')[:80]}")
