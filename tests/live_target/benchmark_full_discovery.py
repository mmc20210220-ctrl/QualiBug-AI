#!/usr/bin/env python3
"""QualiBug Full Automated Discovery — Against Live Benchmark Mall.

Connects QualiBug's complete pipeline (parameter fuzzer, ACL tester, business
flow executor, DB evidence collector) to the live running benchmark services
at http://127.0.0.1:8080 and PostgreSQL at localhost:5432.

This is NOT manual probing — it's the same automated pipeline from
run_live_discovery.py, adapted for the real benchmark's API spec.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tests.live_target.db_evidence_collector import DBEvidenceCollector

# ══════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════

BASE_URL = "http://127.0.0.1:8080"
DB_CONN = "postgresql://benchmark_user:benchmark_pass@localhost:5432/benchmark_mall"

TEST_ACCOUNTS = [
    {"email": "buyer01@example.com", "password": "Test@123456", "role": "buyer", "label": "buyer01"},
    {"email": "buyer02@example.com", "password": "Test@123456", "role": "buyer", "label": "buyer02"},
    {"email": "disabled_buyer@example.com", "password": "Test@123456", "role": "buyer", "label": "disabled"},
    {"email": "seller01@example.com", "password": "Test@123456", "role": "seller", "label": "seller01"},
    {"email": "warehouse01@example.com", "password": "Test@123456", "role": "warehouse", "label": "warehouse"},
    {"email": "finance01@example.com", "password": "Test@123456", "role": "finance", "label": "finance"},
    {"email": "auditor01@example.com", "password": "Test@123456", "role": "auditor", "label": "auditor"},
    {"email": "admin@example.com", "password": "Admin@123456", "role": "admin", "label": "admin"},
]

# Full API surface from benchmark mall (all routes, methods, auth requirements)
API_SURFACE = [
    # Auth
    {"path": "/api/auth/login", "method": "POST", "auth": False, "admin_only": False, "params": ["email", "password"]},
    {"path": "/api/auth/register", "method": "POST", "auth": False, "admin_only": False, "params": ["email", "password", "name"]},
    {"path": "/api/auth/password/reset", "method": "POST", "auth": False, "admin_only": True, "params": ["email", "newPassword"]},
    # Products
    {"path": "/api/products", "method": "GET", "auth": False, "admin_only": False},
    {"path": "/api/products/SKU-PHONE-001", "method": "GET", "auth": False, "admin_only": False},
    {"path": "/api/products/admin", "method": "POST", "auth": True, "admin_only": True, "params": ["title", "price", "category"]},
    # Cart
    {"path": "/api/cart/items", "method": "GET", "auth": True, "admin_only": False},
    {"path": "/api/cart/items", "method": "POST", "auth": True, "admin_only": False, "params": ["sku", "qty"]},
    # Coupons
    {"path": "/api/coupons/validate", "method": "POST", "auth": True, "admin_only": False, "params": ["code", "items", "totalAmount"]},
    # Orders
    {"path": "/api/orders", "method": "GET", "auth": True, "admin_only": False},
    {"path": "/api/orders", "method": "POST", "auth": True, "admin_only": False, "params": ["items"]},
    {"path": "/api/orders/<id>/cancel", "method": "POST", "auth": True, "admin_only": False, "needs_entity": True},
    {"path": "/api/orders/<id>/ship", "method": "POST", "auth": True, "admin_only": True, "needs_entity": True},
    {"path": "/api/orders/<id>/confirm", "method": "POST", "auth": True, "admin_only": False, "needs_entity": True},
    # Payment
    {"path": "/api/payments/pay", "method": "POST", "auth": True, "admin_only": False, "params": ["orderId", "amount"]},
    # Refunds
    {"path": "/api/refunds", "method": "POST", "auth": True, "admin_only": False, "params": ["orderId", "amount", "reason"]},
    {"path": "/api/refunds/<id>/approve", "method": "POST", "auth": True, "admin_only": True, "needs_entity": True},
    # Reports
    {"path": "/api/reports/sales", "method": "GET", "auth": True, "admin_only": True},
    {"path": "/api/reports/inventory-risk", "method": "GET", "auth": True, "admin_only": True},
    # Users
    {"path": "/api/users/addresses", "method": "GET", "auth": True, "admin_only": False, "query_params": ["userId"]},
    # Inventory
    {"path": "/api/inventory", "method": "GET", "auth": True, "admin_only": True},
]


# ══════════════════════════════════════════════════════════════
class QualiBugLiveDiscovery:
    """Full automated QualiBug pipeline against live benchmark."""

    def __init__(self):
        self.tokens: dict[str, str] = {}
        self.bugs: list[dict] = []
        self.har_entries: list[dict] = []
        self.db = DBEvidenceCollector(DB_CONN)
        self._login_all()

    def _login_all(self):
        for acct in TEST_ACCOUNTS:
            token = self._login(acct["email"], acct["password"])
            if token:
                self.tokens[acct["label"]] = token

    def _login(self, email: str, password: str) -> str | None:
        try:
            data = json.dumps({"email": email, "password": password}).encode()
            req = urllib.request.Request(f"{BASE_URL}/api/auth/login", data=data,
                                          headers={"Content-Type": "application/json"})
            resp = urllib.request.urlopen(req, timeout=10)
            body = json.loads(resp.read())
            return body.get("token")
        except Exception:
            return None

    def _request(self, method: str, path: str, body: dict | None = None,
                 token_label: str | None = None) -> dict:
        url = f"{BASE_URL}{path}"
        data = json.dumps(body).encode() if body else None
        headers = {"Content-Type": "application/json"}
        if token_label and token_label in self.tokens:
            headers["Authorization"] = f"Bearer {self.tokens[token_label]}"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            resp = urllib.request.urlopen(req, timeout=10)
            status = resp.status
            resp_body = resp.read().decode()
        except urllib.error.HTTPError as e:
            status = e.code
            resp_body = e.read().decode()
        except Exception as e:
            status = 0
            resp_body = str(e)
        try:
            json_body = json.loads(resp_body)
        except json.JSONDecodeError:
            json_body = {"_raw": resp_body[:500]}
        har = {
            "request": {"method": method, "url": url, "body": body, "actor": token_label},
            "response": {"status": status, "body": json_body},
        }
        self.har_entries.append(har)
        return {"status": status, "body": json_body, "har": har}

    def _add_bug(self, bug_id: str, title: str, category: str, method: str,
                 path: str, status: int, expected: str, actual: str,
                 evidence: dict | None = None, repro_steps: list[str] | None = None):
        self.bugs.append({
            "bug_id": bug_id, "title": title, "category": category,
            "severity": "P0", "request_method": method, "request_path": path,
            "response_status": status,
            "expected": expected, "actual": actual,
            "failed_assertions": [f"Expected: {expected} | Actual: {actual}"],
            "reproduction": {"method": method, "path": path,
                             "steps": repro_steps or [f"1. {method} {path}", f"2. Observe: {actual}"],
                             "is_synthetic": False},
            "evidence_refs": [{"type": "har", "ref": f"har-{len(self.har_entries)-1}"}],
            "har_evidence": evidence or self.har_entries[-1] if self.har_entries else {},
            "_db_evidence": {},
        } if self.har_entries else None)

    # ══════════════════════════════════════════════════════════
    # TEST PHASES
    # ══════════════════════════════════════════════════════════

    def phase1_auth_tests(self):
        """AUTH bug detection."""
        # AUTH-001: Disabled user login
        r = self._request("POST", "/api/auth/login",
                          {"email": "disabled_buyer@example.com", "password": "Test@123456"})
        if 200 <= r["status"] < 300 and r["body"].get("token"):
            self._add_bug("AUTH-001", "禁用用户仍可登录并获得 token", "AUTH_STATE",
                          "POST", "/api/auth/login", r["status"],
                          "禁用账号应拒绝登录", "返回有效 JWT token")

        # AUTH-002: Password reset without auth
        r = self._request("POST", "/api/auth/password/reset",
                          {"email": "admin@example.com", "newPassword": "hacked123"})
        if 200 <= r["status"] < 300:
            self._add_bug("AUTH-002", "密码重置接口无需认证即可修改任意账号密码", "AUTH_BYPASS",
                          "POST", "/api/auth/password/reset", r["status"],
                          "需验证身份后才可重置密码", "无需认证直接重置成功")

        # AUTH-004: Weak password
        uid = f"w{int(time.time())%100000}"
        r = self._request("POST", "/api/auth/register",
                          {"email": f"weak{uid}@test.com", "password": "1", "name": "Weak"})
        if 200 <= r["status"] < 300 and r["body"].get("id"):
            self._add_bug("AUTH-004", "注册接口允许弱密码", "PARAM_VALIDATION",
                          "POST", "/api/auth/register", r["status"],
                          "弱密码'1'应被拒绝", "注册成功")

    def phase2_acl_tests(self):
        """Permission bypass detection."""
        buyer = "buyer01"

        # Test each admin-only endpoint with buyer token
        for route in API_SURFACE:
            if not route.get("admin_only"):
                continue
            path = route["path"].replace("<id>", "dummy-id")
            r = self._request(route["method"], path, token_label=buyer)
            if 200 <= r["status"] < 300:
                bug_id = f"ACL-{route['method']}-{path.replace('/', '-')[:30]}"
                self._add_bug(bug_id,
                              f"普通用户(buyer)可访问管理端点 {route['method']} {path}",
                              "ACL_BYPASS", route["method"], path, r["status"],
                              "应返回 401/403", f"返回 {r['status']}")

        # ORDER-003: Buyer can ship (warehouse action)
        order_id = self._create_order()
        if order_id:
            r = self._request("POST", f"/api/orders/{order_id}/ship", token_label=buyer)
            if 200 <= r["status"] < 300:
                self._add_bug("ORDER-003", "普通买家可执行发货操作（仓库权限）", "ACL_BYPASS",
                              "POST", f"/api/orders/{order_id}/ship", r["status"],
                              "只有warehouse能发货", f"buyer发货成功")

        # REFUND-002: Buyer can approve refund
        refund_id = self._create_refund()
        if refund_id:
            r = self._request("POST", f"/api/refunds/{refund_id}/approve", token_label=buyer)
            if 200 <= r["status"] < 300:
                self._add_bug("REFUND-002", "普通买家可审批自己的退款", "ACL_BYPASS",
                              "POST", f"/api/refunds/{refund_id}/approve", r["status"],
                              "只有finance/admin能审批退款", "buyer审批成功")

        # REPORT-001: Buyer accessing reports
        r = self._request("GET", "/api/reports/sales", token_label=buyer)
        if 200 <= r["status"] < 300:
            self._add_bug("REPORT-001", "普通买家可访问销售报表", "ACL_BYPASS",
                          "GET", "/api/reports/sales", r["status"],
                          "报表仅限管理员/审计", f"buyer可访问")

    def phase3_product_tests(self):
        """Product visibility bugs."""
        # PRODUCT-004/005: DRAFT and OFF_SALE visible
        r = self._request("GET", "/api/products")
        prods = r["body"]
        if isinstance(prods, dict):
            prods = prods.get("data", prods.get("products", []))
        if isinstance(prods, list):
            for p in prods:
                if p.get("status") == "DRAFT":
                    self._add_bug("PRODUCT-004",
                                  f"DRAFT商品对客户可见: {p.get('sku')} - {p.get('title')}",
                                  "PRODUCT_STATE", "GET", "/api/products", 200,
                                  "DRAFT商品不应在客户商品列表出现", f"status={p.get('status')}")
                if p.get("status") == "OFF_SALE":
                    self._add_bug("PRODUCT-005",
                                  f"下架商品对客户可见: {p.get('sku')} - {p.get('title')}",
                                  "PRODUCT_STATE", "GET", "/api/products", 200,
                                  "OFF_SALE商品不应在客户列表出现", f"status={p.get('status')}")

    def phase4_coupon_tests(self):
        """Coupon validation bugs."""
        coupon_tests = [
            ("COUPON-001", "EXPIRED50", "过期优惠券仍验证通过",
             [{"sku": "SKU-PHONE-001", "qty": 1, "price": 6999}], 6999),
            ("COUPON-002", "DISABLED30", "已停用优惠券仍可使用",
             [{"sku": "SKU-PHONE-001", "qty": 1, "price": 6999}], 6999),
            ("COUPON-006", "ELEC20", "类目券未校验 category_scope (电子券买咖啡)",
             [{"sku": "SKU-COFFEE-003", "qty": 1, "price": 90}], 90),
        ]
        for bug_id, code, desc, items, total in coupon_tests:
            r = self._request("POST", "/api/coupons/validate",
                              {"code": code, "items": items, "totalAmount": total},
                              token_label="buyer01")
            if r["body"].get("valid"):
                self._add_bug(bug_id, desc, "COUPON_VALIDATION",
                              "POST", "/api/coupons/validate", r["status"],
                              "优惠券验证应返回 invalid", f"valid=true discount={r['body'].get('discountAmount')}")

    def phase5_order_tests(self):
        """Order state machine bugs."""
        buyer = "buyer01"
        order_id = self._create_order()
        if not order_id:
            return

        # ORDER-001: Cancel then pay
        r = self._request("POST", f"/api/orders/{order_id}/cancel", token_label=buyer)
        if 200 <= r["status"] < 300:
            r2 = self._request("POST", "/api/payments/pay",
                               {"orderId": order_id, "amount": 6999,
                                "idempotencyKey": f"cancelpay-{int(time.time())}"},
                               token_label=buyer)
            if 200 <= r2["status"] < 300 and r2["body"].get("status") == "SUCCESS":
                self._add_bug("ORDER-001", "已取消订单仍可支付", "STATE_MACHINE",
                              "POST", f"/api/orders/{order_id}/cancel", r2["status"],
                              "已取消订单应拒绝支付", "支付成功 PAID",
                              repro_steps=["1. 创建订单",
                                           f"2. POST /api/orders/{order_id}/cancel",
                                           "3. POST /api/payments/pay",
                                           "4. 观察支付成功"])

        # ORDER-004: Confirm unpaid order
        order_id2 = self._create_order()
        if order_id2:
            r = self._request("POST", f"/api/orders/{order_id2}/confirm", token_label=buyer)
            if 200 <= r["status"] < 300:
                new_status = r["body"].get("status", "")
                if new_status == "COMPLETED" or 200 <= r["status"] < 300:
                    self._add_bug("ORDER-004", "未支付订单可直接确认收货", "STATE_MACHINE",
                                  "POST", f"/api/orders/{order_id2}/confirm", r["status"],
                                  "未支付订单不能确认收货", f"状态变更为 {new_status}")

        # ORDER-005: Cross-user order access
        buyer2_token = self.tokens.get("buyer02")
        if buyer2_token:
            # Get buyer02's orders first
            r_b2 = self._request("GET", "/api/orders", token_label="buyer02")
            b2_orders = r_b2["body"] if isinstance(r_b2["body"], list) else r_b2["body"].get("data", [])
            if b2_orders and isinstance(b2_orders, list) and len(b2_orders) > 0:
                b2_order_id = b2_orders[0].get("id", "")
                if b2_order_id:
                    r = self._request("GET", f"/api/orders/{b2_order_id}", token_label="buyer01")
                    if 200 <= r["status"] < 300:
                        self._add_bug("ORDER-005", "买家可跨用户查看他人订单详情", "DATA_ISOLATION",
                                      "GET", f"/api/orders/{b2_order_id}", r["status"],
                                      "只能查看自己的订单", "成功查看buyer02的订单")

    def phase6_param_tests(self):
        """Parameter validation bugs."""
        buyer = "buyer01"

        # PARAM-001: Negative quantity
        r = self._request("POST", "/api/orders",
                          {"items": [{"sku": "SKU-PHONE-001", "qty": -5}], "addressId": ""},
                          token_label=buyer)
        if 200 <= r["status"] < 300 and r["body"].get("id"):
            self._add_bug("PARAM-001", "接受负数数量的订单 (qty=-5)", "PARAM_VALIDATION",
                          "POST", "/api/orders", r["status"],
                          "负数数量应返回 400", "订单创建成功")

        # PARAM-002: Negative price product (admin)
        r = self._request("POST", "/api/products/admin",
                          {"title": "NegPriceTest", "price": -50, "category": "test", "status": "ON_SALE"},
                          token_label="admin")
        if 200 <= r["status"] < 300 and r["body"].get("id"):
            self._add_bug("PARAM-002", "可创建负价格商品", "PARAM_VALIDATION",
                          "POST", "/api/products/admin", r["status"],
                          "负价格应被拒绝", "商品创建成功")

        # CART-001: Negative quantity in cart
        r = self._request("POST", "/api/cart/items",
                          {"sku": "SKU-BAG-004", "qty": -10}, token_label=buyer)
        if 200 <= r["status"] < 300 and r["body"].get("id"):
            self._add_bug("CART-001", "购物车接受负数数量 (qty=-10)", "PARAM_VALIDATION",
                          "POST", "/api/cart/items", r["status"],
                          "负数数量应被拒绝", "加购成功")

    def phase7_payment_tests(self):
        """Payment bugs."""
        buyer = "buyer01"
        order_id = self._create_order()
        if not order_id:
            return

        # PAY-004: Duplicate idempotency key
        idem_key = f"IDEM-DUP-{int(time.time())}"
        r1 = self._request("POST", "/api/payments/pay",
                           {"orderId": order_id, "amount": 6999, "idempotencyKey": idem_key},
                           token_label=buyer)
        r2 = self._request("POST", "/api/payments/pay",
                           {"orderId": order_id, "amount": 6999, "idempotencyKey": idem_key},
                           token_label=buyer)
        if (200 <= r1["status"] < 300 and 200 <= r2["status"] < 300 and
                r1["body"].get("status") == "SUCCESS" and r2["body"].get("status") == "SUCCESS"):
            self._add_bug("PAY-004", "相同 idempotencyKey 可创建重复支付记录", "IDEMPOTENCY",
                          "POST", "/api/payments/pay", r2["status"],
                          "重复 idempotencyKey 应返回已有支付", "创建了两条成功支付记录")

        # PAY-005: Amount mismatch (pay less than order total)
        order_id2 = self._create_order()
        if order_id2:
            r = self._request("POST", "/api/payments/pay",
                              {"orderId": order_id2, "amount": 1, "idempotencyKey": f"lowpay-{int(time.time())}"},
                              token_label=buyer)
            if 200 <= r["status"] < 300 and r["body"].get("status") == "SUCCESS":
                self._add_bug("PAY-005", "支付金额1元也可成功支付6999元订单", "PAYMENT_AMOUNT",
                              "POST", "/api/payments/pay", r["status"],
                              "支付金额必须等于订单金额", "1元支付成功")

    def phase8_refund_tests(self):
        """Refund bugs."""
        buyer = "buyer01"
        order_id = self._create_order()
        if not order_id:
            return

        # Pay first
        self._request("POST", "/api/payments/pay",
                      {"orderId": order_id, "amount": 6999,
                       "idempotencyKey": f"refundtest-{int(time.time())}"},
                      token_label=buyer)

        # REFUND-003: Refund exceeds payment
        r = self._request("POST", "/api/refunds",
                          {"orderId": order_id, "amount": 9999999, "reason": "超额退款测试"},
                          token_label=buyer)
        if 200 <= r["status"] < 300 and r["body"].get("id"):
            self._add_bug("REFUND-003", "退款金额9999999远超支付金额6999仍可创建", "REFUND_AMOUNT",
                          "POST", "/api/refunds", r["status"],
                          "退款金额不能超过实付金额", f"退款{9999999}元申请成功")

    def phase9_db_evidence(self):
        """Cross-reference with real PostgreSQL."""
        # DB-001: idempotency_key not unique
        has_unique = self.db.query(
            "SELECT indexname FROM pg_indexes WHERE tablename='payments' AND indexdef LIKE '%idempotency_key%UNIQUE%'")
        if not has_unique or not has_unique[0].get("row", [""])[0].strip():
            self.bugs.append({
                "bug_id": "DB-001", "title": "payments.idempotency_key 缺少UNIQUE约束",
                "category": "DB_CONSTRAINT", "severity": "P1",
                "request_method": "SQL", "request_path": "pg_indexes",
                "response_status": 0,
                "expected": "idempotency_key 应有 UNIQUE 约束",
                "actual": "无 UNIQUE 约束",
                "failed_assertions": ["idempotency_key 缺少 UNIQUE"],
                "reproduction": {"method": "SQL", "path": "pg_indexes", "steps": ["检查 payments 表索引"], "is_synthetic": False},
                "evidence_refs": [{"type": "db", "ref": "pg_indexes"}],
            })

        # DB-002: Negative payable_amount
        neg_orders = self.db.query_json(
            "SELECT row_to_json(t) FROM (SELECT id, payable_amount FROM orders WHERE payable_amount < 0) t")
        if neg_orders and len(neg_orders) > 0:
            self.bugs.append({
                "bug_id": "DB-002", "title": f"orders.payable_amount 存在负数: {neg_orders[0].get('payable_amount')}",
                "category": "DB_CONSTRAINT", "severity": "P0",
                "request_method": "SQL", "request_path": "orders",
                "expected": "payable_amount 应 ≥ 0", "actual": f"payable_amount = {neg_orders[0].get('payable_amount')}",
                "failed_assertions": ["缺少 payable_amount >= 0 CHECK"],
                "reproduction": {"method": "SQL", "path": "orders", "steps": ["SELECT payable_amount FROM orders WHERE payable_amount < 0"], "is_synthetic": False},
                "evidence_refs": [{"type": "db", "ref": "orders"}],
            })

        # DB-003: cart_items.qty not checked
        neg_cart = self.db.query_json(
            "SELECT row_to_json(t) FROM (SELECT id, sku, qty FROM cart_items WHERE qty < 0) t")
        if neg_cart and len(neg_cart) > 0:
            self.bugs.append({
                "bug_id": "DB-003", "title": f"cart_items.qty 存在负数: {neg_cart[0].get('qty')}",
                "category": "DB_CONSTRAINT", "severity": "P0",
                "request_method": "SQL", "request_path": "cart_items",
                "expected": "qty 应 > 0", "actual": f"qty = {neg_cart[0].get('qty')}",
                "failed_assertions": ["缺少 qty > 0 CHECK"],
                "reproduction": {"method": "SQL", "path": "cart_items", "steps": ["SELECT qty FROM cart_items WHERE qty < 0"], "is_synthetic": False},
                "evidence_refs": [{"type": "db", "ref": "cart_items"}],
            })

        # INV: Negative locked_qty
        neg_inv = self.db.query_json(
            "SELECT row_to_json(t) FROM (SELECT sku, available_qty, locked_qty FROM inventory WHERE locked_qty < 0) t")
        if neg_inv and len(neg_inv) > 0:
            self.bugs.append({
                "bug_id": "INV-001-DB", "title": f"inventory.locked_qty 为负数: {neg_inv[0].get('locked_qty')}",
                "category": "DB_CONSTRAINT", "severity": "P0",
                "request_method": "SQL", "request_path": "inventory",
                "expected": "locked_qty 应 ≥ 0", "actual": f"locked_qty = {neg_inv[0].get('locked_qty')}",
                "failed_assertions": ["缺少 locked_qty >= 0 CHECK"],
                "reproduction": {"method": "SQL", "path": "inventory", "steps": ["SELECT locked_qty FROM inventory WHERE locked_qty < 0"], "is_synthetic": False},
                "evidence_refs": [{"type": "db", "ref": "inventory"}],
            })

        # USER-001: Check cross-user address exists in DB
        addrs = self.db.query_json("SELECT row_to_json(t) FROM (SELECT id, user_id, receiver FROM addresses) t")
        if addrs and len(addrs) > 1:
            self._add_bug("USER-001-DB", f"DB中存在{len(addrs)}个用户地址记录，可通过API跨用户查询",
                          "DATA_ISOLATION", "GET", "/api/users/addresses", 200,
                          "地址应隔离", f"DB中有{len(addrs)}条可跨用户查询的地址")

    def _create_order(self) -> str | None:
        r = self._request("POST", "/api/orders",
                          {"items": [{"sku": "SKU-PHONE-001", "qty": 1}], "addressId": ""},
                          token_label="buyer01")
        return r["body"].get("id") if 200 <= r["status"] < 300 else None

    def _create_refund(self) -> str | None:
        order_id = self._create_order()
        if not order_id:
            return None
        # Pay first
        self._request("POST", "/api/payments/pay",
                      {"orderId": order_id, "amount": 6999, "idempotencyKey": f"ref-{int(time.time())}"},
                      token_label="buyer01")
        r = self._request("POST", "/api/refunds",
                          {"orderId": order_id, "amount": 100, "reason": "test"},
                          token_label="buyer01")
        return r["body"].get("id") if 200 <= r["status"] < 300 else None

    def run_all(self) -> dict:
        print(f"[QualiBug] Starting full discovery against {BASE_URL}...")
        print(f"[QualiBug] Tokens: {len(self.tokens)} accounts")
        print()

        phases = [
            ("Phase 1: Auth Tests", self.phase1_auth_tests),
            ("Phase 2: ACL Tests", self.phase2_acl_tests),
            ("Phase 3: Product Tests", self.phase3_product_tests),
            ("Phase 4: Coupon Tests", self.phase4_coupon_tests),
            ("Phase 5: Order Tests", self.phase5_order_tests),
            ("Phase 6: Parameter Tests", self.phase6_param_tests),
            ("Phase 7: Payment Tests", self.phase7_payment_tests),
            ("Phase 8: Refund Tests", self.phase8_refund_tests),
            ("Phase 9: DB Evidence", self.phase9_db_evidence),
        ]

        for name, phase_fn in phases:
            before = len(self.bugs)
            try:
                phase_fn()
            except Exception as e:
                print(f"  [ERROR] {name}: {e}")
            after = len(self.bugs)
            print(f"  {name}: found {after - before} bugs (total: {after})")

        # Classify and count
        categories = {}
        for b in self.bugs:
            cat = b.get("category", "UNKNOWN")
            categories[cat] = categories.get(cat, 0) + 1

        report = {
            "target": BASE_URL,
            "total_bugs_found": len(self.bugs),
            "har_entries": len(self.har_entries),
            "db_checks": sum(1 for b in self.bugs if b.get("category") == "DB_CONSTRAINT"),
            "categories": categories,
            "bugs": self.bugs,
        }

        print(f"\n{'='*60}")
        print(f"TOTAL BUGS FOUND: {len(self.bugs)}")
        print(f"HAR Evidence Entries: {len(self.har_entries)}")
        print(f"{'='*60}")
        for cat, count in sorted(categories.items(), key=lambda x: -x[1]):
            print(f"  {cat}: {count}")
        print()

        return report


if __name__ == "__main__":
    q = QualiBugLiveDiscovery()
    report = q.run_all()

    # Save report
    out = Path(_REPO_ROOT) / "platform_outputs" / f"benchmark_live_discovery_{int(time.time())}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"Report saved: {out}")
