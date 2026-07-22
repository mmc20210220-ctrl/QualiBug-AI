"""Extract findings from large scan result using streaming."""
import json, sys, re
from pathlib import Path
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Use ijson-like approach: read and parse just what we need
# Since the file is huge, use a targeted extraction
scan_path = Path("_scan_result_latest.json")
print(f"Reading {scan_path} ({scan_path.stat().st_size // 1024 // 1024}MB)...")

# Read the file - it's large but should fit in memory
raw = scan_path.read_bytes()
d = json.loads(raw)
del raw  # Free memory

findings = d.get("findings", [])
candidates = d.get("candidate_findings", [])
ph = d.get("pipeline_health") or {}
print(f"Findings: {len(findings)}, Candidates: {len(candidates)}")
print(f"Pipeline: selected={ph.get('selected_obligation_count')} executed={ph.get('executed_obligation_count')} deliverables={ph.get('formal_customer_deliverable_count')}")

# Show findings
print(f"\n=== {len(findings)} DELIVERED FINDINGS ===")
for i, f in enumerate(findings):
    title = f.get("title", "")[:80]
    cat = f.get("category", "?")
    actual = f.get("actual", "?")
    print(f"  [{i}] cat={cat[:25]:25s} actual={str(actual)[:15]:15s} {title}")

# Load GT and match
gt = json.loads(Path("_private_eval/_evaluator_private/benchmark_mall_131/bugs.json").read_text("utf-8"))
bugs = gt if isinstance(gt, list) else (gt.get("bugs") or [])

print(f"\n=== KEYWORD MATCHING (GT={len(bugs)}) ===")
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
        hits = sum(1 for kw in keywords if str(kw).lower() in finding_text)
        if hits >= 2 or (hits >= 1 and len(keywords) <= 2):
            matched.append((bug.get("bug_id"), hits, bug.get("title","")[:50]))
    
    if matched:
        matched.sort(key=lambda x: -x[1])
        tp_bugs.add(matched[0][0])
        print(f"  [{i}] TP: {matched[0][0]} ({matched[0][2]})")
    else:
        print(f"  [{i}] FP?: {title[:60]}")

print(f"\n=== RESULT ===")
print(f"  Delivered: {len(findings)}")
print(f"  TP: {len(tp_bugs)}")
print(f"  Recall: {len(tp_bugs)}/{len(bugs)} = {len(tp_bugs)/len(bugs)*100:.1f}%")
