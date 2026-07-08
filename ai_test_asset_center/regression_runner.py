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
from .enterprise_project_config import (
    match_production_data_exclusion,
    _load_execution_safety_boundary,
)

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


def _is_production_data_blocked(boundary: dict[str, Any], probe: dict[str, Any]) -> str:
    """主链 9 × 主链 1: a regression probe whose path/risk_type matches a
    customer-declared production-data-exclusion must NEVER be sent — not in real
    mode and not even in dry_run. Returns the matched reason or '' when allowed."""
    if not isinstance(boundary, dict):
        return ""
    return match_production_data_exclusion(
        boundary, str(probe.get("path") or ""), str(probe.get("risk_type") or "")
    ) or ""


def _load_or_build_suite(project: str, root: Path, options: dict[str, Any]) -> dict[str, Any]:
    suite_path = root / "platform_outputs" / project / "regression_suite" / "regression_suite.json"
    suite = _load_json_safe(suite_path, {})
    if isinstance(suite, dict) and suite.get("modes"):
        return suite
    return build_regression_suite(project, root, options={"allow_destructive_regression": bool(options.get("allow_destructive_regression"))})


def _headers_from_accounts(project: str, root: Path) -> dict[str, str]:
    # Keep this intentionally conservative and industry-agnostic. Real auth/token
    # orchestration belongs to onboarding. Never hardcode a role name: prefer a
    # top-level token, then any account explicitly flagged default, then the first
    # account entry that carries a usable token.
    accounts = _load_json_safe(root / "platform_inputs" / project / "test_accounts.json", {})
    headers = {"User-Agent": "AI-Test-Asset-Center-RegressionRunner/1.0", "Accept": "application/json,text/plain,*/*"}

    def _token_of(entry: Any) -> str:
        if isinstance(entry, dict):
            return str(entry.get("token") or entry.get("bearer_token") or "").strip()
        return ""

    token = ""
    if isinstance(accounts, dict):
        token = str(accounts.get("token") or accounts.get("bearer_token") or "").strip()
        if not token:
            entries = [v for v in accounts.values() if isinstance(v, dict)]
            default_entries = [v for v in entries if v.get("default") or v.get("is_default")]
            for entry in default_entries + entries:
                token = _token_of(entry)
                if token:
                    break
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


def _execute_http_probe(probe: dict[str, Any], cfg: dict[str, Any], project: str, root: Path, timeout: float, safety_boundary: dict[str, Any] | None = None) -> dict[str, Any]:
    base_url = str(cfg.get("base_url") or "").strip()
    method = str(probe.get("method") or "GET").upper()
    path = str(probe.get("path") or "/")
    # 主链 9 × 主链 1: defense-in-depth — even if called directly (bypassing the
    # loop-level guard), a probe targeting a production-data-exclusion path is
    # never sent. The request simply never leaves the process.
    _block = match_production_data_exclusion(
        safety_boundary or {}, path, str(probe.get("risk_type") or "")
    )
    if _block:
        return {
            "reachable": False,
            "error": "production_data_blocked",
            "status_code": None,
            "body_excerpt": "",
            "production_data_blocked": True,
            "block_reason": _block,
            "url": None,
        }
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


def _reverify_confirmed_findings(project: str, root: Path, cfg: dict[str, Any], safety_boundary: dict[str, Any], timeout: float, dry_run: bool) -> dict[str, Any]:
    """主链 9 Gap B2: post-fix regression verification of already-confirmed defects.

    Consumes the 主链 6 product (``confirmed_findings.json`` — deliverable confirmed
    defects keyed by stable evidence_id) and the 主链 7 product (``evidence_chains/
    {evidence_id}.json`` for invariant context), replays each reproduction request
    under the SAME production-data safety boundary, and judges:

    * ``resolved``     — the fix removed the defect (replay status differs from the
                         recorded buggy status code, or the probe is now healthy).
    * ``persisted``    — the defect still reproduces (replay returns the same buggy
                         status code) → a real regression that must block release.
    * ``blocked``      — the reproduction path is excluded by the safety boundary;
                         cannot be auto-verified, surfaced for manual review.
    * ``needs_review`` — the system was unreachable / no oracle, or a dry run.

    Returns a dict with the per-defect verdicts and a summary count block.
    """
    ws = root / "platform_workspace" / project / "defect_discovery"
    ledger = _load_json_safe(ws / "confirmed_findings.json", {})
    if not isinstance(ledger, dict) or not ledger:
        return {"consumed": False, "verdicts": [], "counts": {"total": 0, "resolved": 0, "persisted": 0, "blocked": 0, "needs_review": 0}}
    verdicts: list[dict[str, Any]] = []
    c = {"total": 0, "resolved": 0, "persisted": 0, "blocked": 0, "needs_review": 0}
    for evidence_id, defect in ledger.items():
        if not isinstance(defect, dict):
            continue
        c["total"] += 1
        repro = defect.get("reproduction") if isinstance(defect.get("reproduction"), dict) else {}
        method = str(repro.get("method") or "GET").upper()
        path = str(repro.get("path") or "").strip()
        buggy_status = int(defect.get("buggy_status_code") or 0)
        # Consume 主链 7 evidence chain for invariant context (best-effort).
        invariant_ctx = ""
        chain = _load_json_safe(ws / "evidence_chains" / f"{evidence_id}.json", {})
        if isinstance(chain, dict):
            for layer in (chain.get("layers") if isinstance(chain.get("layers"), list) else []):
                if isinstance(layer, dict) and layer.get("invariant_evaluation"):
                    invariant_ctx = str(layer.get("invariant_evaluation") or "")
                    break
        probe = {"method": method, "path": path, "risk_type": str(repro.get("risk_type") or ""), "expected": str(defect.get("expected") or "")}
        # Hard safety boundary applies to re-verification exactly as to discovery.
        block = _is_production_data_blocked(safety_boundary, probe)
        if block:
            verdicts.append({
                "evidence_id": evidence_id,
                "title": str(defect.get("title") or ""),
                "severity": str(defect.get("severity") or "P2"),
                "status": "blocked",
                "buggy_status_code": buggy_status,
                "current_status_code": None,
                "reason": f"复现路径命中生产数据禁触边界，已跳过自动复验：{block}",
                "invariant_context": invariant_ctx,
            })
            c["blocked"] += 1
            continue
        if dry_run:
            verdicts.append({
                "evidence_id": evidence_id,
                "title": str(defect.get("title") or ""),
                "severity": str(defect.get("severity") or "P2"),
                "status": "needs_review",
                "buggy_status_code": buggy_status,
                "current_status_code": None,
                "reason": "dry run 模式下未发起真实复现请求，需 QA 复核。",
                "invariant_context": invariant_ctx,
            })
            c["needs_review"] += 1
            continue
        execution = _execute_http_probe(probe, cfg, project, root, timeout, safety_boundary)
        if not execution.get("reachable"):
            verdicts.append({
                "evidence_id": evidence_id,
                "title": str(defect.get("title") or ""),
                "severity": str(defect.get("severity") or "P2"),
                "status": "needs_review",
                "buggy_status_code": buggy_status,
                "current_status_code": execution.get("status_code"),
                "reason": f"被测系统不可访问（{execution.get('error')}），无法判定修复结果。",
                "invariant_context": invariant_ctx,
            })
            c["needs_review"] += 1
            continue
        current = int(execution.get("status_code") or 0)
        if buggy_status and current == buggy_status:
            status = "persisted"
            reason = f"复现请求仍返回缺陷状态码 {current}，缺陷未修复（疑似回归）。"
        else:
            status = "resolved"
            reason = f"复现请求返回 {current}，与缺陷状态码 {buggy_status} 不同，缺陷已修复。"
        verdicts.append({
            "evidence_id": evidence_id,
            "title": str(defect.get("title") or ""),
            "severity": str(defect.get("severity") or "P2"),
            "status": status,
            "buggy_status_code": buggy_status,
            "current_status_code": current,
            "reason": reason,
            "invariant_context": invariant_ctx,
        })
        c[status] += 1
    return {"consumed": True, "verdicts": verdicts, "counts": c}


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
    # 主链 9 × 主链 1: load the SAME production-data safety boundary the v12
    # executor honors, so regression probes cannot touch customer-excluded paths.
    safety_boundary = _load_execution_safety_boundary(project, root)
    suite = _load_or_build_suite(project, root, {"allow_destructive_regression": allow_destructive})
    suite_mode = suite.get("modes", {}).get(mode, {}) if isinstance(suite.get("modes"), dict) else {}
    probes = [p for p in (suite_mode.get("items") or []) if isinstance(p, dict)]
    items: list[dict[str, Any]] = []
    production_data_blocked = 0
    for raw in probes:
        # 主链 9 × 主链 1: hard safety boundary — never probe production-data
        # exclusion paths, even in dry_run. This is checked before destructive/
        # dry_run branching so the guard holds in every mode.
        _block = _is_production_data_blocked(safety_boundary, raw)
        if _block:
            _blocked = _judge_probe(raw, {}, skipped=True, skip_reason=_block)
            _blocked["production_data_blocked"] = True
            items.append(_blocked)
            production_data_blocked += 1
            continue
        destructive = _is_destructive(raw)
        if destructive and not allow_destructive:
            items.append(_judge_probe(raw, {}, skipped=True, skip_reason="默认跳过破坏性回归探针。"))
            continue
        if dry_run:
            execution = {"reachable": True, "status_code": _expected_status_from_text(str(raw.get("expected") or "")) or 200, "body_excerpt": "dry run", "error": ""}
        else:
            execution = _execute_http_probe(raw, cfg, project, root, timeout, safety_boundary)
        items.append(_judge_probe(raw, execution))
    failures = [i for i in items if i.get("status") == "failed"]
    # 主链 9 Gap B2: re-verify already-confirmed (主链 6) defects after the fix,
    # consuming both the confirmed-findings ledger and the 主链 7 evidence chains.
    reverification = _reverify_confirmed_findings(project, root, cfg, safety_boundary, timeout, dry_run)
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
        "production_data_blocked_count": production_data_blocked,
        "reverification": dict(reverification.get("counts") or {}),
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
        # 主链 9 Gap B2: post-fix re-verification verdicts for 主链 6/7 products.
        "reverification": reverification,
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
    _write_json(ws_dir / "regression_reverification.json", reverification)
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
