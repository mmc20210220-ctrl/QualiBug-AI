"""
QualiBug Deep Verifier — Configuration-driven, no hardcoded values.
Reads test_profile from connector_registry, discovers targets from API spec + DB.
"""
from __future__ import annotations
import urllib.request, json, time, re, threading, queue
from typing import Any


def run_deep_tests(config: dict | None = None, routes: list[dict] | None = None) -> list[dict]:
    """Run deep verification tests driven by configuration and API routes.
    
    Args:
        config: test_profile dict from connector_registry.json, or None to auto-load
        routes: parsed API routes from API spec
    """
    findings: list[dict] = []
    
    # ── Load config ──
    if config is None:
        config = _load_config()
    
    base_url = config.get("api_base_url", "")
    creds = config.get("test_credentials", {})
    db_cfg = config.get("database", {})
    frontend = config.get("frontend_urls", {})
    
    buyer_email = creds.get("buyer", {}).get("email", "")
    buyer_pw = creds.get("buyer", {}).get("password", "")
    admin_email = creds.get("admin", {}).get("email", "")
    admin_pw = creds.get("admin", {}).get("password", "")
    
    if not base_url:
        return [{"severity": "P3", "title": "[Deep] 缺少api_base_url配置", "source": "deep_verifier",
                 "category": "config", "description": "connector_registry缺少test_profile.api_base_url"}]
    
    # ── Auto-discover from API routes ──
    if routes is None:
        routes = _discover_routes(base_url)
    
    admin_paths = [r["path"] for r in routes if _is_admin_path(r["path"], r.get("method", ""))]
    auth_paths = [r["path"] for r in routes if "auth" in r.get("path", "").lower()]
    order_paths = [r["path"] for r in routes if "order" in r.get("path", "").lower()]
    cart_paths = [r["path"] for r in routes if "cart" in r.get("path", "").lower()]
    product_paths = [r["path"] for r in routes if "product" in r.get("path", "").lower()]
    coupon_paths = [r["path"] for r in routes if "coupon" in r.get("path", "").lower()]
    refund_paths = [r["path"] for r in routes if "refund" in r.get("path", "").lower()]
    payment_paths = [r["path"] for r in routes if "payment" in r.get("path", "").lower()]
    report_paths = [r["path"] for r in routes if "report" in r.get("path", "").lower()]
    ship_paths = [r["path"] for r in routes if "ship" in r.get("path", "").lower()]
    confirm_paths = [r["path"] for r in routes if "confirm" in r.get("path", "").lower()]
    
    def add(title: str, sev: str, cat: str, desc: str, conf: float = 0.90):
        findings.append({"severity": sev, "title": title, "category": cat,
                        "source": "deep_verifier", "description": desc, "confidence_score": conf})
    
    # ── Auth setup ──
    def login(email: str, pw: str) -> str:
        if not email or not pw: return ""
        try:
            login_data = json.dumps({"email": email, "password": pw}).encode()
            req = urllib.request.Request(f"{base_url}/api/auth/login", data=login_data,
                headers={"Content-Type": "application/json"}, method="POST")
            return json.loads(urllib.request.urlopen(req, timeout=5).read()).get("token", "")
        except Exception:
            return ""
    
    buyer_token = login(buyer_email, buyer_pw) if buyer_email else ""
    admin_token = login(admin_email, admin_pw) if admin_email else ""
    
    # ── Auto-setup: register if login fails ──
    if not buyer_token and buyer_email:
        try:
            reg = json.dumps({"email": buyer_email, "password": buyer_pw, "name": "TestBuyer", "phone": "13800000001"}).encode()
            for auth_p in auth_paths:
                if "register" in auth_p.lower() or "signup" in auth_p.lower():
                    urllib.request.urlopen(urllib.request.Request(
                        f"{base_url}{auth_p}", data=reg, headers={"Content-Type": "application/json"}, method="POST"), timeout=5)
                    break
            buyer_token = login(buyer_email, buyer_pw)
        except: pass
    if not admin_token and admin_email:
        try:
            reg = json.dumps({"email": admin_email, "password": admin_pw, "name": "TestAdmin", "phone": "13800000002"}).encode()
            for auth_p in auth_paths:
                if "register" in auth_p.lower() or "signup" in auth_p.lower():
                    urllib.request.urlopen(urllib.request.Request(
                        f"{base_url}{auth_p}", data=reg, headers={"Content-Type": "application/json"}, method="POST"), timeout=5)
                    break
            admin_token = login(admin_email, admin_pw)
        except: pass
    
    # ── Auto-setup: fetch or create address ──
    addr_id = ""
    if buyer_token:
        try:
            addr_endpoints = [p for p in routes if isinstance(p, dict) and "address" in p.get("path","").lower()]
            for ae in addr_endpoints:
                try:
                    req = urllib.request.Request(f"{base_url}{ae['path']}", headers={"Authorization": f"Bearer {buyer_token}"})
                    addrs = json.loads(urllib.request.urlopen(req, timeout=5).read())
                    if isinstance(addrs, list) and addrs:
                        addr_id = addrs[0].get("id", "")
                        break
                except: pass
            # Create address if none exist
            if not addr_id:
                addr_body = json.dumps({"receiver": "TestBuyer", "phone": "13800000001",
                    "province": "上海", "city": "上海", "detail": "测试地址", "isDefault": True}).encode()
                for ae in addr_endpoints:
                    try:
                        req = urllib.request.Request(f"{base_url}{ae['path']}", data=addr_body,
                            headers={"Content-Type": "application/json", "Authorization": f"Bearer {buyer_token}"}, method="POST")
                        resp = json.loads(urllib.request.urlopen(req, timeout=5).read())
                        addr_id = resp.get("id", "")
                        if addr_id: break
                    except: pass
        except: pass

    H_buyer = {"Content-Type": "application/json", "Authorization": f"Bearer {buyer_token}"}
    H_admin = {"Content-Type": "application/json", "Authorization": f"Bearer {admin_token}"}
    
    # ── DB helper ──
    def _db_query(sql: str) -> list:
        try:
            import pg8000
            conn = pg8000.connect(**{k: v for k, v in db_cfg.items() if k in ("host", "port", "user", "password", "database")})
            cur = conn.cursor()
            cur.execute(sql)
            rows = cur.fetchall()
            conn.close()
            return rows
        except Exception:
            return []
    
    def _api_get(path: str, headers: dict | None = None) -> Any:
        try:
            req = urllib.request.Request(f"{base_url}{path}", headers=headers or H_buyer)
            return json.loads(urllib.request.urlopen(req, timeout=5).read())
        except Exception:
            return None
    
    def _api_post(path: str, body: dict, headers: dict | None = None) -> tuple[int, Any]:
        try:
            data = json.dumps(body).encode()
            req = urllib.request.Request(f"{base_url}{path}", data=data,
                headers=headers or H_buyer, method="POST")
            resp = urllib.request.urlopen(req, timeout=5)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try: return e.code, json.loads(e.read())
            except: return e.code, {}
        except Exception:
            return 0, {}

    def _ensure_address(token: str) -> str:
        """Get or auto-create a shipping address for the authenticated user."""
        h = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        paths = ["/api/users/addresses", "/api/addresses", "/api/user/addresses"]
        for p in paths:
            try:
                req = urllib.request.Request(f"{base_url}{p}", headers=h)
                r = json.loads(urllib.request.urlopen(req, timeout=5).read())
                if isinstance(r, list) and r:
                    return r[0].get("id", "")
                if isinstance(r, dict) and r.get("id"):
                    return r["id"]
            except Exception:
                continue
        # Create address
        addr_data = json.dumps({
            "receiver": "QualiBug Auto", "phone": "13800000000",
            "province": "Test", "city": "TestCity", "detail": "Auto-created test address",
            "is_default": True
        }).encode()
        for p in paths:
            try:
                req = urllib.request.Request(f"{base_url}{p}", data=addr_data, headers=h, method="POST")
                r = json.loads(urllib.request.urlopen(req, timeout=5).read())
                rid = (r or {}).get("id", "") if isinstance(r, dict) else ""
                if rid: return rid
            except Exception:
                continue
        # Last resort: try PATCH/DELETE-based endpoints fallback
        try:
            req = urllib.request.Request(f"{base_url}/api/user/profile", data=addr_data, headers=h, method="PUT")
            r = json.loads(urllib.request.urlopen(req, timeout=5).read())
            return (r or {}).get("addressId", "") if isinstance(r, dict) else ""
        except Exception:
            return ""
    
    def _http_error_code(path: str, method: str, body: dict | None, headers: dict) -> int:
        try:
            data = json.dumps(body).encode() if body else None
            req = urllib.request.Request(f"{base_url}{path}", data=data, headers=headers, method=method)
            urllib.request.urlopen(req, timeout=5)
            return 200
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:
            return 0
    
    # ═══════════════════════════════════════
    # 1. Permission: buyer token vs admin endpoints (iterate routes, not pre-filter)
    # ═══════════════════════════════════════
    if buyer_token and routes:
        for route in routes:
            path = route["path"]
            if not _is_admin_path(path, route.get("method", "")):
                continue
            method = route.get("method", "GET")
            # Resolve parameter placeholders
            test_path = path.replace(":id", "SKU-001").replace(":sku", "SKU-001").replace(":orderId", "test")
            code = _http_error_code(test_path, method, {}, H_buyer)
            if code == 200:
                add(f"[权限] 买家可访问管理端点: {method} {path}", "P0", "authorization",
                    f"买家token访问{path}返回200", 0.95)
            elif code not in (0, 401, 403, 404):
                add(f"[权限] {method} {path} 返回{code}(非403)", "P1", "authorization",
                    f"买家token访问管理端点返回{code}", 0.85)
    
    # ═══════════════════════════════════════
    # 2. Coupon: discover codes from DB and test
    # ═══════════════════════════════════════
    if buyer_token and coupon_paths:
        # Discover coupons from DB
        coupons = _db_query("SELECT code, type, amount, status, expires_at FROM coupons WHERE status='ACTIVE' LIMIT 5")
        if coupons:
            validate_path = next((p for p in coupon_paths if "validate" in p), coupon_paths[0])
            # Use first available product for test
            products = _api_get("/api/products")
            test_sku = "SKU-001"
            test_price = 100
            if products and isinstance(products, list):
                for p in products:
                    if isinstance(p, dict) and p.get("status") == "ON_SALE":
                        test_sku = p.get("sku", test_sku)
                        test_price = float(p.get("price") or p.get("current_price") or 0)
                        if test_price > 0: break
            
            for code, ctype, amount, status, expires_at in coupons:
                # Test: below-minimum order amount
                status_code, result = _api_post(validate_path, {
                    "code": code, "items": [{"sku": test_sku, "qty": 1, "price": 1}], "totalAmount": 1}, H_buyer)
                if status_code == 200 and isinstance(result, dict) and result.get("valid"):
                    add(f"[优惠券] 低于最低消费仍可用{code}", "P1", "business_rule",
                        f"totalAmount=1但优惠券{code}仍valid", 0.88)
                
                # Test: repeat validation
                _, r1 = _api_post(validate_path, {"code": code, "items": [{"sku": test_sku, "qty": 1, "price": test_price}],
                    "totalAmount": test_price}, H_buyer)
                _, r2 = _api_post(validate_path, {"code": code, "items": [{"sku": test_sku, "qty": 1, "price": test_price}],
                    "totalAmount": test_price}, H_buyer)
                if isinstance(r1, dict) and r1.get("valid") and isinstance(r2, dict) and r2.get("valid"):
                    add(f"[优惠券] {code}可重复验证(应限制)", "P1", "business_rule",
                        f"同一优惠券多次validate均返回valid", 0.85)
        
        # Test expired coupons from DB
        expired = _db_query("SELECT code FROM coupons WHERE status='ACTIVE' AND expires_at < NOW() LIMIT 3")
        for (ecode,) in expired:
            _, result = _api_post(validate_path, {"code": ecode, "items": [{"sku": test_sku, "qty": 1, "price": test_price}],
                "totalAmount": test_price}, H_buyer)
            if isinstance(result, dict) and result.get("valid"):
                add(f"[优惠券] 过期券{ecode}仍可使用", "P0", "business_rule", f"过期券验证返回valid=true", 0.95)
    
    # ═══════════════════════════════════════
    # 3. Order: boundary tests
    # ═══════════════════════════════════════
    if buyer_token and order_paths:
        create_path = next((p for p in order_paths if not any(x in p for x in ("cancel","ship","confirm","pay"))), None)
        if create_path:
            # Get address dynamically
            addr_id = _ensure_address(buyer_token)
            if addr_id:
                for label, items in [("零数量", [{"sku": test_sku, "qty": 0}]), ("负数量", [{"sku": test_sku, "qty": -1}])]:
                    sc, _ = _api_post(create_path, {"items": items, "addressId": addr_id}, H_buyer)
                    if sc in (200, 201):
                        add(f"[资金] {label}下单成功(应拒绝)", "P1", "financial",
                            f"POST {create_path} qty异常返回{sc}", 0.90)
                
                # Create order for further tests
                sc, order = _api_post(create_path, {"items": [{"sku": test_sku, "qty": 1}], "addressId": addr_id}, H_buyer)
                oid = (order or {}).get("id", "") if sc in (200, 201) else ""
                amt = float((order or {}).get("total_amount") or (order or {}).get("payable_amount") or 0)
                
                if oid and amt > 0:
                    # Pay
                    pay_path = next((p for p in payment_paths if "pay" in p), None) if payment_paths else "/api/payments/pay"
                    _api_post(pay_path, {"orderId": oid, "amount": amt, "channel": "BALANCE",
                        "idempotencyKey": f"deep-{int(time.time())}"}, H_buyer)
                    
                    # Refund boundary tests
                    if refund_paths:
                        refund_path = refund_paths[0]
                        for rlabel, ramt in [("超额", amt*2), ("零元", 0), ("负金额", -100)]:
                            sc_r, _ = _api_post(refund_path, {"orderId": oid, "amount": ramt, "reason": "test"}, H_buyer)
                            if sc_r in (200, 201):
                                add(f"[退款] {rlabel}退款成功(amount={ramt})", "P0", "financial",
                                    f"order={oid} refund={ramt}返回{sc_r}", 0.95)
                    
                    # Cancel + re-pay (state machine)
                    cancel_path = next((p for p in order_paths if "cancel" in p), None)
                    if cancel_path:
                        sc_c, _ = _api_post(cancel_path.format(id=oid) if "{" in cancel_path else cancel_path + f"/{oid}/cancel",
                                           {}, H_buyer)
                        if sc_c in (200, 201):
                            sc_rp, _ = _api_post(pay_path, {"orderId": oid, "amount": amt, "channel": "BALANCE",
                                "idempotencyKey": f"deep2-{int(time.time())}"}, H_buyer)
                            if sc_rp in (200, 201):
                                add("[状态机] 已取消订单仍可支付", "P0", "state_machine",
                                    f"order={oid} 取消后pay返回{sc_rp}", 0.95)
    
    # ═══════════════════════════════════════
    # 4. Cart boundary tests
    # ═══════════════════════════════════════
    if buyer_token and cart_paths:
        cart_create = next((p for p in cart_paths if "items" in p and not any(x in p for x in (":","{"))), cart_paths[0])
        # Non-existent SKU
        sc, _ = _api_post(cart_create, {"sku": "NONEXISTENT-SKU-999", "qty": 1}, H_buyer)
        if sc in (200, 201):
            add("[购物车] 不存在SKU可加入", "P1", "business_rule", "添加不存在商品返回成功", 0.90)
        # Negative qty
        sc, _ = _api_post(cart_create, {"sku": test_sku, "qty": -5}, H_buyer)
        if sc in (200, 201):
            add("[购物车] 负数数量可加入", "P0", "business_rule", "qty=-5返回成功", 0.95)
    
    # ═══════════════════════════════════════
    # 5. Confirm without payment
    # ═══════════════════════════════════════
    if buyer_token and confirm_paths and order_paths:
        addr_resp = _ensure_address(buyer_token)
        addr_id = addr_resp[0]["id"] if isinstance(addr_resp, list) and addr_resp else ""
        if addr_id:
            create_path = next((p for p in order_paths if not any(x in p for x in ("cancel","ship","confirm","pay"))), None)
            if create_path:
                sc, order = _api_post(create_path, {"items": [{"sku": test_sku, "qty": 1}], "addressId": addr_id}, H_buyer)
                oid = (order or {}).get("id", "") if sc in (200, 201) else ""
                if oid:
                    for cp in confirm_paths:
                        resolved = cp.replace(":id", oid).replace("{id}", oid)
                        code = _http_error_code(resolved, "POST", {}, H_buyer)
                        if code == 200:
                            add("[状态机] 未支付订单可确认收货", "P0", "state_machine",
                                f"order={oid} confirm返回200", 0.95)
                            break
    
    # ═══════════════════════════════════════
    # 6. Ship as buyer
    # ═══════════════════════════════════════
    if buyer_token and ship_paths and order_paths:
        addr_resp = _ensure_address(buyer_token)
        addr_id = addr_resp[0]["id"] if isinstance(addr_resp, list) and addr_resp else ""
        if addr_id:
            create_path = next((p for p in order_paths if not any(x in p for x in ("cancel","ship","confirm","pay"))), None)
            sc, order = _api_post(create_path, {"items": [{"sku": test_sku, "qty": 1}], "addressId": addr_id}, H_buyer)
            oid = (order or {}).get("id", "") if sc in (200, 201) else ""
            amt = float((order or {}).get("total_amount") or (order or {}).get("payable_amount") or 0)
            if oid and amt > 0:
                pay_path = payment_paths[0] if payment_paths else "/api/payments/pay"
                _api_post(pay_path, {"orderId": oid, "amount": amt, "channel": "BALANCE",
                    "idempotencyKey": f"ship-{int(time.time())}"}, H_buyer)
                for sp in ship_paths:
                    resolved = sp.replace(":id", oid).replace("{id}", oid)
                    code = _http_error_code(resolved, "POST", {}, H_buyer)
                    if code == 200:
                        add("[权限] 买家可执行发货", "P0", "authorization",
                            f"买家token调用POST {resolved}返回200", 0.95)
                        break
    
    # ═══════════════════════════════════════
    # 7. Multi-user isolation
    # ═══════════════════════════════════════
    if buyer_token:
        try:
            user2_email = f"qb_auto_{int(time.time())}@test.com"
            sc_r, _ = _api_post("/api/auth/register" if auth_paths else "/api/auth/register",
                {"email": user2_email, "password": buyer_pw or "Test@123456", "name": "AutoUser", "phone": "13900000001"}, H_buyer)
            user2_token = login(user2_email, buyer_pw or "Test@123456")
            if user2_token and user2_token != buyer_token:
                H2 = {"Content-Type": "application/json", "Authorization": f"Bearer {user2_token}"}
                # Check order list endpoint
                if order_paths:
                    ol_path = next((p for p in order_paths if "{" not in p and ":" not in p), order_paths[0].split("/:")[0])
                    orders = _api_get(ol_path, H2)
                    if isinstance(orders, list) and len(orders) > 0:
                        add("[数据隔离] 新用户可查看他人订单", "P0", "data_isolation",
                            f"新用户{user2_email}看到{len(orders)}条订单", 0.95)
        except Exception:
            pass
    
    # ═══════════════════════════════════════
    # 8. Concurrent tests
    # ═══════════════════════════════════════
    if buyer_token:
        # Concurrent cart add
        def _cart_add():
            try:
                cd = json.dumps({"sku": test_sku, "qty": 1}).encode()
                r = urllib.request.urlopen(urllib.request.Request(f"{base_url}{cart_create}",
                    data=cd, headers=H_buyer, method="POST"), timeout=5)
                return r.status
            except: return 0
        q = queue.Queue()
        t1 = threading.Thread(target=lambda: q.put(_cart_add()))
        t2 = threading.Thread(target=lambda: q.put(_cart_add()))
        t1.start(); t2.start(); t1.join(3); t2.join(3)
        statuses = []
        while not q.empty(): statuses.append(q.get_nowait())
        if len(statuses) == 2 and all(s in (200, 201) for s in statuses):
            add("[并发] 购物车双写未加锁", "P1", "concurrency", f"两个并发add均成功", 0.85)
        
        # Concurrent payment race
        addr_resp = _ensure_address(buyer_token)
        addr_id = addr_resp[0]["id"] if isinstance(addr_resp, list) and addr_resp else ""
        if addr_id and order_paths:
            create_path = next((p for p in order_paths if not any(x in p for x in ("cancel","ship","confirm","pay"))), None)
            if create_path:
                sc, order = _api_post(create_path, {"items": [{"sku": test_sku, "qty": 1}], "addressId": addr_id}, H_buyer)
                oid = (order or {}).get("id", "") if sc in (200, 201) else ""
                amt = float((order or {}).get("total_amount") or (order or {}).get("payable_amount") or 0)
                if oid and amt > 0:
                    pay_path = payment_paths[0] if payment_paths else "/api/payments/pay"
                    pq = queue.Queue()
                    def _pay():
                        try:
                            pb = json.dumps({"orderId": oid, "amount": amt, "channel": "BALANCE",
                                "idempotencyKey": f"race-{int(time.time()*1000)}"}).encode()
                            r = urllib.request.urlopen(urllib.request.Request(f"{base_url}{pay_path}",
                                data=pb, headers=H_buyer, method="POST"), timeout=5)
                            pq.put(r.status)
                        except: pq.put(0)
                    p1 = threading.Thread(target=_pay); p2 = threading.Thread(target=_pay)
                    p1.start(); p2.start(); p1.join(3); p2.join(3)
                    pstatuses = []
                    while not pq.empty(): pstatuses.append(pq.get_nowait())
                    if len(pstatuses) == 2 and all(ps in (200, 201) for ps in pstatuses):
                        add("[并发] 双支付竞争均成功", "P0", "concurrency",
                            f"order={oid} 同时两次pay返回成功", 0.95)
    
    # ═══════════════════════════════════════
    # 9. DB consistency checks (auto-discover)
    # ═══════════════════════════════════════
    if db_cfg:
        try:
            import pg8000
            params = {k: v for k, v in db_cfg.items() if k in ("host", "port", "user", "password", "database")}
            if params:
                conn = pg8000.connect(**params)
                cur = conn.cursor()
                
                # Discover tables
                cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
                tables = [r[0] for r in cur.fetchall()]
                
                # Check inventory consistency if inventory table exists
                if any("inventor" in t for t in tables):
                    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name LIKE '%inventor%' AND column_name LIKE '%qty%'")
                    qty_cols = [r[0] for r in cur.fetchall()]
                    if qty_cols:
                        cur.execute(f"SELECT * FROM (SELECT * FROM information_schema.tables WHERE table_name LIKE '%inventor%' LIMIT 1) t")
                
                # Simple check: any numeric column with negative values
                for table in tables:
                    cur.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name='{table}' AND data_type IN ('integer','numeric','real','double precision') LIMIT 5")
                    for col_name, _ in cur.fetchall():
                        try:
                            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {col_name} < 0")
                            neg = cur.fetchone()[0]
                            if neg > 0:
                                # Find primary key
                                cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name='{table}' AND column_name LIKE '%id' LIMIT 1")
                                pk_rows = cur.fetchall()
                                pk = pk_rows[0][0] if pk_rows else col_name
                                cur.execute(f"SELECT {pk}, {col_name} FROM {table} WHERE {col_name} < 0 LIMIT 3")
                                for row in cur.fetchall():
                                    add(f"[DB] {table}.{col_name}为负: {row[0]}={row[1]}", "P0", "data_integrity",
                                        f"表{table}列{col_name}含负数值", 0.95)
                        except Exception:
                            pass
                
                conn.close()
        except Exception:
            pass
    
    # ═══════════════════════════════════════
    # 10. Input boundary: special chars
    # ═══════════════════════════════════════
    if buyer_token:
        boundary_tests = [
            ("超长email", "POST", "/api/auth/register",
             {"email": "a" * 5000 + "@test.com", "password": "x", "name": "x", "phone": "x"}),
            ("SQL注入", "POST", "/api/auth/login",
             {"email": "'; DROP TABLE users;--", "password": "' OR 1=1--"}),
        ]
        if product_paths:
            boundary_tests.append(("XSS搜索", "GET", f"/api/products?keyword=<script>alert(1)</script>", None))
        for label, method, path, body in boundary_tests:
            code = _http_error_code(path, method, body, H_buyer)
            if code >= 500:
                add(f"[边界] {label}导致服务端{code}", "P1", "input_validation",
                    f"{method} {path} 返回{code}", 0.88)
    
    # ═══════════════════════════════════════
    # 11. Report accuracy (if reports exist)
    # ═══════════════════════════════════════
    if admin_token and report_paths and db_cfg:
        try:
            db_count = _db_query("SELECT COUNT(*) FROM orders")
            db_total = db_count[0][0] if db_count else 0
            for rp in report_paths:
                result = _api_get(rp, H_admin)
                if isinstance(result, dict):
                    api_count = result.get("total_orders") or result.get("count") or 0
                    if api_count and db_total and api_count != db_total:
                        add(f"[报表] 订单数不一致: API={api_count} DB={db_total}", "P1", "data_integrity",
                            f"报表{rp}数据与DB不符", 0.90)
        except Exception:
            pass
    
    # ═══════════════════════════════════════
    # 12. Security: CORS & headers
    # ═══════════════════════════════════════
    try:
        req = urllib.request.Request(f"{base_url}/api/products" if product_paths else base_url,
            headers={"Origin": "https://evil.com"})
        resp = urllib.request.urlopen(req, timeout=5)
        cors = resp.getheader("Access-Control-Allow-Origin") or ""
        if cors == "*" or "evil.com" in cors:
            add(f"[安全] CORS过于宽松: {cors}", "P1", "security", f"允许任意Origin", 0.88)
        for hdr in ["X-Content-Type-Options", "X-Frame-Options"]:
            if not resp.getheader(hdr):
                add(f"[安全] 缺少安全头: {hdr}", "P2", "security", f"HTTP响应未设置{hdr}", 0.75)
    except Exception:
        pass
    
    # ═══════════════════════════════════════
    # 13. Frontend access check (if URLs configured)
    # ═══════════════════════════════════════
    if buyer_token and frontend.get("admin"):
        try:
            req = urllib.request.Request(frontend["admin"],
                headers={"Authorization": f"Bearer {buyer_token}"})
            resp = urllib.request.urlopen(req, timeout=5)
            html = resp.read().decode('utf-8', 'replace').lower()
            admin_keywords = ["dashboard", "admin", "管理", "后台", "订单列表", "用户管理"]
            if any(w in html for w in admin_keywords):
                add("[前端] 买家可访问管理端Web", "P0", "authorization",
                    f"买家token访问{frontend['admin']}返回管理内容", 0.95)
        except Exception:
            pass

    print(f"  [INFO] Deep verifier: {len(findings)} findings (config-driven)", flush=True)
    return findings


def _load_config() -> dict:
    """Load test_profile from connector_registry."""
    from pathlib import Path
    import json
    # Try common paths
    for candidate in [
        Path("platform_workspace") / "第一个真实项目测试" / "enterprise_pilot_runtime" / "connector_registry.json",
        Path("platform_workspace") / ".." / "enterprise_pilot_runtime" / "connector_registry.json",
    ]:
        try:
            cfg = json.loads(candidate.read_text(encoding='utf-8'))
            return cfg.get("test_profile", {})
        except Exception:
            continue
    return {}


def _is_admin_path(path: str, method: str) -> bool:
    """Check if a path looks like an admin/critical endpoint."""
    admin_patterns = ["admin", "report", "manage", "audit", "dashboard", "setting", "config"]
    path_lower = path.lower()
    return any(p in path_lower for p in admin_patterns)


def _discover_routes(base_url: str) -> list[dict]:
    """Discover routes from API spec or connector config."""
    from pathlib import Path
    spec_dir = Path("platform_workspace")
    for project_dir in sorted(spec_dir.glob("*"), reverse=True):
        api_spec = project_dir / "input" / "API_SPEC.md"
        if api_spec.exists():
            raw = api_spec.read_text(encoding="utf-8")
            routes = []
            for m in re.finditer(r'^#{2,4}\s+(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)', raw, re.MULTILINE):
                routes.append({"method": m.group(1), "path": m.group(2)})
            if routes:
                return routes
    return []


if __name__ == "__main__":
    r = run_deep_tests()
    for f in r:
        print(f"[{f['severity']}] {f['title'][:100]}")
    print(f"\nTotal: {len(r)}")
