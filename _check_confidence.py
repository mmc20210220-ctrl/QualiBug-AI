"""Check obligation confidence levels."""
import json
from collections import Counter

d = json.load(open("_scan_result_project_b.json", encoding="utf-8"))

ledger = d.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])

print(f"=== Obligation Confidence Analysis ({len(attempts)} attempts) ===")

# Check confidence distribution
confidence_levels = Counter()
for a in attempts:
    conf = a.get("confidence", a.get("confidence_level", "unknown"))
    if isinstance(conf, (int, float)):
        if conf >= 0.8:
            confidence_levels["HIGH (>=0.8)"] += 1
        elif conf >= 0.5:
            confidence_levels["MEDIUM (0.5-0.8)"] += 1
        else:
            confidence_levels["LOW (<0.5)"] += 1
    else:
        confidence_levels[str(conf)] += 1

print(f"\nConfidence distribution:")
for level, count in confidence_levels.most_common():
    print(f"  {level}: {count}")

# Check DELIVERABLE obligations (these are HIGH_CONFIDENCE by definition)
deliverable = [a for a in attempts if a.get("terminal_status") == "DELIVERABLE"]
print(f"\n=== DELIVERABLE Obligations ({len(deliverable)}) ===")
for a in deliverable[:10]:
    oid = a.get("obligation_id", "?")
    reason = a.get("reason_code", "?")
    print(f"  {oid[:50]}: {reason}")

# Check obligation kinds
kinds = Counter()
for a in attempts:
    kind = a.get("obligation_kind", a.get("kind", "unknown"))
    kinds[kind] += 1

print(f"\n=== Obligation Kinds ===")
for kind, count in kinds.most_common(10):
    print(f"  {kind}: {count}")

# Check reason codes for DELIVERABLE
print(f"\n=== DELIVERABLE Reason Codes ===")
reasons = Counter(a.get("reason_code", "?") for a in deliverable)
for reason, count in reasons.most_common():
    print(f"  {reason}: {count}")
