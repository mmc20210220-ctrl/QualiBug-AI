from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _html_escape, _join_url, _load_json, _safe_project_id, load_real_project_config
from .real_project_defect_discovery import _fetch_json_or_text, _login
from .issue_sync_exporter import build_issue_export_bundle

PRIVATE_MARKERS = {
    "private_ground_truth",
    "ground_truth_bugs",
    "bug_sets",
    "enabled_bugs",
    "current_bug_set",
    "bug_instance_id",
}
DESTRUCTIVE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
FIXED_STATUS_WORDS = {"fixed", "resolved", "verified_fixed", "修复", "已修复", "已验证修复"}
REOPEN_STATUS_WORDS = {"reopen", "still_failing", "not_fixed", "仍失败", "未修复"}


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _safe_text(value: Any, limit: int = 2000) -> str:
    text = str(value or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    return text[:limit]


def _load_json_safe(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return default
    return default


def _load_jsonl_safe(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if isinstance(item, dict):
                    rows.append(item)
    except Exception:
        return rows
    return rows


def _load_issue_drafts(project: str, root: Path) -> list[dict[str, Any]]:
    project = _safe_project_id(project)
    normalized = root / "platform_outputs" / project / "issue_sync" / "normalized_issue_drafts.json"
    data = _load_json_safe(normalized, {})
    if isinstance(data, dict) and isinstance(data.get("items"), list) and data["items"]:
        return [i for i in data["items"] if isinstance(i, dict)]
    real = root / "platform_outputs" / project / "real_project" / "real_project_defect_data.json"
    data = _load_json_safe(real, {})
    if isinstance(data, dict) and isinstance(data.get("issues"), list) and data["issues"]:
        return [i for i in data["issues"] if isinstance(i, dict)]
    try:
        bundle = build_issue_export_bundle(project, root)
        return [i for i in bundle.get("exported_issues", []) if isinstance(i, dict)]
    except Exception:
        return []


def _load_fix_status(project: str, root: Path) -> dict[str, dict[str, Any]]:
    project = _safe_project_id(project)
    input_dir = root / "platform_inputs" / project / "fix_verification"
    rows: list[dict[str, Any]] = []
    rows.extend(_load_jsonl_safe(input_dir / "fix_status.jsonl"))
    rows.extend(_load_jsonl_safe(input_dir / "fixed_issues.jsonl"))
    for name in ("fix_status.json", "fixed_issues.json"):
        data = _load_json_safe(input_dir / name, {})
        if isinstance(data, list):
            rows.extend([i for i in data if isinstance(i, dict)])
        elif isinstance(data, dict):
            if isinstance(data.get("items"), list):
                rows.extend([i for i in data["items"] if isinstance(i, dict)])
            elif data.get("issue_id"):
                rows.append(data)
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        issue_id = str(row.get("issue_id") or row.get("id") or "").strip()
        if issue_id:
            by_id[issue_id] = row
    return by_id


def _issue_id(issue: dict[str, Any], index: int) -> str:
    raw = str(issue.get("issue_id") or issue.get("id") or f"ISSUE_{index:04d}")
    raw = re.sub(r"[^A-Za-z0-9_-]+", "_", raw)
    return raw[:96] or f"ISSUE_{index:04d}"


def _method_path(issue: dict[str, Any]) -> tuple[str, str]:
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    request = evidence.get("request") if isinstance(evidence.get("request"), dict) else {}
    method = issue.get("method") or request.get("method") or "GET"
    path = issue.get("path") or request.get("url") or request.get("path") or "/"
    return str(method or "GET").upper(), str(path or "/")


def _is_destructive(method: str, issue: dict[str, Any]) -> bool:
    risk = str(issue.get("risk_type") or "").lower()
    return method.upper() in DESTRUCTIVE_METHODS or risk in {"payment", "refund", "idempotency", "duplicate_submit", "concurrency", "delete", "cancel_order"}


def _status_text(issue: dict[str, Any], fix: dict[str, Any] | None) -> str:
    parts = [issue.get("status"), issue.get("qa_feedback_status")]
    if fix:
        parts.extend([fix.get("fix_status"), fix.get("status"), fix.get("developer_status"), fix.get("qa_status")])
    return " ".join(str(p or "") for p in parts).lower()


def _marked_fixed(issue: dict[str, Any], fix: dict[str, Any] | None) -> bool:
    text = _status_text(issue, fix)
    return any(word.lower() in text for word in FIXED_STATUS_WORDS)


def _marked_reopen(issue: dict[str, Any], fix: dict[str, Any] | None) -> bool:
    text = _status_text(issue, fix)
    return any(word.lower() in text for word in REOPEN_STATUS_WORDS)


def _login_tokens(cfg: dict[str, Any], root: Path, project: str) -> dict[str, str | None]:
    project = _safe_project_id(project)
    accounts = _load_json_safe(root / "platform_inputs" / project / "test_accounts.json", {})
    timeout = int(cfg.get("request_timeout_seconds") or 10)
    tokens: dict[str, str | None] = {}
    for actor, account in (accounts or {}).items():
        if isinstance(account, dict):
            try:
                tokens[str(actor)] = _login(cfg, account, timeout).get("token")
            except Exception:
                tokens[str(actor)] = account.get("token")
    return tokens


def _response_indicates_fixed(issue: dict[str, Any], response: dict[str, Any]) -> tuple[str, str, float]:
    risk = str(issue.get("risk_type") or "").lower()
    status_code = response.get("status_code")
    if response.get("error"):
        return "needs_review", f"验证请求未完成：{response.get('error')}", 0.45
    if risk in {"permission_bypass", "idor", "tenant_isolation"}:
        if status_code in {401, 403, 404}:
            return "fixed", "越权访问已被拒绝，原缺陷信号未复现。", 0.88
        if status_code is not None and 200 <= int(status_code) < 300:
            return "still_failing", "原越权访问仍返回成功状态码。", 0.86
    if status_code is not None and 200 <= int(status_code) < 300:
        # For business consistency issues a 2xx response alone is not enough to prove failure; require human review.
        return "needs_review", "接口仍可调用，需要结合业务状态/账务/库存断言确认是否修复。", 0.55
    if status_code is not None and int(status_code) >= 400:
        return "fixed", "接口已拒绝原异常请求，原缺陷信号未复现。", 0.74
    return "needs_review", "缺少可判定响应，需要人工复核。", 0.4


def _build_verification_probe(issue: dict[str, Any], index: int, fix: dict[str, Any] | None, allow_destructive: bool) -> dict[str, Any]:
    issue_id = _issue_id(issue, index)
    method, path = _method_path(issue)
    destructive = _is_destructive(method, issue)
    return {
        "verification_id": f"FIX_VERIFY_{index:04d}",
        "issue_id": issue_id,
        "title": f"修复验证：{_safe_text(issue.get('title'), 180)}",
        "risk_type": _safe_text(issue.get("risk_type") or "business_risk", 100),
        "severity": str(issue.get("severity") or "P2"),
        "actor": _safe_text(issue.get("actor") or "normal_user", 80),
        "method": method,
        "path": path,
        "expected_after_fix": "原缺陷信号不再复现，接口返回符合权限/状态/金额/库存等业务规则。",
        "original_expected": _safe_text(issue.get("expected"), 1200),
        "original_actual": _safe_text(issue.get("actual"), 1200),
        "destructive": destructive,
        "execution_policy": "execute" if (not destructive or allow_destructive) else "candidate_only",
        "developer_fix_status": _safe_text((fix or {}).get("fix_status") or (fix or {}).get("status") or "not_marked", 120),
        "source": "issue_sync_export",
    }


def _render_report(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    cards = "".join(
        f"<div class='card'><span>{_html_escape(k)}</span><b>{_html_escape(v)}</b></div>"
        for k, v in summary.items()
        if k in {"verification_count", "fixed_count", "still_failing_count", "needs_review_count", "reopen_blocker_count", "regression_probe_count"}
    )
    rows: list[str] = []
    for item in result.get("items", [])[:120]:
        rows.append(
            "<tr>"
            f"<td>{_html_escape(item.get('verification_status'))}</td>"
            f"<td>{_html_escape(item.get('severity'))}</td>"
            f"<td>{_html_escape(item.get('title'))}</td>"
            f"<td>{_html_escape(item.get('risk_type'))}</td>"
            f"<td>{_html_escape(item.get('confidence'))}</td>"
            f"<td>{_html_escape(item.get('reason'))}</td>"
            "</tr>"
        )
    advice = summary.get("release_gate_impact") or "needs_review"
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>缺陷修复验证与回归闭环</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:white;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:24px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#ecfdf5;color:#065f46}}.warn{{background:#fff7ed;color:#9a3412}}</style></head><body>
<section class='hero'><span class='badge'>Phase30 Fix Verification</span><h1>{_html_escape(summary.get('project_name'))}</h1><p>读取缺陷单草稿和研发/QA 修复状态，生成修复验证探针，判断 fixed / still_failing / needs_review，并沉淀回归探针，防止同类问题回归。</p><p>发布门禁影响：<b>{_html_escape(advice)}</b> · 生成时间：{_html_escape(summary.get('generated_at'))}</p></section>
<section class='panel'><h2>验证概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>验证结果</h2><table><thead><tr><th>状态</th><th>等级</th><th>标题</th><th>风险</th><th>置信度</th><th>原因</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="6">暂无待验证缺陷</td></tr>'}</tbody></table></section>
<section class='panel'><h2>下一步</h2><p>still_failing 的问题建议重新打开缺陷单并阻断发布；needs_review 的问题建议 QA 补充业务断言或测试环境数据；fixed 的问题已写入回归探针库。</p></section>
</body></html>"""


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False).lower()
    leaks = [m for m in PRIVATE_MARKERS if m.lower() in text]
    return {"passed": not leaks, "checked": True, "leak_count": len(leaks)}


def run_fix_verification(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    allow_destructive = bool(options.get("allow_destructive_verification", False) or cfg.get("allow_destructive_tests"))
    max_count = int(options.get("max_verification_count") or cfg.get("max_verification_count") or 80)

    issues = _load_issue_drafts(project, root)[:max_count]
    fix_status = _load_fix_status(project, root)
    tokens = _login_tokens(cfg, root, project) if cfg.get("base_url") else {}
    timeout = int(cfg.get("request_timeout_seconds") or 10)

    items: list[dict[str, Any]] = []
    probes: list[dict[str, Any]] = []
    regression_probes: list[dict[str, Any]] = []
    for idx, issue in enumerate(issues, start=1):
        issue_id = _issue_id(issue, idx)
        fix = fix_status.get(issue_id) or {}
        probe = _build_verification_probe(issue, idx, fix, allow_destructive)
        probes.append(probe)
        response: dict[str, Any] = {"ok": False, "status_code": None, "body": "", "error": "not_executed"}
        if _marked_reopen(issue, fix):
            status, reason, confidence = "still_failing", "缺陷状态已标记为未修复 / 重新打开。", 0.9
        elif probe["execution_policy"] == "candidate_only":
            if _marked_fixed(issue, fix):
                status, reason, confidence = "needs_review", "破坏性修复验证未自动执行；研发已标记修复，需 QA 在测试环境复核。", 0.58
            else:
                status, reason, confidence = "needs_review", "缺陷尚未标记修复或验证探针为候选模式。", 0.42
        elif not cfg.get("base_url"):
            if _marked_fixed(issue, fix):
                status, reason, confidence = "needs_review", "缺少 Base URL，无法自动验证；研发已标记修复，需人工复核。", 0.55
            else:
                status, reason, confidence = "needs_review", "缺少 Base URL，无法自动验证。", 0.4
        else:
            response = _fetch_json_or_text(_join_url(str(cfg.get("base_url") or ""), str(probe["path"])), probe["method"], token=tokens.get(str(probe.get("actor") or "normal_user")), timeout=timeout)
            status, reason, confidence = _response_indicates_fixed(issue, response)
            if _marked_fixed(issue, fix) and status == "needs_review" and response.get("error"):
                reason = "研发已标记修复，但自动请求未完成，需要人工复核环境或账号。"
        item = {
            "verification_id": probe["verification_id"],
            "issue_id": issue_id,
            "title": _safe_text(issue.get("title") or probe["title"], 240),
            "risk_type": probe["risk_type"],
            "severity": probe["severity"],
            "method": probe["method"],
            "path": probe["path"],
            "verification_status": status,
            "confidence": round(float(confidence), 3),
            "reason": reason,
            "developer_fix_status": probe["developer_fix_status"],
            "execution_policy": probe["execution_policy"],
            "response_status": response.get("status_code"),
            "response_error": response.get("error"),
            "evidence": {
                "request": {"method": probe["method"], "url": probe["path"], "actor": probe["actor"]},
                "response": {"status_code": response.get("status_code"), "body_excerpt": _safe_text(response.get("body"), 500), "error": response.get("error")},
                "expected_after_fix": probe["expected_after_fix"],
            },
        }
        items.append(item)
        if status == "fixed":
            regression_probes.append({
                "regression_probe_id": f"REG_{probe['verification_id']}",
                "issue_id": issue_id,
                "risk_type": probe["risk_type"],
                "severity": probe["severity"],
                "method": probe["method"],
                "path": probe["path"],
                "actor": probe["actor"],
                "expected": probe["expected_after_fix"],
                "source": "fix_verification_loop",
            })

    fixed_count = sum(1 for i in items if i["verification_status"] == "fixed")
    still_failing_count = sum(1 for i in items if i["verification_status"] == "still_failing")
    needs_review_count = sum(1 for i in items if i["verification_status"] == "needs_review")
    reopen_blocker_count = sum(1 for i in items if i["verification_status"] == "still_failing" and i["severity"] in {"P0", "P1"})
    if reopen_blocker_count:
        release_gate_impact = "block_release"
    elif still_failing_count:
        release_gate_impact = "hold_for_review"
    elif needs_review_count:
        release_gate_impact = "manual_review_required"
    else:
        release_gate_impact = "allow_release"

    summary = {
        "phase": "phase30_fix_verification_loop",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_issue_count": len(issues),
        "verification_count": len(items),
        "fixed_count": fixed_count,
        "still_failing_count": still_failing_count,
        "needs_review_count": needs_review_count,
        "reopen_blocker_count": reopen_blocker_count,
        "regression_probe_count": len(regression_probes),
        "release_gate_impact": release_gate_impact,
        "allow_destructive_verification": allow_destructive,
        "external_api_called": bool(cfg.get("base_url")),
    }
    result = {
        "phase": "phase30_fix_verification_loop",
        "project_id": project,
        "summary": summary,
        "items": items,
        "verification_probes": probes,
        "regression_probes": regression_probes,
    }
    private_check = _private_leak_check(result)
    summary["private_leak_check_passed"] = private_check["passed"]
    result["private_leak_check"] = private_check

    out_dir = root / "platform_outputs" / project / "fix_verification"
    workspace_dir = root / "platform_workspace" / project / "defect_discovery"
    _write_json(out_dir / "fix_verification_plan.json", {"items": probes})
    _write_json(out_dir / "fix_verification_result.json", result)
    _write_json(out_dir / "fix_verification_summary.json", summary)
    _write_text(out_dir / "fix_verification_report.html", _render_report(result))
    _write_json(workspace_dir / "fix_regression_probes.json", {"items": regression_probes})
    _write_json(workspace_dir / "fix_verification_manifest.json", {"summary": summary, "artifacts": {"report_html": str((out_dir / 'fix_verification_report.html').relative_to(root)).replace('\\', '/')}})
    return result


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    allow_destructive = str(os.environ.get("ALLOW_DESTRUCTIVE_FIX_VERIFY", "0")).lower() in {"1", "true", "yes", "on"}
    result = run_fix_verification(project, options={"allow_destructive_verification": allow_destructive})
    print(json.dumps({"ok": True, "project_id": project, "summary": result.get("summary")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
