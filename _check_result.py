"""Check scan result for errors."""
import json

d = json.load(open("_scan_result_latest.json", encoding="utf-8"))
print("success:", d.get("success"))
print("error:", str(d.get("error", ""))[:300])

# Check for traceback
for key in ("traceback", "detail", "debug", "exception"):
    if d.get(key):
        print(f"{key}: {str(d[key])[:500]}")

# Check ledger
ledger = d.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
print(f"\nTotal attempts: {len(attempts)}")

# Terminal status distribution
statuses = {}
for a in attempts:
    s = a.get("terminal_status", "UNKNOWN")
    statuses[s] = statuses.get(s, 0) + 1
print("Terminal statuses:", statuses)

# Check HARNESS_FAILED
harness_failed = [a for a in attempts if a.get("terminal_status") == "HARNESS_FAILED"]
print(f"\nHARNESS_FAILED: {len(harness_failed)}")
if harness_failed:
    a = harness_failed[0]
    print(f"  reason_code: {a.get('reason_code')}")
    print(f"  reason_detail: {str(a.get('reason_detail', ''))[:200]}")
