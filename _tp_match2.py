"""Quick TP match for latest scan."""
import json, sys, re
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Load GT
gt = json.loads(Path("_private_eval/_evaluator_private/benchmark_mall_131/bugs.json").read_text("utf-8"))
bugs = gt if isinstance(gt, list) else (gt.get("bugs") or [])
print(f"GT: {len(bugs)} bugs")

# Load scan - try _scan_result_latest.json first
scan_path = Path("_scan_result_latest.json")
if not scan_path.exists() or scan_path.stat().st_size < 1000:
    scan_path = Path("platform_outputs/benchmark_mall/intelligence_report.json")
scan = json.loads(scan_path.read_text("utf-8", errors="replace"))
findings = scan.get("findings", [])
candidates = scan.get("candidate_findings", [])
print(f"Scan: {len(findings)} findings, {len(candidates)} candidates (from {scan_path.name})")

# Show all findings
print(f"\n=== {len(findings)} DELIVERED FINDINGS ===")
for i, f in enumerate(findings):
    title = f.get("title", "")[:80]
    cat = f.get("category", "?")
    actual = f.get("actual", "?")
    expected = f.get("expected", "?")
    print(f"  [{i}] cat={cat[:25]:25s} expected={str(expected)[:10]:10s} actual={str(actual)[:10]:10s} {title}")

# Match using GT match_keywords
print(f"\n=== KEYWORD MATCHING ===")
tp_bugs = set()
for i, f in enumerate(findings):
    title = (f.get("title") or "").lower()
    desc = (f.get("description") or "").lower()
    cat = (f.get("category") or "").lower()
    evidence = f.get("evidence") or {}
    repro = str(evidence.get("reproduction_steps") or f.get("reproduction_steps") or "").lower()
    finding_text = f"{title} {desc} {cat} {repro}"
    
    matched = []
    for bug in bugs:
        if not isinstance(bug, dict):
            continue
        keywords = bug.get("match_keywords") or []
        # Count keyword matches
        hits = sum(1 for kw in keywords if str(kw).lower() in finding_text)
        if hits >= 2 or (hits >= 1 and len(keywords) <= 2):
            matched.append((bug.get("bug_id"), hits, bug.get("title","")[:50]))
    
    if matched:
        matched.sort(key=lambda x: -x[1])
        tp_bugs.add(matched[0][0])
        print(f"  [{i}] MATCH: {matched[0][0]} ({matched[0][2]})")
    else:
        print(f"  [{i}] NO MATCH: {title[:60]}")

print(f"\n=== SUMMARY ===")
print(f"  Findings: {len(findings)}")
print(f"  TP (keyword match): {len(tp_bugs)}")
print(f"  Recall: {len(tp_bugs)}/{len(bugs)} = {len(tp_bugs)/len(bugs)*100:.1f}%")

# Pipeline stats
ph = scan.get("pipeline_health") or {}
print(f"\n  Pipeline: selected={ph.get('selected_obligation_count')} executed={ph.get('executed_obligation_count')} blocked={ph.get('blocked_obligation_count')} deliverables={ph.get('formal_customer_deliverable_count')}")
