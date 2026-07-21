# -*- coding: utf-8 -*-
"""DB invariant audit: check business rule violations directly in PostgreSQL."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import psycopg2

DSN = "postgresql://benchmark_user:benchmark_pass@localhost:5432/benchmark_mall"
conn = psycopg2.connect(DSN, connect_timeout=5)
cur = conn.cursor()
violations = []

def check(name, sql, desc):
    try:
        cur.execute(sql)
        rows = cur.fetchall()
        if rows:
            violations.append({"check": name, "count": len(rows), "desc": desc, "samples": [str(r) for r in rows[:3]]})
            print(f"  [VIOLATION] {name}: {len(rows)}行 - {desc}")
            for r in rows[:3]:
                print(f"    {r}")
        else:
            print(f"  [OK] {name}")
    except Exception as e:
        print(f"  [ERROR] {name}: {e}")
        conn.rollback()

print("=== 1. 订单状态机检查 ===")
# Valid states
check("order_invalid_status",
    "SELECT id, order_no, status FROM orders WHERE status NOT IN ('CREATED','PENDING_PAYMENT','PAID','SHIPPED','COMPLETED','CANCELLED','REFUNDED')",
    "订单状态不在合法枚举中")

# CANCELLED orders should not have paid_at
check("cancelled_but_paid",
    "SELECT id, order_no, status, paid_at FROM orders WHERE status='CANCELLED' AND paid_at IS NOT NULL",
    "已取消订单但有支付时间")

# COMPLETED orders must have paid_at
check("completed_not_paid",
    "SELECT id, order_no, status, paid_at FROM orders WHERE status='COMPLETED' AND paid_at IS NULL",
    "已完成订单但无支付时间")

# SHIPPED orders must have paid_at
check("shipped_not_paid",
    "SELECT id, order_no, status, paid_at FROM orders WHERE status='SHIPPED' AND paid_at IS NULL",
    "已发货订单但无支付时间")

# PAID orders must have paid_at
check("paid_no_paid_at",
    "SELECT id, order_no, status, paid_at FROM orders WHERE status='PAID' AND paid_at IS NULL",
    "已支付状态但paid_at为空")

print("\n=== 2. 金额守恒检查 ===")
# total_amount = sum(order_items.line_amount)
check("total_amount_mismatch",
    """SELECT o.id, o.order_no, o.total_amount, COALESCE(SUM(oi.line_amount),0) as items_sum
       FROM orders o LEFT JOIN order_items oi ON o.id=oi.order_id
       GROUP BY o.id, o.order_no, o.total_amount
       HAVING o.total_amount != COALESCE(SUM(oi.line_amount),0)""",
    "订单总额≠明细行金额之和")

# payable_amount = total_amount - discount_amount
check("payable_mismatch",
    "SELECT id, order_no, total_amount, discount_amount, payable_amount FROM orders WHERE payable_amount != total_amount - discount_amount",
    "应付金额≠总额-折扣")

# line_amount = price * qty
check("line_amount_mismatch",
    "SELECT id, order_id, sku, price, qty, line_amount FROM order_items WHERE line_amount != price * qty",
    "行金额≠单价×数量")

# Negative amounts
check("negative_total",
    "SELECT id, order_no, total_amount FROM orders WHERE total_amount < 0",
    "订单总额为负")
check("negative_payable",
    "SELECT id, order_no, payable_amount FROM orders WHERE payable_amount < 0",
    "应付金额为负")

print("\n=== 3. 库存检查 ===")
check("negative_available",
    "SELECT sku, warehouse_code, available_qty FROM inventory WHERE available_qty < 0",
    "可用库存为负")
check("negative_locked",
    "SELECT sku, warehouse_code, locked_qty FROM inventory WHERE locked_qty < 0",
    "锁定库存为负")

print("\n=== 4. 支付一致性 ===")
# Payment amount should match order payable
check("payment_amount_mismatch",
    """SELECT p.id, p.payment_no, p.order_id, p.amount as pay_amt, o.payable_amount
       FROM payments p JOIN orders o ON p.order_id=o.id
       WHERE p.status='SUCCESS' AND p.amount != o.payable_amount""",
    "成功支付金额≠订单应付金额")

# Paid orders should have successful payment
check("paid_order_no_success_payment",
    """SELECT o.id, o.order_no, o.status FROM orders o
       WHERE o.status IN ('PAID','SHIPPED','COMPLETED')
       AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.order_id=o.id AND p.status='SUCCESS')""",
    "已支付/发货/完成订单但无成功支付记录")

print("\n=== 5. 退款一致性 ===")
# Refund amount should not exceed order payable
check("refund_exceeds_order",
    """SELECT r.id, r.refund_no, r.order_id, r.amount as refund_amt, o.payable_amount
       FROM refunds r JOIN orders o ON r.order_id=o.id
       WHERE r.status='SUCCESS' AND r.amount > o.payable_amount""",
    "成功退款金额超过订单应付")

# Refunded orders should have refund records
check("refunded_order_no_refund",
    """SELECT o.id, o.order_no FROM orders o
       WHERE o.status='REFUNDED'
       AND NOT EXISTS (SELECT 1 FROM refunds r WHERE r.order_id=o.id AND r.status='SUCCESS')""",
    "已退款订单但无成功退款记录")

print("\n=== 6. 优惠券检查 ===")
# Coupon usage count vs user_limit
check("coupon_over_user_limit",
    """SELECT c.id, c.code, c.user_limit, COUNT(cu.id) as usage_count
       FROM coupons c JOIN coupon_usage cu ON c.id=cu.coupon_id
       GROUP BY c.id, c.code, c.user_limit
       HAVING c.user_limit > 0 AND COUNT(cu.id) > c.user_limit""",
    "优惠券使用次数超过用户限制")

# Coupon usage count vs global_limit
check("coupon_over_global_limit",
    """SELECT c.id, c.code, c.global_limit, COUNT(cu.id) as usage_count
       FROM coupons c JOIN coupon_usage cu ON c.id=cu.coupon_id
       GROUP BY c.id, c.code, c.global_limit
       HAVING c.global_limit > 0 AND COUNT(cu.id) > c.global_limit""",
    "优惠券使用次数超过全局限制")

# Expired coupon usage
check("expired_coupon_used",
    """SELECT cu.id, c.code, c.expires_at, cu.used_at
       FROM coupon_usage cu JOIN coupons c ON cu.coupon_id=c.id
       WHERE c.expires_at IS NOT NULL AND cu.used_at > c.expires_at""",
    "过期优惠券被使用")

print("\n=== 7. 用户余额检查 ===")
check("negative_balance",
    "SELECT id, email, balance FROM users WHERE balance < 0",
    "用户余额为负")

print("\n=== 8. 订单-用户一致性 ===")
check("order_invalid_user",
    """SELECT o.id, o.order_no, o.user_id FROM orders o
       WHERE NOT EXISTS (SELECT 1 FROM users u WHERE u.id=o.user_id)""",
    "订单关联不存在的用户")

print("\n=== 9. 库存锁定一致性 ===")
# Active locks for cancelled/completed orders
check("stale_inventory_locks",
    """SELECT il.id, il.order_id, il.sku, il.qty, il.status, o.status as order_status
       FROM inventory_locks il JOIN orders o ON il.order_id=o.id
       WHERE il.status='LOCKED' AND o.status IN ('CANCELLED','COMPLETED','REFUNDED')""",
    "已取消/完成/退款订单仍有锁定库存")

print("\n=== 10. 购物车价格一致性 ===")
check("cart_price_stale",
    """SELECT ci.id, ci.sku, ci.price_snapshot, p.price as current_price
       FROM cart_items ci JOIN products p ON ci.sku=p.sku
       WHERE ci.price_snapshot != p.price""",
    "购物车价格快照与当前商品价格不一致")

conn.close()

print(f"\n{'='*60}")
print(f"总违反数: {len(violations)}")
for v in violations:
    print(f"  {v['check']}: {v['count']}行 - {v['desc']}")
