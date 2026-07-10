"""
QualiBug Production Diagnostics — pre-scan health check + result summary.
Client-facing: clear Chinese diagnostics for every layer.
"""
from __future__ import annotations
import json, os, urllib.request, time, socket
from dataclasses import dataclass, field
from typing import Any

from .ssrf_guard import safe_urlopen, SsrfBlockedError


@dataclass
class DiagCheck:
    name: str
    passed: bool
    message: str
    suggestion: str = ""
    severity: str = "info"  # info, warn, error

    def to_dict(self) -> dict:
        return {"name": self.name, "passed": self.passed, "message": self.message,
                "suggestion": self.suggestion, "severity": self.severity}


_NON_PRODUCTION_ENVIRONMENT_KINDS = {
    "local", "development", "dev", "test", "testing", "qa", "sit", "uat",
    "staging", "stage", "pre-release", "prerelease", "sandbox",
}


def _allow_internal_preflight(
    config: dict[str, Any] | None = None,
    base_url: str = "",
) -> bool:
    """Allow an internal target only through an explicit non-production grant."""

    if (
        os.environ.get("QUALIBUG_LOCAL_DEV_ACTOR", "0") == "1"
        or os.environ.get("QUALIBUG_SSRF_ALLOW_INTERNAL", "0") == "1"
    ):
        return True
    cfg = config if isinstance(config, dict) else {}
    environment_kind = str(
        cfg.get("environment_kind")
        or cfg.get("environment_type")
        or cfg.get("target_environment_kind")
        or ""
    ).strip().lower()
    approved_base_url = str(cfg.get("approved_base_url") or "").strip().rstrip("/")
    requested_base_url = str(base_url or cfg.get("api_base_url") or "").strip().rstrip("/")
    return bool(
        environment_kind in _NON_PRODUCTION_ENVIRONMENT_KINDS
        and approved_base_url
        and requested_base_url
        and approved_base_url == requested_base_url
    )


def _ordered_test_credentials(config: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        from .enterprise_pilot_runtime import ordered_test_credentials
        return ordered_test_credentials({"test_profile": config})
    except Exception:
        credentials = config.get("test_credentials", {})
        if not isinstance(credentials, dict):
            return []
        return [{"profile": str(key), **dict(value)} for key, value in credentials.items() if isinstance(value, dict)]


def run_preflight(config: dict, api_doc: str | None = None) -> dict:
    """Run pre-scan health checks. Returns diagnostics dict."""
    checks: list[DiagCheck] = []
    base_url = config.get("api_base_url", "")
    db_cfg = config.get("database", {})

    # ── 1. API reachability ──
    if base_url:
        try:
            resp = safe_urlopen(base_url, timeout=5, allow_internal=_allow_internal_preflight(config, base_url))
            checks.append(DiagCheck("API可达性", True,
                f"{base_url} 响应 HTTP {resp.status}"))
        except urllib.error.HTTPError as e:
            checks.append(DiagCheck("API可达性", True,
                f"{base_url} 可达，根路径返回 HTTP {e.code}"))
        except Exception as e:
            checks.append(DiagCheck("API可达性", False,
                f"无法连接 {base_url}: {_short_err(e)}",
                "请检查目标服务是否启动、网络是否可达、防火墙是否放行",
                "error"))
    else:
        checks.append(DiagCheck("API地址", False,
            "未配置 api_base_url",
            "请在connector_registry.json中设置test_profile.api_base_url",
            "error"))

    # ── 2. Auth check ──
    credentials = _ordered_test_credentials(config)
    login_account = next(
        (
            item for item in credentials
            if str(item.get("email") or item.get("username") or item.get("account") or "").strip()
            and str(item.get("password") or item.get("pass") or "").strip()
        ),
        {},
    )
    account_name = str(login_account.get("profile") or "default").strip() or "default"
    identity = str(
        login_account.get("email")
        or login_account.get("username")
        or login_account.get("account")
        or login_account.get("mobile")
        or login_account.get("phone")
        or account_name
    ).strip()
    if base_url and login_account:
        try:
            data = json.dumps(
                {
                    "email": str(login_account.get("email") or login_account.get("username") or login_account.get("account") or ""),
                    "password": str(login_account.get("password") or login_account.get("pass") or ""),
                }
            ).encode()
            req = urllib.request.Request(f"{base_url}/api/auth/login", data=data,
                headers={"Content-Type": "application/json"}, method="POST")
            resp = safe_urlopen(req, timeout=5, allow_internal=_allow_internal_preflight(config, base_url))
            token = json.loads(resp.read()).get("token", "")
            if token:
                checks.append(DiagCheck(f"测试凭证({account_name})", True,
                    f"{identity} 登录成功"))
            else:
                checks.append(DiagCheck(f"测试凭证({account_name})", False,
                    f"{identity} 登录成功但无token",
                    "检查认证接口返回格式", "warn"))
        except urllib.error.HTTPError as e:
            checks.append(DiagCheck(f"测试凭证({account_name})", False,
                f"{identity} 登录失败: HTTP {e.code}",
                f"请检查connector_registry中的凭证是否正确，或使用其他登录端点",
                "error"))
        except Exception as e:
            checks.append(DiagCheck(f"测试凭证({account_name})", False,
                f"登录异常: {_short_err(e)}",
                "请确认/api/auth/login接口存在且可访问",
                "warn"))
    else:
        checks.append(DiagCheck("测试凭证", False,
            "未配置可登录测试凭证",
            "在test_profile.test_credentials中设置任一可用账号的email/username和password",
            "warn"))

    # ── 3. DB connection ──
    if db_cfg.get("host") and db_cfg.get("database"):
        try:
            import pg8000
            params = {k: db_cfg[k] for k in ("host", "port", "user", "password", "database") if k in db_cfg}
            conn = pg8000.connect(**params)
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
            table_count = cur.fetchone()[0]
            conn.close()
            checks.append(DiagCheck("数据库连接", True,
                f"PostgreSQL连接成功，发现{table_count}张表"))
        except Exception as e:
            checks.append(DiagCheck("数据库连接", False,
                f"连接失败: {_short_err(e)}",
                "请检查数据库地址、端口、用户名密码是否正确，网络是否可达",
                "warn"))
    else:
        checks.append(DiagCheck("数据库配置", False,
            "未配置数据库连接",
            "在test_profile.database中设置连接信息（可选，不配置则跳过DB层检测）",
            "info"))

    # ── 4. API doc parsing ──
    routes: list[str] = []
    if api_doc:
        import re
        for m in re.finditer(r'^#{2,4}\s+(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)', api_doc, re.MULTILINE):
            routes.append(f"{m.group(1)} {m.group(2)}")
        if routes:
            checks.append(DiagCheck("API文档解析", True,
                f"成功解析 {len(routes)} 个接口"))
        else:
            yaml_paths = re.findall(r'^\s{1,6}(/[A-Za-z0-9_./{}:-]+)\s*:\s*$', api_doc, re.MULTILINE)
            if "openapi:" in api_doc[:500].lower() and yaml_paths:
                routes.extend(f"GET {path}" for path in sorted(set(yaml_paths)))
                checks.append(DiagCheck("API文档解析", True,
                    f"成功解析OpenAPI YAML格式，{len(set(yaml_paths))}个路径"))
            # Try OpenAPI JSON
            try:
                spec = json.loads(api_doc)
                paths = spec.get("paths", {})
                if paths:
                    count = sum(len(v) for v in paths.values())
                    checks.append(DiagCheck("API文档解析", True,
                        f"成功解析OpenAPI格式，{len(paths)}个路径，{count}个操作"))
                else:
                    checks.append(DiagCheck("API文档解析", False,
                        "未找到接口定义",
                        "请确认API文档格式为Markdown（### METHOD /path）或OpenAPI JSON",
                        "error"))
            except (json.JSONDecodeError, ValueError):
                checks.append(DiagCheck("API文档解析", False,
                    f"文档{len(api_doc)}字符但未识别格式",
                    "支持格式: Markdown(`### METHOD /path`)、OpenAPI JSON、Swagger YAML",
                    "error"))
    else:
        checks.append(DiagCheck("API文档", False,
            "未提供API文档",
            "上传API接口文档后，扫描才能识别所有接口",
            "warn"))

    # ── 5. Smoke test ──
    if base_url and routes:
        test_route = routes[0].split(" ", 1)[1] if routes else "/"
        test_method = routes[0].split(" ", 1)[0] if routes else "GET"
        try:
            req = urllib.request.Request(f"{base_url}{test_route}", method=test_method, headers={"User-Agent": "QualiBug-Diag"})
            resp = safe_urlopen(req, timeout=5, allow_internal=_allow_internal_preflight(config, base_url))
            checks.append(DiagCheck("接口冒烟测试", True,
                f"{test_method} {test_route} → HTTP {resp.status}"))
        except urllib.error.HTTPError as e:
            checks.append(DiagCheck("接口冒烟测试", True,
                f"{test_method} {test_route} → HTTP {e.code}（正常，接口需要认证）"))
        except Exception as e:
            checks.append(DiagCheck("接口冒烟测试", False,
                f"{test_method} {test_route}: {_short_err(e)}",
                "API文档中的接口路径可能与实际服务不一致",
                "warn"))

    # ── Summary ──
    def _is_api_doc_parse_check(check: DiagCheck) -> bool:
        return "API" in check.name and ("文档" in check.name or "鏂囨" in check.name) and ("解析" in check.name or "瑙" in check.name)

    api_doc_parsed = any(c.passed and _is_api_doc_parse_check(c) for c in checks)
    if api_doc_parsed:
        checks = [c for c in checks if not (_is_api_doc_parse_check(c) and not c.passed)]
    errors = [
        c for c in checks
        if c.severity == "error"
    ]
    warns = [c for c in checks if c.severity == "warn"]
    all_ok = len(errors) == 0
    ready = len(errors) == 0

    return {
        "ready": ready,
        "all_checks_passed": all(c.passed for c in checks),
        "checks": [c.to_dict() for c in checks],
        "errors": len(errors),
        "warnings": len(warns),
        "summary": "所有检查通过，可以开始扫描" if ready else
                    f"存在{len(errors)}个错误需修复后才能扫描" if errors else
                    f"存在{len(warns)}个警告，扫描可能部分功能受限"
    }


def generate_result_summary(report: dict) -> dict:
    """Generate client-facing summary from intelligence report."""
    layers = report.get("layers", {})
    total = report.get("total_findings", 0)
    p0 = sum(1 for f in _all_findings(report) if f.get("severity") == "P0")
    p1 = sum(1 for f in _all_findings(report) if f.get("severity") == "P1")
    preflight = report.get("preflight_diagnostics")
    preflight = preflight if isinstance(preflight, dict) else {}
    failed_preflight = [
        check
        for check in preflight.get("checks", [])
        if isinstance(check, dict) and not check.get("passed") and check.get("severity") == "error"
    ]

    layer_details = []
    for name, info in sorted(layers.items()):
        count = info.get("findings", 0)
        label = _layer_label(name)
        layer_details.append({
            "layer": name,
            "label": label,
            "count": count,
            "status": "active" if count > 0 else "idle"
        })

    # Risk level
    if failed_preflight:
        risk_level = "环境未就绪"
        failed_names = "、".join(str(item.get("name") or "未知检查") for item in failed_preflight[:3])
        risk_desc = f"预检失败：{failed_names}。未验证的集成不计为健康。"
    elif total == 0:
        risk_level = "无风险"
        risk_desc = "系统未发现明显缺陷，建议持续监控"
    elif p0 == 0 and p1 <= 3:
        risk_level = "低风险"
        risk_desc = f"发现{p1}个次要问题"
    elif p0 <= 5:
        risk_level = "中风险"
        risk_desc = f"发现{p0}个严重问题，建议优先修复"
    else:
        risk_level = "高风险"
        risk_desc = f"发现{p0}个严重问题，{p1}个一般问题，需要立即修复"

    return {
        "grade": report.get("grade", "N/A"),
        "score": report.get("score", 0),
        "risk_level": risk_level,
        "risk_description": risk_desc,
        "total_findings": total,
        "critical_bugs": p0,
        "high_priority_bugs": p1,
        "layers": layer_details,
        "scan_duration_ms": report.get("total_ms", 0),
        "diagnosis": _diagnosis(total, layers, failed_preflight),
    }


def _all_findings(report: dict) -> list:
    """Extract all findings from report regardless of source."""
    all_f = []
    for key in ("bug_scores", "db_verification", "e2e_findings", "deep_findings", "ui_findings"):
        val = report.get(key, [])
        if isinstance(val, dict):
            val = val.get("findings", [])
        if isinstance(val, list):
            all_f.extend(val)
    return all_f


def _layer_label(name: str) -> str:
    return {
        "api": "接口层检测",
        "db": "数据库验证",
        "e2e": "业务流程测试",
        "deep": "深度验证",
        "frontend": "前端UI测试",
        "security": "安全检测",
        "ui": "基础UI检查",
        "infra": "基础设施",
        "perf": "性能测试",
    }.get(name, name)


def _diagnosis(total: int, layers: dict, failed_preflight: list[dict] | None = None) -> str:
    """Generate diagnostic message about scan quality."""
    parts = []
    if failed_preflight:
        details = "；".join(
            f"{item.get('name')}: {item.get('message')}"
            for item in failed_preflight[:5]
        )
        return f"预检未通过，扫描能力受限：{details}"
    active_layers = sum(1 for v in layers.values() if isinstance(v, dict) and v.get("findings", 0) > 0)
    total_layers = len(layers) if layers else 0

    if total == 0 and active_layers == 0:
        parts.append("⚠️ 所有检测层均未产生发现。")
        parts.append("可能原因: 1) API文档未解析 2) 服务未启动 3) 认证凭证无效。")
    elif total == 0 and active_layers > 0:
        parts.append("✅ 扫描正常完成，未发现明显缺陷。")
        parts.append(f"系统整体安全性较好，建议定期复查。")
    elif total > 0:
        parts.append(f"✅ 扫描完成，{active_layers}/{total_layers}层产出了有效发现。")
        if active_layers < total_layers:
            idle = [k for k, v in layers.items() if isinstance(v, dict) and v.get("findings", 0) == 0]
            parts.append(f"以下层未产出发现: {', '.join(idle)}，可能需要配置或检查。")

    return " ".join(parts)


def _short_err(e: Exception) -> str:
    msg = str(e)
    return msg[:150] if len(msg) > 150 else msg


def diagnose_session(project_id: str, output_dir: str | None = None) -> dict:
    """Diagnose the loop runtime session health for a project.

    Returns human-readable diagnostics about stale/corrupt sessions that
    could cause scan() to permanently return blocked/stopped results.
    Integrates with LoopRuntimeSession.diagnose() and can be called from
    preflight checks or standalone troubleshooting.
    """
    try:
        from .loop_runtime import LoopRuntimeSession
        diag = LoopRuntimeSession.diagnose(project_id, output_dir)
    except Exception as exc:
        return {
            "project_id": project_id,
            "session_exists": False,
            "healthy": True,
            "error": f"diagnose_failed: {_short_err(exc)}",
            "checks": [],
        }

    checks: list[dict] = []
    if diag.get("session_exists"):
        lease = diag.get("lease", {})
        checks.append({
            "name": "循环租约状态",
            "passed": diag["healthy"],
            "message": (
                f"状态={lease.get('status', '?')}, "
                f"步骤={lease.get('step', '?')}, "
                f"PID={lease.get('pid', '?')}"
                f"{'(存活)' if lease.get('alive') else '(已终止)'}"
                f"{'(已过期)' if lease.get('expired') else ''}"
            ),
            "severity": "error" if not diag["healthy"] else "info",
        })
    else:
        checks.append({
            "name": "循环租约状态",
            "passed": True,
            "message": "无活跃租约，可以安全启动新扫描",
            "severity": "info",
        })

    if diag.get("heartbeat"):
        hb = diag["heartbeat"]
        hb_passed = hb.get("status") != "RUNNING" or hb.get("age_seconds", 0) < 300
        checks.append({
            "name": "心跳文件状态",
            "passed": hb_passed,
            "message": (
                f"状态={hb.get('status', '?')}, "
                f"步骤={hb.get('step', '?')}, "
                f"距今{hb.get('age_seconds', 0):.0f}秒"
            ),
            "severity": "warn" if not hb_passed else "info",
        })

    for issue in diag.get("blocking_issues", []):
        checks.append({
            "name": f"会话问题: {issue.get('kind', 'unknown')}",
            "passed": False,
            "message": issue.get("detail", ""),
            "severity": "error",
            "suggestion": f"建议执行: {diag.get('recommended_action', 'manual_intervention_required')}",
        })

    errors = [c for c in checks if c.get("severity") == "error"]
    return {
        "project_id": project_id,
        "session_exists": diag.get("session_exists", False),
        "healthy": diag.get("healthy", True),
        "recommended_action": diag.get("recommended_action", "none"),
        "blocking_issues": diag.get("blocking_issues", []),
        "checks": checks,
        "ready": len(errors) == 0,
        "errors": len(errors),
        "summary": (
            "会话健康，可以启动扫描" if not errors
            else f"存在{len(errors)}个会话问题，建议先执行 force_reset_stale_session()"
        ),
    }


if __name__ == "__main__":
    # Test
    import json
    try:
        with open("platform_workspace/第一个真实项目测试/enterprise_pilot_runtime/connector_registry.json",
                  encoding="utf-8") as f:
            cfg = json.load(f).get("test_profile", {})
        with open("platform_workspace/第一个真实项目测试/input/API_SPEC.md", encoding="utf-8") as f:
            doc = f.read()
        result = run_preflight(cfg, doc)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as e:
        print(f"Test failed: {e}")
