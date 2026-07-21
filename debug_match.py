# -*- coding: utf-8 -*-
"""Debug why Finding 2 doesn't match INV-003."""
import sys, io, json, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")

from benchmark_evaluator.benchmark_compute import (
    _match_finding_to_gt, _finding_text_blob, _finding_paths,
    _canonical_match_family, _extract_api_paths, _paths_overlap
)

gt_path = r"D:\QualiBug-AI\QualiBug-AI-main\_private_eval\_evaluator_private\benchmark_mall_131\bugs.json"
with open(gt_path, 'r', encoding='utf-8') as f:
    gt = json.load(f)
truth_bugs = gt if isinstance(gt, list) else gt.get('bugs', [])

with open("db_audit_findings.json", 'r', encoding='utf-8') as f:
    findings = json.load(f)

finding = findings[1]  # Finding 2: locked_qty
blob = _finding_text_blob(finding)
f_paths = _finding_paths(finding)
finding_family = _canonical_match_family(finding)

print(f"Finding family: {finding_family}")
print(f"Finding paths: {f_paths}")
print(f"Blob (first 300): {blob[:300]}")
print()

# Find INV-003
inv003 = None
for b in truth_bugs:
    if b.get('id','') == 'INV-003' or b.get('bug_id','') == 'INV-003':
        inv003 = b
        break

if inv003:
    keywords = inv003.get('match_keywords', [])
    kw_hits = sum(1 for kw in keywords if str(kw).lower() in blob)
    kw_score = min(0.55, kw_hits * 0.12) if keywords else 0
    
    gt_paths = _extract_api_paths(str(inv003.get("trigger") or ""))
    gt_paths |= _extract_api_paths(str(inv003.get("endpoint_hint") or inv003.get("api_path") or ""))
    for ep in inv003.get("affected_endpoints") or inv003.get("related_endpoints") or []:
        if isinstance(ep, dict):
            gt_paths |= _extract_api_paths(str(ep.get("path") or ep.get("api_path") or ""))
        else:
            gt_paths |= _extract_api_paths(str(ep))
    gt_paths |= _extract_api_paths(" ".join(str(k) for k in keywords))
    
    path_matches = _paths_overlap(f_paths, gt_paths)
    gt_family = _canonical_match_family(inv003)
    family_matches = (finding_family != "unclassified" and gt_family != "unclassified" and finding_family == gt_family)
    
    gt_title = str(inv003.get("title") or "").lower()
    title_match = gt_title and any(tok in blob for tok in gt_title.split() if len(tok) >= 4)
    
    score = kw_score
    if path_matches: score += 0.30
    if family_matches: score += 0.35
    elif finding_family != "unclassified" and gt_family != "unclassified" and finding_family != gt_family:
        score -= 0.20
    if title_match: score += 0.12
    
    print(f"INV-003 keywords: {keywords}")
    print(f"kw_hits: {kw_hits}, kw_score: {kw_score}")
    print(f"gt_paths: {gt_paths}")
    print(f"path_matches: {path_matches}")
    print(f"gt_family: {gt_family}")
    print(f"family_matches: {family_matches}")
    print(f"title_match: {title_match}")
    print(f"TOTAL score: {score}")
    print(f"Threshold check: score < 0.58 = {score < 0.58}")
    print(f"  path_matches and not family_matches and score < 0.70 = {path_matches and not family_matches and score < 0.70}")
    
    # Check all GT bugs that score > 0.5 for this finding
    print(f"\n--- All GT bugs with score > 0.4 for Finding 2 ---")
    for gt_bug in truth_bugs:
        gt_id = str(gt_bug.get('bug_id') or gt_bug.get('id') or '')
        kws = gt_bug.get('match_keywords', [])
        hits = sum(1 for kw in kws if str(kw).lower() in blob)
        s = min(0.55, hits * 0.12) if kws else 0
        gtf = _canonical_match_family(gt_bug)
        fm = (finding_family != "unclassified" and gtf != "unclassified" and finding_family == gtf)
        if fm: s += 0.35
        elif finding_family != "unclassified" and gtf != "unclassified" and finding_family != gtf:
            s -= 0.20
        gtp = _extract_api_paths(str(gt_bug.get("trigger") or ""))
        gtp |= _extract_api_paths(" ".join(str(k) for k in kws))
        pm = _paths_overlap(f_paths, gtp)
        if pm: s += 0.30
        gtt = str(gt_bug.get("title") or "").lower()
        if gtt and any(tok in blob for tok in gtt.split() if len(tok) >= 4):
            s += 0.12
        if s > 0.4:
            print(f"  [{gt_id}] {gt_bug.get('title','')} → score={s:.2f} (kw={hits}, pm={pm}, fm={fm})")
