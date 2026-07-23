"""Check obligations and rules from scan result."""
import json
from collections import Counter

d = json.load(open("_scan_result_project_b.json", encoding="utf-8"))

# Check obligation_attempt_ledger
ledger = d.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
print(f"=== Obligation Attempts: {len(attempts)} ===")

# Count by terminal status
status_counter = Counter(a.get("terminal_status", "?") for a in attempts)
print(f"Terminal statuses: {dict(status_counter)}")

# Check obligation types
obl_types = Counter()
for a in attempts:
    oid = a.get("obligation_id", "")
    # Extract type from obligation_id (e.g., "obl_state_transition_..." -> "state_transition")
    parts = oid.split("_")
    if len(parts) >= 3:
        obl_type = "_".join(parts[1:3]) if parts[0] == "obl" else parts[0]
        obl_types[obl_type] += 1

print(f"\nObligation types (top 15):")
for t, c in obl_types.most_common(15):
    print(f"  {c:4d}x {t}")

# Check discovery_funnel
funnel = d.get("discovery_funnel", {})
print(f"\n=== Discovery Funnel ===")
for k, v in funnel.items():
    if isinstance(v, (int, float, str)):
        print(f"  {k}: {v}")

# Check campaign info
camp = d.get("campaign", {})
print(f"\n=== Campaign ===")
print(f"  campaign_id: {camp.get('campaign_id')}")
print(f"  round_count: {camp.get('round_count')}")
print(f"  slice_budget: {camp.get('slice_budget')}")

# Check source_knowledge
sk = d.get("source_knowledge", {})
if sk:
    print(f"\n=== Source Knowledge ===")
    print(f"  keys: {list(sk.keys())[:10]}")
