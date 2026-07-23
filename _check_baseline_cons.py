#!/usr/bin/env python
"""Check baseline conservation findings vs current."""
import json, sys
sys.stdout.reconfigure(encoding='utf-8')

# Baseline
try:
    d = json.loads(open('_scan_result_latest.json', encoding='utf-8').read())
    findings = d.get('findings', [])
    cons = [f for f in findings if isinstance(f, dict) and f.get('category', '') == 'conservation']
    print(f"Baseline conservation findings: {len(cons)}")
    for f in cons:
        print(f"  {f.get('title', '?')[:80]}")
except Exception as e:
    print(f"Baseline error: {e}")

# Current
print()
d2 = json.loads(open('_scan_result_p13_v2.json', encoding='utf-8').read())
findings2 = d2.get('findings', [])
cons2 = [f for f in findings2 if isinstance(f, dict) and f.get('category', '') == 'conservation']
print(f"Current conservation findings: {len(cons2)}")
for f in cons2:
    print(f"  {f.get('title', '?')[:80]}")
    print(f"    experiment_id: {f.get('experiment_id')}")
    print(f"    obligation_id: {f.get('obligation_id')}")

# Check all non-auth categories in current
print("\n--- Current non-auth/validation findings ---")
non_auth = [f for f in findings2 if isinstance(f, dict)
            and f.get('category', '') not in ('authorization', 'validation', 'permission_boundary')]
cats = {}
for f in non_auth:
    c = f.get('category', '?')
    cats[c] = cats.get(c, 0) + 1
for c, n in sorted(cats.items()):
    print(f"  {c}: {n}")

# GT matching for conservation finding
print("\n--- GT match check ---")
gt = json.loads(open('_private_eval/_evaluator_private/benchmark_mall_131/bugs.json', encoding='utf-8').read())
bugs = gt if isinstance(gt, list) else gt.get('bugs', gt.get('defects', []))

for f in cons2:
    title = f.get('title', '')
    desc = f.get('description', '')
    finding_text = f"{title} {desc}".lower()
    print(f"\nFinding: {title[:60]}")
    matched = []
    for b in bugs:
        keywords = b.get('match_keywords', [])
        # Check if finding matches GT bug by keywords
        kw_match = sum(1 for kw in keywords if kw.lower() in finding_text)
        if kw_match >= 2 or ('adjust' in finding_text and 'adjust' in str(keywords).lower()):
            matched.append((b.get('bug_id'), b.get('title'), kw_match))
    for bid, btitle, score in matched:
        print(f"  MATCH: {bid} - {btitle} (score={score})")
