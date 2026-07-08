from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from .real_project_onboarding import ROOT, _html_escape, _safe_project_id, load_real_project_config
from .regression_suite_builder import build_regression_suite

PRIVATE_MARKERS = {
    "private_ground_truth",
    "ground_truth_bugs",
    "bug_sets",
    "enabled_bugs",
    "current_bug_set",
    "bug_instance_id",
}
DESTRUCTIVE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
DEFAULT_MODE = "release"
MODE_NAMES = {"smoke": "Smoke 快速回归", "release": "Release 发布回归", "full": "Full 完整回归"}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _load_json_safe(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return default
    return default


def _safe_text(value: Any, limit: int = 3000) -> str:
    text = str(value or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    return text[:limit]


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "y", "on"}


def _is_destructive(probe: dict[str, Any]) -> bool:
    method = str(probe.get("method") or "GET").upper()
    risk_type = str(probe.get("risk_type") or "").lower()
    return bool(probe.get("destructive")) or method in DESTRUCTIVE_METHODS or risk_type in {"payment", "refund", "duplicate_submit", "idempotency", "concurrency", "delete", "cancel_order"}


def _load_or_build_suite(project: str, root: Path, options: dict[str, Any]) -> dict[str, Any]:
    suite_path = root / "platform_outputs" / project / "regression_suite" / "regression_suite.json"
    suite = _load_json_safe(suite_path, {})
    if isinstance(suite, dict) and suite.get("modes"):
        return suite
    return build_regression_suite(project, root, options={"allow_destructive_regression": bool(options.get("allow_destructive_regression"))})


def _headers_from_accounts(project: str, root: Path) -> dict[str, str]:
    # Keep this intentionally conservative. Real auth/token orchestration belongs to onboarding.
    # The regression runner can still use a manually supplied bearer token from test_accounts.json.
    accounts = _load_json_safe(root / "platform_inputs" / project / "test_accounts.json", {})
    headers = {"User-Agent": "AI-Test-Asset-Center-RegressionRunner/1.0", "Accept": "application/json,text/plain,*/*"}
    if isinstance(accounts, dict):
        token = accounts.get("token") or accounts.get("bearer_token")
        if not token and isinstance(accounts.get("normal_user"), dict):
            token = accounts["normal_user"].get("token") or accounts["normal_user"].get("bearer_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    return headers


def _encode_request_body(probe: dict[str, Any]) -> bytes | None:
    request_body = probe.get("request_body")
    if request_body is None:
        return None
    if isinstance(request_body, (dict, list)):
        return json.dumps(request_body, ensure_ascii=False).encode("utf-8")
    if isinstance(request_body, (str, int, float, bool)):
        return str(request_body).encode("utf-8")
    return None


def _execute_http_probe(probe: dict[str, Any], cfg: dict[str, Any], project: str, root: Path, timeout: float) -> dict[str, Any]:
    base_url = str(cfg.get("base_url") or "").strip()
    method = str(probe.get("method") or "GET").upper()
    path = str(probe.get("path") or "/")
    if not base_url:
        return {"reachable": False, "error": "base_url_missing", "status_code": None, "body_excerpt": ""}
    url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    headers = _headers_from_accounts(project, root)
    data = None
    if method in {"POST", "PUT", "PATCH"}:
        headers["Content-Type"] = "application/json"
        data = _encode_request_body(probe) or b"{}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user supplied test env URL by design
            raw = resp.read(4096)
            body = raw.decode("utf-8", errors="replace")
            return {"reachable": True, "url": url, "status_code": int(resp.status), "body_excerpt": _safe_text(body, 1200), "error": ""}
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace") if exc.fp else ""
        return {"reachable": True, "url": url, "status_code": int(exc.code), "body_excerpt": _safe_text(body, 1200), "error": ""}
    except Exception as exc:
        return {"reachable": False, "url": url, "status_code": None, "body_excerpt": "", "error": f"{type(exc).__name__}: {exc}"}


def _expected_status_from_text(expected: str) -> int | None:
    text = (expected or "").lower()
    patterns = [
        (403, ["403", "forbidden", "禁止", "无权", "没有权限", "权限", "rbac"]),
        (401, ["401", "unauthorized", "未登录", "未认证"]),
        (400, ["400", "bad request", "非法", "校验失败", "参数错误"]),
        (409, ["409", "conflict", "冲突", "重复", "幂等"]),
    ]
    for code, keys in patterns:
        if any(k in text for k in keys):
            return code
    return None


def _judge_probe(probe: dict[str, Any], execution: dict[str, Any], skipped: bool = False, skip_reason: str = "") -> dict[str, Any]:
    severity = str(probe.get("severity") or "P2").upper()
    issue_id = _safe_text(probe.get("issue_id"), 120)
    if skipped:
        status = "skipped"
        passed = False
        reason = skip_reason or "探针被跳过。"
    elif not execution.get("reachable"):
        status = "needs_review"
        passed = False
        reason = "被测系统不可访问或请求失败，无法自动判断回归结果。"
    else:
        expected_status = _expected_status_from_text(str(probe.get("expected") or ""))
        actual_status = execution.get("status_code")
        if expected_status is not None:
            if actual_status == expected_status:
                status = "passed"
                passed = True
                reason = f"响应状态 {actual_status} 符合预期状态 {expected_status}。"
            else:
                status = "failed"
                passed = False
                reason = f"响应状态 {actual_status} 不符合预期状态 {expected_status}，疑似回归失败。"
        else:
            # No strong oracle: collect evidence but require QA confirmation.
            status = "needs_review"
            passed = False
            reason = "该探针缺少可自动判定的强断言，已采集证据，建议 QA 复核。"
    return {
        "regression_probe_id": probe.get("regression_probe_id"),
        "issue_id": issue_id,
        "title": _safe_text(probe.get("title"), 260),
        "module": _safe_text(probe.get("module"), 80),
        "risk_type": _safe_text(probe.get("risk_type"), 120),
        "severity": severity if severity in {"P0", "P1", "P2", "P3"} else "P2",
        "method": str(probe.get("method") or "GET").upper(),
        "path": _safe_text(probe.get("path") or "/", 500),
        "status": status,
        "passed": passed,
        "reason": reason,
        "expected": _safe_text(probe.get("expected"), 1000),
        "execution": {
            "reachable": bool(execution.get("reachable")),
            "status_code": execution.get("status_code"),
            "error": _safe_text(execution.get("error"), 500),
            "body_excerpt": _safe_text(execution.get("body_excerpt"), 1200),
        },
    }


def _count(items: list[dict[str, Any]], status: str) -> int:
    return sum(1 for i in items if i.get("status") == status)


def _count_by(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        value = str(item.get(key) or "unknown")
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False).lower()
    leaks = [m for m in PRIVATE_MARKERS if m.lower() in text]
    return {"passed": not leaks, "checked": True, "leak_count": len(leaks)}


def _build_ci_feedback(project: str, mode: str, summary: dict[str, Any], failures: list[dict[str, Any]]) -> dict[str, Any]:
    p0p1_failures = [f for f in failures if f.get("severity") in {"P0", "P1"}]
    p2_failures = [f for f in failures if f.get("severity") == "P2"]
    needs_review = int(summary.get("needs_review_count") or 0)
    total_probe_count = int(summary.get("total_probe_count") or 0)
    if total_probe_count <= 0:
        gate_status = "manual_approval_required"
        exit_code = 1
        release_gate_override = "continue_regression"
    elif p0p1_failures:
        gate_status = "failed"
        exit_code = 2
        release_gate_override = "block_release"
    elif p2_failures or needs_review > 0:
        gate_status = "manual_approval_required"
        exit_code = 1
        release_gate_override = "hold_for_review"
    else:
        gate_status = "passed"
        exit_code = 0
        release_gate_override = "allow_release"
    reopen_issue_ids = [f.get("issue_id") for f in failures if f.get("issue_id")]
    return {
        "project_id": project,
        "suite_mode": mode,
        "gate_status": gate_status,
        "exit_code": exit_code,
        "release_gate_override": release_gate_override,
        "should_block_release": gate_status == "failed",
        "manual_review_required": gate_status == "manual_approval_required",
        "reopen_issue_ids": reopen_issue_ids,
        "regression_failure_count": len(failures),
        "p0_p1_regression_failure_count": len(p0p1_failures),
        "p2_regression_failure_count": len(p2_failures),
        "needs_review_count": needs_review,
        "ci_message": (
            "当前回归套件为空，不能作为发布依据，需先补齐回归探针后再执行真实验证。" if total_probe_count <= 0 else
            "P0/P1 回归失败，建议阻断发布。" if gate_status == "failed" else
            "存在 P2 回归失败或无法自动判定项，建议人工审批。" if gate_status == "manual_approval_required" else
            "回归套件通过，允许继续发布。"
        ),
    }


def _append_regression_history(project: str, root: Path, result: dict[str, Any]) -> list[dict[str, Any]]:
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    ci_feedback = result.get("ci_feedback") if isinstance(result.get("ci_feedback"), dict) else {}
    items = result.get("items") if isinstance(result.get("items"), list) else []
    run_entry = {
        "generated_at": summary.get("generated_at"),
        "suite_mode": summary.get("suite_mode"),
        "suite_mode_label": summary.get("suite_mode_label"),
        "gate_status": ci_feedback.get("gate_status"),
        "ci_message": ci_feedback.get("ci_message"),
        "summary": {
            "total_probe_count": summary.get("total_probe_count"),
            "executed_count": summary.get("executed_count"),
            "passed_count": summary.get("passed_count"),
            "failed_count": summary.get("failed_count"),
            "needs_review_count": summary.get("needs_review_count"),
            "skipped_count": summary.get("skipped_count"),
        },
        "items": [
            {
                "issue_id": item.get("issue_id"),
                "regression_probe_id": item.get("regression_probe_id"),
                "title": item.get("title"),
                "path": item.get("path"),
                "method": item.get("method"),
                "severity": item.get("severity"),
                "status": item.get("status"),
                "reason": item.get("reason"),
            }
            for item in items
            if isinstance(item, dict)
        ],
    }
    history_path = root / "platform_outputs" / project / "regression_run" / "regression_run_history.json"
    history = _load_json_safe(history_path, [])
    if not isinstance(history, list):
        history = []
    history.append(run_entry)
    history = history[-30:]
    _write_json(history_path, history)
    _write_json(root / "platform_workspace" / project / "defect_discovery" / "regression_run_history.json", history)
    return history


def _render_failure_report(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    ci = result.get("ci_feedback") or {}
    status = str(ci.get("gate_status") or "manual_approval_required")
    cls = "danger" if status == "failed" else "warn" if status == "manual_approval_required" else "ok"
    cards = "".join(
        f"<div class='card'><span>{_html_escape(k)}</span><b>{_html_escape(v)}</b></div>"
        for k, v in {
            "套件模式": summary.get("suite_mode"),
            "总探针": summary.get("total_probe_count"),
            "执行": summary.get("executed_count"),
            "通过": summary.get("passed_count"),
            "失败": summary.get("failed_count"),
            "需复核": summary.get("needs_review_count"),
            "跳过": summary.get("skipped_count"),
            "CI 状态": ci.get("gate_status"),
        }.items()
    )
    failure_rows = []
    for item in result.get("failures", [])[:120]:
        failure_rows.append(
            "<tr>"
            f"<td>{_html_escape(item.get('severity'))}</td>"
            f"<td>{_html_escape(item.get('module'))}</td>"
            f"<td>{_html_escape(item.get('risk_type'))}</td>"
            f"<td>{_html_escape(item.get('method'))} {_html_escape(item.get('path'))}</td>"
            f"<td>{_html_escape(item.get('title'))}</td>"
            f"<td>{_html_escape(item.get('reason'))}</td>"
            "</tr>"
        )
    review_rows = []
    for item in result.get("items", []):
        if item.get("status") == "needs_review":
            review_rows.append(
                "<tr>"
                f"<td>{_html_escape(item.get('severity'))}</td>"
                f"<td>{_html_escape(item.get('method'))} {_html_escape(item.get('path'))}</td>"
                f"<td>{_html_escape(item.get('title'))}</td>"
                f"<td>{_html_escape(item.get('reason'))}</td>"
                "</tr>"
            )
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>Regression Run Result</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.badge{{display:inline-block;padding:6px 12px;border-radius:999px;font-weight:700}}.danger{{background:#fee2e2;color:#991b1b}}.warn{{background:#fef3c7;color:#92400e}}.ok{{background:#dcfce7;color:#166534}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{background:#fafafa;border:1px solid #e5e7eb;border-radius:14px;padding:14px}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:22px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}code{{background:#f3f4f6;padding:2px 6px;border-radius:6px}}@media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}</style></head><body>
<section class='hero'><span class='badge {cls}'>Phase32 Regression Runner</span><h1>{_html_escape(summary.get('project_name'))} · 回归执行结果</h1><p>CI 反馈：<b>{_html_escape(ci.get('ci_message'))}</b> · Exit Code：<code>{_html_escape(ci.get('exit_code'))}</code></p><p>生成时间：{_html_escape(summary.get('generated_at'))} · 私有数据隔离：{_html_escape(summary.get('private_leak_check_passed'))}</p></section>
<section class='panel'><h2>执行概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>回归失败</h2><table><thead><tr><th>等级</th><th>模块</th><th>风险</th><th>接口</th><th>标题</th><th>原因</th></tr></thead><tbody>{''.join(failure_rows) or '<tr><td colspan="6">暂无失败回归</td></tr>'}</tbody></table></section>
<section class='panel'><h2>需要 QA 复核</h2><table><thead><tr><th>等级</th><th>接口</th><th>标题</th><th>原因</th></tr></thead><tbody>{''.join(review_rows[:120]) or '<tr><td colspan="4">暂无需要复核项</td></tr>'}</tbody></table></section>
<section class='panel'><h2>CI/CD 使用建议</h2><p>在流水线中执行：<code>set REAL_PROJECT_ID={_html_escape(result.get('project_id'))} &amp;&amp; set REGRESSION_SUITE_MODE={_html_escape(summary.get('suite_mode'))} &amp;&amp; RUN_REGRESSION_RUNNER.cmd</code></p><p>输出文件：<code>platform_outputs\\{_html_escape(result.get('project_id'))}\\regression_run\\regression_ci_feedback.json</code></p></section>
</body></html>"""


def run_regression_suite(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    mode = str(options.get("mode") or os.environ.get("REGRESSION_SUITE_MODE") or DEFAULT_MODE).lower()
    if mode not in {"smoke", "release", "full"}:
        mode = DEFAULT_MODE
    allow_destructive = bool(options.get("allow_destructive_execution") or _bool_env("ALLOW_DESTRUCTIVE_REGRESSION_RUN", False))
    dry_run = bool(options.get("dry_run", False) or _bool_env("REGRESSION_RUN_DRY_RUN", False))
    cfg = load_real_project_config(project, root)
    timeout = float(options.get("timeout_seconds") or cfg.get("request_timeout_seconds") or 3)
    suite = _load_or_build_suite(project, root, {"allow_destructive_regression": allow_destructive})
    suite_mode = suite.get("modes", {}).get(mode, {}) if isinstance(suite.get("modes"), dict) else {}
    probes = [p for p in (suite_mode.get("items") or []) if isinstance(p, dict)]
    items: list[dict[str, Any]] = []
    for raw in probes:
        destructive = _is_destructive(raw)
        if destructive and not allow_destructive:
            items.append(_judge_probe(raw, {}, skipped=True, skip_reason="默认跳过破坏性回归探针。"))
            continue
        if dry_run:
            execution = {"reachable": True, "status_code": _expected_status_from_text(str(raw.get("expected") or "")) or 200, "body_excerpt": "dry run", "error": ""}
        else:
            execution = _execute_http_probe(raw, cfg, project, root, timeout)
        items.append(_judge_probe(raw, execution))
    failures = [i for i in items if i.get("status") == "failed"]
    summary = {
        "phase": "phase32_regression_runner_ci_feedback",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "suite_mode": mode,
        "suite_mode_label": MODE_NAMES.get(mode, mode),
        "dry_run": dry_run,
        "allow_destructive_execution": allow_destructive,
        "total_probe_count": len(probes),
        "executed_count": sum(1 for i in items if i.get("status") not in {"skipped"}),
        "passed_count": _count(items, "passed"),
        "failed_count": _count(items, "failed"),
        "needs_review_count": _count(items, "needs_review"),
        "skipped_count": _count(items, "skipped"),
        "p0_p1_failed_count": sum(1 for i in failures if i.get("severity") in {"P0", "P1"}),
        "module_distribution": _count_by(items, "module"),
        "risk_distribution": _count_by(items, "risk_type"),
        "severity_distribution": _count_by(items, "severity"),
    }
    ci_feedback = _build_ci_feedback(project, mode, summary, failures)
    result = {
        "phase": "phase32_regression_runner_ci_feedback",
        "project_id": project,
        "summary": summary,
        "items": items,
        "failures": failures,
        "ci_feedback": ci_feedback,
        "regression_suite_ref": f"platform_outputs/{project}/regression_suite/regression_suite.json",
        "governance": {
            "real_project_mode": True,
            "uses_regression_suite": True,
            "does_not_require_benchmark_answers": True,
            "safe_by_default": not allow_destructive,
        },
    }
    private = _private_leak_check(result)
    result["private_leak_check"] = private
    summary["private_leak_check_passed"] = private["passed"]

    out_dir = root / "platform_outputs" / project / "regression_run"
    ws_dir = root / "platform_workspace" / project / "defect_discovery"
    _write_json(out_dir / "regression_run_result.json", result)
    _write_json(out_dir / "regression_run_summary.json", summary)
    _write_json(out_dir / "regression_ci_feedback.json", ci_feedback)
    _write_json(out_dir / "regression_failures_for_issue_sync.json", {"project_id": project, "items": failures})
    _write_text(out_dir / "regression_failure_report.html", _render_failure_report(result))
    _write_json(ws_dir / "regression_run_result.json", result)
    _write_json(ws_dir / "regression_ci_feedback.json", ci_feedback)
    history = _append_regression_history(project, root, result)
    result["history_ref"] = f"platform_outputs/{project}/regression_run/regression_run_history.json"
    result["history_size"] = len(history)
    return result


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    mode = os.environ.get("REGRESSION_SUITE_MODE") or (argv[1] if len(argv) > 1 else DEFAULT_MODE)
    allow = _bool_env("ALLOW_DESTRUCTIVE_REGRESSION_RUN", False)
    dry_run = _bool_env("REGRESSION_RUN_DRY_RUN", False)
    result = run_regression_suite(project, options={"mode": mode, "allow_destructive_execution": allow, "dry_run": dry_run})
    print(json.dumps({"ok": True, "project_id": project, "summary": result.get("summary"), "ci_feedback": result.get("ci_feedback"), "private_leak_check": result.get("private_leak_check")}, ensure_ascii=False, indent=2))
    if _bool_env("REGRESSION_RUNNER_NO_FAIL", False):
        return 0
    return int((result.get("ci_feedback") or {}).get("exit_code") or 0)


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
