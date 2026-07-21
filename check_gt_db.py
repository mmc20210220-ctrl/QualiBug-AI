# -*- coding: utf-8 -*-
"""Check GT for DB-violation-related bugs."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

gt_path = r"D:\QualiBug-AI\QualiBug-AI-main\_private_eval\_evaluator_private\benchmark_mall_131\bugs.json"
with open(gt_path, 'r', encoding='utf-8') as f:
    gt = json.load(f)

bugs = gt if isinstance(gt, list) else gt.get('bugs', [])
print(f"GT bugs: {len(bugs)}")

# Keywords related to our DB violations
keywords_map = {
    "状态机/非法状态": ["status", "state", "transition", "invalid", "illegal", "REFUND_REQUESTED", "状态"],
    "支付金额/NaN": ["payment", "amount", "NaN", "payable", "mismatch", "支付", "金额"],
    "库存负数": ["inventory", "stock", "negative", "locked", "库存", "负"],
    "优惠券超限": ["coupon", "limit", "usage", "exceed", "优惠券", "限制"],
    "发货无支付": ["ship", "paid", "payment", "发货", "支付"],
}

for category, kws in keywords_map.items():
    matches = []
    for b in bugs:
        title = str(b.get('title', '') or b.get('name', '') or '')
        desc = str(b.get('description', '') or b.get('desc', '') or '')
        text = (title + ' ' + desc).lower()
        if any(kw.lower() in text for kw in kws):
            matches.append(b)
    print(f"\n{category}: {len(matches)}个GT bug")
    for m in matches[:5]:
        bid = m.get('id', m.get('bug_id', ''))
        title = m.get('title', m.get('name', ''))
        print(f"  [{bid}] {title}")
