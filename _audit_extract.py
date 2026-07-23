"""Extract all 33 findings with full evidence for audit."""
import json, sys
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

p = Path("_scan_result_latest.json")
with open(p, encoding="utf-8") as f:
    result = json.load(f)

findings = result.get("findings") or []
print(f"Total findings: {len(findings)}")

# Extract structured summary of each finding
audit_data = []
for i, f in enumerate(findings):
    entry = {
        "index": i,
        "finding_id": f.get("finding_id"),
        "title": f.get("title"),
        "category": f.get("category"),
        "risk_family": f.get("risk_family"),
        "severity": f.get("severity"),
        "confidence_score": f.get("confidence_score"),
        "semantic_verdict": f.get("semantic_verdict"),
        "customer_delivery_status": f.get("customer_delivery_status"),
        "obligation_id": f.get("obligation_id"),
        "experiment_id": f.get("experiment_id"),
        "execution_status": f.get("execution_status"),
        "confirmation_status": f.get("confirmation_status"),
        "bug_status": f.get("bug_status"),
        # Evidence
        "expected": f.get("expected"),
        "actual": f.get("actual"),
        "description": f.get("description"),
        # Raw evidence
        "raw_evidence": f.get("raw_evidence"),
        # Evidence details
        "evidence": f.get("evidence"),
        # Oracle
        "oracle": f.get("oracle"),
        # Failed assertions
        "failed_assertions": f.get("failed_assertions"),
        # Reproduction
        "reproduction_steps": f.get("reproduction_steps"),
        "reproduction_receipt": f.get("reproduction_receipt"),
        # Delivery gate
        "delivery_gate_receipt": f.get("delivery_gate_receipt"),
        "canonical_defect_id": f.get("canonical_defect_id"),
        "delivery_occurrence_count": f.get("delivery_occurrence_count"),
        "delivery_occurrence_finding_ids": f.get("delivery_occurrence_finding_ids"),
    }
    audit_data.append(entry)

# Save full audit data
Path("_audit_findings_full.json").write_text(
    json.dumps(audit_data, indent=2, ensure_ascii=False, default=str),
    encoding="utf-8"
)
print(f"Saved full audit data to _audit_findings_full.json")

# Print summary table
print(f"\n{'='*100}")
print(f"{'#':<3} {'finding_id':<30} {'category':<25} {'risk_family':<15} {'verdict':<22} {'occ':<4}")
print(f"{'='*100}")
for e in audit_data:
    print(f"{e['index']:<3} {e['finding_id']:<30} {e['category']:<25} {e['risk_family']:<15} {e['semantic_verdict']:<22} {e['delivery_occurrence_count']:<4}")

# Group by category
print(f"\n=== BY CATEGORY ===")
by_cat = {}
for e in audit_data:
    cat = e["category"]
    by_cat.setdefault(cat, []).append(e)
for cat, items in sorted(by_cat.items(), key=lambda x: -len(x[1])):
    print(f"  {cat}: {len(items)}")

# Group by canonical_defect_id (dedup)
print(f"\n=== BY CANONICAL DEFECT ID (dedup) ===")
by_defect = {}
for e in audit_data:
    did = e.get("canonical_defect_id") or "none"
    by_defect.setdefault(did, []).append(e)
print(f"  Unique canonical defects: {len(by_defect)}")
for did, items in sorted(by_defect.items(), key=lambda x: -len(x[1])):
    if len(items) > 1:
        print(f"  {did}: {len(items)} findings")
        for it in items:
            print(f"    - {it['finding_id']} [{it['category']}] {it['title'][:60]}")

# Check reproduction receipts
print(f"\n=== REPRODUCTION STATUS ===")
reproduced = 0
for e in audit_data:
    rr = e.get("reproduction_receipt") or {}
    status = rr.get("status") or rr.get("reproduction_status") or "unknown"
    if status in ("reproduced", "REPRODUCED", "confirmed"):
        reproduced += 1
print(f"  Findings with reproduction receipt: {sum(1 for e in audit_data if e.get('reproduction_receipt'))}")
print(f"  Reproduced status: {reproduced}")

# Check evidence quality
print(f"\n=== EVIDENCE QUALITY ===")
for e in audit_data[:3]:
    ev = e.get("evidence") or {}
    raw = e.get("raw_evidence") or {}
    print(f"  {e['finding_id']}:")
    print(f"    has_real_evidence: {raw.get('has_real_evidence')}")
    print(f"    execution_semantics: {ev.get('execution_semantics')}")
    print(f"    control_succeeded: {ev.get('control_succeeded')}")
