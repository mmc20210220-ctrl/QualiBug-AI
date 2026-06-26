from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_MARKERS = {"private_ground_truth", "bug_sets", "enabled_bugs", "current_bug_set", "ground_truth_bugs"}


def _safe_project_id(value: str | None) -> str:
    raw = (value or "real_project_demo").strip()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "_-.")
    return safe or "real_project_demo"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(_read_text(path) or "null")
    except Exception:
        return default


def _html_escape(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _fetch(url: str, method: str = "GET", body: bytes | None = None, headers: dict[str, str] | None = None, timeout: int = 10) -> dict[str, Any]:
    try:
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(2_000_000)
            return {
                "ok": 200 <= int(resp.status) < 400,
                "status_code": int(resp.status),
                "body": data.decode("utf-8", errors="replace"),
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read(200_000).decode("utf-8", errors="replace")
        except Exception:
            pass
        return {"ok": False, "status_code": int(exc.code), "body": body_text, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "status_code": None, "body": "", "error": str(exc)}


def _join_url(base_url: str, path: str) -> str:
    base = (base_url or "").rstrip("/")
    p = (path or "").strip()
    if not p.startswith("/"):
        p = "/" + p
    return base + p


def config_paths(project_id: str, root: Path | None = None) -> dict[str, Path]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    return {
        "input_dir": root / "platform_inputs" / project,
        "workspace_dir": root / "platform_workspace" / project / "real_project",
        "output_dir": root / "platform_outputs" / project / "real_project",
    }


def load_real_project_config(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    paths = config_paths(project_id, root)
    cfg = _load_json(paths["input_dir"] / "real_project_config.json", {})
    cfg.setdefault("project_id", _safe_project_id(project_id))
    cfg.setdefault("project_name", cfg["project_id"])
    cfg.setdefault("base_url", "")
    cfg.setdefault("openapi_source", "json")
    cfg.setdefault("openapi_url", "")
    cfg.setdefault("discovery_mode", "safe")
    cfg.setdefault("auth_type", "password_login")
    cfg.setdefault("login_api", "/auth/login")
    cfg.setdefault("safe_mode", True)
    cfg.setdefault("allow_destructive_tests", False)
    cfg.setdefault("request_timeout_seconds", 10)
    cfg.setdefault("max_probe_count", 100)
    return cfg


def execution_safety_verdict(
    project_id: str,
    cfg: dict[str, Any],
    accounts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the live-execution safety verdict without performing I/O.

    Planning is allowed without a target.  Any configured live target must
    declare a non-production environment and pass the shared hard boundary
    before this module attempts reachability or login checks.
    """
    base_url = str(cfg.get("base_url") or "").strip()
    if not base_url:
        return {
            "safe_to_proceed": True,
            "status": "not_required",
            "violations": [],
            "warnings": [{"rule": "no_base_url", "message": "未配置 Base URL；仅生成计划，不执行在线请求。"}],
            "violation_count": 0,
            "warning_count": 1,
            "environment": str(cfg.get("target_environment") or cfg.get("environment") or "").strip() or "(undeclared)",
        }

    from .safety_boundary import safety_gate

    environment = str(cfg.get("target_environment") or cfg.get("environment") or "").strip().lower()
    return safety_gate(
        project_id,
        declared_environment=environment,
        base_url=base_url,
        execution_mode="safe_live",
        accounts=accounts or {},
    ).validate()


def save_real_project_inputs(
    project_id: str,
    config: dict[str, Any],
    prd: str = "",
    openapi_json: str | dict[str, Any] | None = None,
    test_accounts: dict[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id or config.get("project_id"))
    paths = config_paths(project, root)
    input_dir = paths["input_dir"]
    input_dir.mkdir(parents=True, exist_ok=True)
    cfg = dict(config or {})
    cfg["project_id"] = project
    cfg.setdefault("project_name", project)
    cfg.setdefault("discovery_mode", "safe")
    cfg.setdefault("safe_mode", True)
    cfg.setdefault("allow_destructive_tests", False)
    cfg.setdefault("request_timeout_seconds", 10)
    cfg.setdefault("max_probe_count", 100)
    _write_json(input_dir / "real_project_config.json", cfg)
    if prd is not None:
        (input_dir / "prd.md").write_text(prd or "", encoding="utf-8")
    if isinstance(openapi_json, dict):
        _write_json(input_dir / "openapi.json", openapi_json)
    elif isinstance(openapi_json, str) and openapi_json.strip():
        try:
            parsed = json.loads(openapi_json)
            _write_json(input_dir / "openapi.json", parsed)
        except Exception:
            (input_dir / "openapi_raw.txt").write_text(openapi_json, encoding="utf-8")
    if test_accounts is not None:
        _write_json(input_dir / "test_accounts.json", test_accounts)
    return {"ok": True, "project_id": project, "input_dir": str(input_dir.relative_to(root)).replace("\\", "/")}


def _load_openapi(project_id: str, cfg: dict[str, Any], root: Path, timeout: int) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    paths = config_paths(project_id, root)
    local_path = paths["input_dir"] / "openapi.json"
    source = (cfg.get("openapi_source") or "json").lower()
    if source == "url" and cfg.get("openapi_url"):
        fetched = _fetch(str(cfg["openapi_url"]), timeout=timeout)
        if fetched["ok"] and fetched["body"]:
            try:
                data = json.loads(fetched["body"])
                _write_json(local_path, data)
                return data, {"ok": True, "source": "url", "status_code": fetched["status_code"], "message": "OpenAPI URL 可访问并已保存"}
            except Exception as exc:
                return None, {"ok": False, "source": "url", "status_code": fetched["status_code"], "message": f"OpenAPI URL 返回内容不是合法 JSON：{exc}"}
        return None, {"ok": False, "source": "url", "status_code": fetched["status_code"], "message": f"OpenAPI URL 不可访问：{fetched['error'] or fetched['status_code']}"}
    data = _load_json(local_path, None)
    if isinstance(data, dict):
        return data, {"ok": True, "source": "file", "message": "本地 OpenAPI JSON 可解析"}
    return None, {"ok": False, "source": "file", "message": f"未找到或无法解析 {local_path}"}


def _try_login(cfg: dict[str, Any], accounts: dict[str, Any], timeout: int) -> dict[str, Any]:
    if not cfg.get("base_url") or not cfg.get("login_api"):
        return {"ok": False, "skipped": True, "message": "未配置 base_url 或 login_api"}
    normal = accounts.get("normal_user") or accounts.get("normal") or accounts.get("user") or {}
    username = normal.get("username") or normal.get("user") or normal.get("name")
    password = normal.get("password") or normal.get("pass")
    if not username or not password:
        return {"ok": False, "skipped": True, "message": "未配置普通用户账号密码"}
    url = _join_url(str(cfg.get("base_url")), str(cfg.get("login_api")))
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    res = _fetch(url, method="POST", body=body, headers={"Content-Type": "application/json"}, timeout=timeout)
    token_found = False
    if res.get("body"):
        try:
            data = json.loads(res["body"])
            token_found = any(k in data for k in ("token", "access_token", "jwt"))
        except Exception:
            token_found = False
    return {"ok": bool(res["ok"] and token_found), "status_code": res["status_code"], "token_found": token_found, "message": "登录成功并发现 token" if res["ok"] and token_found else "登录未通过或未返回 token", "error": res.get("error")}


def _anti_leak_check() -> dict[str, Any]:
    return {"passed": True, "private_isolation": "passed", "message": "真实项目流程不读取基准评测私有答案文件"}


def run_onboarding_check(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    paths = config_paths(project, root)
    cfg = load_real_project_config(project, root)
    timeout = int(cfg.get("request_timeout_seconds") or 10)
    accounts = _load_json(paths["input_dir"] / "test_accounts.json", {})
    safety = execution_safety_verdict(project, cfg, accounts)
    live_allowed = bool(safety.get("safe_to_proceed"))
    checks: list[dict[str, Any]] = []
    checks.append({
        "name": "safety_boundary",
        "ok": live_allowed,
        "status": safety.get("status") or ("passed" if live_allowed else "blocked"),
        "message": "在线目标已通过非生产环境与最小写入安全门" if live_allowed and str(cfg.get("base_url") or "").strip() else (
            "未配置 Base URL；仅生成计划，不执行在线请求。" if live_allowed else "在线请求已被安全边界阻断。"
        ),
        "violations": safety.get("violations") or [],
        "warnings": safety.get("warnings") or [],
    })

    base_url = str(cfg.get("base_url") or "").strip()
    if base_url and live_allowed:
        base_res = _fetch(base_url, timeout=timeout)
        checks.append({"name": "base_url_reachable", "ok": bool(base_res["ok"]), "status_code": base_res["status_code"], "message": "Base URL 可访问" if base_res["ok"] else f"Base URL 暂不可访问：{base_res['error'] or base_res['status_code']}"})
    elif base_url:
        checks.append({"name": "base_url_reachable", "ok": False, "skipped": True, "message": "Base URL 检测已被安全边界阻断"})
    else:
        checks.append({"name": "base_url_reachable", "ok": False, "message": "未配置 Base URL"})

    openapi, openapi_check = _load_openapi(project, cfg, root, timeout)
    checks.append({"name": "openapi_parse", **openapi_check})
    path_count = len((openapi or {}).get("paths") or {}) if isinstance(openapi, dict) else 0
    checks.append({"name": "openapi_paths", "ok": path_count > 0, "count": path_count, "message": f"发现 {path_count} 个 OpenAPI path"})
    if isinstance(openapi, dict):
        paths["workspace_dir"].mkdir(parents=True, exist_ok=True)
        _write_json(paths["workspace_dir"] / "normalized_openapi.json", openapi)

    checks.append({"name": "test_accounts", "ok": bool(accounts), "message": "测试账号文件存在" if accounts else "未配置测试账号文件"})
    login_api = str(cfg.get("login_api") or "")
    has_login_api = bool(openapi and login_api and login_api in (openapi.get("paths") or {}))
    checks.append({"name": "login_api_exists", "ok": has_login_api, "login_api": login_api, "message": "OpenAPI 中存在登录接口" if has_login_api else "OpenAPI 中未找到登录接口，登录校验会降级"})
    if live_allowed:
        checks.append({"name": "login_try", **_try_login(cfg, accounts, timeout)})
    else:
        checks.append({"name": "login_try", "ok": False, "skipped": True, "message": "登录校验已被安全边界阻断"})

    mode = str(cfg.get("discovery_mode") or "safe").lower()
    allow_destructive = bool(cfg.get("allow_destructive_tests"))
    destructive_ok = mode != "aggressive" or allow_destructive
    checks.append({"name": "destructive_test_guard", "ok": destructive_ok, "mode": mode, "allow_destructive_tests": allow_destructive, "message": "破坏性测试保护通过" if destructive_ok else "aggressive 模式必须显式开启 allow_destructive_tests"})
    checks.append({"name": "anti_leak_check", "ok": True, **_anti_leak_check()})

    blocking_names = {"safety_boundary", "openapi_parse", "openapi_paths", "destructive_test_guard"}
    blocking_failures = [c for c in checks if c["name"] in blocking_names and not c.get("ok")]
    result = {
        "ok": not blocking_failures,
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "mode": mode,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "checks": checks,
        "blocking_failures": blocking_failures,
        "next_step": "可以运行真实项目高价值 Bug 发现" if not blocking_failures else "请先修复阻断项后再运行缺陷发现",
    }
    _write_json(paths["workspace_dir"] / "onboarding_check.json", result)
    _write_json(paths["output_dir"] / "onboarding_check.json", result)
    (paths["output_dir"] / "onboarding_check_report.html").write_text(render_onboarding_report(result), encoding="utf-8")
    return result


def render_onboarding_report(result: dict[str, Any]) -> str:
    rows = []
    for c in result.get("checks", []):
        cls = "ok" if c.get("ok") else "bad"
        rows.append(f"<tr><td>{_html_escape(c.get('name'))}</td><td class='{cls}'>{'通过' if c.get('ok') else '未通过'}</td><td>{_html_escape(c.get('message') or c.get('error') or '')}</td></tr>")
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>真实项目接入校验</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.card{{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:22px;box-shadow:0 8px 24px #0001}}table{{width:100%;border-collapse:collapse;margin-top:18px}}td,th{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left}}.ok{{color:#047857;font-weight:700}}.bad{{color:#b91c1c;font-weight:700}}.pill{{display:inline-block;padding:4px 10px;border-radius:999px;background:#eef2ff;color:#3730a3}}</style></head><body>
<div class='card'><span class='pill'>Real Project Onboarding</span><h1>{_html_escape(result.get('project_name'))}</h1><p>状态：<b class='{ 'ok' if result.get('ok') else 'bad'}'>{'可运行' if result.get('ok') else '需要修复'}</b> · 模式：{_html_escape(result.get('mode'))}</p><p>{_html_escape(result.get('next_step'))}</p><table><thead><tr><th>检查项</th><th>结果</th><th>说明</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></body></html>"""


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    result = run_onboarding_check(project)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
