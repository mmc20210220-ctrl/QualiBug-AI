from __future__ import annotations

import csv
import io
import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _html_escape, _safe_project_id
from .real_project_defect_discovery import run_real_project_discovery

PRIVATE_MARKERS = {
    "private_ground_truth",
    "ground_truth_bugs",
    "bug_sets",
    "enabled_bugs",
    "current_bug_set",
    "bug_instance_id",
}

STATUS_SKIP = {"false_positive", "duplicate", "low_value", "rejected", "not_a_bug", "ignored"}
SEVERITY_TO_PRIORITY = {"P0": "Highest", "P1": "High", "P2": "Medium", "P3": "Low", "P4": "Lowest"}
SEVERITY_TO_ZENTAO = {"P0": "1", "P1": "2", "P2": "3", "P3": "4", "P4": "4"}


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return default
    return default


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _safe_text(value: Any, limit: int = 5000) -> str:
    text = str(value or "")
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", text)
    return text[:limit]


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value or "issue_export")
    return value.strip("._") or "issue_export"


def _issue_key(issue: dict[str, Any], index: int) -> str:
    raw = str(issue.get("issue_id") or f"ISSUE_{index:04d}")
    raw = re.sub(r"[^A-Za-z0-9_-]+", "_", raw)
    return raw[:80] or f"ISSUE_{index:04d}"


def _load_real_project_data(project: str, root: Path) -> dict[str, Any]:
    out_dir = root / "platform_outputs" / project / "real_project"
    data = _read_json(out_dir / "real_project_defect_data.json", {})
    if isinstance(data, dict) and isinstance(data.get("issues"), list):
        return data
    discovered = _read_json(out_dir / "discovered_issues.json", {})
    items = discovered.get("items") if isinstance(discovered, dict) else []
    return {
        "project_id": project,
        "project_name": project,
        "mode": "unknown",
        "metrics": discovered.get("metrics", {}) if isinstance(discovered, dict) else {},
        "risk_distribution": discovered.get("risk_distribution", {}) if isinstance(discovered, dict) else {},
        "issues": items if isinstance(items, list) else [],
    }


def _ensure_real_project_data(project: str, root: Path) -> dict[str, Any]:
    data = _load_real_project_data(project, root)
    if data.get("issues"):
        return data
    try:
        generated = run_real_project_discovery(project, root)
        if isinstance(generated, dict):
            return generated
    except Exception:
        pass
    return data


def _normalize_issue(issue: dict[str, Any], index: int) -> dict[str, Any]:
    evidence = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    request = evidence.get("request") if isinstance(evidence.get("request"), dict) else {}
    response = evidence.get("response") if isinstance(evidence.get("response"), dict) else {}
    severity = str(issue.get("severity") or "P2").upper()
    if severity not in SEVERITY_TO_PRIORITY:
        severity = "P2"
    status = str(issue.get("qa_feedback_status") or issue.get("status") or "pending")
    return {
        "issue_id": _issue_key(issue, index),
        "title": _safe_text(issue.get("title") or f"疑似高价值 Bug {index}", 240),
        "risk_type": _safe_text(issue.get("risk_type") or "business_risk", 120),
        "severity": severity,
        "priority": SEVERITY_TO_PRIORITY.get(severity, "Medium"),
        "confidence": float(issue.get("confidence") or 0),
        "status": status,
        "expected": _safe_text(issue.get("expected"), 1500),
        "actual": _safe_text(issue.get("actual"), 1500),
        "business_impact": _safe_text(issue.get("business_impact"), 1500),
        "suggested_fix": _safe_text(issue.get("suggested_fix"), 1500),
        "actor": _safe_text(evidence.get("actor"), 120),
        "method": _safe_text(request.get("method"), 20),
        "path": _safe_text(request.get("url") or request.get("path"), 500),
        "status_code": response.get("status_code"),
        "response_excerpt": _safe_text(response.get("body_excerpt") or response.get("body") or response.get("error"), 1800),
        "qa_feedback_status": _safe_text(issue.get("qa_feedback_status") or "pending", 80),
        "source": _safe_text(issue.get("source") or "real_project_discovery", 120),
    }


def _filter_exportable_issues(issues: list[dict[str, Any]], confirmed_only: bool = False) -> list[dict[str, Any]]:
    exported: list[dict[str, Any]] = []
    for idx, issue in enumerate(issues, start=1):
        normalized = _normalize_issue(issue, idx)
        status_text = f"{normalized['status']} {normalized['qa_feedback_status']}".lower()
        if any(s in status_text for s in STATUS_SKIP):
            continue
        if confirmed_only and "confirmed" not in status_text and "valid" not in status_text and "有效" not in status_text:
            continue
        exported.append(normalized)
    return exported


def _markdown_description(issue: dict[str, Any], project_name: str) -> str:
    return "\n".join([
        f"# {issue['title']}",
        "",
        f"- Project: {project_name}",
        f"- Issue ID: {issue['issue_id']}",
        f"- Severity: {issue['severity']}",
        f"- Priority: {issue['priority']}",
        f"- Risk Type: {issue['risk_type']}",
        f"- Confidence: {issue['confidence']:.2f}",
        f"- QA Status: {issue['qa_feedback_status']}",
        "",
        "## Expected",
        issue["expected"] or "待补充",
        "",
        "## Actual",
        issue["actual"] or "待补充",
        "",
        "## Evidence",
        f"- Actor: {issue['actor'] or '-'}",
        f"- Request: {issue['method'] or '-'} {issue['path'] or '-'}",
        f"- Response Status: {issue['status_code'] if issue['status_code'] is not None else '-'}",
        f"- Response Excerpt: {issue['response_excerpt'] or '-'}",
        "",
        "## Business Impact",
        issue["business_impact"] or "待 QA 确认业务影响",
        "",
        "## Suggested Fix",
        issue["suggested_fix"] or "待研发确认修复方案",
    ])


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _jira_rows(issues: list[dict[str, Any]], project_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for issue in issues:
        labels = ["ai_quality", "real_project", issue["risk_type"], issue["severity"]]
        rows.append({
            "Summary": f"[{issue['severity']}] {issue['title']}",
            "Issue Type": "Bug",
            "Priority": issue["priority"],
            "Description": _markdown_description(issue, project_name),
            "Labels": ",".join(labels),
            "Component/s": issue["risk_type"],
            "External issue ID": issue["issue_id"],
            "Risk Type": issue["risk_type"],
            "Confidence": f"{issue['confidence']:.2f}",
            "QA Status": issue["qa_feedback_status"],
        })
    return rows


def _zentao_rows(issues: list[dict[str, Any]], project_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for issue in issues:
        steps = "\n".join([
            f"[项目] {project_name}",
            f"[接口] {issue['method']} {issue['path']}",
            f"[期望] {issue['expected'] or '待补充'}",
            f"[实际] {issue['actual'] or '待补充'}",
            f"[响应] {issue['response_excerpt'] or '-'}",
            f"[影响] {issue['business_impact'] or '-'}",
            f"[建议] {issue['suggested_fix'] or '-'}",
        ])
        rows.append({
            "Bug标题": f"[{issue['severity']}] {issue['title']}",
            "模块": issue["risk_type"],
            "严重程度": SEVERITY_TO_ZENTAO.get(issue["severity"], "3"),
            "优先级": SEVERITY_TO_ZENTAO.get(issue["severity"], "3"),
            "重现步骤": steps,
            "关键词": f"AI质量平台,{issue['risk_type']},{issue['severity']}",
            "外部ID": issue["issue_id"],
            "置信度": f"{issue['confidence']:.2f}",
        })
    return rows


def _github_markdown(issues: list[dict[str, Any]], project_name: str) -> str:
    if not issues:
        return "# GitHub Issues Drafts\n\n本次没有可导出的缺陷单草稿。\n"
    parts = ["# GitHub Issues Drafts", "", "以下内容可复制到 GitHub Issues，或由后续 API 集成自动创建。", ""]
    for issue in issues:
        labels = ["bug", "ai-quality", issue["severity"], issue["risk_type"]]
        parts.extend([
            "---",
            "",
            f"## {issue['title']}",
            "",
            f"Labels: `{', '.join(labels)}`",
            "",
            _markdown_description(issue, project_name),
            "",
        ])
    return "\n".join(parts)


def _github_json(issues: list[dict[str, Any]], project_name: str) -> list[dict[str, Any]]:
    payloads = []
    for issue in issues:
        payloads.append({
            "title": f"[{issue['severity']}] {issue['title']}",
            "body": _markdown_description(issue, project_name),
            "labels": ["bug", "ai-quality", issue["severity"], issue["risk_type"]],
        })
    return payloads


def _render_report(summary: dict[str, Any], issues: list[dict[str, Any]], artifacts: dict[str, str]) -> str:
    cards = "".join(f"<div class='card'><span>{_html_escape(k)}</span><b>{_html_escape(v)}</b></div>" for k, v in summary.items() if k in {"exported_issue_count", "p0_p1_count", "github_issue_count", "jira_issue_count", "zentao_issue_count", "skipped_issue_count"})
    rows = []
    for issue in issues[:100]:
        rows.append(f"<tr><td>{_html_escape(issue.get('severity'))}</td><td>{_html_escape(issue.get('title'))}</td><td>{_html_escape(issue.get('risk_type'))}</td><td>{_html_escape(issue.get('confidence'))}</td><td>{_html_escape(issue.get('qa_feedback_status'))}</td></tr>")
    links = "".join(f"<li><code>{_html_escape(name)}</code>：{_html_escape(path)}</li>" for name, path in artifacts.items())
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>缺陷单同步导出报告</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:white;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:24px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#eef2ff;color:#3730a3}}</style></head><body>
<section class='hero'><span class='badge'>Phase29 Issue Sync Export</span><h1>{_html_escape(summary.get('project_name'))}</h1><p>把真实项目发现的疑似高价值 Bug 转成 Jira CSV、禅道 CSV、GitHub Issues Markdown/JSON 和统一导出包。默认不直接调用外部系统，先生成可审查草稿。</p><p>生成时间：{_html_escape(summary.get('generated_at'))}</p></section>
<section class='panel'><h2>导出概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>导出文件</h2><ul>{links}</ul></section>
<section class='panel'><h2>缺陷单草稿</h2><table><thead><tr><th>等级</th><th>标题</th><th>风险</th><th>置信度</th><th>QA 状态</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="5">暂无可导出缺陷</td></tr>'}</tbody></table></section>
</body></html>"""


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False).lower()
    leaks = [marker for marker in PRIVATE_MARKERS if marker.lower() in text]
    return {"passed": not leaks, "checked": True, "leak_count": len(leaks)}


def build_issue_export_bundle(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    data = _ensure_real_project_data(project, root)
    project_name = str(data.get("project_name") or project)
    raw_issues = data.get("issues") if isinstance(data.get("issues"), list) else []
    exported = _filter_exportable_issues(raw_issues, confirmed_only=bool(options.get("confirmed_only", False)))

    out_dir = root / "platform_outputs" / project / "issue_sync"
    workspace_dir = root / "platform_workspace" / project / "defect_discovery"
    out_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)

    jira_csv = out_dir / "jira_issues.csv"
    zentao_csv = out_dir / "zentao_issues.csv"
    github_md = out_dir / "github_issues.md"
    github_json = out_dir / "github_issues.json"
    normalized_json = out_dir / "normalized_issue_drafts.json"
    report_html = out_dir / "issue_sync_export_report.html"
    bundle_zip = out_dir / "issue_export_bundle.zip"

    jira_rows = _jira_rows(exported, project_name)
    zentao_rows = _zentao_rows(exported, project_name)
    _write_csv(jira_csv, jira_rows, ["Summary", "Issue Type", "Priority", "Description", "Labels", "Component/s", "External issue ID", "Risk Type", "Confidence", "QA Status"])
    _write_csv(zentao_csv, zentao_rows, ["Bug标题", "模块", "严重程度", "优先级", "重现步骤", "关键词", "外部ID", "置信度"])
    _write_text(github_md, _github_markdown(exported, project_name))
    _write_json(github_json, {"items": _github_json(exported, project_name)})
    _write_json(normalized_json, {"items": exported})

    severity_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    for issue in exported:
        severity_counts[issue["severity"]] = severity_counts.get(issue["severity"], 0) + 1
        risk_counts[issue["risk_type"]] = risk_counts.get(issue["risk_type"], 0) + 1

    artifacts = {
        "jira_csv": str(jira_csv.relative_to(root)).replace("\\", "/"),
        "zentao_csv": str(zentao_csv.relative_to(root)).replace("\\", "/"),
        "github_markdown": str(github_md.relative_to(root)).replace("\\", "/"),
        "github_json": str(github_json.relative_to(root)).replace("\\", "/"),
        "normalized_issue_drafts": str(normalized_json.relative_to(root)).replace("\\", "/"),
        "issue_export_bundle": str(bundle_zip.relative_to(root)).replace("\\", "/"),
        "report_html": str(report_html.relative_to(root)).replace("\\", "/"),
    }
    summary = {
        "phase": "phase29_issue_sync_export",
        "project_id": project,
        "project_name": project_name,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source_issue_count": len(raw_issues),
        "exported_issue_count": len(exported),
        "skipped_issue_count": max(0, len(raw_issues) - len(exported)),
        "p0_p1_count": sum(1 for i in exported if i["severity"] in {"P0", "P1"}),
        "jira_issue_count": len(jira_rows),
        "zentao_issue_count": len(zentao_rows),
        "github_issue_count": len(exported),
        "severity_distribution": severity_counts,
        "risk_distribution": risk_counts,
        "confirmed_only": bool(options.get("confirmed_only", False)),
        "external_api_called": False,
        "export_mode": "draft_bundle",
    }
    private_check = _private_leak_check({"summary": summary, "issues": exported, "artifacts": artifacts})
    summary["private_leak_check_passed"] = private_check["passed"]

    result = {
        "phase": "phase29_issue_sync_export",
        "project_id": project,
        "summary": summary,
        "artifacts": artifacts,
        "exported_issues": exported,
        "private_leak_check": private_check,
    }
    _write_json(out_dir / "issue_sync_export_summary.json", summary)
    _write_json(out_dir / "issue_sync_export_result.json", result)
    _write_json(workspace_dir / "issue_sync_export_manifest.json", {"summary": summary, "artifacts": artifacts})
    _write_text(report_html, _render_report(summary, exported, artifacts))

    with zipfile.ZipFile(bundle_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in [jira_csv, zentao_csv, github_md, github_json, normalized_json, out_dir / "issue_sync_export_summary.json", report_html]:
            zf.write(path, arcname=path.name)
    return result


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    confirmed_only = str(os.environ.get("ISSUE_SYNC_CONFIRMED_ONLY", "0")).lower() in {"1", "true", "yes", "on"}
    result = build_issue_export_bundle(project, options={"confirmed_only": confirmed_only})
    print(json.dumps({"ok": True, "project_id": project, "summary": result.get("summary"), "artifacts": result.get("artifacts")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
