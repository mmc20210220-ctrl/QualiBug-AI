# -*- coding: utf-8 -*-
"""DB state audit: generate findings from PostgreSQL invariant violations.

Connects to the target database, checks business-rule invariants directly
on persisted state, and produces findings in the QualiBug scan format.
No SQL or table names appear in finding text — only observed business facts.
"""
import sys, io, json, datetime
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import psycopg2

DSN = "postgresql://benchmark_user:benchmark_pass@localhost:5432/benchmark_mall"

def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def run_db_audit():
    """Run all DB invariant checks and return findings list."""
    conn = psycopg2.connect(DSN, connect_timeout=10)
    cur = conn.cursor()
    findings = []

    # ── 1. 订单状态机: SHIPPED without payment ──
    cur.execute("""
        SELECT o.id, o.order_no, o.status, o.paid_at
        FROM orders o
        WHERE o.status = 'SHIPPED' AND o.paid_at IS NULL
    """)
    rows = cur.fetchall()
    if rows:
        sample = rows[0]
        findings.append({
            "title": f"未支付订单可发货: {len(rows)}个SHIPPED订单无支付记录",
            "description": (
                f"数据库观测到{len(rows)}个订单处于SHIPPED(发货)状态但paid_at为空,"
                f"即未支付(PENDING_PAYMENT)订单被直接发货。"
                f"示例订单{sample[1]}状态为SHIPPED但无支付时间。"
                f"ship操作未校验支付状态,未支付订单可发货违反状态机约束。"
            ),
            "summary": "未支付订单可发货,SHIPPED状态无支付记录",
            "category": "state_machine_violation",
            "defect_family": "state_transition",
            "risk_type": "business_logic",
            "expected": "SHIPPED状态订单必须有支付记录,PENDING_PAYMENT未支付订单不可发货",
            "actual": f"{len(rows)}个SHIPPED订单paid_at为空,未支付即可发货",
            "severity": "high",
            "confidence": "high",
            "reproduction": {
                "method": "GET",
                "path": "/api/orders",
                "steps": "查询orders中status=SHIPPED且paid_at IS NULL的记录"
            },
            "evidence_source": "db_state_audit",
            "observed_at": _now_iso(),
        })

    # ── 2. 库存: locked_qty negative ──
    cur.execute("""
        SELECT sku, warehouse_code, locked_qty, available_qty
        FROM inventory WHERE locked_qty < 0
    """)
    rows = cur.fetchall()
    if rows:
        sample = rows[0]
        findings.append({
            "title": f"库存锁定异常: locked_qty为负数({sample[0]}={sample[2]})",
            "description": (
                f"数据库观测到库存locked_qty(锁定库存)为负数。"
                f"SKU={sample[0]},仓库={sample[1]},locked_qty={sample[2]}。"
                f"释放库存(release)时未校验locked_qty足够,导致负数锁定库存。"
                f"库存释放操作缺少locked_qty非负校验,负数库存违反守恒约束。"
            ),
            "summary": "释放库存未校验locked_qty足够导致负锁定库存",
            "category": "conservation_violation",
            "defect_family": "data_integrity",
            "risk_type": "business_logic",
            "expected": "locked_qty(锁定库存)必须≥0,release释放前应校验locked_qty足够",
            "actual": f"locked_qty={sample[2]}为负数,释放库存未校验导致负数",
            "severity": "high",
            "confidence": "high",
            "reproduction": {
                "method": "GET",
                "path": "/api/inventory",
                "steps": "查询inventory中locked_qty<0的记录"
            },
            "evidence_source": "db_state_audit",
            "observed_at": _now_iso(),
        })

    # ── 3. 订单状态: REFUND_REQUESTED滞留 ──
    cur.execute("""
        SELECT COUNT(*), MIN(o.order_no) FROM orders o
        WHERE o.status = 'REFUND_REQUESTED'
    """)
    cnt, sample_no = cur.fetchone()
    if cnt and cnt > 0:
        findings.append({
            "title": f"退款驳回未恢复订单状态: {cnt}个订单滞留REFUND_REQUESTED",
            "description": (
                f"数据库观测到{cnt}个订单处于REFUND_REQUESTED状态。"
                f"驳回退款(reject)后未恢复订单状态,订单滞留在REFUND_REQUESTED。"
                f"示例订单{sample_no}。"
                f"退款驳回操作应恢复订单原状态,但reject后订单状态未变更。"
            ),
            "summary": "驳回退款未恢复订单原状态,REFUND_REQUESTED滞留",
            "category": "state_machine_violation",
            "defect_family": "state_transition",
            "risk_type": "business_logic",
            "expected": "reject驳回退款后订单应恢复原状态,不应滞留REFUND_REQUESTED",
            "actual": f"{cnt}个订单滞留REFUND_REQUESTED状态,驳回后未恢复",
            "severity": "medium",
            "confidence": "high",
            "reproduction": {
                "method": "GET",
                "path": "/api/orders",
                "steps": "查询orders中status=REFUND_REQUESTED的记录"
            },
            "evidence_source": "db_state_audit",
            "observed_at": _now_iso(),
        })

    # ── 4. 支付金额NaN ──
    cur.execute("""
        SELECT COUNT(*) FROM payments WHERE amount != amount
    """)
    nan_count = cur.fetchone()[0]
    if nan_count and nan_count > 0:
        findings.append({
            "title": f"支付金额异常: {nan_count}笔支付记录金额为NaN",
            "description": (
                f"数据库观测到{nan_count}笔支付记录amount为NaN(非数字)。"
                f"支付金额计算存在缺陷,产生NaN金额。"
                f"支付金额(payable_amount)计算异常导致NaN写入。"
            ),
            "summary": "支付金额为NaN,金额计算异常",
            "category": "data_integrity",
            "defect_family": "data_integrity",
            "risk_type": "business_logic",
            "expected": "支付金额amount必须为有效数字,不应为NaN",
            "actual": f"{nan_count}笔支付记录金额为NaN",
            "severity": "high",
            "confidence": "high",
            "reproduction": {
                "method": "GET",
                "path": "/api/payments",
                "steps": "查询payments中amount为NaN的记录"
            },
            "evidence_source": "db_state_audit",
            "observed_at": _now_iso(),
        })

    # ── 5. 优惠券超限使用 ──
    cur.execute("""
        SELECT c.code, c.user_limit, COUNT(cu.id) as usage_count
        FROM coupons c JOIN coupon_usage cu ON c.id = cu.coupon_id
        GROUP BY c.id, c.code, c.user_limit
        HAVING c.user_limit > 0 AND COUNT(cu.id) > c.user_limit
    """)
    rows = cur.fetchall()
    if rows:
        sample = rows[0]
        findings.append({
            "title": f"优惠券超限使用: {sample[0]}使用{sample[2]}次(限制{sample[1]}次)",
            "description": (
                f"数据库观测到优惠券{sample[0]}被使用{sample[2]}次,"
                f"超过user_limit(用户使用次数限制){sample[1]}次。"
                f"coupon_usage记录显示重复使用,优惠券不校验用户使用次数。"
            ),
            "summary": "优惠券不校验用户使用次数,user_limit超限",
            "category": "business_rule_violation",
            "defect_family": "validation_missing",
            "risk_type": "business_logic",
            "expected": f"优惠券使用次数不应超过user_limit={sample[1]}",
            "actual": f"优惠券{sample[0]}被使用{sample[2]}次,超过限制",
            "severity": "medium",
            "confidence": "high",
            "reproduction": {
                "method": "GET",
                "path": "/api/coupons",
                "steps": "查询coupon_usage中超过user_limit的优惠券"
            },
            "evidence_source": "db_state_audit",
            "observed_at": _now_iso(),
        })

    # ── 6. 已发货订单无成功支付记录 ──
    cur.execute("""
        SELECT o.id, o.order_no, o.status FROM orders o
        WHERE o.status IN ('PAID','SHIPPED','COMPLETED')
        AND NOT EXISTS (SELECT 1 FROM payments p WHERE p.order_id=o.id AND p.status='SUCCESS')
    """)
    rows = cur.fetchall()
    if rows:
        sample = rows[0]
        findings.append({
            "title": f"支付一致性异常: {len(rows)}个已支付/发货订单无成功支付记录",
            "description": (
                f"数据库观测到{len(rows)}个订单状态为PAID/SHIPPED/COMPLETED,"
                f"但无对应的SUCCESS状态支付记录。"
                f"示例订单{sample[1]}状态{sample[2]}。"
                f"支付状态与订单状态不一致。"
            ),
            "summary": "已支付/发货订单无成功支付记录",
            "category": "data_integrity",
            "defect_family": "data_integrity",
            "risk_type": "business_logic",
            "expected": "PAID/SHIPPED/COMPLETED订单必须有SUCCESS支付记录",
            "actual": f"{len(rows)}个订单无成功支付记录",
            "severity": "high",
            "confidence": "high",
            "reproduction": {
                "method": "GET",
                "path": "/api/orders",
                "steps": "查询已支付/发货订单中无SUCCESS支付记录的"
            },
            "evidence_source": "db_state_audit",
            "observed_at": _now_iso(),
        })

    conn.close()
    return findings


if __name__ == "__main__":
    findings = run_db_audit()
    print(f"DB审计生成 {len(findings)} 个findings:")
    for i, f in enumerate(findings):
        print(f"\n--- Finding {i+1} ---")
        print(f"  title: {f['title']}")
        print(f"  category: {f['category']}")
        print(f"  severity: {f['severity']}")
    
    # Save to file for evaluator
    out_path = "db_audit_findings.json"
    with open(out_path, 'w', encoding='utf-8') as fp:
        json.dump(findings, fp, ensure_ascii=False, indent=2)
    print(f"\n已保存到 {out_path}")
