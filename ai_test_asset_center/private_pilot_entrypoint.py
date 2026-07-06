from __future__ import annotations

"""Deployment entrypoint for the patched private pilot HTTP service.

This module is the canonical executable entrypoint for local and Docker private
pilot deployments. It installs the runtime patches from ``private_pilot_server``
and normalizes the health contract before delegating to the legacy HTTP server.
"""

import html
import json
import os
import platform
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ai_test_asset_center import private_pilot_server as _server_patch
from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.private_pilot_server import install_customer_delivery_gate_patch
from ai_test_asset_center.version import (
    CANONICAL_HEALTH_PATH,
    DEFAULT_PRIVATE_PILOT_PORT,
    LEGACY_HEALTH_PATH,
    PRODUCT_CHANNEL,
    PRODUCT_NAME,
    PRODUCT_PHASE,
    PRODUCT_VERSION,
)

PATCH_SOURCE = "ai_test_asset_center.private_pilot_entrypoint"
MOJIBAKE_MARKERS = ("鎵", "鐢", "鍒", "椤", "鏃", "瑕", "绉", "娴", "缃", "搴", "", "€")


def _int_env(name: str, fallback: int) -> int:
    try:
        return int(os.environ.get(name, "") or fallback)
    except Exception:
        return fallback


def _pattern_library_count(root: Path) -> int:
    for candidate in (
        root / "pattern_library" / "patterns.json",
        root / "platform_workspace" / "pattern_library" / "patterns.json",
    ):
        try:
            if candidate.exists():
                data = json.loads(candidate.read_text(encoding="utf-8") or "{}")
                patterns = data.get("patterns") if isinstance(data, dict) else []
                return len(patterns) if isinstance(patterns, list) else 0
        except Exception:
            continue
    return 0


def _browser_ui_status() -> dict[str, Any]:
    try:
        from ai_test_asset_center.browser_ui_smoke import is_browser_ui_enabled

        enabled = is_browser_ui_enabled()
    except Exception:
        enabled = False
    return {
        "enabled": enabled,
        "env_flag": "QUALIBUG_BROWSER_UI_SMOKE",
        "mode": "smoke" if enabled else "disabled",
        "evidence": ["page_reachability", "console_errors", "network_errors", "screenshots", "har"],
    }


def _health_payload(handler: Any) -> dict[str, Any]:
    try:
        root = handler._root()
    except Exception:
        root = _service._root()
    try:
        llm_health = handler._llm_health()
    except Exception as exc:
        llm_health = {
            "available": False,
            "status": "offline",
            "label": "offline",
            "error": str(exc)[:300],
        }
    return {
        "ok": True,
        "service": "qualibug_private_pilot",
        "product": PRODUCT_NAME,
        "version": PRODUCT_VERSION,
        "product_version": PRODUCT_VERSION,
        "phase": PRODUCT_PHASE,
        "channel": PRODUCT_CHANNEL,
        "api_version": "v1",
        "private_root": str(root),
        "private_root_exists": root.exists(),
        "public_bind_allowed": os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") == "1",
        "bind_host": os.environ.get("QUALIBUG_BIND_HOST", "127.0.0.1"),
        "port": _int_env("QUALIBUG_PORT", DEFAULT_PRIVATE_PILOT_PORT),
        "canonical_health_path": CANONICAL_HEALTH_PATH,
        "legacy_health_path": LEGACY_HEALTH_PATH,
        "python_version": sys.version.split()[0],
        "platform": platform.system(),
        "llm_available": bool(llm_health.get("available")),
        "llm_status": llm_health,
        "browser_ui_smoke": _browser_ui_status(),
        "pattern_library_patterns": _pattern_library_count(root),
        "deployment_contract_patch": {
            "patched": True,
            "source": PATCH_SOURCE,
            "port_contract": f"container:{DEFAULT_PRIVATE_PILOT_PORT}",
            "health_contract": CANONICAL_HEALTH_PATH,
        },
    }


def _scan_project_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    value = kwargs.get("project")
    if not value and args:
        value = args[0]
    return str(value or os.environ.get("QUALIBUG_PROJECT") or "real_project_demo")


def _scan_root_from_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> Path:
    value = kwargs.get("root")
    if value is None and len(args) >= 2:
        value = args[1]
    try:
        return Path(value).resolve() if value is not None else _service._root()
    except Exception:
        return _service._root()


def _scan_base_url_from_context(kwargs: dict[str, Any]) -> str:
    for key in ("ui_base_url", "base_url"):
        value = str(kwargs.get(key) or "").strip()
        if value:
            return value.rstrip("/")
    try:
        pending = _server_patch._SCAN_CAMPAIGN_CONTEXT.get()  # type: ignore[attr-defined]
    except Exception:
        pending = None
    if isinstance(pending, dict):
        for key in ("ui_base_url", "base_url", "target_url"):
            value = str(pending.get(key) or "").strip()
            if value:
                return value.rstrip("/")
    for env_name in ("QUALIBUG_BROWSER_UI_BASE_URL", "QUALIBUG_TARGET_UI_BASE_URL", "QUALIBUG_TARGET_BASE_URL"):
        value = str(os.environ.get(env_name) or "").strip()
        if value:
            return value.rstrip("/")
    return ""


def _html_text(value: Any, limit: int = 300) -> str:
    text = str(value if value is not None else "").strip()
    return html.escape(text[:limit] if limit > 0 else text)


def _read_json_file(path: Path, fallback: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return fallback
    return fallback


def _report_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    candidates = [
        report.get("real_findings"),
        report.get("findings"),
        report.get("bug_scores"),
        (report.get("stage2_discovery") or {}).get("findings") if isinstance(report.get("stage2_discovery"), dict) else None,
    ]
    for value in candidates:
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def render_customer_safe_report_html(project: str, root: Path) -> str:
    """Render customer-facing report HTML without mojibake or internal placeholders."""
    report_path = root / "platform_outputs" / project / "pipeline_reports" / "latest_pipeline_report.json"
    history_path = root / "platform_outputs" / project / "pipeline_reports" / "scan_history.json"
    report = _read_json_file(report_path, {})
    report = report if isinstance(report, dict) else {}
    history = _read_json_file(history_path, [])
    history = history if isinstance(history, list) else []
    findings = _report_findings(report)
    stage1 = report.get("stage1_industry") if isinstance(report.get("stage1_industry"), dict) else {}
    stage3 = report.get("stage3_impact_analysis") if isinstance(report.get("stage3_impact_analysis"), dict) else {}
    generated_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

    rows = []
    for finding in findings:
        evidence = finding.get("evidence")
        if isinstance(evidence, dict):
            evidence_text = evidence.get("summary") or evidence.get("actual") or evidence.get("path") or json.dumps(evidence, ensure_ascii=False)[:240]
        else:
            evidence_text = evidence or finding.get("actual") or finding.get("description") or "待补充证据"
        rows.append(
            "<tr>"
            f"<td>{_html_text(finding.get('severity') or 'P2', 40)}</td>"
            f"<td>{_html_text(finding.get('title') or finding.get('description') or '未命名发现', 180)}</td>"
            f"<td>{_html_text(finding.get('category') or finding.get('defect_family') or '业务质量', 120)}</td>"
            f"<td>{_html_text(finding.get('confidence_score') or finding.get('score') or '-', 40)}</td>"
            f"<td>{_html_text(evidence_text, 260)}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan='5'>暂无客户可交付缺陷；请查看覆盖缺口、测试数据缺口和内部线索。</td></tr>")

    history_rows = []
    for item in history[-10:]:
        if not isinstance(item, dict):
            continue
        history_rows.append(
            "<tr>"
            f"<td>{_html_text(item.get('timestamp_utc') or item.get('timestamp') or '-', 80)}</td>"
            f"<td>{_html_text(item.get('status') or '-', 60)}</td>"
            f"<td>{_html_text(item.get('total_findings') or item.get('findings') or 0, 40)}</td>"
            f"<td>{_html_text(item.get('p0p1_count') or item.get('critical_bugs') or 0, 40)}</td>"
            f"<td>{_html_text(item.get('industry') or item.get('project') or '-', 120)}</td>"
            "</tr>"
        )
    if not history_rows:
        history_rows.append("<tr><td colspan='5'>暂无历史扫描记录。</td></tr>")

    total = len(findings)
    p0p1 = sum(1 for finding in findings if str(finding.get("severity") or "") in {"P0", "P1"})
    llm_count = stage3.get("llm_powered", 0) if isinstance(stage3, dict) else 0
    object_count = stage1.get("object_count", 0) if isinstance(stage1, dict) else 0

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>QualiBug AI 缺陷扫描报告 - {_html_text(project, 120)}</title>
<style>
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:980px;margin:40px auto;padding:0 20px;color:#1e293b;background:#f8fafc}}
h1{{font-size:26px;border-bottom:2px solid #3b82f6;padding-bottom:12px}}
h2{{font-size:18px;margin-top:32px;color:#334155}}
table{{width:100%;border-collapse:collapse;margin:16px 0;font-size:13px;background:#fff}}
th,td{{text-align:left;padding:9px 12px;border-bottom:1px solid #e2e8f0;vertical-align:top}}
th{{background:#f1f5f9;font-weight:700;color:#475569}}
.metric{{display:inline-block;text-align:center;padding:16px 24px;border-radius:8px;margin:8px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
.metric strong{{display:block;font-size:28px;color:#3b82f6}}
.metric span{{font-size:12px;color:#64748b}}
.notice{{background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:12px 14px;margin:16px 0;color:#1e40af}}
.footer{{margin-top:32px;font-size:12px;color:#64748b;border-top:1px solid #e2e8f0;padding-top:12px}}
</style>
</head>
<body>
<h1>QualiBug AI 缺陷扫描报告</h1>
<p>项目：<strong>{_html_text(project, 120)}</strong> · 生成时间：<strong>{_html_text(generated_at, 40)}</strong></p>
<div class="notice">本报告仅展示客户可读结果。未复现、证据不足或仍需授权的线索应保留在内部线索区，不作为客户可交付缺陷声明。</div>
<div>
  <div class="metric"><span>发现总数</span><strong>{total}</strong></div>
  <div class="metric"><span>P0/P1</span><strong>{p0p1}</strong></div>
  <div class="metric"><span>LLM 分析</span><strong>{_html_text(llm_count, 20)}</strong></div>
  <div class="metric"><span>对象/接口</span><strong>{_html_text(object_count, 20)}</strong></div>
</div>
<h2>缺陷发现列表</h2>
<table><tr><th>严重度</th><th>标题</th><th>类别</th><th>置信度</th><th>证据摘要</th></tr>{''.join(rows)}</table>
<h2>扫描历史（最近 10 次）</h2>
<table><tr><th>时间</th><th>状态</th><th>发现数</th><th>P0/P1</th><th>对象</th></tr>{''.join(history_rows)}</table>
<div class="footer">QualiBug AI Enterprise Edition · 私有化部署 · 报告版本 {PRODUCT_VERSION}</div>
</body>
</html>"""


def contains_mojibake(text: str) -> bool:
    return any(marker in str(text or "") for marker in MOJIBAKE_MARKERS)


def install_customer_report_patch() -> None:
    """Replace legacy customer report HTML that contained mojibake strings."""
    if getattr(_service, "_CUSTOMER_REPORT_PATCHED", False):
        return
    original_renderer = getattr(_service.PrivatePilotHandler, "_render_report_html")

    def _render_report_html_clean(self: Any, project: str, root: Path) -> Any:
        return self._html(render_customer_safe_report_html(project, root))

    _service.PrivatePilotHandler._render_report_html = _render_report_html_clean
    _service._ORIGINAL_RENDER_REPORT_HTML = original_renderer  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCHED = True  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCH_SOURCE = PATCH_SOURCE  # type: ignore[attr-defined]


def restore_customer_report_patch() -> None:
    original_renderer = getattr(_service, "_ORIGINAL_RENDER_REPORT_HTML", None)
    if original_renderer is not None:
        _service.PrivatePilotHandler._render_report_html = original_renderer
    _service._ORIGINAL_RENDER_REPORT_HTML = None  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCHED = False  # type: ignore[attr-defined]
    _service._CUSTOMER_REPORT_PATCH_SOURCE = ""  # type: ignore[attr-defined]


def install_browser_ui_smoke_patch() -> None:
    """Attach non-blocking browser UI smoke evidence to patched scans."""
    if getattr(_service, "_BROWSER_UI_SMOKE_PATCHED", False):
        return
    from ai_test_asset_center import __main__ as scanner_module
    from ai_test_asset_center.browser_ui_smoke import attach_browser_ui_health

    original_scan = getattr(scanner_module, "scan")

    def _scan_with_browser_ui_smoke(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = original_scan(*args, **kwargs)
        if not isinstance(result, dict):
            return result
        project = _scan_project_from_args(args, kwargs)
        root = _scan_root_from_args(args, kwargs)
        base_url = _scan_base_url_from_context(kwargs)
        try:
            return attach_browser_ui_health(result, project=project, root=root, base_url=base_url)
        except Exception as exc:
            updated = dict(result)
            updated["browser_ui_health"] = {
                "schema_version": "browser-ui-smoke-v1",
                "enabled": False,
                "status": "error",
                "reason_code": "E_BROWSER_UI_RUNTIME_ERROR",
                "message": str(exc)[:500],
                "page_count": 0,
                "reachable_page_count": 0,
                "console_error_count": 0,
                "network_error_count": 0,
                "screenshot_count": 0,
                "pages": [],
                "evidence_files": [],
            }
            return updated

    scanner_module.scan = _scan_with_browser_ui_smoke
    _service._ORIGINAL_BROWSER_UI_SMOKE_SCAN = original_scan  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCHED = True  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCH_SOURCE = PATCH_SOURCE  # type: ignore[attr-defined]


def restore_browser_ui_smoke_patch() -> None:
    original_scan = getattr(_service, "_ORIGINAL_BROWSER_UI_SMOKE_SCAN", None)
    if original_scan is not None:
        from ai_test_asset_center import __main__ as scanner_module

        scanner_module.scan = original_scan
    _service._ORIGINAL_BROWSER_UI_SMOKE_SCAN = None  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCHED = False  # type: ignore[attr-defined]
    _service._BROWSER_UI_SMOKE_PATCH_SOURCE = ""  # type: ignore[attr-defined]


def install_deployment_contract_patch() -> None:
    """Normalize health/version behavior for patched deployments."""
    if getattr(_service, "_DEPLOYMENT_CONTRACT_PATCHED", False):
        return
    original_do_get = getattr(_service.PrivatePilotHandler, "do_GET")

    def _do_get_with_deployment_contract(self: Any) -> Any:
        parsed = urlparse(self.path)
        if parsed.path in {CANONICAL_HEALTH_PATH, LEGACY_HEALTH_PATH}:
            return self._json(_health_payload(self))
        return original_do_get(self)

    _service.PrivatePilotHandler.do_GET = _do_get_with_deployment_contract
    _service._ORIGINAL_DEPLOYMENT_DO_GET = original_do_get  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCHED = True  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCH_SOURCE = PATCH_SOURCE  # type: ignore[attr-defined]


def restore_deployment_contract_patch() -> None:
    original_do_get = getattr(_service, "_ORIGINAL_DEPLOYMENT_DO_GET", None)
    if original_do_get is not None:
        _service.PrivatePilotHandler.do_GET = original_do_get
    _service._ORIGINAL_DEPLOYMENT_DO_GET = None  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCHED = False  # type: ignore[attr-defined]
    _service._DEPLOYMENT_CONTRACT_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    restore_browser_ui_smoke_patch()
    restore_customer_report_patch()


def run_server() -> None:
    install_customer_delivery_gate_patch()
    install_browser_ui_smoke_patch()
    install_customer_report_patch()
    install_deployment_contract_patch()
    _service.run_server()


if __name__ == "__main__":
    run_server()
