from __future__ import annotations

"""Customer-safe report renderer for private-pilot deployments.

This module owns customer-visible HTML report text. It deliberately avoids the
legacy renderer that contained mojibake strings and keeps the delivery wording
aligned with the evidence gate: unverified clues are not customer-deliverable
bugs, and QualiBug-AI does not provide remediation advice.
"""

import html
import json
import time
from pathlib import Path
from typing import Any

from ai_test_asset_center.version import PRODUCT_VERSION

MOJIBAKE_MARKERS = ("鎵", "鐢", "鍒", "椤", "鏃", "瑕", "绉", "娴", "缃", "搴", "", "€")
PRODUCT_BOUNDARY_TEXT = "QualiBug-AI 只提供缺陷事实、可核验证据链、客户处理后的回归验证和发布状态；不作根因承诺。"


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


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off", ""}:
            return False
    return False


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


def normalize_release_check(value: Any) -> dict[str, Any] | None:
    row = as_dict(value)
    name = str(row.get("name") or "").strip()
    status = str(row.get("status") or "").strip()
    if not name or status not in {"pass", "pending", "fail"}:
        return None
    return {
        "name": name,
        "status": status,
        "detail": str(row.get("detail") or row.get("reason") or "未提供门禁详情。"),
        "source": str(row.get("source") or "release_gate"),
    }


def normalize_release_gate(value: Any) -> dict[str, Any]:
    gate = as_dict(value)
    checks = [check for check in (normalize_release_check(item) for item in as_list(gate.get("checks"))) if check]
    if not checks:
        return {}
    overall = str(gate.get("overall_status") or "").strip()
    if overall not in {"pass", "pending", "fail"}:
        overall = "fail" if any(item["status"] == "fail" for item in checks) else "pending" if any(item["status"] == "pending" for item in checks) else "pass"
    return {
        "overall_status": overall,
        "checks": checks,
        "blocking_check_count": sum(1 for item in checks if item["status"] == "fail"),
        "pending_check_count": sum(1 for item in checks if item["status"] == "pending"),
        "pass_check_count": sum(1 for item in checks if item["status"] == "pass"),
        "release_recommendation": str(gate.get("release_recommendation") or ("block_release" if overall == "fail" else "hold_for_validation" if overall == "pending" else "candidate_release")),
        "honesty_rule": str(gate.get("honesty_rule") or "发布门禁基于已持久化的扫描、回归和人工复核状态；未覆盖范围不能被声明为安全。"),
        "source": str(gate.get("source") or "release_gate"),
    }


def _should_replace_release_check(existing: dict[str, Any], incoming: dict[str, Any]) -> bool:
    if incoming.get("name") != "客户处理后回归 Gate":
        return False
    incoming_source = str(incoming.get("source") or "")
    existing_source = str(existing.get("source") or "")
    if incoming_source == "regression_run_result":
        return True
    if incoming_source == "regression_suite_refresh" and existing_source != "regression_run_result":
        return True
    return False


def merge_release_gates(*gates: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    sources: list[str] = []
    honesty_rules: list[str] = []
    for gate in gates:
        normalized = normalize_release_gate(gate)
        if not normalized:
            continue
        source = str(normalized.get("source") or "")
        if source and source not in sources:
            sources.append(source)
        rule = str(normalized.get("honesty_rule") or "")
        if rule and rule not in honesty_rules:
            honesty_rules.append(rule)
        checks.extend(as_dict(item) for item in as_list(normalized.get("checks")))
    if not checks:
        return {}
    unique: list[dict[str, Any]] = []
    index_by_name: dict[str, int] = {}
    for item in checks:
        check = normalize_release_check(item)
        if not check:
            continue
        if check["name"] == "修复后回归 Gate":
            check["name"] = "客户处理后回归 Gate"
        key = check["name"]
        existing_index = index_by_name.get(key)
        if existing_index is None:
            index_by_name[key] = len(unique)
            unique.append(check)
            continue
        if _should_replace_release_check(unique[existing_index], check):
            unique[existing_index] = check
    overall = "fail" if any(item["status"] == "fail" for item in unique) else "pending" if any(item["status"] == "pending" for item in unique) else "pass"
    return {
        "overall_status": overall,
        "checks": unique,
        "blocking_check_count": sum(1 for item in unique if item["status"] == "fail"),
        "pending_check_count": sum(1 for item in unique if item["status"] == "pending"),
        "pass_check_count": sum(1 for item in unique if item["status"] == "pass"),
        "release_recommendation": "block_release" if overall == "fail" else "hold_for_validation" if overall == "pending" else "candidate_release",
        "honesty_rule": "；".join(honesty_rules) if honesty_rules else "发布门禁基于已持久化的扫描、回归和人工复核状态；未覆盖范围不能被声明为安全。",
        "source": "+".join(sources) if sources else "merged_release_gate",
    }


def release_gate_from_regression_run(value: Any) -> dict[str, Any]:
    result = as_dict(value)
    if not result:
        return {}
    summary = as_dict(result.get("summary"))
    ci = as_dict(result.get("ci_feedback"))
    gate_status = str(ci.get("gate_status") or summary.get("gate_status") or "").strip()
    failed = safe_int(summary.get("failed_count"))
    needs_review = safe_int(summary.get("needs_review_count"))
    passed = safe_int(summary.get("passed_count"))
    if gate_status == "failed":
        status = "fail"
        detail = f"最近一次回归失败：{failed} 个探针失败，{needs_review} 个需复核。客户内部处理或复核后，必须再次执行回归验证。"
    elif gate_status == "manual_approval_required":
        status = "pending"
        detail = f"最近一次回归仍需人工复核：{needs_review} 个探针缺少强自动判定，不能直接放行发布。"
    elif gate_status == "passed":
        status = "pass"
        detail = f"最近一次回归通过：{passed} 个探针通过。该结论不扩大到未覆盖范围。"
    else:
        return {}
    return normalize_release_gate({
        "checks": [{"name": "客户处理后回归 Gate", "status": status, "detail": detail, "source": "regression_run_result"}],
        "source": "regression_run_result",
    })


def release_gate_from_suite_refresh(report: dict[str, Any], scan_result: dict[str, Any]) -> dict[str, Any]:
    refresh = as_dict(report.get("regression_suite_refresh")) or as_dict(scan_result.get("regression_suite_refresh"))
    suite = as_dict(report.get("regression_suite")) or as_dict(scan_result.get("regression_suite"))
    summary = as_dict(refresh.get("summary"))
    total = safe_int(suite.get("total_probe_count") or summary.get("total_probe_count"))
    confirmed = safe_int(suite.get("confirmed_ledger_probe_count") or summary.get("confirmed_ledger_probe_count"))
    if str(refresh.get("status") or "") != "refreshed" or total <= 0:
        return {}
    return normalize_release_gate({
        "checks": [{
            "name": "客户处理后回归 Gate",
            "status": "pending",
            "detail": f"已自动生成 {total} 个回归探针，其中 {confirmed} 个来自 confirmed bug ledger；发布前必须先执行 Smoke 或 Release 回归。",
            "source": "regression_suite_refresh",
        }],
        "source": "regression_suite_refresh",
    })


def _scan_result(project: str, root: Path) -> dict[str, Any]:
    from .scan_result_store import load_scan_result

    value = load_scan_result(
        root / "platform_outputs" / project / "scan_result.json",
        keys=[
            "release_gate", "findings", "candidate_findings",
            "delivery_occurrences", "campaign", "runtime_contract",
            "total_findings", "pipeline_health",
        ],
    )
    return value if isinstance(value, dict) else {}


def load_customer_release_gate(project: str, root: Path, report: dict[str, Any]) -> dict[str, Any]:
    scan_result = _scan_result(project, root)
    regression_result = read_json_file(root / "platform_outputs" / project / "regression_run" / "regression_run_result.json", {})
    return merge_release_gates(
        as_dict(report.get("release_gate")),
        as_dict(scan_result.get("release_gate")),
        release_gate_from_regression_run(regression_result),
        release_gate_from_suite_refresh(report, scan_result),
    )


def _merge_nested_dicts(*values: dict[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for value in values:
        for key, incoming in as_dict(value).items():
            if isinstance(incoming, dict) and isinstance(merged.get(key), dict):
                merged[key] = _merge_nested_dicts(as_dict(merged[key]), incoming)
            elif incoming is not None:
                merged[key] = incoming
    return merged


def commercial_assets_from_release_gate(gate: dict[str, Any]) -> dict[str, Any]:
    gate = normalize_release_gate(gate)
    if not gate:
        return {}
    overall = str(gate.get("overall_status") or "")
    first_check = as_dict(as_list(gate.get("checks"))[0]) if as_list(gate.get("checks")) else {}
    block_reason = str(first_check.get("detail") or gate.get("honesty_rule") or "")
    assets: dict[str, Any] = {
        "release_gate": gate,
        "release_gate_overall_status": overall,
        "release_recommendation": gate.get("release_recommendation"),
        "release_gate_honesty_rule": gate.get("honesty_rule"),
        "commercial_handoff": {"release_gate_status": overall},
        "tracker_sync": {"payload_gate_status": overall, "release_gate_overall_status": overall},
        "delivery_package": {
            "release_verdict": overall,
            "release_recommendation": gate.get("release_recommendation"),
            "release_gate_overall_status": overall,
        },
    }
    if overall in {"fail", "pending"}:
        blocked_status = "blocked_by_release_gate" if overall == "fail" else "hold_for_validation"
        assets["commercial_handoff"].update({"safe_for_customer": False, "acceptance_status": blocked_status})
        assets["tracker_sync"]["payload_status"] = blocked_status
        assets["delivery_package"].update({"release_gate_blocked": True, "release_gate_block_reason": block_reason})
    return assets


def load_customer_commercial_assets(project: str, root: Path, report: dict[str, Any], release_gate: dict[str, Any]) -> dict[str, Any]:
    scan_result = _scan_result(project, root)
    return _merge_nested_dicts(
        as_dict(report.get("commercial_assets")),
        as_dict(scan_result.get("commercial_assets")),
        commercial_assets_from_release_gate(release_gate),
    )


def commercial_handoff_label(assets: dict[str, Any]) -> str:
    if not assets:
        return "暂无结论"
    handoff = as_dict(assets.get("commercial_handoff"))
    tracker = as_dict(assets.get("tracker_sync"))
    delivery = as_dict(assets.get("delivery_package"))
    if safe_bool(handoff.get("safe_for_customer")):
        return "已放行"
    status = str(handoff.get("acceptance_status") or tracker.get("payload_status") or delivery.get("status") or "").strip()
    if status == "blocked_by_release_gate":
        return "被门禁阻塞"
    if status == "hold_for_validation":
        return "待复核"
    return status or "未放行"


def commercial_handoff_message(assets: dict[str, Any]) -> str:
    if not assets:
        return "暂无商业交付 Handoff 数据；报告不会把发布门禁通过等同为整包可交付。"
    handoff = as_dict(assets.get("commercial_handoff"))
    delivery = as_dict(assets.get("delivery_package"))
    if safe_bool(handoff.get("safe_for_customer")):
        return "后端 commercial_handoff.safe_for_customer 已明确放行，可进入客户验收。"
    return str(
        delivery.get("release_gate_block_reason")
        or "发布门禁通过并不等同于商业交付安全；必须等待 commercial_handoff.safe_for_customer=true。"
    )


def render_commercial_handoff_section(assets: dict[str, Any]) -> str:
    label = commercial_handoff_label(assets)
    tone = "pass" if label == "已放行" else "fail" if label == "被门禁阻塞" else "pending"
    handoff = as_dict(assets.get("commercial_handoff"))
    tracker = as_dict(assets.get("tracker_sync"))
    delivery = as_dict(assets.get("delivery_package"))
    safe_for_customer = safe_bool(handoff.get("safe_for_customer"))
    return f"""
<h2>商业交付 Handoff</h2>
<div class="release-summary gate-{html_text(tone, 20)}">
  <strong>交付安全状态：{html_text(label, 40)}</strong>
  <p>{html_text(commercial_handoff_message(assets), 360)}</p>
  <p>safe_for_customer：{html_text(str(safe_for_customer).lower(), 20)} · acceptance：{html_text(handoff.get('acceptance_status') or '-', 80)} · tracker：{html_text(tracker.get('payload_status') or '-', 80)} · package：{html_text(delivery.get('release_verdict') or delivery.get('status') or '-', 80)}</p>
</div>
<div class="notice warning">报告将“发布门禁结论”和“商业交付安全”分开展示；只有 handoff 明确放行时，才可声明整包进入客户验收。</div>
"""


def release_status_label(status: str) -> str:
    if status == "fail":
        return "阻塞"
    if status == "pending":
        return "待处理"
    if status == "pass":
        return "通过"
    return "暂无结论"


def release_status_message(gate: dict[str, Any]) -> str:
    status = str(gate.get("overall_status") or "")
    if status == "fail":
        return "当前存在发布阻断项，不能进入正式发布。客户内部处理后，需要再次执行回归验证。"
    if status == "pending":
        return "当前仍有待处理门禁项，发布前需要完成回归执行或人工复核。"
    if status == "pass":
        return "当前门禁未发现阻断或待处理项，可进入正式发布评审；这不等同于商业交付已放行。"
    return "暂无发布门禁结论，请先完成一次真实扫描和必要回归。"


def render_release_gate_section(gate: dict[str, Any]) -> str:
    if not gate:
        return """
<h2>发布门禁与客户处理后回归</h2>
<div class="notice warning">暂无发布门禁结论。请先完成真实扫描；如已形成 confirmed bug 回归义务，发布前必须执行回归。</div>
"""
    status = str(gate.get("overall_status") or "")
    rows = []
    for check in as_list(gate.get("checks")):
        item = as_dict(check)
        rows.append(
            "<tr>"
            f"<td>{html_text(item.get('name') or '发布门禁', 120)}</td>"
            f"<td><span class='gate gate-{html_text(item.get('status') or 'pending', 20)}'>{html_text(release_status_label(str(item.get('status') or 'pending')), 20)}</span></td>"
            f"<td>{html_text(item.get('detail') or '-', 320)}</td>"
            f"<td>{html_text(item.get('source') or '-', 80)}</td>"
            "</tr>"
        )
    if not rows:
        rows.append("<tr><td colspan='4'>暂无门禁检查明细。</td></tr>")
    return f"""
<h2>发布门禁与客户处理后回归</h2>
<div class="release-summary gate-{html_text(status or 'pending', 20)}">
  <strong>当前发布结论：{html_text(release_status_label(status), 40)}</strong>
  <p>{html_text(release_status_message(gate), 220)}</p>
  <p>阻塞项：{html_text(gate.get('blocking_check_count') or 0, 20)} · 待处理：{html_text(gate.get('pending_check_count') or 0, 20)} · 通过：{html_text(gate.get('pass_check_count') or 0, 20)}</p>
</div>
<table><tr><th>门禁项</th><th>状态</th><th>说明</th><th>来源</th></tr>{''.join(rows)}</table>
<div class="notice">门禁聚合来源：{html_text(gate.get('source') or 'merged_release_gate', 200)}</div>
<div class="notice warning">{html_text(gate.get('honesty_rule') or '门禁结论仅代表已执行和已持久化的证据，不证明未覆盖范围安全。', 500)}</div>
"""


def render_customer_safe_report_html(project: str, root: Path) -> str:
    """Render customer-facing report HTML without mojibake or internal placeholders."""
    report_path = root / "platform_outputs" / project / "pipeline_reports" / "latest_pipeline_report.json"
    history_path = root / "platform_outputs" / project / "pipeline_reports" / "scan_history.json"
    report = read_json_file(report_path, {})
    report = report if isinstance(report, dict) else {}
    history = read_json_file(history_path, [])
    history = history if isinstance(history, list) else []
    findings = report_findings(report)
    release_gate = load_customer_release_gate(project, root, report)
    commercial_assets = load_customer_commercial_assets(project, root, report, release_gate)
    stage1 = report.get("stage1_industry") if isinstance(report.get("stage1_industry"), dict) else {}
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
    object_count = stage1.get("object_count", 0) if isinstance(stage1, dict) else 0
    release_label = release_status_label(str(release_gate.get("overall_status") or "")) if release_gate else "暂无结论"
    handoff_label = commercial_handoff_label(commercial_assets)
    release_section = render_release_gate_section(release_gate)
    handoff_section = render_commercial_handoff_section(commercial_assets)

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
.notice.warning{{background:#fffbeb;border-color:#fde68a;color:#92400e}}
.release-summary{{border-radius:10px;padding:14px 16px;margin:16px 0;background:#fff;border:1px solid #e2e8f0}}
.release-summary.gate-fail{{border-color:#fecaca;background:#fef2f2;color:#991b1b}}
.release-summary.gate-pending{{border-color:#fde68a;background:#fffbeb;color:#92400e}}
.release-summary.gate-pass{{border-color:#bbf7d0;background:#f0fdf4;color:#166534}}
.gate{{display:inline-block;border-radius:999px;padding:2px 8px;font-size:12px;font-weight:700}}
.gate-fail{{background:#fee2e2;color:#991b1b}}
.gate-pending{{background:#fef3c7;color:#92400e}}
.gate-pass{{background:#dcfce7;color:#166534}}
.footer{{margin-top:32px;font-size:12px;color:#64748b;border-top:1px solid #e2e8f0;padding-top:12px}}
</style>
</head>
<body>
<h1>QualiBug AI 缺陷扫描报告</h1>
<p>项目：<strong>{html_text(project, 120)}</strong> · 生成时间：<strong>{html_text(generated_at, 40)}</strong></p>
<div class="notice">本报告仅展示客户可读结果。未复现、证据不足或仍需授权的线索应保留在内部线索区，不作为客户可交付缺陷声明。</div>
<div class="notice warning">{html_text(PRODUCT_BOUNDARY_TEXT, 260)}</div>
<div>
  <div class="metric"><span>发现总数</span><strong>{total}</strong></div>
  <div class="metric"><span>P0/P1</span><strong>{p0p1}</strong></div>
  <div class="metric"><span>发布门禁</span><strong>{html_text(release_label, 20)}</strong></div>
  <div class="metric"><span>交付安全</span><strong>{html_text(handoff_label, 20)}</strong></div>
  <div class="metric"><span>对象/接口</span><strong>{html_text(object_count, 20)}</strong></div>
</div>
{release_section}
{handoff_section}
<h2>缺陷发现列表</h2>
<table><tr><th>严重度</th><th>标题</th><th>类别</th><th>置信度</th><th>证据摘要</th></tr>{''.join(rows)}</table>
<h2>扫描历史（最近 10 次）</h2>
<table><tr><th>时间</th><th>状态</th><th>发现数</th><th>P0/P1</th><th>对象</th></tr>{''.join(history_rows)}</table>
<div class="footer">QualiBug AI Enterprise Edition · 私有化部署 · 报告版本 {PRODUCT_VERSION}</div>
</body>
</html>"""


def contains_mojibake(text: str) -> bool:
    return any(marker in str(text or "") for marker in MOJIBAKE_MARKERS)
