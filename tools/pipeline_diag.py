#!/usr/bin/env python3
"""Pipeline bottleneck diagnostic tool.
Analyzes scan results and prints clear, actionable insights.
Usage: python tools/pipeline_diag.py [scan_json_path]
"""
import json, sys
from collections import Counter
from pathlib import Path

def diag(scan_path: str = None):
    if scan_path:
        path = Path(scan_path)
    else:
        # Find latest scan
        outputs = sorted(Path("platform_outputs").glob("benchmark_mall_v*/intelligence_report.json"))
        if not outputs:
            print("No scan results found.")
            return
        path = outputs[-1]
    
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    
    ledger = data.get("obligation_attempt_ledger", {})
    attempts = ledger.get("attempts", [])
    
    print(f"=== Pipeline Diagnostic: {path.name} ===")
    print(f"Run: {ledger.get('run_id', '?')}")
    print(f"Campaign: {ledger.get('campaign_id', '?')}")
    print()
    
    # Overall stats
    total = len(attempts)
    statuses = Counter(a.get("terminal_status", "?") for a in attempts)
    print(f"Total obligations: {total}")
    for s, c in statuses.most_common():
        pct = c / max(1, total) * 100
        print(f"  {s}: {c} ({pct:.0f}%)")
    print()
    
    # By risk family
    families = Counter(a.get("risk_family", "?") for a in attempts)
    print("By risk family:")
    for f, c in families.most_common():
        deliverable = sum(1 for a in attempts if a.get("risk_family") == f and a.get("terminal_status") == "DELIVERABLE")
        print(f"  {f}: {c} total, {deliverable} deliverable ({deliverable/max(1,c)*100:.0f}%)")
    print()
    
    # Blocked reasons
    blocked = [a for a in attempts if a.get("terminal_status") == "BLOCKED"]
    if blocked:
        reasons = Counter(a.get("reason_code", "?") for a in blocked)
        print("BLOCKED reasons:")
        for r, c in reasons.most_common():
            families = Counter(a.get("risk_family", "?") for a in blocked if a.get("reason_code") == r)
            fam_str = ", ".join(f"{f}:{n}" for f, n in families.most_common(3))
            print(f"  {r}: {c} [{fam_str}]")
        print()
    
    # HARNESS_FAILED analysis
    harness = [a for a in attempts if a.get("terminal_status") == "HARNESS_FAILED"]
    if harness:
        print(f"HARNESS_FAILED: {len(harness)}")
        for a in harness[:3]:
            obs = len(a.get("observation_receipt_ids", []))
            print(f"  {a.get('risk_family')}: {obs} observations, oracle={a.get('oracle_reason_code','?')}")
        print()
    
    # Formal deliverables
    fcp = data.get("formal_count_projection", {})
    print(f"Formal deliverables: {fcp.get('formal_customer_deliverable_count', 0)}")
    print(f"Canonical defects: {fcp.get('canonical_defect_count', 0)}")
    print()
    
    # Findings detail
    findings = data.get("findings", [])
    if findings:
        print(f"=== Findings ({len(findings)}) ===")
        cats = Counter(f.get("category", "?") for f in findings)
        for cat, count in cats.most_common():
            print(f"  {cat}: {count}")
        print()
        for f in findings[:5]:
            sev = f.get("severity", "?")
            title = f.get("title", "?")[:100]
            print(f"  [{sev}] {title}")
        if len(findings) > 5:
            print(f"  ... and {len(findings)-5} more")
        print()

if __name__ == "__main__":
    diag(sys.argv[1] if len(sys.argv) > 1 else None)
