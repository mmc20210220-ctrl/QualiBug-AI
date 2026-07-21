# -*- coding: utf-8 -*-
"""Run evaluator on DB audit findings to check GT matches."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")

from benchmark_evaluator.benchmark_compute import _match_finding_to_gt, _finding_text_blob, _finding_paths, _canonical_match_family

# Load GT
gt_path = r"D:\QualiBug-AI\QualiBug-AI-main\_private_eval\_evaluator_private\benchmark_mall_131\bugs.json"
with open(gt_path, 'r', encoding='utf-8') as f:
    gt = json.load(f)
truth_bugs = gt if isinstance(gt, list) else gt.get('bugs', [])

# Load DB audit findings
with open("db_audit_findings.json", 'r', encoding='utf-8') as f:
    findings = json.load(f)

print(f"GT bugs: {len(truth_bugs)}, DB findings: {len(findings)}")
print()

matched_gt_ids = set()
tp_count = 0
for i, finding in enumerate(findings):
    blob = _finding_text_blob(finding)
    paths = _finding_paths(finding)
    family = _canonical_match_family(finding)
    
    match = _match_finding_to_gt(finding, truth_bugs, matched_gt_ids)
    if match:
        gt_id = match.get('bug_id', match.get('id', ''))
        matched_gt_ids.add(gt_id)
        tp_count += 1
        score = match.get('__match_score', 0)
        print(f"[TP] Finding {i+1}: {finding['title'][:50]}")
        print(f"  → GT: [{gt_id}] {match.get('title','')}")
        print(f"  → score: {score}")
    else:
        print(f"[--] Finding {i+1}: {finding['title'][:50]}")
        # Show why it didn't match - check top candidates
        best_score = 0
        best_gt = None
        for gt_bug in truth_bugs:
            gt_id = str(gt_bug.get('bug_id') or gt_bug.get('id') or '')
            if gt_id in matched_gt_ids:
                continue
            keywords = gt_bug.get('match_keywords', [])
            kw_hits = sum(1 for kw in keywords if str(kw).lower() in blob)
            score = min(0.55, kw_hits * 0.12) if keywords else 0
            gt_title = str(gt_bug.get('title') or '').lower()
            if gt_title and any(tok in blob for tok in gt_title.split() if len(tok) >= 4):
                score += 0.12
            gt_family = _canonical_match_family(gt_bug)
            if family != "unclassified" and gt_family != "unclassified" and family == gt_family:
                score += 0.35
            if score > best_score:
                best_score = score
                best_gt = gt_bug
        if best_gt:
            bg_id = best_gt.get('bug_id', best_gt.get('id', ''))
            print(f"  最佳候选: [{bg_id}] {best_gt.get('title','')} (score={best_score:.2f})")
            kws = best_gt.get('match_keywords', [])
            hits = [kw for kw in kws if str(kw).lower() in blob]
            misses = [kw for kw in kws if str(kw).lower() not in blob]
            print(f"  命中keywords: {hits}")
            print(f"  缺失keywords: {misses}")
    print()

print(f"{'='*60}")
print(f"DB审计 TP: {tp_count}/{len(findings)}")
print(f"匹配GT IDs: {sorted(matched_gt_ids)}")
