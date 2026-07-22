"""
QualiBug Frontend UI Tester — Configuration-driven, graceful degradation.
Zero hardcoded values. Reads from connector_registry.test_profile.
"""
from __future__ import annotations
import json, time
from typing import Any
import html


def run_frontend_tests(config: dict | None = None) -> list[dict]:
    """Run frontend tests driven by config. Gracefully degrades if Playwright missing."""
    if config is None:
        config = {}

    frontend = config.get("frontend_urls", {})
    creds = config.get("test_credentials", {})
    base_url = config.get("api_base_url", "")

    customer_url = frontend.get("customer", "")
    admin_url = frontend.get("admin", "")

    if not customer_url and not admin_url:
        return [{"severity": "P3", "title": "[UI] 未配置前端URL",
                 "category": "config", "source": "frontend_ui",
                 "description": "在connector_registry.test_profile.frontend_urls中配置customer/admin地址",
                 "confidence_score": 1.0}]

    findings: list[dict] = []

    def add(title: str, sev: str, cat: str, desc: str, conf: float = 0.90):
        findings.append({"severity": sev, "title": title, "category": cat,
                        "source": "frontend_ui", "description": desc, "confidence_score": conf})

    # ── Try Playwright ──
    use_browser = False
    try:
        from playwright.sync_api import sync_playwright
        sync_playwright().start().stop()
        use_browser = True
    except Exception:
        add("[UI] Playwright未安装，跳过浏览器测试", "P3", "config",
            "安装: pip install playwright && playwright install chromium\n将自动降级为HTTP-only检查", 0.70)

    # ── HTTP-based checks (always work) ──
    import urllib.request

    # Check customer web accessibility
    if customer_url:
        try:
            resp = urllib.request.urlopen(urllib.request.Request(customer_url), timeout=5)
            html = resp.read().decode('utf-8', 'replace').lower()
            # Check for obvious data leaks
            sensitive = []
            if 'password' in html: sensitive.append('password')
            if 'secret' in html: sensitive.append('secret')
            if 'token' in html and 'eyJ' in html: sensitive.append('JWT')
            if sensitive:
                add(f"[UI] 前端暴露敏感字段: {','.join(sensitive)}", "P0", "data_leak",
                    f"{customer_url} HTML包含{len(sensitive)}类敏感信息", 0.90)
            # Check for error indicators
            if '500' in html or 'internal server error' in html.lower():
                add("[UI] 用户端页面返回服务端错误", "P1", "availability",
                    f"{customer_url} 内容含500错误", 0.85)
        except Exception:
            add("[UI] 用户端不可达", "P1", "availability", f"{customer_url} 无法访问", 0.88)

    # Check admin web
    if admin_url:
        try:
            resp = urllib.request.urlopen(urllib.request.Request(admin_url), timeout=5)
            html = resp.read().decode('utf-8', 'replace').lower()
            if 'password' in html or 'secret' in html:
                add("[UI] 管理端泄露敏感信息", "P0", "data_leak",
                    f"{admin_url} HTML含敏感字段", 0.95)
        except Exception:
            pass

    # ── Browser-based checks (if Playwright available) ──
    if use_browser and (customer_url or admin_url):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = None
                for launch_method in [
                    lambda: p.chromium.launch(channel='chrome', headless=True),
                    lambda: p.chromium.launch(headless=True, args=['--headless=new']),
                    lambda: p.chromium.launch(headless=True),
                ]:
                    try:
                        browser = launch_method()
                        break
                    except Exception:
                        continue

                if browser:
                    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
                    
                    # Customer web
                    if customer_url:
                        page = ctx.new_page()
                        try:
                            page.goto(customer_url, timeout=10000, wait_until="networkidle")
                            page.wait_for_timeout(500)
                            html = page.content()
                            # Check for draft/hidden/internal state data leaks
                            if any(w in html.lower() for w in ['draft', '草稿', 'off_sale', 'hidden', 'internal']):
                                add("[UI] 用户端暴露内部状态数据", "P1", "data_leak",
                                    "前端含DRAFT/OFF_SALE/HIDDEN/INTERNAL字样", 0.88)
                            # Check for admin links
                            admin_words = ['admin', 'dashboard', '管理', '后台']
                            found_admin = [w for w in admin_words if w in html.lower()]
                            if found_admin:
                                add(f"[UI] 用户端含管理链接: {','.join(found_admin)}", "P1", "authorization",
                                    "前端暴露管理入口", 0.88)
                            
                            # Try to inject token and check logged-in state
                            _user_creds = creds.get("user") or creds.get("buyer") or {}
                            if _user_creds.get("email") and base_url:
                                try:
                                    # Get token via API
                                    import urllib.request as _ur
                                    ld = json.dumps({"email": _user_creds["email"],
                                        "password": _user_creds.get("password", "")}).encode()
                                    req = _ur.Request(f"{base_url}/api/auth/login", data=ld,
                                        headers={"Content-Type": "application/json"}, method="POST")
                                    resp = _ur.urlopen(req, timeout=5)
                                    token = json.loads(resp.read()).get("token", "")
                                    if token:
                                        page.evaluate(f"localStorage.setItem('token','{token}')")
                                        page.reload()
                                        page.wait_for_timeout(500)
                                        post_login = page.content()
                                        if "logout" in post_login.lower() or "退出" in post_login:
                                            # 通用：登录后检查是否暴露内部状态数据
                                            if any(w in post_login.lower() for w in ['draft', 'hidden', 'internal', 'debug']):
                                                add("[UI] 登录后暴露内部状态数据", "P1", "data_leak",
                                                    "登录后页面含内部/调试状态数据", 0.88)
                                except Exception:
                                    pass
                        finally:
                            page.close()

                    # Admin web
                    if admin_url:
                        admin_page = ctx.new_page()
                        try:
                            admin_page.goto(admin_url, timeout=10000, wait_until="networkidle")
                            admin_page.wait_for_timeout(500)
                            admin_html = admin_page.content()
                            # Check for internal URLs exposed
                            import re
                            urls = re.findall(r'(https?://[^\s"\'<>]+)', admin_html)
                            internal = [u for u in urls if any(p in u for p in
                                ['localhost', '127.0.0.1', '192.168', '10.', 'internal'])]
                            if internal:
                                add(f"[UI] 管理端暴露内部地址: {internal[0][:60]}", "P1", "info_leak",
                                    f"管理端HTML含{len(internal)}个内部URL", 0.85)
                        finally:
                            admin_page.close()

                    browser.close()
        except Exception:
            add("[UI] 浏览器测试执行失败(非致命)", "P3", "config",
                "Playwright可用但测试过程出错，HTTP检查已完成", 0.60)

    print(f"  [INFO] Frontend UI: {len(findings)} findings", flush=True)
    return findings


if __name__ == "__main__":
    r = run_frontend_tests()
    for f in r:
        print(f"[{f['severity']}] {f['title'][:100]}")
    print(f"\nTotal: {len(r)}")
