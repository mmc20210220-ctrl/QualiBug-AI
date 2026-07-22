"""Check findings in the result file."""
import json

d = json.load(open("_scan_result_latest.json", encoding="utf-8"))
print("Top-level keys:", list(d.keys())[:20])
print("total_findings:", d.get("total_findings"))
print("findings type:", type(d.get("findings")))
print("findings len:", len(d.get("findings", [])) if isinstance(d.get("findings"), list) else "N/A")

# Check ledger for DELIVERABLE
ledger = d.get("obligation_attempt_ledger", {})
attempts = ledger.get("attempts", [])
deliverable = [a for a in attempts if a.get("terminal_status") == "DELIVERABLE"]
print(f"\nDELIVERABLE attempts: {len(deliverable)}")

if deliverable:
    a = deliverable[0]
    print(f"  finding_id: {a.get('finding_id')}")
    bundle = a.get("delivery_evidence_bundle", {})
    finding = bundle.get("finding", {}) if isinstance(bundle, dict) else {}
    print(f"  bundle finding title: {finding.get('title', '?')[:80]}")
    
    # Check all deliverable findings
    print("\nAll DELIVERABLE findings:")
    for i, att in enumerate(deliverable[:10]):
        b = att.get("delivery_evidence_bundle", {})
        f = b.get("finding", {}) if isinstance(b, dict) else {}
        title = f.get("title", "?")[:70]
        risk = f.get("risk_family", "?")
        print(f"  {i}: [{risk}] {title}")
