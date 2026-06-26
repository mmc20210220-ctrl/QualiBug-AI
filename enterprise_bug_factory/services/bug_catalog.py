from __future__ import annotations

import random
from copy import deepcopy
from typing import Any

# Template-level high value bug library.
# A template is a defect pattern. A bug instance is one concrete scenario variant.
TEMPLATE_DEFS: list[dict[str, Any]] = [
    {"template_id":"AUTH_VERTICAL_BYPASS","domain":"permission","risk_type":"permission_bypass","severity":"P0","title":"非授权角色可访问高权限资源","api":"GET /admin/orders","trigger":"{actor} requests {api}","expected_status":403,"signal":"status_code == 200"},
    {"template_id":"AUTH_UNAUTH_ACCESS","domain":"permission","risk_type":"auth_bypass","severity":"P0","title":"未登录用户可访问受保护接口","api":"POST /admin/products/{product_id}","trigger":"anonymous requests {api}","expected_status":401,"signal":"status_code in [200, 201]"},
    {"template_id":"AUTH_LOCKED_USER_BYPASS","domain":"permission","risk_type":"locked_account_bypass","severity":"P1","title":"锁定账号仍可登录","api":"POST /login","trigger":"locked user logs in","expected_status":403,"signal":"status_code == 200"},
    {"template_id":"AUTH_ROLE_DOWNGRADE_CACHE","domain":"permission","risk_type":"permission_bypass","severity":"P1","title":"角色降级后旧权限仍可用","api":"GET /admin/orders","trigger":"downgraded user reuses cached token","expected_status":403,"signal":"status_code == 200"},
    {"template_id":"AUTH_USER_WRITE_ADMIN","domain":"permission","risk_type":"privilege_escalation","severity":"P0","title":"普通用户可修改管理员资源","api":"POST /admin/products/{product_id}","trigger":"{actor} writes {api}","expected_status":403,"signal":"status_code in [200, 201]"},

    {"template_id":"IDOR_ORDER_ACCESS","domain":"order","risk_type":"idor","severity":"P0","title":"用户可查看他人订单","api":"GET /orders/{order_id}","trigger":"{actor} requests another user's order","expected_status":403,"signal":"status_code == 200"},
    {"template_id":"IDOR_ADDRESS_MODIFY","domain":"order","risk_type":"idor","severity":"P1","title":"用户可修改他人地址","api":"POST /orders","trigger":"{actor} writes another user's address/order data","expected_status":403,"signal":"status_code in [200, 201]"},
    {"template_id":"IDOR_ORDER_CANCEL","domain":"order","risk_type":"idor","severity":"P1","title":"用户可取消他人订单","api":"POST /orders/{order_id}/cancel","trigger":"{actor} cancels another user's order","expected_status":403,"signal":"status_code == 200"},
    {"template_id":"TENANT_DATA_LEAK","domain":"tenant","risk_type":"tenant_isolation","severity":"P0","title":"跨租户数据泄露","api":"GET /tenant/orders","trigger":"tenant_a user requests tenant_b data","expected_status":403,"signal":"status_code == 200"},

    {"template_id":"ORDER_CREATE_MISSING","domain":"order","risk_type":"state_consistency","severity":"P1","title":"订单创建成功但查询不到","api":"POST /orders","trigger":"create order then query it","expected_status":200,"signal":"created order cannot be queried"},
    {"template_id":"ORDER_DUPLICATE_SUBMIT","domain":"order","risk_type":"idempotency","severity":"P1","title":"重复提交生成多个订单","api":"POST /orders","trigger":"same idempotency key creates more than one order","expected_status":200,"signal":"duplicate order ids"},
    {"template_id":"ORDER_CANCEL_STATE","domain":"order","risk_type":"state_flow","severity":"P1","title":"订单取消后状态错误","api":"POST /orders/{order_id}/cancel","trigger":"cancel order then query status","expected_status":200,"signal":"status != cancelled"},
    {"template_id":"ORDER_PAY_CANCELLED","domain":"payment","risk_type":"state_flow","severity":"P0","title":"用户可支付已取消订单","api":"POST /payments/callback","trigger":"pay a cancelled order","expected_status":409,"signal":"status_code == 200"},

    {"template_id":"STOCK_OVERSELL","domain":"stock","risk_type":"stock_consistency","severity":"P0","title":"库存不足仍能下单","api":"POST /orders","trigger":"buy quantity above stock","expected_status":409,"signal":"status_code in [200, 201]"},
    {"template_id":"STOCK_NOT_DECREASED","domain":"stock","risk_type":"stock_consistency","severity":"P1","title":"下单成功后库存未扣减","api":"POST /orders","trigger":"create order then compare stock","expected_status":200,"signal":"stock unchanged"},
    {"template_id":"STOCK_NOT_ROLLBACK","domain":"stock","risk_type":"stock_consistency","severity":"P1","title":"取消订单库存未回滚","api":"POST /orders/{order_id}/cancel","trigger":"cancel order then compare stock","expected_status":200,"signal":"stock not restored"},
    {"template_id":"STOCK_NEGATIVE_QUANTITY","domain":"stock","risk_type":"stock_consistency","severity":"P0","title":"库存被扣成负数","api":"POST /orders","trigger":"repeat order until stock below zero","expected_status":409,"signal":"stock < 0"},

    {"template_id":"COUPON_DOUBLE_DISCOUNT","domain":"coupon","risk_type":"coupon_abuse","severity":"P1","title":"优惠券重复抵扣","api":"POST /cart/apply-coupon","trigger":"apply same coupon twice","expected_status":409,"signal":"discount applied twice"},
    {"template_id":"COUPON_EXPIRED_ALLOWED","domain":"coupon","risk_type":"coupon_abuse","severity":"P1","title":"过期优惠券仍可用","api":"POST /cart/apply-coupon","trigger":"apply expired coupon","expected_status":400,"signal":"status_code == 200"},
    {"template_id":"COUPON_THRESHOLD_BYPASS","domain":"coupon","risk_type":"coupon_abuse","severity":"P1","title":"不满足门槛仍可用","api":"POST /cart/apply-coupon","trigger":"apply coupon below threshold","expected_status":400,"signal":"status_code == 200"},
    {"template_id":"COUPON_OWNERSHIP_BYPASS","domain":"coupon","risk_type":"coupon_abuse","severity":"P0","title":"他人优惠券可被使用","api":"POST /cart/apply-coupon","trigger":"use another user's coupon","expected_status":403,"signal":"status_code == 200"},

    {"template_id":"PAYMENT_DUPLICATE_CALLBACK","domain":"payment","risk_type":"payment_callback","severity":"P1","title":"支付回调重复入账","api":"POST /payments/callback","trigger":"repeat payment callback","expected_status":200,"signal":"paid amount increments twice"},
    {"template_id":"PAYMENT_AMOUNT_MISMATCH","domain":"payment","risk_type":"money_consistency","severity":"P0","title":"金额不一致仍支付成功","api":"POST /payments/callback","trigger":"pay with amount different from order total","expected_status":409,"signal":"status_code == 200"},
    {"template_id":"PAYMENT_STATUS_NOT_UPDATED","domain":"payment","risk_type":"state_consistency","severity":"P1","title":"支付成功订单状态未更新","api":"POST /payments","trigger":"pay then query order","expected_status":200,"signal":"status != paid"},
    {"template_id":"PAYMENT_CANCELLED_ORDER_ALLOWED","domain":"payment","risk_type":"state_flow","severity":"P0","title":"取消订单后仍可支付","api":"POST /payments","trigger":"pay cancelled order","expected_status":409,"signal":"status_code == 200"},

    {"template_id":"REFUND_DUPLICATE","domain":"refund","risk_type":"refund_abuse","severity":"P1","title":"重复退款","api":"POST /refunds","trigger":"refund same order twice","expected_status":409,"signal":"status_code == 200 twice"},
    {"template_id":"REFUND_UNPAID_ORDER","domain":"refund","risk_type":"refund_abuse","severity":"P1","title":"未支付订单可退款","api":"POST /refunds","trigger":"refund unpaid order","expected_status":409,"signal":"status_code == 200"},
    {"template_id":"REFUND_OVER_AMOUNT","domain":"refund","risk_type":"money_consistency","severity":"P0","title":"退款金额超过支付金额","api":"POST /refunds","trigger":"refund greater than paid amount","expected_status":409,"signal":"status_code == 200"},
    {"template_id":"REFUND_STATE_INCONSISTENCY","domain":"refund","risk_type":"state_consistency","severity":"P1","title":"退款后库存或订单状态不一致","api":"POST /refunds","trigger":"refund then query order and stock","expected_status":200,"signal":"state not refunded or stock not restored"},

    {"template_id":"IDEMPOTENCY_DUPLICATE_ORDER","domain":"idempotency","risk_type":"idempotency","severity":"P1","title":"同一 idempotency key 重复创建订单","api":"POST /orders","trigger":"repeat create order with same key","expected_status":200,"signal":"different order ids"},
    {"template_id":"IDEMPOTENCY_DUPLICATE_STOCK_DEDUCT","domain":"idempotency","risk_type":"stock_consistency","severity":"P1","title":"重复提交重复扣库存","api":"POST /orders","trigger":"repeat submit and compare stock","expected_status":200,"signal":"stock deducted twice"},
    {"template_id":"IDEMPOTENCY_DUPLICATE_PAYMENT","domain":"idempotency","risk_type":"idempotency","severity":"P1","title":"重复回调重复处理","api":"POST /payments/callback","trigger":"repeat callback id","expected_status":200,"signal":"state changes twice"},

    {"template_id":"MONEY_FLOAT_PRECISION","domain":"money","risk_type":"money_consistency","severity":"P2","title":"浮点精度错误","api":"POST /payments","trigger":"create fractional price order","expected_status":200,"signal":"total has precision drift"},
    {"template_id":"MONEY_DISCOUNT_OVER_TOTAL","domain":"money","risk_type":"money_consistency","severity":"P0","title":"优惠金额超过订单金额","api":"POST /cart/apply-coupon","trigger":"apply coupon larger than total","expected_status":400,"signal":"total < 0"},
    {"template_id":"MONEY_PAY_TOTAL_DIFF","domain":"money","risk_type":"money_consistency","severity":"P0","title":"订单金额和支付金额不一致","api":"POST /payments/callback","trigger":"pay with mismatched amount","expected_status":409,"signal":"status_code == 200"},
]

# Backward compatibility aliases used by the Phase2 Bug Factory runtime.
TEMPLATE_ALIASES = {
    "AUTH_VERTICAL_BYPASS": ["AUTH_ADMIN_BYPASS"],
    "AUTH_UNAUTH_ACCESS": ["AUTH_ANON_ACCESS"],
    "AUTH_LOCKED_USER_BYPASS": ["AUTH_LOCKED_LOGIN"],
    "COUPON_DOUBLE_DISCOUNT": ["COUPON_REUSE"],
    "COUPON_EXPIRED_ALLOWED": ["COUPON_EXPIRED"],
    "COUPON_THRESHOLD_BYPASS": ["COUPON_THRESHOLD"],
    "COUPON_OWNERSHIP_BYPASS": ["COUPON_OTHER_USER"],
    "PAYMENT_DUPLICATE_CALLBACK": ["PAY_CALLBACK_DUP"],
    "PAYMENT_CANCELLED_ORDER_ALLOWED": ["PAY_CANCELLED_ORDER"],
    "REFUND_DUPLICATE": ["REFUND_DUP"],
    "REFUND_UNPAID_ORDER": ["REFUND_UNPAID"],
    "IDEMPOTENCY_DUPLICATE_ORDER": ["IDEMP_ORDER_KEY", "ORDER_DUPLICATE_SUBMIT"],
    "IDEMPOTENCY_DUPLICATE_PAYMENT": ["IDEMP_CALLBACK"],
    "IDEMPOTENCY_DUPLICATE_STOCK_DEDUCT": ["IDEMP_STOCK_DEDUCT"],
    "STOCK_NOT_DECREASED": ["STOCK_NOT_DEDUCTED"],
    "STOCK_NOT_ROLLBACK": ["STOCK_CANCEL_NO_ROLLBACK"],
    "STOCK_NEGATIVE_QUANTITY": ["STOCK_NEGATIVE"],
    "PAYMENT_STATUS_NOT_UPDATED": ["PAY_STATUS_NOT_UPDATED"],
}

ACTORS = ["normal_user", "vip_user", "seller", "tenant_admin", "guest"]
RESOURCES = ["admin_orders", "orders", "products", "refunds", "payments", "tenant_orders", "cart"]
OPERATIONS = ["view", "create", "update", "delete", "cancel", "export", "callback"]
TENANT_SCOPES = ["same_tenant", "cross_tenant"]
AUTH_STATES = ["logged_in", "anonymous", "locked", "cached_token"]
DATA_CONDITIONS = ["normal", "boundary", "expired", "duplicate", "mismatch", "negative", "over_limit"]


def template_by_id(template_id: str) -> dict[str, Any] | None:
    for item in TEMPLATE_DEFS:
        if item["template_id"] == template_id:
            return item
    return None


def all_template_ids(include_aliases: bool = False) -> set[str]:
    ids = {item["template_id"] for item in TEMPLATE_DEFS}
    if include_aliases:
        for aliases in TEMPLATE_ALIASES.values():
            ids.update(aliases)
    return ids


def variant_for(template: dict[str, Any], index: int, rng: random.Random) -> dict[str, str]:
    domain = template["domain"]
    risk = template["risk_type"]
    actor = rng.choice(ACTORS)
    resource = template["api"].split(" ", 1)[1].strip("/").split("/")[0] or rng.choice(RESOURCES)
    operation = "view" if template["api"].startswith("GET") else "create"
    if "cancel" in template["api"]:
        operation = "cancel"
    if "callback" in template["api"]:
        operation = "callback"
    if "refund" in template["api"]:
        operation = "refund"
    if domain == "permission":
        actor = rng.choice(["normal_user", "guest", "seller"])
    if risk == "auth_bypass":
        actor = "anonymous"
    if risk == "locked_account_bypass":
        actor = "locked_user"
    return {
        "actor": actor,
        "resource": resource,
        "operation": operation,
        "tenant_scope": rng.choice(TENANT_SCOPES),
        "auth_state": "anonymous" if actor == "anonymous" else ("locked" if actor == "locked_user" else rng.choice(["logged_in", "cached_token"])),
        "data_condition": rng.choice(DATA_CONDITIONS),
        "variant_index": str(index),
    }


def make_instance(template: dict[str, Any], sequence: int, rng: random.Random) -> dict[str, Any]:
    variant = variant_for(template, sequence, rng)
    template_id = template["template_id"]
    short_actor = variant["actor"].upper().replace("_", "")
    short_res = variant["resource"].upper().replace("_", "")
    short_op = variant["operation"].upper().replace("_", "")
    bug_instance_id = f"{template_id}_{short_actor}_{short_op}_{short_res}_{sequence:04d}"
    bug_id = f"{template_id}_{sequence:04d}"
    aliases = TEMPLATE_ALIASES.get(template_id, [])
    enabled_ids = [template_id, bug_id, bug_instance_id, *aliases]
    return {
        "bug_id": bug_id,
        "template_id": template_id,
        "bug_instance_id": bug_instance_id,
        "enabled_ids": enabled_ids,
        "title": template["title"],
        "domain": template["domain"],
        "risk_type": template["risk_type"],
        "severity": template["severity"],
        "business_impact": impact_for(template["severity"], template["title"]),
        "trigger_condition": template["trigger"].format(actor=variant["actor"], api=template["api"]),
        "expected_behavior": f"{template['api']} 应返回 {template['expected_status']} 或保持业务状态一致",
        "actual_bug_behavior": f"缺陷实例违反 {template['risk_type']} 业务不变量",
        "related_apis": [template["api"]],
        "oracle": {"type": template["risk_type"], "expected_status": template["expected_status"], "bug_signal": template["signal"]},
        "evidence_required": ["actor_role", "request", "response_status", "response_body", "expected", "actual"],
        "variant_dimensions": variant,
        "enabled": True,
    }


def generate_bug_catalog(count: int = 50, seed: int | None = None) -> list[dict[str, Any]]:
    rng = random.Random(seed if seed is not None else 20260619)
    templates = deepcopy(TEMPLATE_DEFS)
    bugs: list[dict[str, Any]] = []
    seq = 1
    while len(bugs) < count:
        rng.shuffle(templates)
        for template in templates:
            bugs.append(make_instance(template, seq, rng))
            seq += 1
            if len(bugs) >= count:
                break
    return bugs


def public_bug_set(bugs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # Public bug set intentionally hides answers; it is useful for product docs only,
    # not consumed by ai_test_asset_center during blind discovery.
    hidden = {"bug_id", "bug_instance_id", "trigger_condition", "actual_bug_behavior", "oracle", "enabled", "enabled_ids", "variant_dimensions"}
    return [{k: v for k, v in bug.items() if k not in hidden} for bug in bugs]


def impact_for(severity: str, title: str) -> str:
    if severity == "P0":
        return f"{title}，可能造成资损、敏感数据泄露或发布阻断。"
    if severity == "P1":
        return f"{title}，会破坏核心交易链路并显著增加客服和测试成本。"
    return f"{title}，影响账务准确性或用户体验，需要排期修复。"

# Phase2 compatibility exports.
TEMPLATES = [(t["template_id"], t["domain"], t["risk_type"], t["severity"], t["title"], t["trigger"], t["expected_status"], t["signal"]) for t in TEMPLATE_DEFS]
TEMPLATE_API_MAP = {t["template_id"]: t["api"] for t in TEMPLATE_DEFS}
API_MAP: dict[str, list[str]] = {}
for t in TEMPLATE_DEFS:
    API_MAP.setdefault(t["domain"], []).append(t["api"])
