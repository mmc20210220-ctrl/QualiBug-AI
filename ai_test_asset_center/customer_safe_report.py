from __future__ import annotations

"""Customer-safe report renderer for private-pilot deployments.

This module owns customer-visible HTML report text. It deliberately avoids the
legacy renderer that contained mojibake strings and keeps the delivery wording
aligned with the evidence gate: unverified clues are not customer-deliverable
bugs.
"""

import html
import json
import time
from pathlib import Path
from typing import Any

from ai_test_asset_center.version import PRODUCT_VERSION

MOJIBAKE_MARKERS = ("鎵", "鐢", "鍒", "椤", "鏃", "瑕", "绉", "娴", "缃", "搴", "", "€")


def html_text(value: Any, limit: int = 300) -> str:
    text = str(value if value is not None else "").strip()
    return html.escape(text[:limit] if limit > 0 else text)


def read_json_file(path: Path, fallback: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return fallback
    return fallback


def report_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
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
    report = read_json_file(report_path, {})
    report = report if isinstance(report, dict) else {}
    history = read_json_file(history_path, [])
    history = history if isinstance(history, list) else []
    findings = report_findings(report)
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
            f"<td>{html_text(finding.get('severity') or 'P2', 40)}</td>"
            f"<td>{html_text(finding.get('title') or finding.get('description') or '未命名发现', 180)}</td>"
            f"<td>{html_text(finding.get('category') or finding.get('defect_family') or '业务质量', 120)}</td>"
            f"<td>{html_text(finding.get('confidence_score') or finding.get('score') or '-', 40)}</td>"
            f"<td>{html_text(evidence_text, 260)}</td>"
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
            f"<td>{html_text(item.get('timestamp_utc') or item.get('timestamp') or '-', 80)}</td>"
            f"<td>{html_text(item.get('status') or '-', 60)}</td>"
            f"<td>{html_text(item.get('total_findings') or item.get('findings') or 0, 40)}</td>"
            f"<td>{html_text(item.get('p0p1_count') or item.get('critical_bugs') or 0, 40)}</td>"
            f"<td>{html_text(item.get('industry') or item.get('project') or '-', 120)}</td>"
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
<title>QualiBug AI 缺陷扫描报告 - {html_text(project, 120)}</title>
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
<p>项目：<strong>{html_text(project, 120)}</strong> · 生成时间：<strong>{html_text(generated_at, 40)}</strong></p>
<div class="notice">本报告仅展示客户可读结果。未复现、证据不足或仍需授权的线索应保留在内部线索区，不作为客户可交付缺陷声明。</div>
<div>
  <div class="metric"><span>发现总数</span><strong>{total}</strong></div>
  <div class="metric"><span>P0/P1</span><strong>{p0p1}</strong></div>
  <div class="metric"><span>LLM 分析</span><strong>{html_text(llm_count, 20)}</strong></div>
  <div class="metric"><span>对象/接口</span><strong>{html_text(object_count, 20)}</strong></div>
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
