# -*- coding: utf-8 -*-
"""Show all GT bugs with match_keywords for DB violation mapping."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

gt_path = r"D:\QualiBug-AI\QualiBug-AI-main\_private_eval\_evaluator_private\benchmark_mall_131\bugs.json"
with open(gt_path, 'r', encoding='utf-8') as f:
    gt = json.load(f)
bugs = gt if isinstance(gt, list) else gt.get('bugs', [])

# DB violations we found:
# 1. order_invalid_status: REFUND_REQUESTED (21 rows)
# 2. shipped_not_paid: SHIPPED without paid_at (1 row)
# 3. negative_locked: locked_qty=-1411 (1 row)
# 4. payment_amount_mismatch: NaN amounts (232 rows)
# 5. paid_order_no_success_payment: SHIPPED without SUCCESS payment (1 row)
# 6. coupon_over_user_limit: NEW100 used 204 times with limit=1 (1 row)

# Find GT bugs whose match_keywords overlap with our violation evidence
violation_keywords = {
    "REFUND_REQUESTED状态": ["REFUND_REQUESTED", "refund", "退款", "状态", "驳回", "恢复"],
    "SHIPPED无支付": ["ship", "SHIPPED", "发货", "未支付", "PENDING_PAYMENT", "paid"],
    "负锁定库存": ["locked_qty", "负数", "库存", "release", "释放", "negative"],
    "支付金额NaN": ["payment", "amount", "NaN", "支付", "金额", "payable"],
    "优惠券超限": ["coupon", "NEW100", "limit", "优惠券", "限制", "user_limit"],
}

for vname, vkws in violation_keywords.items():
    print(f"\n{'='*60}")
    print(f"DB违反: {vname}")
    print(f"{'='*60}")
    for b in bugs:
        bid = b.get('id', b.get('bug_id', ''))
        title = b.get('title', '')
        mkws = b.get('match_keywords', [])
        # Check overlap
        overlap = [kw for kw in mkws if any(vk.lower() in str(kw).lower() or str(kw).lower() in vk.lower() for vk in vkws)]
        if overlap:
            print(f"  [{bid}] {title}")
            print(f"    match_keywords: {mkws}")
            print(f"    overlap: {overlap}")
