# -*- coding: utf-8 -*-
"""Show GT bug details for DB-violation-related bugs."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

gt_path = r"D:\QualiBug-AI\QualiBug-AI-main\_private_eval\_evaluator_private\benchmark_mall_131\bugs.json"
with open(gt_path, 'r', encoding='utf-8') as f:
    gt = json.load(f)
bugs = gt if isinstance(gt, list) else gt.get('bugs', [])

# Show specific bugs related to our DB violations
target_ids = ["ORDER-011", "INV-003", "PAY-007", "REFUND-005", "REFUND-006", "COUPON-001", "COUPON-002", "COUPON-003"]
for b in bugs:
    bid = b.get('id', b.get('bug_id', ''))
    if bid in target_ids:
        print(f"\n=== {bid} ===")
        print(f"  title: {b.get('title','')}")
        print(f"  match_keywords: {b.get('match_keywords',[])}")
        print(f"  family/risk_family: {b.get('family', b.get('risk_family',''))}")
        print(f"  trigger: {b.get('trigger','')}")
        print(f"  endpoint_hint: {b.get('endpoint_hint', b.get('api_path',''))}")
        print(f"  affected_endpoints: {b.get('affected_endpoints', b.get('related_endpoints',''))}")
