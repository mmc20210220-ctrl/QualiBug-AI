from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterable

from .real_project_onboarding import ROOT, _html_escape, _load_json, _read_text, _safe_project_id, _write_json, config_paths, load_real_project_config
from .business_adaptation_layer import build_business_adaptation_profile, load_business_adaptation_profile

PRIVATE_MARKERS = {"private_ground_truth", "ground_truth_bugs", "bug_sets", "enabled_bugs", "current_bug_set", "bug_instance_id"}
HIGH_VALUE_RISKS = {
    "permission_bypass",
    "idor",
    "tenant_isolation",
    "money_consistency",
    "account_ownership",
    "privacy_leak",
    "payment",
    "refund",
    "approval_bypass",
    "score_tampering",
    "prescription_rule",
    "coupon_abuse",
    "stock_consistency",
    "idempotency",
}
SEVERITY_FACTOR = {"P0": 1.0, "P1": 0.75, "P2": 0.4, "P3": 0.18}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in _read_text(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]], append: bool = False) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if append else "w"
    count = 0
    with path.open(mode, encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_bool(value: Any) -> bool | None:
    if value is True or value is False:
        return bool(value)
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "有效", "是", "valid"}:
        return True
    if text in {"0", "false", "no", "n", "误报", "否", "invalid"}:
        return False
    return None


def _probe_endpoint(probe: dict[str, Any] | None) -> str:
    probe = probe or {}
    method = str(probe.get("method") or "GET").upper()
    path = str(probe.get("path") or probe.get("url") or "/")
    return f"{method} {path}"


def _module_from_path(path: Any) -> str:
    text = str(path or "/")
    bits = [b for b in text.strip("/").split("/") if b and not b.startswith("{")]
    return bits[0].lower() if bits else "root"


def _load_real_project_issues(project_id: str, root: Path | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    p = root / "platform_outputs" / project / "real_project" / "real_project_defect_data.json"
    data = _load_json(p, {})
    if isinstance(data, dict) and isinstance(data.get("issues"), list):
        return [x for x in data["issues"] if isinstance(x, dict)]
    p2 = root / "platform_outputs" / project / "real_project" / "discovered_issues.json"
    data2 = _load_json(p2, {})
    if isinstance(data2, dict) and isinstance(data2.get("items"), list):
        return [x for x in data2["items"] if isinstance(x, dict)]
    return []


def _issue_to_feedback_row(issue: dict[str, Any], default_source: str = "seeded_from_discovery") -> dict[str, Any]:
    evidence = issue.get("evidence") or {}
    request = evidence.get("request") if isinstance(evidence, dict) else {}
    risk = str(issue.get("risk_type") or "unknown")
    sev = str(issue.get("severity") or "P2")
    confidence = _safe_float(issue.get("confidence"), 0.42)
    candidate_only = str(issue.get("actual") or "").startswith("未执行") or confidence <= 0.43
    is_high_value = sev in {"P0", "P1"} or risk in HIGH_VALUE_RISKS
    return {
        "feedback_id": f"FB_{issue.get('issue_id') or issue.get('title') or risk}",
        "issue_id": issue.get("issue_id"),
        "probe_id": str(issue.get("issue_id") or "").replace("ISSUE_", ""),
        "feedback_type": "candidate_review",
        "is_valid_bug": None if candidate_only else True,
        "is_false_positive": None,
        "is_missed_bug": False,
        "is_duplicate": False,
        "is_high_value": bool(is_high_value),
        "human_severity": sev,
        "risk_type": risk,
        "business_domain": issue.get("business_domain") or issue.get("domain"),
        "endpoint": f"{(request or {}).get('method') or 'GET'} {(request or {}).get('url') or (request or {}).get('path') or '/'}",
        "source": default_source,
        "confidence": confidence,
        "reviewer": "sample_reviewer" if default_source.startswith("sample") else "",
        "reviewed_at_utc": _now(),
        "feedback_notes": "Seeded from current discovery output. Replace with real QA review for production learning.",
    }


def seed_sample_strategy_feedback(project_id: str = "real_project_demo", root: Path | None = None, max_items: int = 80) -> list[dict[str, Any]]:
    """Create deterministic sample feedback rows from the latest real-project findings.

    Production usage should replace these rows with real QA feedback. The seeded data is useful for
    demos, tests, and proving the learning loop without requiring a live defect review system.
    """
    root = root or ROOT
    project = _safe_project_id(project_id)
    issues = _load_real_project_issues(project, root)
    rows: list[dict[str, Any]] = []
    for issue in issues[:max_items]:
        row = _issue_to_feedback_row(issue, "sample_seeded_from_real_project_discovery")
        conf = _safe_float(row.get("confidence"), 0.0)
        if conf >= 0.75:
            row["is_valid_bug"] = True
            row["is_false_positive"] = False
        elif conf < 0.45:
            row["is_valid_bug"] = False
            row["is_false_positive"] = True
            row["is_high_value"] = False
        else:
            row["is_valid_bug"] = True
            row["is_false_positive"] = False
        row["_seed"] = True
        row["source"] = "seed_sample"
        rows.append(row)
    if not rows:
        # Fallback rows make the cockpit meaningful even before a live run.
        rows = [
            {"feedback_id": "FB_SAMPLE_001", "_seed": True, "feedback_type": "discovered_bug", "is_valid_bug": True, "is_false_positive": False, "is_missed_bug": False, "is_duplicate": False, "is_high_value": True, "human_severity": "P1", "risk_type": "permission_bypass", "business_domain": "ecommerce", "endpoint": "GET /admin/orders", "source": "sample_seeded_feedback", "reviewer": "sample_reviewer", "reviewed_at_utc": _now(), "feedback_notes": "普通用户访问管理员订单接口被 QA 确认为有效高价值缺陷。"},
            {"feedback_id": "FB_SAMPLE_002", "feedback_type": "false_positive", "is_valid_bug": False, "is_false_positive": True, "is_missed_bug": False, "is_duplicate": False, "is_high_value": False, "human_severity": "P3", "risk_type": "audit_trace", "business_domain": "ecommerce", "endpoint": "GET /orders", "source": "sample_seeded_feedback", "reviewer": "sample_reviewer", "reviewed_at_utc": _now(), "feedback_notes": "审计留痕缺失在当前项目不是阻断风险，降低权重。"},
            {"feedback_id": "FB_SAMPLE_003", "feedback_type": "missed_bug", "is_valid_bug": None, "is_false_positive": False, "is_missed_bug": True, "is_duplicate": False, "is_high_value": True, "human_severity": "P0", "risk_type": "payment", "business_domain": "ecommerce", "endpoint": "POST /payments/callback", "source": "sample_seeded_feedback", "reviewer": "sample_reviewer", "reviewed_at_utc": _now(), "feedback_notes": "QA 反馈漏检支付回调幂等导致重复入账，需要提升 payment/idempotency。"},
        ]
    save_strategy_feedback(project, rows, root=root, append=False)
    return rows


def save_strategy_feedback(project_id: str, rows: list[dict[str, Any]], root: Path | None = None, append: bool = False) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    clean: list[dict[str, Any]] = []
    now = _now()
    for i, row in enumerate(rows or [], start=1):
        if not isinstance(row, dict):
            continue
        item = dict(row)
        item.setdefault("feedback_id", f"FB_{int(time.time())}_{i:04d}")
        item.setdefault("reviewed_at_utc", now)
        item["is_valid_bug"] = _safe_bool(item.get("is_valid_bug"))
        item["is_false_positive"] = _safe_bool(item.get("is_false_positive"))
        item["is_missed_bug"] = bool(_safe_bool(item.get("is_missed_bug")) or str(item.get("feedback_type") or "").lower() == "missed_bug")
        item["is_duplicate"] = bool(_safe_bool(item.get("is_duplicate")) or False)
        item["is_high_value"] = bool(_safe_bool(item.get("is_high_value")) or str(item.get("human_severity") or "") in {"P0", "P1"})
        item["risk_type"] = str(item.get("risk_type") or "unknown")
        item["business_domain"] = str(item.get("business_domain") or item.get("domain") or "unknown")
        item["endpoint"] = str(item.get("endpoint") or _probe_endpoint(item.get("probe") if isinstance(item.get("probe"), dict) else {}))
        item["source"] = str(item.get("source") or "manual_qa_feedback")
        clean.append(item)
    out = root / "platform_workspace" / project / "strategy_learning" / "qa_feedback.jsonl"
    written = _write_jsonl(out, clean, append=append)
    # Keep a copy in inputs so enterprise teams can version this file if desired.
    input_copy = root / "platform_inputs" / project / "strategy_feedback.jsonl"
    if not append:
        _write_jsonl(input_copy, clean, append=False)
    return {"ok": True, "project_id": project, "saved_feedback_rows": written, "path": str(out.relative_to(root)).replace("\\", "/")}


def load_strategy_feedback(project_id: str = "real_project_demo", root: Path | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    rows = _read_jsonl(root / "platform_workspace" / project / "strategy_learning" / "qa_feedback.jsonl")
    if not rows:
        rows = _read_jsonl(root / "platform_inputs" / project / "strategy_feedback.jsonl")
    return rows


def _bump(bucket: dict[str, dict[str, Any]], key: str, row: dict[str, Any], value: float, reason: str) -> None:
    key = str(key or "unknown")
    entry = bucket.setdefault(key, {"key": key, "score_delta": 0.0, "feedback_count": 0, "valid_bug_count": 0, "false_positive_count": 0, "missed_bug_count": 0, "high_value_count": 0, "duplicate_count": 0, "reasons": []})
    entry["score_delta"] = round(float(entry["score_delta"]) + value, 6)
    entry["feedback_count"] += 1
    if row.get("is_valid_bug") is True:
        entry["valid_bug_count"] += 1
    if row.get("is_false_positive") is True or row.get("is_valid_bug") is False:
        entry["false_positive_count"] += 1
    if row.get("is_missed_bug") is True:
        entry["missed_bug_count"] += 1
    if row.get("is_high_value") is True:
        entry["high_value_count"] += 1
    if row.get("is_duplicate") is True:
        entry["duplicate_count"] += 1
    if reason and reason not in entry["reasons"]:
        entry["reasons"].append(reason)


def _row_signal(row: dict[str, Any]) -> tuple[float, str]:
    severity = str(row.get("human_severity") or row.get("severity") or "P2").upper()
    sev = SEVERITY_FACTOR.get(severity, 0.35)
    if row.get("is_missed_bug") is True:
        return 0.22 + sev * 0.16, "QA 标记漏检，提升同类风险和接口探针"
    if row.get("is_false_positive") is True or row.get("is_valid_bug") is False:
        return -(0.16 + sev * 0.08), "QA 标记误报/无效，降低同类风险噪音"
    if row.get("is_duplicate") is True:
        return -0.05, "QA 标记重复，轻微降低重复报告权重"
    if row.get("is_valid_bug") is True and row.get("is_high_value") is True:
        return 0.16 + sev * 0.12, "QA 确认有效高价值 Bug，提升策略权重"
    if row.get("is_valid_bug") is True:
        return 0.07 + sev * 0.05, "QA 确认有效 Bug，适度提升权重"
    return 0.0, "未完成 QA 判定，仅记录样本"


def _normalize_weight(delta: float) -> float:
    # Keep weights bounded so one bad feedback batch cannot dominate discovery.
    return round(max(0.35, min(1.85, 1.0 + delta)), 6)


def _finalize_bucket(bucket: dict[str, dict[str, Any]], name_field: str, top_n: int = 80) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, entry in bucket.items():
        delta = float(entry.get("score_delta") or 0)
        weight = _normalize_weight(delta)
        status = "raise" if weight >= 1.08 else "suppress" if weight <= 0.92 else "neutral"
        rows.append({
            name_field: key,
            "weight": weight,
            "score_delta": round(delta, 6),
            "status": status,
            "feedback_count": entry.get("feedback_count", 0),
            "valid_bug_count": entry.get("valid_bug_count", 0),
            "false_positive_count": entry.get("false_positive_count", 0),
            "missed_bug_count": entry.get("missed_bug_count", 0),
            "high_value_count": entry.get("high_value_count", 0),
            "duplicate_count": entry.get("duplicate_count", 0),
            "reasons": (entry.get("reasons") or [])[:5],
            "recommendation": "increase_probe_budget" if status == "raise" else "downgrade_or_require_more_evidence" if status == "suppress" else "keep_current_budget",
        })
    return sorted(rows, key=lambda x: (-abs(float(x.get("score_delta") or 0)), str(x.get(name_field))))[:top_n]


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False).lower()
    leaks = sorted(m for m in PRIVATE_MARKERS if m.lower() in text)
    return {"passed": not leaks, "leak_terms": leaks}


def build_enterprise_strategy_learning(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    if bool(options.get("seed_sample_feedback")) and not load_strategy_feedback(project, root):
        seed_sample_strategy_feedback(project, root=root)
    cfg = load_real_project_config(project, root)
    profile = load_business_adaptation_profile(project, root) or build_business_adaptation_profile(project, root)
    feedback = load_strategy_feedback(project, root)
    risk_bucket: dict[str, dict[str, Any]] = {}
    domain_bucket: dict[str, dict[str, Any]] = {}
    endpoint_bucket: dict[str, dict[str, Any]] = {}
    source_bucket: dict[str, dict[str, Any]] = {}
    module_bucket: dict[str, dict[str, Any]] = {}
    for row in feedback:
        # Skip seed/demo feedback — only use real QA-verified feedback for learning
        if row.get("source") in ("seed_sample", "demo_feedback") or row.get("_seed", False):
            continue
        signal, reason = _row_signal(row)
        if signal == 0:
            continue
        endpoint = str(row.get("endpoint") or "GET /")
        # Apply strongest signal to risk, then smaller spillover to domain/module/source.
        _bump(risk_bucket, str(row.get("risk_type") or "unknown"), row, signal, reason)
        _bump(domain_bucket, str(row.get("business_domain") or row.get("domain") or "unknown"), row, signal * 0.65, reason)
        _bump(endpoint_bucket, endpoint, row, signal * 0.85, reason)
        _bump(source_bucket, str(row.get("source") or "manual_qa_feedback"), row, signal * 0.35, reason)
        _bump(module_bucket, _module_from_path(endpoint.split(" ", 1)[-1]), row, signal * 0.55, reason)
    risk_weights = _finalize_bucket(risk_bucket, "risk_type")
    domain_weights = _finalize_bucket(domain_bucket, "business_domain")
    endpoint_weights = _finalize_bucket(endpoint_bucket, "endpoint")
    source_weights = _finalize_bucket(source_bucket, "source")
    module_weights = _finalize_bucket(module_bucket, "module")
    raised_risks = [r for r in risk_weights if r.get("status") == "raise"]
    suppressed_risks = [r for r in risk_weights if r.get("status") == "suppress"]
    valid = [r for r in feedback if r.get("is_valid_bug") is True]
    false_pos = [r for r in feedback if r.get("is_false_positive") is True or r.get("is_valid_bug") is False]
    missed = [r for r in feedback if r.get("is_missed_bug") is True]
    high_value = [r for r in feedback if r.get("is_high_value") is True]
    learned_probe_overrides = []
    for row in [*raised_risks[:12], *[m for m in endpoint_weights if m.get("status") == "raise"][:12]]:
        learned_probe_overrides.append({
            "target": row.get("risk_type") or row.get("endpoint"),
            "weight": row.get("weight"),
            "action": row.get("recommendation"),
            "reason": "; ".join(row.get("reasons") or []),
        })
    summary = {
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "feedback_rows": len(feedback),
        "valid_bug_count": len(valid),
        "false_positive_count": len(false_pos),
        "missed_bug_count": len(missed),
        "high_value_bug_count": len(high_value),
        "risk_weight_count": len(risk_weights),
        "endpoint_weight_count": len(endpoint_weights),
        "raised_risk_count": len(raised_risks),
        "suppressed_risk_count": len(suppressed_risks),
        "business_domains": [d.get("domain") for d in profile.get("selected_domains", [])],
    }
    result = {
        "phase": "phase36_enterprise_strategy_learning",
        "summary": summary,
        "risk_type_weights": risk_weights,
        "business_domain_weights": domain_weights,
        "endpoint_weights": endpoint_weights,
        "source_weights": source_weights,
        "module_weights": module_weights,
        "learned_probe_overrides": learned_probe_overrides,
        "feedback_sample": feedback[:20],
        "business_adaptation_digest": {
            "selected_domains": profile.get("selected_domains", []),
            "adaptive_risk_matrix_count": len(profile.get("adaptive_risk_matrix") or []),
            "private_leak_check": profile.get("private_leak_check", {}),
        },
        "governance": {
            "source": "qa_feedback_and_real_project_outputs_only",
            "uses_no_benchmark_answer_files": True,
            "requires_human_confirmation_for_production_promotion": True,
            "bounded_weight_range": [0.35, 1.85],
        },
    }
    result["private_leak_check"] = _private_leak_check(result)
    out_dir = root / "platform_outputs" / project / "strategy_learning"
    ws_dir = root / "platform_workspace" / project / "defect_discovery"
    _write_json(out_dir / "enterprise_strategy_learning.json", result)
    _write_json(out_dir / "enterprise_strategy_learning_summary.json", {"summary": summary, "private_leak_check": result["private_leak_check"]})
    _write_json(ws_dir / "enterprise_strategy_learning.json", result)
    _write_json(ws_dir / "strategy_learning_weights.json", {
        "risk_type_weights": risk_weights,
        "business_domain_weights": domain_weights,
        "endpoint_weights": endpoint_weights,
        "source_weights": source_weights,
        "module_weights": module_weights,
        "summary": summary,
        "private_leak_check": result["private_leak_check"],
    })
    (out_dir / "enterprise_strategy_learning_report.html").write_text(render_enterprise_strategy_learning_report(result), encoding="utf-8")
    return result


def load_enterprise_strategy_learning(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    for p in [
        root / "platform_workspace" / project / "defect_discovery" / "enterprise_strategy_learning.json",
        root / "platform_outputs" / project / "strategy_learning" / "enterprise_strategy_learning.json",
    ]:
        data = _load_json(p, {})
        if isinstance(data, dict) and data:
            return data
    return None


def weights_by_key(rows: list[dict[str, Any]], key_field: str) -> dict[str, dict[str, Any]]:
    return {str(r.get(key_field)): r for r in rows if isinstance(r, dict)}


def render_enterprise_strategy_learning_report(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(k)}</span><b>{_html_escape(v)}</b></div>" for k, v in summary.items() if k not in {"business_domains"})
    def rows(items: list[dict[str, Any]], key: str) -> str:
        return "".join(
            f"<tr><td>{_html_escape(x.get(key))}</td><td>{_html_escape(x.get('weight'))}</td><td>{_html_escape(x.get('status'))}</td><td>{_html_escape(x.get('feedback_count'))}</td><td>{_html_escape(x.get('valid_bug_count'))}</td><td>{_html_escape(x.get('false_positive_count'))}</td><td>{_html_escape(x.get('missed_bug_count'))}</td><td>{_html_escape('; '.join(x.get('reasons') or []))}</td></tr>"
            for x in items[:80]
        ) or "<tr><td colspan='8'>暂无学习权重，请先保存 QA 反馈。</td></tr>"
    leak = result.get("private_leak_check") or {}
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>Enterprise Strategy Learning</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#fef3c7;color:#92400e}}</style></head><body>
<section class='hero'><span class='badge'>Phase36</span><h1>企业 Bug 发现策略自学习中心</h1><p>把 QA 确认的有效 Bug、误报、漏检沉淀为 risk/domain/endpoint/source 权重，并影响下一轮 Risk-based Probe Planner。</p><p>私有数据泄露检查：<b>{_html_escape('passed' if leak.get('passed') else 'failed')}</b></p></section>
<section class='panel'><h2>学习概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>风险类型权重</h2><table><thead><tr><th>Risk</th><th>Weight</th><th>Status</th><th>Feedback</th><th>Valid</th><th>FP</th><th>Missed</th><th>Reason</th></tr></thead><tbody>{rows(result.get('risk_type_weights') or [], 'risk_type')}</tbody></table></section>
<section class='panel'><h2>业务域权重</h2><table><thead><tr><th>Domain</th><th>Weight</th><th>Status</th><th>Feedback</th><th>Valid</th><th>FP</th><th>Missed</th><th>Reason</th></tr></thead><tbody>{rows(result.get('business_domain_weights') or [], 'business_domain')}</tbody></table></section>
<section class='panel'><h2>接口权重</h2><table><thead><tr><th>Endpoint</th><th>Weight</th><th>Status</th><th>Feedback</th><th>Valid</th><th>FP</th><th>Missed</th><th>Reason</th></tr></thead><tbody>{rows(result.get('endpoint_weights') or [], 'endpoint')}</tbody></table></section>
<section class='panel'><h2>下一轮策略</h2><p>Risk-based Probe Planner 会读取 <code>platform_workspace/&lt;project&gt;/defect_discovery/strategy_learning_weights.json</code>，对已确认高价值风险加权，对误报高发风险降权。</p></section>
</body></html>"""


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = argv[0] if argv else "real_project_demo"
    result = build_enterprise_strategy_learning(project, options={"seed_sample_feedback": True})
    print(json.dumps({"ok": True, "project_id": project, "summary": result.get("summary"), "private_leak_check": result.get("private_leak_check")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
