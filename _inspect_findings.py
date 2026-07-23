"""Inspect finding structure to understand missing fields."""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

p = Path("_scan_result_latest.json")
with open(p, encoding="utf-8") as f:
    result = json.load(f)

findings = result.get("findings") or []
print(f"Total findings: {len(findings)}")

# Show first finding structure
if findings:
    print("\n=== FIRST FINDING KEYS ===")
    f0 = findings[0]
    print(sorted(f0.keys()))
    print("\n=== FIRST FINDING (truncated) ===")
    # Print without large nested objects
    for k, v in f0.items():
        if isinstance(v, (dict, list)):
            print(f"  {k}: [{type(v).__name__} len={len(v)}]")
        else:
            print(f"  {k}: {v}")

# Show conservation finding
print("\n=== CONSERVATION FINDING ===")
for f_item in findings:
    cat = f_item.get("category") or f_item.get("risk_family") or ""
    if "conservation" in cat:
        for k, v in f_item.items():
            if isinstance(v, (dict, list)):
                s = json.dumps(v, ensure_ascii=False, default=str)
                print(f"  {k}: {s[:300]}")
            else:
                print(f"  {k}: {v}")
        break

# Check evidence structure
print("\n=== EVIDENCE STRUCTURE (first 3 findings) ===")
for i, f_item in enumerate(findings[:3]):
    ev = f_item.get("evidence") or {}
    print(f"\nFinding {i}: {f_item.get('finding_id', f_item.get('title', '?'))}")
    print(f"  evidence keys: {sorted(ev.keys()) if ev else 'NONE'}")
    if ev:
        for k, v in ev.items():
            if isinstance(v, (dict, list)):
                print(f"    {k}: [{type(v).__name__} len={len(v)}]")
            else:
                s = str(v)
                print(f"    {k}: {s[:100]}")

# Check if there's obligation_attempt_ledger with more detail
ledger = result.get("obligation_attempt_ledger") or {}
attempts = ledger.get("attempts") or []
print(f"\n=== LEDGER ATTEMPTS: {len(attempts)} ===")
if attempts:
    # Find a DELIVERABLE attempt
    for a in attempts[:5]:
        print(f"  {a.get('obligation_id', '?')}: status={a.get('terminal_status', '?')} risk_family={a.get('risk_family', '?')}")

# Check for causal obligations in ledger
print("\n=== CAUSAL OBLIGATIONS IN LEDGER ===")
causal_attempts = [a for a in attempts if a.get("risk_family") == "causal_postcondition" or "causal" in str(a.get("obligation_type", ""))]
print(f"Total causal attempts: {len(causal_attempts)}")
causal_statuses = {}
for a in causal_attempts:
    s = a.get("terminal_status", "?")
    causal_statuses[s] = causal_statuses.get(s, 0) + 1
print(f"Causal status distribution: {causal_statuses}")

# Show a few causal attempts
for a in causal_attempts[:3]:
    print(f"  {a.get('obligation_id', '?')}: status={a.get('terminal_status')} reason={a.get('terminal_reason', a.get('reason', '?'))}")
