# -*- coding: utf-8 -*-
"""Check GT families for target bugs."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, r"d:\QualiBug-AI\QualiBug-AI-main")
from benchmark_evaluator.benchmark_compute import _canonical_match_family

gt_path = r"D:\QualiBug-AI\QualiBug-AI-main\_private_eval\_evaluator_private\benchmark_mall_131\bugs.json"
with open(gt_path, 'r', encoding='utf-8') as f:
    gt = json.load(f)
bugs = gt if isinstance(gt, list) else gt.get('bugs', [])

targets = ["ORDER-011","INV-003","REFUND-006","COUPON-004","COUPON-005","ORDER-004","PAY-002","DB-002","DB-003","INV-012","ORDER-009"]
for b in bugs:
    bid = b.get('id', b.get('bug_id',''))
    if bid in targets:
        fam = _canonical_match_family(b)
        print(f"  [{bid}] family={fam} | {b.get('title','')}")
        print(f"    keywords: {b.get('match_keywords',[])}")
