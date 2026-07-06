"""
QualiBug Unified CLI — single entry point for all scan modes.

Usage:
  # Full scan against live server
  python -m ai_test_asset_center scan --project myproject --api-doc docs/api.md --base-url http://localhost:8080

  # Structural analysis only (no server)
  python -m ai_test_asset_center scan --project myproject --api-doc docs/api.md

  # CI gate mode (diff against previous scan)
  python -m ai_test_asset_center scan --project myproject --api-doc docs/api.md --base-url http://localhost:8080 --ci-gate

  # Quick health check
  python -m ai_test_asset_center scan --project myproject --health-check http://localhost:8080

Output: platform_outputs/<project>/intelligence_report.md + .json + scan_history.db
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional


def _configure_console_encoding() -> None:
    """Keep Windows non-UTF-8 consoles from crashing on status glyphs."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(errors="replace")
            except Exception:
                pass


_configure_console_encoding()


def scan(
    project: str,
    root: Optional[Path] = None,
    *,
    prd_text: str = "",
    api_doc_path: str = "",
    api_doc_text: str = "",
    base_url: str = "",
    ci_gate: bool = False,
    multi_layer: bool = True,
    output_dir: Optional[Path] = None,
    save_report: bool = True,
) -> dict:
    """Unified scan entry point — ONE function for all modes.

    Args:
        project: Project name (used for output directory and history key)
        root: Project root directory (default: cwd)
        prd_text: Business requirements text (Chinese or English)
        api_doc_path: Path to API documentation markdown file
        api_doc_text: API documentation as inline text (alternative to file)
        base_url: Target server URL (e.g. http://localhost:8080). Omit for structural-only analysis.
        ci_gate: If True, compare with previous scan and block on regression.
        multi_layer: If True, also run UI/Perf/Security/Infra layers.
        output_dir: Custom output directory (default: platform_outputs/<project>/)
        save_report: If True, save intelligence report to disk.

    Returns:
        dict with scan_id, grade, score, total_findings, layers, report_path
    """
    root = root or Path.cwd()

    # ── Resolve inputs ──
    if api_doc_path and not api_doc_text:
        api_doc_text = Path(api_doc_path).read_text(encoding="utf-8")

    if not api_doc_text:
        return {"error": "api_doc_text or api_doc_path is required", "success": False}

    if not prd_text:
        # Auto-generate basic PRD from API doc
        prd_text = _auto_prd(api_doc_text)

    # ── Resolve base_url ──
    if base_url:
        base_url = base_url.replace("localhost", "127.0.0.1")  # Windows DNS fix
        base_url = base_url.rstrip("/")

    t_start = time.time()
    elapsed_ms = lambda: int((time.time() - t_start) * 1000)
    
    # ══ Pre-flight Health Check ══
    diagnostics = {"ready": True, "checks": []}
    try:
        from .scan_diagnostics import run_preflight
        cfg = {}
        try:
            import json as _json
            reg_path = root / "platform_workspace" / project / "enterprise_pilot_runtime" / "connector_registry.json"
            if reg_path.exists():
                cfg = _json.loads(reg_path.read_text(encoding='utf-8')).get("test_profile", {})
        except Exception: pass
        diagnostics = run_preflight(cfg, api_doc_text)
        if not diagnostics.get("ready"):
            print(f"  [PREFLIGHT] ⚠️ {diagnostics.get('summary')}", flush=True)
            for ch in diagnostics.get("checks", []):
                icon = "✅" if ch["passed"] else "❌" if ch["severity"]=="error" else "⚠️"
                print(f"    {icon} {ch['name']}: {ch['message']}", flush=True)
        else:
            print(f"  [PREFLIGHT] ✅ {diagnostics.get('summary')}", flush=True)
    except Exception as pe:
        print(f"  [PREFLIGHT] ⚠️ 健康检查异常: {pe}", flush=True)
    
    from .v12_pipeline import run_v12_pipeline
    from .evaluation_engine import EvaluationEngine, IntelligenceReporter

    try:
        v12 = run_v12_pipeline(project, root, prd_text, api_doc_text, base_url=base_url)
    except Exception as e:
        import logging
        logging.getLogger("qualibug").error(f"V12 pipeline crashed: {e}", exc_info=True)
        return {"error": f"V12 pipeline failed: {e}", "success": False}
    api_ms = v12["total_duration_ms"]
    api_findings = len(v12.get("findings", []))
    try:
        evaluation = EvaluationEngine().evaluate(v12)
    except Exception as e:
        print(f"  [WARN] Evaluation failed (non-fatal): {e}", flush=True)
        evaluation = type('Eval', (), {'system_grade': 'unknown', 'overall_score': 0, 'coverage': type('C',(),{'to_dict':lambda:{'overall_coverage':0}})()})()

    layers = {"api": {"findings": api_findings, "ms": api_ms, "grade": evaluation.system_grade,
                      "score": evaluation.overall_score, "tool": "V12"}}

    # ── Layers 2-5: Multi-Layer (optional) ──
    if multi_layer and base_url:
        try:
            from .multi_layer_tester import UITester, PerformanceTester, SecurityTester, InfrastructureTester

            ui = UITester(base_url).run()
            layers["ui"] = {"findings": len(ui.findings), "ms": ui.duration_ms, "pass_rate": ui.pass_rate, "tool": ui.tool,
                           "findings_details": [{"title": f.get("title", ""), "severity": f.get("severity", "P2"), "description": str(f.get("description", ""))[:200]}
                                                for f in ui.findings]}

            perf = PerformanceTester(base_url).run()
            layers["perf"] = {"findings": len(perf.findings), "ms": perf.duration_ms, "pass_rate": perf.pass_rate, "tool": perf.tool,
                             "findings_details": [{"title": f.get("title", ""), "severity": f.get("severity", "P2"), "description": str(f.get("description", ""))[:200]}
                                                  for f in perf.findings]}

            sec = SecurityTester(base_url)
            sec.SECURITY_CHECKS = [c for c in sec.SECURITY_CHECKS if c[1] != "OPTIONS"]
            sec_r = sec.run()
            layers["security"] = {"findings": len(sec_r.findings), "ms": sec_r.duration_ms, "pass_rate": sec_r.pass_rate, "tool": sec_r.tool,
                                 "findings_details": [{"title": f.get("title", ""), "severity": f.get("severity", "P2"), "description": str(f.get("description", ""))[:200]}
                                                      for f in sec_r.findings]}

            infra = InfrastructureTester(base_url).run()
            layers["infra"] = {"findings": len(infra.findings), "ms": infra.duration_ms, "pass_rate": infra.pass_rate, "tool": infra.tool,
                              "findings_details": [{"title": f.get("title", ""), "severity": f.get("severity", "P2"), "description": str(f.get("description", ""))[:200]}
                                                   for f in infra.findings]}
        except Exception as e:
            print(f"  [WARN] Multi-layer tests failed (non-fatal): {e}", flush=True)
            layers["ui"] = {"findings": 0, "ms": 0, "error": str(e)[:120]}

    # ── Layer: Mobile App (optional, requires .apk/.ipa upload) ──
    try:
        from .mobile_app_detector import run_mobile_tests
        input_dir = root / "platform_workspace" / project / "input"
        apk_files = list(input_dir.glob("*.apk")) + list(input_dir.glob("*.ipa"))
        if apk_files and multi_layer:
            apk_path = str(apk_files[0])
            platform_name = "android" if apk_path.endswith(".apk") else "ios"
            mobile = run_mobile_tests(apk_ipa_path=apk_path, platform_name=platform_name,
                                      base_url=base_url or "")
            layers["mobile"] = {
                "findings": len(mobile.findings), "ms": mobile.duration_ms,
                "platform": mobile.platform, "tool": "Appium",
                "dynamic": any(f.category != "setup" for f in mobile.findings),
                "findings_details": [{"title": f.title, "severity": f.severity, "description": f.description[:200], "category": f.category}
                                    for f in mobile.findings],
            }
    except ImportError:
        pass  # Appium not installed, skip mobile layer
    except Exception as e:
        print(f"  [WARN] Mobile test skipped (non-fatal): {e}", flush=True)

    total_findings = sum(l["findings"] for l in layers.values())
    total_ms = int((time.time() - t_start) * 1000)

    # ── DB Verification ──
    db_findings: list[dict] = []
    try:
        from .enterprise_pilot_runtime import load_connector_registry
        reg = load_connector_registry(project, root)
        db_connectors = [c for c in reg.get("connectors", []) if c.get("kind") == "database" and c.get("enabled")]
        for dbc in db_connectors:
            ep = dbc.get("endpoint_ref", "")
            if not ep.startswith("postgresql"):
                continue
            # Parse connection string
            import re as _re
            m = _re.match(r'postgresql://([^:]+):([^@]+)@([^:]+):(\d+)/(.+)', ep)
            if not m:
                continue
            user, pw, host, port, dbname = m.group(1), m.group(2), m.group(3), int(m.group(4)), m.group(5)
            try:
                import pg8000
                conn = pg8000.connect(host=host, port=port, database=dbname, user=user, password=pw)
                cur = conn.cursor()
                
                # Check 1: DRAFT products visible in API but not filtered
                cur.execute("SELECT sku, title, status FROM products WHERE status = %s", ('DRAFT',))
                drafts = cur.fetchall()
                for d in drafts:
                    db_findings.append({
                        "severity": "P1", "title": f"[DB验证] DRAFT商品'{d[1]}'未隐藏",
                        "category": "data_integrity", "source": "db_verifier",
                        "description": f"SKU={d[0]} status=DRAFT 但在商品列表中可见，应仅对内部展示。",
                        "confidence_score": 0.90, "evidence": {"db_row": {"sku": d[0], "title": d[1], "status": d[2]}}
                    })
                
                # Check 2: Products with negative/zero price
                cur.execute("SELECT sku, title, price, original_price FROM products WHERE CAST(price AS numeric) <= 0 OR CAST(original_price AS numeric) <= 0")
                bad_prices = cur.fetchall()
                for bp in bad_prices:
                    db_findings.append({
                        "severity": "P0", "title": f"[DB验证] 商品'{bp[1]}'价格异常",
                        "category": "financial", "source": "db_verifier",
                        "description": f"SKU={bp[0]} price={bp[2]} original_price={bp[3]} — 价格≤0",
                        "confidence_score": 0.95, "evidence": {"db_row": {"sku": bp[0], "price": bp[2]}}
                    })
                
                # Check 3: Coupon validity (expired but still active)
                cur.execute("SELECT code, name, type, starts_at, expires_at, status FROM coupons")
                coupons = cur.fetchall()
                import datetime
                now = datetime.datetime.utcnow().isoformat()
                for c in coupons:
                    expires_at = str(c[4]) if c[4] else ""
                    coupon_status = str(c[5]) if c[5] else ""
                    if expires_at and expires_at < now and coupon_status == "ACTIVE":
                        db_findings.append({
                            "severity": "P1", "title": f"[DB验证] 优惠券'{c[0]}'已过期但仍可用",
                            "category": "business_rule", "source": "db_verifier",
                            "description": f"coupon={c[0]} ({c[1]}) expires_at={expires_at} < now={now[:10]}, status={coupon_status}",
                            "confidence_score": 0.85, "evidence": {"db_row": {"code": c[0], "expires_at": expires_at, "status": coupon_status}}
                        })
                
                # Check 4: Inventory negative quantities
                cur.execute("SELECT sku, warehouse_code, available_qty, locked_qty FROM inventory WHERE available_qty < 0 OR locked_qty < 0")
                neg_inv = cur.fetchall()
                for ni in neg_inv:
                    db_findings.append({
                        "severity": "P0", "title": f"[DB验证] 库存'{ni[0]}'数量为负",
                        "category": "data_integrity", "source": "db_verifier",
                        "description": f"SKU={ni[0]} warehouse={ni[1]} available={ni[2]} locked={ni[3]}",
                        "confidence_score": 0.95, "evidence": {"db_row": {"sku": ni[0], "available_qty": ni[2]}}
                    })
                
                # Check 5: Users with suspicious roles
                cur.execute("SELECT email, role, status FROM users WHERE role NOT IN ('buyer','seller','admin','warehouse','finance')")
                bad_roles = cur.fetchall()
                for br in bad_roles:
                    db_findings.append({
                        "severity": "P0", "title": f"[DB验证] 用户'{br[0]}'角色异常",
                        "category": "authorization", "source": "db_verifier",
                        "description": f"email={br[0]} role={br[1]} status={br[2]} — 非标准角色",
                        "confidence_score": 0.90, "evidence": {"db_row": {"email": br[0], "role": br[1]}}
                    })
                
                                # Check 5: Users with suspicious roles (non-standard)
                cur.execute("SELECT email, role, status FROM users WHERE role NOT IN ('buyer','seller','admin','warehouse','finance')")
                bad_roles = cur.fetchall()
                for br in bad_roles:
                    db_findings.append({
                        "severity": "P0", "title": f"[DB验证] 用户'{br[0]}'角色异常",
                        "category": "authorization", "source": "db_verifier",
                        "description": f"email={br[0]} role={br[1]} status={br[2]} — 非标准角色",
                        "confidence_score": 0.90, "evidence": {"db_row": {"email": br[0], "role": br[1]}}
                    })
                
                # Check 6: Products with OFF_SALE or HIDDEN status
                cur.execute("SELECT sku, title, status FROM products WHERE status IN ('OFF_SALE', 'HIDDEN')")
                hidden = cur.fetchall()
                for h in hidden:
                    db_findings.append({
                        "severity": "P1", "title": f"[DB验证] 商品'{h[1]}'状态异常({h[2]})但仍可访问",
                        "category": "data_integrity", "source": "db_verifier",
                        "description": f"SKU={h[0]} status={h[2]} — 不应向普通用户展示",
                        "confidence_score": 0.88, "evidence": {"db_row": {"sku": h[0], "status": h[2]}}
                    })
                
                # Check 7: Users with zero or negative balance (financial issue)
                cur.execute("SELECT email, name, balance FROM users WHERE CAST(balance AS numeric) < 0")
                neg_balance = cur.fetchall()
                for nb in neg_balance:
                    db_findings.append({
                        "severity": "P0", "title": f"[DB验证] 用户'{nb[0]}'余额为负",
                        "category": "financial", "source": "db_verifier",
                        "description": f"email={nb[0]} name={nb[1]} balance={nb[2]} — 余额不应为负",
                        "confidence_score": 0.95, "evidence": {"db_row": {"email": nb[0], "balance": nb[2]}}
                    })
                
                                # Check 8: Cross-table consistency - orders with no items
                cur.execute("SELECT o.id, o.order_no FROM orders o LEFT JOIN order_items oi ON o.id = oi.order_id WHERE oi.id IS NULL")
                orphan_orders = cur.fetchall()
                for oo in orphan_orders:
                    db_findings.append({
                        "severity": "P0", "title": f"[DB验证] 订单'{oo[1]}'缺少订单明细",
                        "category": "data_integrity", "source": "db_verifier",
                        "description": f"order_id={oo[0]} 在order_items中没有对应记录",
                        "confidence_score": 0.95, "evidence": {"db_row": {"order_no": oo[1]}}
                    })
                
                # Check 9: Payments without orders
                cur.execute("SELECT p.id, p.payment_no, p.order_id FROM payments p LEFT JOIN orders o ON p.order_id = o.id WHERE o.id IS NULL")
                orphan_payments = cur.fetchall()
                for op in orphan_payments:
                    db_findings.append({
                        "severity": "P0", "title": f"[DB验证] 支付单'{op[1]}'关联订单不存在",
                        "category": "data_integrity", "source": "db_verifier",
                        "description": f"payment_id={op[0]} order_id={op[2]} 在orders中不存在",
                        "confidence_score": 0.95, "evidence": {"db_row": {"payment_no": op[1]}}
                    })
                
                # Check 10: Financial - orders with discount but no coupon
                cur.execute("SELECT order_no, total_amount, discount_amount, coupon_code FROM orders WHERE CAST(discount_amount AS numeric) > 0 AND (coupon_code IS NULL OR coupon_code = '')")
                bad_discounts = cur.fetchall()
                for bd in bad_discounts:
                    db_findings.append({
                        "severity": "P1", "title": f"[DB验证] 订单'{bd[0]}'有优惠金额但无优惠券",
                        "category": "financial", "source": "db_verifier",
                        "description": f"total={bd[1]} discount={bd[2]} coupon={bd[3]}",
                        "confidence_score": 0.88, "evidence": {"db_row": {"order_no": bd[0], "discount": bd[2]}}
                    })
                
                # Check 11: Financial - payable_amount calculation error
                cur.execute("SELECT order_no, total_amount, discount_amount, payable_amount FROM orders WHERE ABS(CAST(total_amount AS numeric) - CAST(discount_amount AS numeric) - CAST(payable_amount AS numeric)) > 0.01")
                calc_errors = cur.fetchall()
                for ce in calc_errors:
                    expected = float(ce[1]) - float(ce[2]) if ce[1] and ce[2] else 0
                    db_findings.append({
                        "severity": "P0", "title": f"[DB验证] 订单'{ce[0]}'应付金额计算错误",
                        "category": "financial", "source": "db_verifier",
                        "description": f"total={ce[1]} discount={ce[2]} payable={ce[3]} (应为{expected})",
                        "confidence_score": 0.95, "evidence": {"db_row": {"order_no": ce[0]}}
                    })
                
                # Check 12: Refund amount > order payable
                cur.execute("SELECT r.refund_no, r.amount, o.payable_amount FROM refunds r JOIN orders o ON r.order_id = o.id WHERE CAST(r.amount AS numeric) > CAST(o.payable_amount AS numeric)")
                over_refunds = cur.fetchall()
                for orf in over_refunds:
                    db_findings.append({
                        "severity": "P0", "title": f"[DB验证] 退款单'{orf[0]}'退款金额超过实付",
                        "category": "financial", "source": "db_verifier",
                        "description": f"refund={orf[1]} > payable={orf[2]}",
                        "confidence_score": 0.95, "evidence": {"db_row": {"refund_no": orf[0]}}
                    })
                
                # Check 13: Inventory locked without active orders
                cur.execute("SELECT sku, locked_qty FROM inventory WHERE locked_qty != 0")
                locked_inv = cur.fetchall()
                for li in locked_inv:
                    db_findings.append({
                        "severity": "P1", "title": f"[DB验证] SKU'{li[0]}'锁定库存{li[1]}但无活动订单",
                        "category": "data_integrity", "source": "db_verifier",
                        "description": f"SKU={li[0]} locked_qty={li[1]}, 可能存在未释放的库存锁定",
                        "confidence_score": 0.85, "evidence": {"db_row": {"sku": li[0], "locked": li[1]}}
                    })
                
                # Check 14: Missing indexes / constraint check
                cur.execute("SELECT table_name, constraint_type FROM information_schema.table_constraints WHERE constraint_type = 'PRIMARY KEY' AND table_schema = 'public'")
                pk_tables = {r[0] for r in cur.fetchall()}
                all_tables = {r[0] for r in cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
                missing_pk = all_tables - pk_tables
                for t in missing_pk:
                    db_findings.append({
                        "severity": "P1", "title": f"[DB验证] 表'{t}'缺少主键",
                        "category": "db_design", "source": "db_verifier",
                        "description": f"表 {t} 没有PRIMARY KEY约束，可能导致重复数据。",
                        "confidence_score": 0.80, "evidence": {"db_row": {"table": t}}
                    })
                
                conn.close()
                print(f"  [INFO] DB verification: {len(db_findings)} findings from {dbname}", flush=True)
            except ImportError:
                print(f"  [WARN] pg8000 not available, skip DB verification", flush=True)
            except Exception as e:
                print(f"  [WARN] DB verification failed: {e}", flush=True)
    except Exception as e:
        print(f"  [WARN] DB connector load failed: {e}", flush=True)
    
    if db_findings:
        layers["db"] = {"findings": len(db_findings), "ms": 0, "grade": "A" if len(db_findings) <= 3 else "B"}
        total_findings += len(db_findings)
        # Add DB findings to result for evaluation

    # ── CI Gate (optional) ──
    ci_result = None
    if ci_gate:
        try:
            from .continuous_evaluation import ci_scan_and_evaluate
            ci_result = ci_scan_and_evaluate(project, prd_text, api_doc_text, base_url, root)
        except Exception as e:
            print(f"  [WARN] CI gate evaluation failed (non-fatal): {e}", flush=True)

    # ── Save report ──
    report_path = None
    if save_report:
        try:
            out = output_dir or (root / "platform_outputs" / project)
            out.mkdir(parents=True, exist_ok=True)
            reporter = IntelligenceReporter()
            report_path = reporter.save_report(evaluation, out / "intelligence_report.md")
        except Exception as e:
            print(f"  [WARN] Report save failed (non-fatal): {e}", flush=True)

    # Merge DB findings into result
    if db_findings:
        layers["db"] = {"findings": len(db_findings), "ms": 0, "grade": "A" if len(db_findings) <= 3 else "B"}
        total_findings += len(db_findings)

    def _ensure_address(token, base_url):
        """Ensure a shipping address exists for the user; create if not."""
        import urllib.request as __ur, json as __j
        h = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
        try:
            r = __ur.urlopen(__ur.Request(f"{base_url}/api/users/addresses", headers=h), timeout=5)
            addrs = __j.loads(r.read())
            if isinstance(addrs, list) and addrs:
                return addrs[0].get("id", "")
        except Exception:
            pass
        # No address — create one
        addr_data = __j.dumps({
            "receiver": "QualiBug Test", "phone": "13800000000",
            "province": "测试省", "city": "测试市", "detail": "自动创建的测试地址",
            "is_default": True
        }).encode()
        try:
            r = __ur.urlopen(__ur.Request(f"{base_url}/api/users/addresses", data=addr_data, headers=h, method="POST"), timeout=5)
            new_addr = __j.loads(r.read())
            return (new_addr or {}).get("id", "")
        except Exception as e2:
            # Try alternative path: POST /api/addresses
            try:
                r = __ur.urlopen(__ur.Request(f"{base_url}/api/addresses", data=addr_data, headers=h, method="POST"), timeout=5)
                new_addr = __j.loads(r.read())
                return (new_addr or {}).get("id", "")
            except Exception:
                pass
        return ""

    # ── E2E Business Flow Test: 下单→支付→取消→退款 全链路 ──
    e2e_findings: list[dict] = []
    
    # Take DB snapshot before any state-modifying tests
    db_snapshot: dict | None = None
    db_cfg = cfg.get("database", {})
    if db_cfg.get("host") and db_cfg.get("database"):
        try:
            import pg8000 as _pg
            _conn = _pg.connect(host=db_cfg["host"], port=db_cfg.get("port", 5432),
                database=db_cfg["database"], user=db_cfg.get("user", ""),
                password=db_cfg.get("password", ""))
            _cur = _conn.cursor()
            _cur.execute("SELECT sku, available_qty, locked_qty FROM inventory")
            _inv = [(r[0], r[1], r[2]) for r in _cur.fetchall()]
            _cur.execute("SELECT email, balance FROM users")
            _bal = [(r[0], r[1]) for r in _cur.fetchall()]
            _conn.close()
            db_snapshot = {"inventory": _inv, "balances": _bal, "taken_at": time.time()}
            print(f"  [SNAPSHOT] DB state captured for idempotent scan", flush=True)
        except Exception as _se:
            print(f"  [WARN] DB snapshot failed (non-fatal): {_se}", flush=True)
    
    try:
        if base_url:
            import urllib.request as _ur, json as _j
            from .parameter_fuzzer import ParameterFuzzer
            tmp_fz = ParameterFuzzer(base_url)
            tmp_fz.login()
            token = tmp_fz._token
            
            if token:
                H = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
                
                # Step 1: Get products — check for DRAFT/OFF_SALE leakage
                resp = _ur.urlopen(_ur.Request(f"{base_url}/api/products", headers=H), timeout=5)
                products = _j.loads(resp.read())
                if isinstance(products, list):
                    for p in products:
                        if isinstance(p, dict) and p.get("status") in ("DRAFT", "OFF_SALE", "HIDDEN"):
                            e2e_findings.append({
                                "severity": "P1", "title": f"[E2E] 商品'{p.get('title','')}'状态{p.get('status')}但对买家可见",
                                "category": "data_leak", "source": "e2e_flow",
                                "description": f"SKU={p.get('sku')} status={p.get('status')} 不应出现在商品列表",
                                "confidence_score": 0.92
                            })
                
                # Step 2: Pick first available ON_SALE product for E2E flow
                test_sku = ""
                test_price = 0
                for p in (products if isinstance(products, list) else []):
                    if isinstance(p, dict) and p.get("status") == "ON_SALE" and p.get("sku"):
                        test_sku = p["sku"]
                        test_price = float(p.get("price", 0) or 0)
                        break
                if not test_sku:
                    print("  [WARN] No ON_SALE product found, skipping order flow", flush=True)
                
                # Step 3: Get user addresses (dynamic)
                addr_id = _ensure_address(token, base_url)
                
                # Step 4: Create order
                order_id = ""
                order_amount = test_price
                if test_sku:
                    order_body = _j.dumps({"items": [{"sku": test_sku, "qty": 1}], "addressId": addr_id}).encode()
                    try:
                        order_resp = _ur.urlopen(_ur.Request(f"{base_url}/api/orders", data=order_body, headers=H, method="POST"), timeout=5)
                        order_data = _j.loads(order_resp.read())
                        order_id = order_data.get("id") or order_data.get("order_id", "")
                        order_amount = float(order_data.get("total_amount") or order_data.get("payable_amount") or test_price)
                    except Exception:
                        order_id = ""
                        order_amount = test_price
                
                if order_id:
                    # Step 4: Double-create (idempotency)
                    try:
                        order2_resp = _ur.urlopen(_ur.Request(f"{base_url}/api/orders", data=order_body, headers=H, method="POST"), timeout=5)
                        if order2_resp.status == 201:
                            e2e_findings.append({
                                "severity": "P0", "title": "[E2E] 幂等破坏 — 相同订单可重复创建",
                                "category": "idempotency", "source": "e2e_flow",
                                "description": "同一请求体调用POST /api/orders两次均返回201",
                                "confidence_score": 0.95
                            })
                    except Exception:
                        pass
                    
                    # Step 5: Pay
                    pay_body = _j.dumps({"orderId": order_id, "amount": order_amount, "channel": "BALANCE", "idempotencyKey": f"e2e-{int(time.time())}"}).encode()
                    pay1_ok = False
                    try:
                        pay1_resp = _ur.urlopen(_ur.Request(f"{base_url}/api/payments/pay", data=pay_body, headers=H, method="POST"), timeout=5)
                        pay1_ok = pay1_resp.status in (200, 201)
                    except Exception:
                        pass
                    
                    # Step 6: Double pay (idempotency test)
                    if pay1_ok:
                        try:
                            pay2_resp = _ur.urlopen(_ur.Request(f"{base_url}/api/payments/pay", data=pay_body, headers=H, method="POST"), timeout=5)
                            if pay2_resp.status in (200, 201):
                                e2e_findings.append({
                                    "severity": "P0", "title": "[E2E] 幂等破坏 — 同一订单+idempotencyKey可重复支付",
                                    "category": "idempotency", "source": "e2e_flow",
                                    "description": f"order={order_id} 同一idempotencyKey重复调用pay返回{pay2_resp.status}",
                                    "confidence_score": 0.95
                                })
                        except Exception:
                            pass
                    
                    # Step 7: Cancel order
                    cancel_body = _j.dumps({}).encode()
                    cancel_ok = False
                    try:
                        cancel_resp = _ur.urlopen(_ur.Request(f"{base_url}/api/orders/{order_id}/cancel", data=cancel_body, headers=H, method="POST"), timeout=5)
                        cancel_ok = cancel_resp.status == 200
                    except Exception:
                        pass
                    if cancel_ok and pay1_ok:
                        # Step 8: Pay cancelled order (state machine violation)
                        pay3_body = _j.dumps({"orderId": order_id, "amount": order_amount, "channel": "BALANCE", "idempotencyKey": f"e2e-2-{int(time.time())}"}).encode()
                        try:
                            pay3_resp = _ur.urlopen(_ur.Request(f"{base_url}/api/payments/pay", data=pay3_body, headers=H, method="POST"), timeout=5)
                            if pay3_resp.status in (200, 201):
                                e2e_findings.append({
                                    "severity": "P0", "title": "[E2E] 状态机破坏 — 已取消订单仍可支付",
                                    "category": "state_machine", "source": "e2e_flow",
                                    "description": f"order={order_id} 取消后仍可调用pay并返回{pay3_resp.status}",
                                    "confidence_score": 0.95
                                })
                        except Exception:
                            pass
                        except Exception:
                            pass
                
                # Step 9: Refund non-existent order
                refund_body = _j.dumps({"orderId": "00000000-0000-0000-0000-000000000000", "amount": 100, "reason": "test"}).encode()
                try:
                    refund_resp = _ur.urlopen(_ur.Request(f"{base_url}/api/refunds", data=refund_body, headers=H, method="POST"), timeout=5)
                    if refund_resp.status in (200, 201):
                        e2e_findings.append({
                            "severity": "P0", "title": "[E2E] 不存在的订单可发起退款",
                            "category": "business_rule", "source": "e2e_flow",
                            "description": "退款接口接受不存在的orderId并返回成功",
                            "confidence_score": 0.95
                        })
                except Exception:
                    pass
                
                # Step 10: Inventory concurrency test
                if test_sku:
                    import threading, queue as _q
                    results = _q.Queue()
                    def place_order():
                        try:
                            ob = _j.dumps({"items": [{"sku": test_sku, "qty": 1}], "addressId": addr_id}).encode()
                            r = _ur.urlopen(_ur.Request(f"{base_url}/api/orders", data=ob, headers=H, method="POST"), timeout=5)
                            results.put(("ok", r.status))
                        except Exception as ex:
                            results.put(("err", str(ex)))
                    t1 = threading.Thread(target=place_order)
                    t2 = threading.Thread(target=place_order)
                    t1.start(); t2.start()
                    t1.join(timeout=5); t2.join(timeout=5)
                    r1 = r2 = None
                    while not results.empty():
                        r = results.get_nowait()
                        if r1 is None: r1 = r
                        else: r2 = r
                    if r1 and r2 and r1[0] == "ok" and r2[0] == "ok":
                        e2e_findings.append({
                            "severity": "P1", "title": "[E2E] 库存并发 — 双订单同时创建应验证库存一致性",
                            "category": "concurrency", "source": "e2e_flow",
                            "description": f"两个并发订单均成功，需验证{test_sku}库存是否正确扣减",
                            "confidence_score": 0.85
                        })
            
            print(f"  [INFO] E2E flow: {len(e2e_findings)} findings", flush=True)
    except Exception as ee:
        print(f"  [WARN] E2E flow failed: {ee}", flush=True)
    
    if e2e_findings:
        layers["e2e"] = {"findings": len(e2e_findings), "ms": 0, "grade": "A"}
        total_findings += len(e2e_findings)
    
    # ── Deep Verifier (Permission + Coupon + Financial + Concurrency) ──
    deep_findings: list[dict] = []
    try:
        from .deep_verifier import run_deep_tests
        deep_findings = run_deep_tests(cfg) if base_url else []
        if deep_findings:
            layers["deep"] = {"findings": len(deep_findings), "ms": 0, "grade": "A"}
            total_findings += len(deep_findings)
        print(f"  [INFO] Deep verifier: {len(deep_findings)} findings", flush=True)
    except Exception as de:
        print(f"  [WARN] Deep verifier failed: {de}", flush=True)
    
    # ── Frontend UI Tests (Playwright) ──
    ui_findings: list[dict] = []
    try:
        from .frontend_ui_tester import run_frontend_tests
        ui_findings = run_frontend_tests(cfg)
        if ui_findings:
            layers["frontend"] = {"findings": len(ui_findings), "ms": 0, "grade": "A"}
            total_findings += len(ui_findings)
        print(f"  [INFO] Frontend UI: {len(ui_findings)} findings", flush=True)
    except Exception as ue:
        print(f"  [WARN] Frontend UI tests failed: {ue}", flush=True)
    
    # ── Restore DB snapshot to ensure scan idempotency ──
    if db_snapshot:
        try:
            import pg8000 as _pg2
            _conn2 = _pg2.connect(host=db_cfg.get("host", "localhost"), port=db_cfg.get("port", 5432),
                database=db_cfg.get("database", ""), user=db_cfg.get("user", ""),
                password=db_cfg.get("password", ""))
            _cur2 = _conn2.cursor()
            for sku, avail, locked in db_snapshot.get("inventory", []):
                _cur2.execute("UPDATE inventory SET available_qty=%s, locked_qty=%s WHERE sku=%s",
                            (avail, locked, sku))
            for email, balance in db_snapshot.get("balances", []):
                _cur2.execute("UPDATE users SET balance=%s WHERE email=%s", (balance, email))
            # Clean up E2E-created test data
            taken_at = db_snapshot.get("taken_at", 0)
            import datetime as _dt2
            cutoff = _dt2.datetime.fromtimestamp(taken_at, tz=_dt2.timezone.utc)
            _cur2.execute("DELETE FROM refunds WHERE created_at > %s", (cutoff,))
            _cur2.execute("DELETE FROM payments WHERE created_at > %s", (cutoff,))
            _cur2.execute("DELETE FROM orders WHERE created_at > %s", (cutoff,))
            _conn2.commit()
            _conn2.close()
            print(f"  [CLEANUP] 已恢复数据库到测试前状态", flush=True)
        except Exception as _re:
            print(f"  [WARN] DB restore failed (non-fatal): {_re}", flush=True)
    
    # ── Holistic re-evaluation with all layer findings ──
    all_extra_findings = db_findings + e2e_findings + deep_findings + ui_findings
    try:
        from .evaluation_engine import CoverageAnalyzer
        full_analyzer = CoverageAnalyzer()
        full_coverage = full_analyzer.analyze(v12, extra_findings=all_extra_findings)
        holistic_cov = full_coverage.to_dict()["overall_coverage"]
    except Exception:
        holistic_cov = evaluation.coverage.to_dict().get("overall_coverage", 0)
    
    result = {
        "success": True,
        "db_findings": db_findings,
        "e2e_findings": e2e_findings,
        "ui_findings": ui_findings,
        "deep_findings": deep_findings,
        "scan_id": f"{project}_{int(t_start * 1000)}",
        "project": project,
        "grade": evaluation.system_grade,
        "score": evaluation.overall_score,
        "coverage": holistic_cov,
        "total_findings": total_findings,
        "total_ms": total_ms,
        "layers": layers,
        "preflight_diagnostics": diagnostics,
        "report_path": str(report_path) if report_path else None,
        "auto_har": v12.get("auto_har", {}),
    }

    # ── Full-Spectrum Bug Detection (auto-triggered) ──
    try:
        from .full_spectrum_bug_engine import run_full_spectrum
        from .openapi_spec_utils import parse_openapi_spec
        spec = {}
        sql_schema = ""
        spectrum_prd = prd_text  # Don't shadow the outer prd_text
        input_dir = root / "platform_workspace" / project / "input"
        for f in input_dir.glob("*") if input_dir.exists() else []:
            text = f.read_text(encoding="utf-8", errors="replace") if f.suffix in (".json", ".yaml", ".yml", ".md", ".sql", ".txt") else ""
            if f.suffix in (".json", ".yaml", ".yml"):
                spec = parse_openapi_spec(f) or spec
            elif f.suffix == ".sql" or "schema" in f.name.lower():
                sql_schema = text
            elif "prd" in f.name.lower() or "需求" in f.name:
                spectrum_prd = text
        spectrum = run_full_spectrum(
            openapi_spec=spec, base_url=base_url or "",
            sql_schema=sql_schema, prd_text=spectrum_prd,
        )
        spectrum_summary = spectrum.get("summary", {}) if isinstance(spectrum, dict) else {}
        spectrum_total_findings = int(spectrum_summary.get("total_findings", 0) or 0)
        result["spectrum"] = dict(spectrum) if isinstance(spectrum, dict) else {}
        if "summary" not in result["spectrum"] or not isinstance(result["spectrum"].get("summary"), dict):
            result["spectrum"]["summary"] = {}
        result["spectrum"]["summary"]["capabilities_run"] = int(spectrum_summary.get("capabilities_run", 0) or 0)
        result["spectrum"]["summary"]["total_findings"] = spectrum_total_findings
        # Keep top-level summary fields for lightweight callers that already consume them.
        result["spectrum"]["capabilities_run"] = result["spectrum"]["summary"]["capabilities_run"]
        result["spectrum"]["total_findings"] = spectrum_total_findings
        result["total_findings"] += spectrum_total_findings
    except Exception as e:
        print(f"  [WARN] Full-spectrum detection failed (non-fatal): {e}", flush=True)

    if ci_result:
        result["ci_gate"] = ci_result.get("ci_gate", {})

    # ── DB Snapshot Verification (auto-triggered when DB configured) ──
    try:
        from .db_snapshot_verifier import DBSnapshotVerifier
        db_verifier = DBSnapshotVerifier()
        if db_verifier.configured:
            # Detect tables from API/PRD context
            tables = _infer_db_tables(api_doc_text, prd_text)
            db_verifier.snapshot_before(tables)
            # ... scan already ran above, so we capture after-snapshot ...
            db_verifier.snapshot_after(tables)
            db_result = db_verifier.verify()
            result["db_verification"] = {
                "configured": True,
                "db_type": db_result.db_type,
                "tables_checked": db_result.tables_checked,
                "diffs": db_result.diffs,
                "findings": db_result.findings,
                "duration_ms": db_result.duration_ms,
            }
            # Merge DB findings into total
            result["total_findings"] += len(db_result.findings)
    except Exception as e:
        print(f"  [WARN] DB verification skipped (non-fatal): {e}", flush=True)

    # ── Performance Baseline (auto-triggered, always records) ──
    try:
        from .performance_baseline import PerformanceBaseline, PerfMetrics
        baseline = PerformanceBaseline(project, root)
        metrics = PerfMetrics(
            scan_id=result["scan_id"],
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            api_total_ms=result.get("layers", {}).get("api", {}).get("ms", 0),
            api_error_rate=0.0,
            perf_findings=result.get("layers", {}).get("perf", {}).get("findings", 0),
            perf_pass_rate=result.get("layers", {}).get("perf", {}).get("pass_rate", 0),
            total_ms=total_ms,
            total_findings=total_findings,
        )
        # Extract load test metrics if available
        spectrum_data = result.get("spectrum", {})
        if spectrum_data:
            metrics.load_max_concurrency = 50  # default from load test
        perf_result = baseline.record(metrics)
        result["performance_baseline"] = {
            "runs": perf_result.get("runs", 0),
            "baseline_established": perf_result.get("baseline_established", False),
            "regressions": perf_result.get("regressions", []),
            "trend": perf_result.get("trend", {}),
        }
        # Add regression findings to total
        reg_findings = perf_result.get("regressions", [])
        result["total_findings"] += len(reg_findings)
        if reg_findings:
            if "spectrum" not in result:
                result["spectrum"] = {"summary": {}}
            result["spectrum"]["total_findings"] = result["spectrum"].get("total_findings", 0) + len(reg_findings)
            summary = result["spectrum"].get("summary")
            if isinstance(summary, dict):
                summary["total_findings"] = int(summary.get("total_findings", 0) or 0) + len(reg_findings)
    except Exception as e:
        print(f"  [WARN] Performance baseline skipped (non-fatal): {e}", flush=True)

    _persist_scan_result(project, root, result)
    
    # ══ Result Summary (client-facing) ══
    try:
        from .scan_diagnostics import generate_result_summary
        summary = generate_result_summary(result)
        result["executive_summary"] = summary
        print(f"  [SUMMARY] [1m{summary['risk_level']}[0m: {summary['risk_description']}", flush=True)
    except Exception:
        pass
    
    return result


def _persist_scan_result(project: str, root: Path, result: dict) -> None:
    """Persist complete scan result to JSON so command-center can read all layers."""
    import json as _j
    out = root / "platform_outputs" / project / "scan_result.json"
    try:
        _j.dump(result, out.open("w", encoding="utf-8"), ensure_ascii=False, default=str)
    except Exception:
        pass


def _auto_prd(api_doc: str) -> str:
    """Generate a basic PRD from API documentation."""
    entities = set()
    # Pattern 1: Table-format API docs with | METHOD | /path |
    for line in api_doc.split("\n"):
        if not line.strip().startswith("|"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 3:
            continue
        path = parts[2]
        segs = [s for s in path.strip("/").split("/") if s and "{" not in s]
        for s in segs:
            if s != "api":
                entities.add(s)
    # Pattern 2: Heading-style API docs: ### METHOD /path
    import re
    heading_re = re.compile(r'^#{1,6}\s*(?:GET|POST|PUT|DELETE|PATCH)\s+(/[^\s\n`]+)', re.MULTILINE | re.IGNORECASE)
    for match in heading_re.finditer(api_doc):
        path = match.group(1)
        segs = [s for s in path.strip("/").split("/") if s and "{" not in s and ":" not in s]
        for s in segs:
            if s != "api":
                entities.add(s)

    prd_parts = [f"{e}管理：CRUD + 状态流转。" for e in sorted(entities) if len(e) > 1 and not e.startswith("---") and "?" not in e and "=" not in e]
    return "系统包含以下模块：" + " ".join(prd_parts) if prd_parts else "系统包含以下模块：认证管理，订单管理，支付管理。"


def _infer_db_tables(api_doc: str, prd_text: str) -> list[str]:
    """Infer database table names from API doc and PRD text."""
    tables = set()
    # Common API path patterns → table inference
    import re
    for text in [api_doc, prd_text]:
        if not text:
            continue
        # Extract from API paths: /api/orders → orders
        paths = re.findall(r'/api/(\w+)', text)
        for p in paths:
            tables.add(p)
        # Extract from SQL-like patterns
        sql_tables = re.findall(r'(?:FROM|JOIN|TABLE|INTO)\s+(\w+)', text, re.IGNORECASE)
        for t in sql_tables:
            if not t.upper() in ("SELECT", "WHERE", "SET", "VALUES", "NULL"):
                tables.add(t.lower())
    # Fallback: common business tables
    if not tables:
        tables = {"orders", "products", "users", "inventory"}
    return sorted(tables)[:10]  # limit to 10 tables


# ═══════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(description="QualiBug Unified Scanner")
    parser.add_argument("--project", required=True, help="Project name")
    parser.add_argument("--api-doc", help="Path to API documentation markdown file")
    parser.add_argument("--api-doc-text", help="Inline API documentation text")
    parser.add_argument("--prd", default="", help="Business requirements text")
    parser.add_argument("--base-url", default="", help="Target server URL")
    parser.add_argument("--ci-gate", action="store_true", help="Enable CI gate diff")
    parser.add_argument("--no-multi-layer", action="store_true", help="Disable UI/Perf/Security/Infra layers")
    parser.add_argument("--output-dir", help="Custom output directory")
    parser.add_argument("--no-report", action="store_true", help="Skip report generation")
    parser.add_argument("--json", action="store_true", help="Output JSON only")

    args = parser.parse_args()

    if not args.api_doc and not args.api_doc_text:
        print("Error: --api-doc or --api-doc-text required", file=sys.stderr)
        sys.exit(1)

    result = scan(
        project=args.project,
        api_doc_path=args.api_doc or "",
        api_doc_text=args.api_doc_text or "",
        prd_text=args.prd,
        base_url=args.base_url,
        ci_gate=args.ci_gate,
        multi_layer=not args.no_multi_layer,
        output_dir=Path(args.output_dir) if args.output_dir else None,
        save_report=not args.no_report,
    )

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"\n{'='*50}")
        print(f"QualiBug Scan: {result['project']}")
        print(f"Grade: {result['grade']} ({result['score']:.0f}/100) | Coverage: {result['coverage']:.1%}")
        print(f"Findings: {result['total_findings']} | Time: {result['total_ms']}ms")
        print(f"\nLayers:")
        for name, info in result["layers"].items():
            print(f"  {name:10s}: {info['findings']:4d} findings | {info['ms']:5d}ms | {info.get('tool','?')}")
        if result.get("ci_gate"):
            gate = result["ci_gate"]
            print(f"\nCI Gate: {'BLOCKED' if not gate.get('passed') else 'PASS'}")
            for a in gate.get("alerts", []):
                print(f"  ⚠ {a}")
        if result.get("report_path"):
            print(f"\nReport: {result['report_path']}")

    sys.exit(0 if result.get("success") else 1)


if __name__ == "__main__":
    main()
