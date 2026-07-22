"""Check latest scan results after delivery gate bypass fix."""
import json, sys
from pathlib import Path
from collections import Counter
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

raw = Path("_scan_result_latest.json").read_bytes()
d = json.loads(raw)
del raw

print(f"success={d.get('success')}")
print(f"total_findings={d.get('total_findings')}")
print(f"total_candidates={d.get('total_candidates')}")

if not d.get("success"):
    print(f"ERROR: {d.get('error','')}")
    sys.exit(1)

# Ledger stats
ledger = d.get("obligation_attempt_ledger") or {}
attempts = ledger.get("attempts") or []
tc = Counter(a.get("terminal_status", "?") for a in attempts if isinstance(a, dict))
print(f"\nLedger: {len(attempts)} attempts")
for k, v in tc.most_common():
    print(f"  {k}: {v}")

# Findings summary
findings = d.get("findings") or []
print(f"\n=== DELIVERED FINDINGS ({len(findings)}) ===")
for f in findings[:30]:
    if not isinstance(f, dict):
        continue
    title = f.get("title", "")[:80]
    cat = f.get("category") or f.get("assertion_kind") or "?"
    sev = f.get("severity", "?")
    http_ev = f.get("_http_evidence_violation", False)
    print(f"  [{sev}] {cat}: {title} {'[HTTP_EV]' if http_ev else ''}")

# Candidates
candidates = d.get("candidate_findings") or []
print(f"\n=== CANDIDATES ({len(candidates)}) ===")
gate_reasons = Counter()
for c in candidates:
    if isinstance(c, dict):
        reasons = c.get("customer_delivery_gate_reasons") or []
        for r in reasons:
            gate_reasons[r] += 1
for r, cnt in gate_reasons.most_common(10):
    print(f"  {r}: {cnt}")
