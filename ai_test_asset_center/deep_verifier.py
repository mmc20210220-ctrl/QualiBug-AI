"""
QualiBug Deep Verifier — Configuration-driven, no hardcoded values.
Reads test_profile from connector_registry, discovers targets from API spec + DB.
"""
from __future__ import annotations
import urllib.request, urllib.error, json, time, re, threading, queue, logging
from typing import Any

logger = logging.getLogger(__name__)

_SAFE_IDENTIFIER_RE = re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]*$')


def _safe_identifier(name: str) -> str:
    """Validate a SQL identifier (table/column name) against a strict
    whitelist to prevent injection.  Raises ValueError if the name
    contains anything other than alphanumerics and underscores."""
    if not _SAFE_IDENTIFIER_RE.match(name):
        raise ValueError(f"unsafe SQL identifier: {name!r}")
    return name


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
    
    # 通用角色命名：支持 "user"(通用) 和 "buyer"(向后兼容) 配置键
    _user_creds = creds.get("user") or creds.get("buyer") or {}
    user_email = _user_creds.get("email", "")
    user_pw = _user_creds.get("password", "")
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
    # 通用路由分类：按 HTTP 方法分组，不硬编码行业路径
    post_paths = [r["path"] for r in routes if r.get("method", "GET").upper() in ("POST", "PUT", "PATCH")]
    list_paths = [r["path"] for r in routes if r.get("method", "GET").upper() == "GET" and not _is_admin_path(r["path"], "GET")]
    state_paths = [r["path"] for r in routes if any(kw in r.get("path", "").lower() for kw in ("status", "state", "cancel", "approve", "reject", "close", "complete", "confirm", "activate", "disable"))]
    
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
    
    user_token = login(user_email, user_pw) if user_email else ""
    admin_token = login(admin_email, admin_pw) if admin_email else ""
    
    # ── Auto-setup: register if login fails ──
    if not user_token and user_email:
        try:
            reg = json.dumps({"email": user_email, "password": user_pw, "name": "AutoTestUser", "phone": "13800000001"}).encode()
            for auth_p in auth_paths:
                if "register" in auth_p.lower() or "signup" in auth_p.lower():
                    urllib.request.urlopen(urllib.request.Request(
                        f"{base_url}{auth_p}", data=reg, headers={"Content-Type": "application/json"}, method="POST"), timeout=5)
                    break
            user_token = login(user_email, user_pw)
        except Exception as exc:
            logger.warning(
                f"deep_verifier: 测试账号自动注册/登录失败，用户流程验证将跳过",
                extra={"error_code": "QB-X001", "context": {"email": user_email, "base_url": base_url, "error": str(exc)[:200]}},
            )
    if not admin_token and admin_email:
        try:
            reg = json.dumps({"email": admin_email, "password": admin_pw, "name": "TestAdmin", "phone": "13800000002"}).encode()
            for auth_p in auth_paths:
                if "register" in auth_p.lower() or "signup" in auth_p.lower():
                    urllib.request.urlopen(urllib.request.Request(
                        f"{base_url}{auth_p}", data=reg, headers={"Content-Type": "application/json"}, method="POST"), timeout=5)
                    break
            admin_token = login(admin_email, admin_pw)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            logger.debug("Admin auto-setup login failed: %s", exc)
    
    H_user = {"Content-Type": "application/json", "Authorization": f"Bearer {user_token}"}
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
            req = urllib.request.Request(f"{base_url}{path}", headers=headers or H_user)
            return json.loads(urllib.request.urlopen(req, timeout=5).read())
        except Exception:
            return None
    
    def _api_post(path: str, body: dict, headers: dict | None = None) -> tuple[int, Any]:
        try:
            data = json.dumps(body).encode()
            req = urllib.request.Request(f"{base_url}{path}", data=data,
                headers=headers or H_user, method="POST")
            resp = urllib.request.urlopen(req, timeout=5)
            return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            try: return e.code, json.loads(e.read())
            except Exception: return e.code, {}
        except Exception:
            return 0, {}

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
    # 1. Permission: low-privilege token vs admin endpoints
    # ═══════════════════════════════════════
    if user_token and routes:
        for route in routes:
            path = route["path"]
            if not _is_admin_path(path, route.get("method", "")):
                continue
            method = route.get("method", "GET")
            test_path = re.sub(r'[:{]\w+[}]?', 'test-id', path)
            code = _http_error_code(test_path, method, {}, H_user)
            if code == 200:
                add(f"[权限] 低权限用户可访问管理端点: {method} {path}", "P0", "authorization",
                    f"低权限token访问{path}返回200", 0.95)
            elif code not in (0, 401, 403, 404):
                add(f"[权限] {method} {path} 返回{code}(非403)", "P1", "authorization",
                    f"低权限token访问管理端点返回{code}", 0.85)
    
    # ═══════════════════════════════════════
    # 2. Numeric boundary: POST endpoints with invalid numeric values
    # ═══════════════════════════════════════
    if user_token and post_paths:
        boundary_values = [("零值", 0), ("负数", -1), ("超大值", 999999999)]
        tested = 0
        for pp in post_paths[:15]:  # 限制测试端点数量
            if tested >= 20:
                break
            resolved = re.sub(r'[:{]\w+[}]?', 'test-id', pp)
            for label, val in boundary_values:
                tested += 1
                sc, _ = _api_post(resolved, {"quantity": val, "amount": val, "count": val}, H_user)
                if sc in (200, 201):
                    add(f"[边界] {label}提交被接受: POST {pp}", "P1", "input_validation",
                        f"POST {pp} 数值={val} 返回{sc}，应拒绝非法数值", 0.85)
                    break  # 每个端点只报一次
        
    # ═══════════════════════════════════════
    # 3. State machine: invalid state transitions
    # ═══════════════════════════════════════
    if user_token and state_paths:
        for sp in state_paths[:10]:
            resolved = re.sub(r'[:{]\w+[}]?', 'test-id', sp)
            # 尝试对不存在的实体执行状态转换
            code = _http_error_code(resolved, "POST", {}, H_user)
            if code == 200:
                add(f"[状态机] 无效实体可执行状态转换: POST {sp}", "P1", "state_machine",
                    f"对不存在的实体执行状态操作返回200，应返回404或400", 0.85)
        
    # ═══════════════════════════════════════
    # 4. Idempotency: duplicate POST should not create duplicates
    # ═══════════════════════════════════════
    if user_token and post_paths:
        idem_tested = 0
        for pp in post_paths[:8]:
            if idem_tested >= 5:
                break
            resolved = re.sub(r'[:{]\w+[}]?', 'test-id', pp)
            idem_key = f"qb-idem-{int(time.time()*1000)}"
            body = {"idempotencyKey": idem_key, "idempotency_key": idem_key, "name": "QualiBug Idem Test"}
            sc1, r1 = _api_post(resolved, body, H_user)
            sc2, r2 = _api_post(resolved, body, H_user)
            idem_tested += 1
            if sc1 in (200, 201) and sc2 in (200, 201):
                id1 = (r1 or {}).get("id", "") if isinstance(r1, dict) else ""
                id2 = (r2 or {}).get("id", "") if isinstance(r2, dict) else ""
                if id1 and id2 and id1 != id2:
                    add(f"[幂等性] 重复提交产生不同资源: POST {pp}", "P0", "idempotency",
                        f"相同请求两次提交产生不同 ID ({id1} vs {id2})", 0.90)
    
    # ═══════════════════════════════════════
    # 5. Multi-user data isolation
    # ═══════════════════════════════════════
    if user_token:
        try:
            user2_email = f"qb_auto_{int(time.time())}@test.com"
            sc_r, _ = _api_post("/api/auth/register" if auth_paths else "/api/auth/register",
                {"email": user2_email, "password": user_pw or "Test@123456", "name": "AutoUser", "phone": "13900000001"}, H_user)
            user2_token = login(user2_email, user_pw or "Test@123456")
            if user2_token and user2_token != user_token:
                H2 = {"Content-Type": "application/json", "Authorization": f"Bearer {user2_token}"}
                # 通用隔离检查：新用户访问列表端点应返回空
                if list_paths:
                    for lp in list_paths[:5]:
                        resolved = re.sub(r'[:{]\w+[}]?', '', lp).rstrip('/')
                        data = _api_get(resolved, H2)
                        if isinstance(data, list) and len(data) > 0:
                            add(f"[数据隔离] 新用户可查看他人数据: GET {resolved}", "P0", "data_isolation",
                                f"新用户{user2_email}访问{resolved}看到{len(data)}条记录", 0.95)
                            break
        except Exception as exc:
            logger.warning(
                f"deep_verifier: 数据隔离检查异常",
                extra={"error_code": "QB-O003", "context": {"error": str(exc)[:200]}},
            )
    
    # ═══════════════════════════════════════
    # 6. Concurrent write race condition
    # ═══════════════════════════════════════
    if user_token and post_paths:
        # 通用并发测试：对同一 POST 端点发起并发请求
        conc_target = post_paths[0] if post_paths else None
        if conc_target:
            resolved = re.sub(r'[:{]\w+[}]?', 'test-id', conc_target)
            def _conc_post():
                try:
                    cd = json.dumps({"name": f"conc-{time.time()}", "value": 1}).encode()
                    r = urllib.request.urlopen(urllib.request.Request(f"{base_url}{resolved}",
                        data=cd, headers=H_user, method="POST"), timeout=5)
                    return r.status
                except Exception:
                    return 0
            q = queue.Queue()
            t1 = threading.Thread(target=lambda: q.put(_conc_post()))
            t2 = threading.Thread(target=lambda: q.put(_conc_post()))
            t1.start(); t2.start(); t1.join(3); t2.join(3)
            statuses = []
            while not q.empty(): statuses.append(q.get_nowait())
            if len(statuses) == 2 and all(s in (200, 201) for s in statuses):
                add(f"[并发] 并发写入未加锁: POST {conc_target}", "P1", "concurrency",
                    f"两个并发POST {conc_target}均成功，可能存在竞态条件", 0.80)
    
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
                
                # 通用完整性检查：数值列不应为负
                for table in tables:
                    table = _safe_identifier(table)
                    cur.execute(
                        "SELECT column_name, data_type FROM information_schema.columns "
                        "WHERE table_name=%s AND data_type IN "
                        "('integer','numeric','real','double precision') LIMIT 5",
                        (table,),
                    )
                    for col_name, _ in cur.fetchall():
                        try:
                            col_name = _safe_identifier(col_name)
                            cur.execute(f"SELECT COUNT(*) FROM \"{table}\" WHERE \"{col_name}\" < 0")
                            neg = cur.fetchone()[0]
                            if neg > 0:
                                cur.execute(
                                    "SELECT column_name FROM information_schema.columns "
                                    "WHERE table_name=%s AND column_name LIKE '%%id' LIMIT 1",
                                    (table,),
                                )
                                pk_rows = cur.fetchall()
                                pk = pk_rows[0][0] if pk_rows else col_name
                                cur.execute(
                                    "SELECT column_name FROM information_schema.columns "
                                    "WHERE table_name=%s AND column_name IN "
                                    "('user_id','email','name','code','phone','amount','status')",
                                    (table,),
                                )
                                biz_cols = [r[0] for r in cur.fetchall()]
                                select_cols = [pk, col_name] + [c for c in biz_cols if c != pk and c != col_name]
                                select_col_names = ", ".join(f'"{_safe_identifier(c)}"' for c in select_cols)
                                cur.execute(
                                    f"SELECT {select_col_names} FROM \"{table}\" WHERE \"{col_name}\" < 0 LIMIT 3"
                                )
                                for row in cur.fetchall():
                                    pk_val, neg_val = row[0], row[1]
                                    # 构建业务可读的区分信息（通用：跳过UUID格式值，用户看不懂）
                                    biz_parts = []
                                    business_key_val = ""  # 用于 source_value
                                    for i, col in enumerate(biz_cols, start=2):
                                        val = row[i] if i < len(row) else None
                                        if val is not None and str(val).strip():
                                            val_str = str(val).strip()
                                            # 跳过标准UUID格式（用户看不懂，只用在detail）
                                            if re.match(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$', val_str):
                                                continue
                                            biz_parts.append(f"{col}={val_str}")
                                            if not business_key_val:
                                                business_key_val = val_str
                                    # 如果没有业务可读字段，用主键值（但也跳过UUID）
                                    if not biz_parts:
                                        pk_str = str(pk_val).strip() if pk_val is not None else ""
                                        if not re.match(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$', pk_str):
                                            biz_parts.append(f"{pk}={pk_str}")
                                            business_key_val = pk_str
                                        else:
                                            biz_parts.append(f"id={pk_str[:8]}")
                                    biz_info = "，".join(biz_parts[:3])
                                    # 标题只用业务可读信息，UUID放detail
                                    add(f"[DB] {table}.{col_name}为负: {biz_info}（值={neg_val}）", "P0", "data_integrity",
                                        f"表{table}列{col_name}含负数值，主键={pk_val}", 0.95)
                        except Exception:
                            pass
                
                conn.close()
        except Exception as exc:
            logger.warning(
                f"deep_verifier: DB验证整体失败，数据完整性检查将跳过",
                extra={"error_code": "QB-X004", "context": {"error": str(exc)[:200]}},
            )
    
    # ═══════════════════════════════════════
    # 10. Input boundary: dynamic tests for ALL POST/PUT endpoints
    # ═══════════════════════════════════════
    # 通用边界测试：对所有 POST/PUT 路由动态生成边界测试用例，
    # 不硬编码特定端点（之前只测 /api/auth/login 和 /api/auth/register）
    if user_token and routes:
        # 通用边界测试数据（非业务概念）
        boundary_payloads = [
            ("超长字符串", {"_boundary_field_": "a" * 5000}),
            ("SQL注入", {"_boundary_field_": "'; DROP TABLE--"}),
            ("XSS", {"_boundary_field_": "<script>alert(1)</script>"}),
            ("Null字节", {"_boundary_field_": "\x00\x01\x02"}),
            ("空对象", {}),
        ]
        tested_endpoints: set[str] = set()
        for route in routes:
            method = route.get("method", "GET").upper()
            if method not in ("POST", "PUT", "PATCH"):
                continue
            path = route.get("path", "")
            if not path or path in tested_endpoints:
                continue
            tested_endpoints.add(path)
            # 从路由声明中提取 body 参数名（通用）
            body_props = route.get("body_properties") or {}
            if not isinstance(body_props, dict) or not body_props:
                # 没有声明参数的端点，用一个通用字段名测试
                body_props = {"input": ""}
            # 取第一个参数名作为边界测试字段
            test_field = next(iter(body_props.keys()), "input")
            for label, template in boundary_payloads:
                # 用实际参数名替换通用字段
                payload = {test_field: v} if isinstance(template, dict) and "_boundary_field_" in template else template
                code = _http_error_code(path, method, payload, H_user)
                if code >= 500:
                    add(f"[边界] {label}导致服务端{code}: {method} {path}", "P1", "input_validation",
                        f"{method} {path} 返回{code}", 0.88)
    
    # ═══════════════════════════════════════
    # 9. Report accuracy (API vs DB count)
    # ═══════════════════════════════════════
    if admin_token and list_paths and db_cfg:
        try:
            # 通用报表一致性：对比 API 返回的 total/count 与 DB 实际行数
            for lp in list_paths[:3]:
                resolved = re.sub(r'[:{]\w+[}]?', '', lp).rstrip('/')
                result = _api_get(resolved, H_admin)
                if isinstance(result, dict):
                    api_count = result.get("total") or result.get("count") or result.get("total_count") or 0
                    if api_count and isinstance(api_count, int) and api_count > 0:
                        # 尝试从路径推断表名（通用：取路径最后一段）
                        table_guess = resolved.rstrip('/').split('/')[-1]
                        if table_guess and _SAFE_IDENTIFIER_RE.match(table_guess):
                            db_count = _db_query(f'SELECT COUNT(*) FROM "{_safe_identifier(table_guess)}"')
                            db_total = db_count[0][0] if db_count else 0
                            if db_total and api_count != db_total:
                                add(f"[报表] 数据不一致: API={api_count} DB={db_total} ({resolved})", "P1", "data_integrity",
                                    f"端点{resolved}返回total={api_count}但DB实际{db_total}条", 0.88)
        except Exception:
            pass
    
    # ═══════════════════════════════════════
    # 12. Security: CORS & headers
    # ═══════════════════════════════════════
    try:
        req = urllib.request.Request(base_url,
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
    if frontend.get("admin") and user_token:
        try:
            req = urllib.request.Request(frontend["admin"],
                headers={"Authorization": f"Bearer {user_token}"})
            resp = urllib.request.urlopen(req, timeout=5)
            html = resp.read().decode('utf-8', 'replace').lower()
            admin_keywords = ["dashboard", "admin", "管理", "后台", "用户管理", "系统设置"]
            if any(w in html for w in admin_keywords):
                add("[前端] 低权限用户可访问管理端Web", "P0", "authorization",
                    f"低权限token访问{frontend['admin']}返回管理内容", 0.95)
        except Exception:
            pass

    print(f"  [INFO] Deep verifier: {len(findings)} findings (config-driven)", flush=True)
    return findings


def _load_config() -> dict:
    """Load test_profile from connector_registry (generic, no hardcoded project name)."""
    from pathlib import Path
    import json
    # Scan all project directories — do not hardcode a specific project name
    workspace_root = Path(".")
    for base_dir in ("platform_workspace", "platform_outputs"):
        ws = workspace_root / base_dir
        if not ws.exists():
            continue
        for proj_dir in ws.iterdir():
            if not proj_dir.is_dir():
                continue
            candidate = proj_dir / "enterprise_pilot_runtime" / "connector_registry.json"
            try:
                cfg = json.loads(candidate.read_text(encoding='utf-8'))
                profile = cfg.get("test_profile", {})
                if profile:
                    return profile
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
