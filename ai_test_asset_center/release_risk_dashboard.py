from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _html_escape, _safe_project_id, config_paths, load_real_project_config
from .risk_based_probe_planner import build_risk_based_probe_plan, load_risk_based_probe_plan
from .multi_industry_business_reasoning import build_multi_industry_business_profile, load_multi_industry_business_profile
from .enterprise_knowledge_center import build_enterprise_business_knowledge_asset, load_enterprise_business_knowledge_asset
from .enterprise_testops_control_plane import build_enterprise_testops_control_plane, load_enterprise_testops_control_plane
from .product_ui import callout, detail_list, h, metric_card, product_shell, section, status_badge, table

PRIVATE_MARKERS = {
    "private_ground_truth",
    "ground_truth_bugs",
    "bug_sets",
    "enabled_bugs",
    "current_bug_set",
    "bug_instance_id",
}

SEVERITY_WEIGHT = {"P0": 1.0, "P1": 0.75, "P2": 0.38, "P3": 0.18}
RELEASE_DECISION_LABELS = {
    "block_release": "建议阻断发布",
    "hold_for_review": "需要人工复核",
    "limited_canary": "仅建议小流量灰度",
    "allow_release": "可继续发布流程",
}


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


def _counter(items: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(item or "unknown")
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _load_real_project_data(project: str, root: Path) -> dict[str, Any]:
    paths = config_paths(project, root)
    data = _read_json(paths["output_dir"] / "real_project_defect_data.json", {})
    return data if isinstance(data, dict) else {}


def _load_historical_profile(project: str, root: Path) -> dict[str, Any]:
    path = root / "platform_workspace" / project / "defect_discovery" / "real_project_risk_profile.json"
    data = _read_json(path, {})
    return data if isinstance(data, dict) else {}


def _load_issue_items(project: str, root: Path) -> list[dict[str, Any]]:
    paths = config_paths(project, root)
    data = _read_json(paths["output_dir"] / "discovered_issues.json", {})
    items = data.get("items") if isinstance(data, dict) else []
    if not isinstance(items, list):
        items = []
    return [x for x in items if isinstance(x, dict)]


def _issue_url(issue: dict[str, Any]) -> str:
    ev = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    req = ev.get("request") if isinstance(ev.get("request"), dict) else {}
    return str(req.get("url") or issue.get("path") or "")


def _issue_method(issue: dict[str, Any]) -> str:
    ev = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
    req = ev.get("request") if isinstance(ev.get("request"), dict) else {}
    return str(req.get("method") or issue.get("method") or "GET").upper()


def _module_from_path(path: str) -> str:
    p = str(path or "").strip("/").lower()
    if not p:
        return "unknown"
    first = p.split("/")[0]
    aliases = {
        "admin": "admin",
        "orders": "order",
        "order": "order",
        "payments": "payment",
        "payment": "payment",
        "refunds": "refund",
        "refund": "refund",
        "coupons": "coupon",
        "coupon": "coupon",
        "inventory": "inventory",
        "stock": "inventory",
        "tenant": "tenant",
        "users": "user",
        "user": "user",
    }
    return aliases.get(first, first or "unknown")


def _qa_counts(issues: list[dict[str, Any]]) -> dict[str, int]:
    statuses = []
    for issue in issues:
        status = issue.get("qa_feedback_status") or issue.get("status") or "pending"
        statuses.append(str(status))
    return _counter(statuses)


def _risk_recurrence(issues: list[dict[str, Any]], historical_profile: dict[str, Any]) -> dict[str, Any]:
    hist_risks = historical_profile.get("risk_distribution") or historical_profile.get("risk_type_distribution") or {}
    if not isinstance(hist_risks, dict):
        hist_risks = {}
    recurrent: list[dict[str, Any]] = []
    for issue in issues:
        risk = str(issue.get("risk_type") or "unknown")
        if risk in hist_risks:
            recurrent.append(
                {
                    "issue_id": issue.get("issue_id"),
                    "risk_type": risk,
                    "historical_count": int(hist_risks.get(risk) or 0),
                    "severity": issue.get("severity"),
                    "confidence": issue.get("confidence"),
                }
            )
    return {
        "recurrent_issue_count": len(recurrent),
        "recurrent_risk_types": _counter([x["risk_type"] for x in recurrent]),
        "recurrence_rate": round(len(recurrent) / max(1, len(issues)), 3),
        "examples": recurrent[:20],
    }


def _probe_coverage(project: str, root: Path, defect_data: dict[str, Any]) -> dict[str, Any]:
    risk_plan = load_risk_based_probe_plan(project, root)
    if risk_plan is None:
        try:
            risk_plan = build_risk_based_probe_plan(project, root)
        except Exception:
            risk_plan = None
    plan_summary = (risk_plan or {}).get("summary", {}) if isinstance(risk_plan, dict) else {}
    openapi_ops = int(plan_summary.get("openapi_operation_count") or 0)
    selected = int(plan_summary.get("selected_probe_count") or len(defect_data.get("probes") or []))
    executed = len(defect_data.get("probe_execution_result") or [])
    risk_dist = plan_summary.get("risk_distribution") if isinstance(plan_summary.get("risk_distribution"), dict) else {}
    operation_coverage = round(min(1.0, selected / max(1, openapi_ops)), 3) if openapi_ops else 0.0
    execution_coverage = round(min(1.0, executed / max(1, selected)), 3) if selected else 0.0
    return {
        "openapi_operation_count": openapi_ops,
        "selected_probe_count": selected,
        "executed_probe_count": executed,
        "operation_coverage_estimate": operation_coverage,
        "execution_coverage": execution_coverage,
        "planned_risk_distribution": risk_dist,
    }


def _rank_high_risk_apis(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for issue in issues:
        path = _issue_url(issue)
        method = _issue_method(issue)
        key = f"{method} {path}"
        row = rows.setdefault(key, {"api": key, "issue_count": 0, "max_confidence": 0.0, "max_severity": "P3", "risk_types": set()})
        row["issue_count"] += 1
        row["max_confidence"] = max(float(row["max_confidence"]), _safe_float(issue.get("confidence")))
        sev = str(issue.get("severity") or "P3")
        if SEVERITY_WEIGHT.get(sev, 0) > SEVERITY_WEIGHT.get(str(row["max_severity"]), 0):
            row["max_severity"] = sev
        row["risk_types"].add(str(issue.get("risk_type") or "unknown"))
    result = []
    for row in rows.values():
        score = row["issue_count"] * 8 + row["max_confidence"] * 20 + SEVERITY_WEIGHT.get(row["max_severity"], 0.18) * 20
        result.append({**row, "risk_types": sorted(row["risk_types"]), "api_risk_score": round(score, 3)})
    return sorted(result, key=lambda x: (-float(x["api_risk_score"]), str(x["api"])))[:20]


def _rank_high_risk_modules(issues: list[dict[str, Any]], historical_profile: dict[str, Any]) -> list[dict[str, Any]]:
    hist_modules = historical_profile.get("module_distribution") or historical_profile.get("high_risk_modules") or {}
    if not isinstance(hist_modules, dict):
        hist_modules = {}
    rows: dict[str, dict[str, Any]] = {}
    for issue in issues:
        module = _module_from_path(_issue_url(issue))
        row = rows.setdefault(module, {"module": module, "issue_count": 0, "p0_p1_count": 0, "historical_count": int(hist_modules.get(module, 0) or 0), "risk_types": set()})
        row["issue_count"] += 1
        if str(issue.get("severity") or "P3") in {"P0", "P1"}:
            row["p0_p1_count"] += 1
        row["risk_types"].add(str(issue.get("risk_type") or "unknown"))
    for module, count in hist_modules.items():
        rows.setdefault(str(module), {"module": str(module), "issue_count": 0, "p0_p1_count": 0, "historical_count": int(count or 0), "risk_types": set()})
    result = []
    for row in rows.values():
        score = row["p0_p1_count"] * 20 + row["issue_count"] * 8 + min(20, row["historical_count"] * 3)
        result.append({**row, "risk_types": sorted(row["risk_types"]), "module_risk_score": round(score, 3)})
    return sorted(result, key=lambda x: (-float(x["module_risk_score"]), str(x["module"])))[:20]


def _industry_business_understanding(project: str, root: Path, defect_data: dict[str, Any]) -> dict[str, Any]:
    """Load the evidence-backed Phase57 business graph for the release view."""
    candidate = defect_data.get("multi_industry_business_profile") if isinstance(defect_data, dict) else None
    if isinstance(candidate, dict) and candidate:
        return candidate
    profile = load_multi_industry_business_profile(project, root)
    if profile is not None:
        return profile
    try:
        return build_multi_industry_business_profile(project, root)
    except Exception as exc:
        return {
            "phase": "phase57_multi_industry_business_reasoning",
            "summary": {"inference_status": "unavailable", "error": str(exc)[:240]},
            "recognized_industries": [],
            "risk_domains": [],
            "industry_oracles": [],
            "business_objects": [],
            "state_machines": [],
            "permission_boundaries": [],
            "data_dependencies": [],
        }


def _enterprise_business_knowledge(project: str, root: Path, defect_data: dict[str, Any]) -> dict[str, Any]:
    """Load the Phase58 traceable enterprise knowledge asset for release governance."""
    candidate = defect_data.get("enterprise_business_knowledge_asset") if isinstance(defect_data, dict) else None
    if isinstance(candidate, dict) and candidate.get("summary"):
        return candidate
    profile = load_enterprise_business_knowledge_asset(project, root)
    if profile is not None:
        return profile
    try:
        return build_enterprise_business_knowledge_asset(project, root)
    except Exception as exc:
        return {
            "phase": "phase58_enterprise_knowledge_unified_ingestion",
            "summary": {"knowledge_ready": False, "error": str(exc)[:240]},
            "module_tree": [],
            "rule_library": [],
            "permission_matrix": [],
            "data_dependencies": [],
            "risk_domains": [],
            "oracle_library": [],
            "relationships": [],
            "source_inventory": [],
        }



def _enterprise_testops_control(project: str, root: Path, defect_data: dict[str, Any]) -> dict[str, Any]:
    """Load the Phase59 control plane without duplicating its owned artifacts."""
    candidate = defect_data.get("enterprise_testops_control_plane") if isinstance(defect_data, dict) else None
    if isinstance(candidate, dict) and candidate.get("phase"):
        return candidate
    profile = load_enterprise_testops_control_plane(project, root)
    if profile is not None:
        return profile
    try:
        return build_enterprise_testops_control_plane(project, root)
    except Exception as exc:
        return {"phase": "phase59_enterprise_testops_control_plane", "error": str(exc)[:240]}


def _enterprise_testops_summary(control: dict[str, Any]) -> dict[str, Any]:
    health = control.get("environment_health") if isinstance(control.get("environment_health"), dict) else {}
    data = control.get("test_data") if isinstance(control.get("test_data"), dict) else {}
    journeys = control.get("journey_graph") if isinstance(control.get("journey_graph"), dict) else {}
    permission = control.get("permission_risk_report") if isinstance(control.get("permission_risk_report"), dict) else {}
    quality = control.get("defect_quality_report") if isinstance(control.get("defect_quality_report"), dict) else {}
    security = control.get("security_audit_report") if isinstance(control.get("security_audit_report"), dict) else {}
    return {
        "enterprise_testops_available": bool(control and not control.get("error")),
        "enterprise_testops_target_environment": health.get("target_environment"),
        "enterprise_testops_environment_testable": bool(health.get("target_testable")),
        "enterprise_testops_auto_data_ratio": _safe_float(data.get("automatic_preparation_ratio")),
        "enterprise_testops_journey_count": int(((journeys.get("coverage") or {}).get("journey_count") or 0)),
        "enterprise_testops_permission_probe_count": int(((permission.get("permission_coverage") or {}).get("probe_count") or 0)),
        "enterprise_testops_high_confidence_defect_count": int(((quality.get("summary") or {}).get("high_confidence_count") or 0)),
        "enterprise_testops_environment_problem_count": int(((quality.get("summary") or {}).get("environment_problem_count") or 0)),
        "enterprise_testops_security_audit_chain_valid": bool((security.get("audit_chain") or {}).get("valid", True)),
    }


def _enterprise_knowledge_summary(asset: dict[str, Any]) -> dict[str, Any]:
    summary = asset.get("summary") if isinstance(asset, dict) and isinstance(asset.get("summary"), dict) else {}
    return {
        "enterprise_knowledge_ready": bool(summary.get("knowledge_ready")),
        "enterprise_knowledge_source_count": int(summary.get("active_source_count") or 0),
        "enterprise_knowledge_module_count": int(summary.get("module_count") or len(asset.get("module_tree") or [])),
        "enterprise_knowledge_rule_count": int(summary.get("rule_count") or len(asset.get("rule_library") or [])),
        "enterprise_knowledge_permission_matrix_count": int(summary.get("permission_matrix_count") or len(asset.get("permission_matrix") or [])),
        "enterprise_knowledge_dependency_count": int(summary.get("data_dependency_count") or len(asset.get("data_dependencies") or [])),
        "enterprise_knowledge_oracle_count": int(summary.get("oracle_count") or len(asset.get("oracle_library") or [])),
        "enterprise_knowledge_risk_domain_count": int(summary.get("risk_domain_count") or len(asset.get("risk_domains") or [])),
        "enterprise_knowledge_relationship_count": int(summary.get("relationship_count") or len(asset.get("relationships") or [])),
        "enterprise_knowledge_probe_count": int(summary.get("generated_probe_count") or 0),
        "enterprise_knowledge_asset_id": asset.get("asset_id"),
    }


def _industry_summary(profile: dict[str, Any]) -> dict[str, Any]:
    rows = profile.get("recognized_industries") if isinstance(profile.get("recognized_industries"), list) else []
    risks = profile.get("risk_domains") if isinstance(profile.get("risk_domains"), list) else []
    oracles = profile.get("industry_oracles") if isinstance(profile.get("industry_oracles"), list) else []
    return {
        "recognized_industry_count": len(rows),
        "recognized_industries": [str(row.get("industry")) for row in rows if isinstance(row, dict)],
        "top_industry": ((profile.get("summary") or {}).get("top_industry") or (rows[0].get("industry") if rows and isinstance(rows[0], dict) else "unknown_general_business")),
        "industry_business_object_count": len(profile.get("business_objects") or []),
        "industry_state_machine_count": len(profile.get("state_machines") or []),
        "industry_permission_boundary_count": len(profile.get("permission_boundaries") or []),
        "industry_oracle_count": len(oracles),
        "industry_risk_domain_count": len(risks),
        "industry_p0_risk_domain_count": sum(1 for item in risks if isinstance(item, dict) and str(item.get("severity")) == "P0"),
        "industry_inference_mode": profile.get("inference_mode") or ((profile.get("summary") or {}).get("inference_mode") or "unknown"),
    }


def _score_release_risk(issues: list[dict[str, Any]], metrics: dict[str, Any], onboarding_ok: bool, recurrence: dict[str, Any], coverage: dict[str, Any]) -> dict[str, Any]:
    p0 = sum(1 for i in issues if str(i.get("severity")) == "P0")
    p1 = sum(1 for i in issues if str(i.get("severity")) == "P1")
    high_conf = sum(1 for i in issues if _safe_float(i.get("confidence")) >= 0.75)
    blockers = int(metrics.get("suggested_release_blockers") or 0)
    evidence = _safe_float(metrics.get("evidence_completeness"))
    recurrence_rate = _safe_float(recurrence.get("recurrence_rate"))
    execution_coverage = _safe_float(coverage.get("execution_coverage"))
    score = 0.0
    score += min(35, p0 * 30 + p1 * 14)
    score += min(25, blockers * 18)
    score += min(15, high_conf * 4)
    score += min(12, recurrence_rate * 24)
    if not onboarding_ok:
        score += 8
    if evidence < 0.65 and issues:
        score += 5
    if execution_coverage < 0.5:
        score += 4
    score = round(min(100.0, score), 3)
    if p0 > 0 or blockers >= 2 or score >= 78:
        decision = "block_release"
    elif blockers >= 1 or score >= 45 or high_conf > 0:
        decision = "hold_for_review"
    elif score >= 25 or issues:
        decision = "limited_canary"
    else:
        decision = "allow_release"
    return {
        "release_risk_score": score,
        "decision": decision,
        "decision_label": RELEASE_DECISION_LABELS[decision],
        "p0_issue_count": p0,
        "p1_issue_count": p1,
        "p0_p1_issue_count": p0 + p1,
        "high_confidence_issue_count": high_conf,
        "blocker_issue_count": blockers,
    }


def _next_actions(score: dict[str, Any], high_risk_apis: list[dict[str, Any]], recurrence: dict[str, Any], industry_summary: dict[str, Any] | None = None, enterprise_knowledge_summary: dict[str, Any] | None = None) -> list[str]:
    actions: list[str] = []
    decision = score.get("decision")
    if decision == "block_release":
        actions.append("先阻断发布，优先复核 P0/P1 和高置信问题，完成修复后重新运行真实项目发现。")
    elif decision == "hold_for_review":
        actions.append("进入 QA 反馈评审，确认高置信问题是否有效，再决定是否进入灰度。")
    elif decision == "limited_canary":
        actions.append("可考虑小流量灰度，但需要补充人工复核和重点接口回归。")
    else:
        actions.append("当前未发现明显发布阻断项，可继续发布流程并保留监控。")
    if high_risk_apis:
        actions.append(f"优先复测高风险接口：{high_risk_apis[0]['api']}。")
    if int(recurrence.get("recurrent_issue_count") or 0) > 0:
        actions.append("本轮疑似问题命中企业历史高发风险，建议将相关模块加入下一轮重点回归。")
    industry_summary = industry_summary or {}
    industries = industry_summary.get("recognized_industries") or []
    p0_domains = int(industry_summary.get("industry_p0_risk_domain_count") or 0)
    if industries:
        actions.append(f"按已识别业务场景（{'、'.join(str(x) for x in industries[:3])}）复核行业 Oracle 与关键业务对象覆盖。")
    if p0_domains:
        actions.append(f"先闭环 {p0_domains} 个行业语义 P0 风险域的 Oracle、隔离环境验证与发布前证据。")
    enterprise_knowledge_summary = enterprise_knowledge_summary or {}
    if enterprise_knowledge_summary.get("enterprise_knowledge_ready"):
        actions.append(f"基于 {enterprise_knowledge_summary.get('enterprise_knowledge_source_count', 0)} 份受控资料，优先验证 {enterprise_knowledge_summary.get('enterprise_knowledge_rule_count', 0)} 条规则与 {enterprise_knowledge_summary.get('enterprise_knowledge_oracle_count', 0)} 个 Oracle 的证据闭环。")
    actions.append("把 QA 确认结果写入反馈评审，驱动 feedback-adjusted 策略更新。")
    return actions


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False).lower()
    leaks = sorted([m for m in PRIVATE_MARKERS if m.lower() in text])
    return {"passed": not leaks, "leak_terms": leaks}


def build_release_risk_dashboard(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    defect_data = _load_real_project_data(project, root)
    issues = defect_data.get("issues") if isinstance(defect_data.get("issues"), list) else _load_issue_items(project, root)
    issues = [x for x in issues if isinstance(x, dict)]
    metrics = defect_data.get("metrics") if isinstance(defect_data.get("metrics"), dict) else {}
    historical_profile = _load_historical_profile(project, root)
    recurrence = _risk_recurrence(issues, historical_profile)
    coverage = _probe_coverage(project, root, defect_data if isinstance(defect_data, dict) else {})
    industry_profile = _industry_business_understanding(project, root, defect_data if isinstance(defect_data, dict) else {})
    industry_summary = _industry_summary(industry_profile)
    enterprise_knowledge_asset = _enterprise_business_knowledge(project, root, defect_data if isinstance(defect_data, dict) else {})
    enterprise_knowledge_summary = _enterprise_knowledge_summary(enterprise_knowledge_asset)
    enterprise_testops_control = _enterprise_testops_control(project, root, defect_data if isinstance(defect_data, dict) else {})
    enterprise_testops_summary = _enterprise_testops_summary(enterprise_testops_control)
    high_risk_apis = _rank_high_risk_apis(issues)
    high_risk_modules = _rank_high_risk_modules(issues, historical_profile)
    qa_status = _qa_counts(issues)
    score = _score_release_risk(issues, metrics, bool(defect_data.get("onboarding_ok")), recurrence, coverage)
    if enterprise_testops_summary.get("enterprise_testops_available") and not enterprise_testops_summary.get("enterprise_testops_environment_testable"):
        score["release_risk_score"] = round(min(100.0, _safe_float(score.get("release_risk_score")) + 14.0), 3)
        score["decision"] = "block_release"
        score["decision_label"] = RELEASE_DECISION_LABELS["block_release"]
        score["environment_preflight_blocked"] = True
    if not enterprise_testops_summary.get("enterprise_testops_security_audit_chain_valid"):
        score["release_risk_score"] = round(min(100.0, _safe_float(score.get("release_risk_score")) + 10.0), 3)
        score["decision"] = "block_release"
        score["decision_label"] = RELEASE_DECISION_LABELS["block_release"]
        score["security_audit_blocked"] = True
    next_actions = _next_actions(score, high_risk_apis, recurrence, industry_summary, enterprise_knowledge_summary)
    if score.get("environment_preflight_blocked"):
        next_actions.insert(1, "目标环境尚不可测，先修复环境健康检查中的缺失项；不要把环境问题误判为业务缺陷。")
    if score.get("security_audit_blocked"):
        next_actions.insert(1, "安全审计链校验失败，先处理审计完整性问题后再继续发布判断。")
    risk_distribution = _counter([i.get("risk_type") for i in issues])
    severity_distribution = _counter([i.get("severity") for i in issues])
    dashboard = {
        "phase": "phase27_release_risk_dashboard",
        "project_id": project,
        "project_name": cfg.get("project_name") or defect_data.get("project_name") or project,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": cfg.get("discovery_mode") or defect_data.get("mode") or "standard",
        "summary": {
            **score,
            "issue_count": len(issues),
            "needs_human_review": qa_status.get("needs_human_review", 0) + qa_status.get("pending", 0),
            "evidence_completeness": metrics.get("evidence_completeness", 0),
            "estimated_hours_saved": metrics.get("estimated_hours_saved", 0),
            "onboarding_ok": bool(defect_data.get("onboarding_ok")),
            **industry_summary,
            **enterprise_knowledge_summary,
            **enterprise_testops_summary,
        },
        "risk_distribution": risk_distribution,
        "severity_distribution": severity_distribution,
        "qa_status_distribution": qa_status,
        "historical_risk_recurrence": recurrence,
        "probe_coverage": coverage,
        "enterprise_testops_control_plane": {
            "summary": enterprise_testops_summary,
            "environment_health": enterprise_testops_control.get("environment_health") if isinstance(enterprise_testops_control, dict) else {},
            "test_data": enterprise_testops_control.get("test_data") if isinstance(enterprise_testops_control, dict) else {},
            "journey_graph": enterprise_testops_control.get("journey_graph") if isinstance(enterprise_testops_control, dict) else {},
            "permission_risk_report": enterprise_testops_control.get("permission_risk_report") if isinstance(enterprise_testops_control, dict) else {},
            "defect_quality_report": enterprise_testops_control.get("defect_quality_report") if isinstance(enterprise_testops_control, dict) else {},
            "security_audit_report": enterprise_testops_control.get("security_audit_report") if isinstance(enterprise_testops_control, dict) else {},
        },
        "enterprise_business_knowledge": {
            "summary": enterprise_knowledge_summary,
            "asset_id": enterprise_knowledge_asset.get("asset_id"),
            "source_inventory": enterprise_knowledge_asset.get("source_inventory") or [],
            "module_tree": enterprise_knowledge_asset.get("module_tree") or [],
            "rule_library": enterprise_knowledge_asset.get("rule_library") or [],
            "permission_matrix": enterprise_knowledge_asset.get("permission_matrix") or [],
            "data_dependencies": enterprise_knowledge_asset.get("data_dependencies") or [],
            "risk_domains": enterprise_knowledge_asset.get("risk_domains") or [],
            "oracle_library": enterprise_knowledge_asset.get("oracle_library") or [],
            "relationships": enterprise_knowledge_asset.get("relationships") or [],
        },
        "industry_business_understanding": {
            "summary": industry_summary,
            "recognized_industries": industry_profile.get("recognized_industries") or [],
            "modules": industry_profile.get("modules") or [],
            "business_objects": industry_profile.get("business_objects") or [],
            "state_machines": industry_profile.get("state_machines") or [],
            "permission_boundaries": industry_profile.get("permission_boundaries") or [],
            "data_dependencies": industry_profile.get("data_dependencies") or [],
            "industry_oracles": industry_profile.get("industry_oracles") or [],
            "risk_domains": industry_profile.get("risk_domains") or [],
            "inference_mode": industry_profile.get("inference_mode"),
        },
        "high_risk_apis": high_risk_apis,
        "high_risk_modules": high_risk_modules,
        "suggested_release_blockers": _sanitize_issue_list(defect_data.get("suggested_release_blockers") or []),
        "top_issues": _sanitize_issue_list(issues)[:50],
        "next_actions": next_actions,
        "governance": {
            "real_project_mode": True,
            "uses_public_project_inputs_only": True,
            "does_not_use_benchmark_answer_files": True,
            "dashboard_inputs": ["real_project_defect_data", "risk_based_probe_plan", "enterprise_risk_profile", "qa_feedback_status", "multi_industry_business_reasoning", "enterprise_knowledge_unified_ingestion"],
        },
    }
    dashboard["private_leak_check"] = _private_leak_check(dashboard)
    out_dir = root / "platform_outputs" / project / "release_risk_dashboard"
    ws_dir = root / "platform_workspace" / project / "defect_discovery"
    _write_json(out_dir / "release_risk_dashboard.json", dashboard)
    _write_json(out_dir / "release_risk_summary.json", {"summary": dashboard["summary"], "private_leak_check": dashboard["private_leak_check"]})
    _write_json(ws_dir / "release_risk_dashboard.json", dashboard)
    _write_text(out_dir / "release_risk_dashboard.html", render_release_risk_dashboard_html(dashboard))
    return dashboard


def _sanitize_issue_list(items: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for issue in items if isinstance(items, list) else []:
        if not isinstance(issue, dict):
            continue
        ev = issue.get("evidence") if isinstance(issue.get("evidence"), dict) else {}
        req = ev.get("request") if isinstance(ev.get("request"), dict) else {}
        resp = ev.get("response") if isinstance(ev.get("response"), dict) else {}
        result.append(
            {
                "issue_id": issue.get("issue_id"),
                "title": issue.get("title"),
                "risk_type": issue.get("risk_type"),
                "severity": issue.get("severity"),
                "confidence": issue.get("confidence"),
                "status": issue.get("status"),
                "qa_feedback_status": issue.get("qa_feedback_status"),
                "expected": issue.get("expected"),
                "actual": issue.get("actual"),
                "business_impact": issue.get("business_impact"),
                "suggested_fix": issue.get("suggested_fix"),
                "request": {"method": req.get("method"), "url": req.get("url")},
                "response": {"status_code": resp.get("status_code"), "error": resp.get("error")},
            }
        )
    return result


def render_release_risk_dashboard_html(dashboard: dict[str, Any]) -> str:
    """Render the release gate in the same enterprise product shell."""
    summary = dashboard.get("summary") or {}
    decision = str(summary.get("decision") or "hold_for_review")
    decision_label = str(summary.get("decision_label") or RELEASE_DECISION_LABELS.get(decision, decision))
    decision_tone = "danger" if decision == "block_release" else "warning" if decision in {"hold_for_review", "limited_canary"} else "success"
    coverage = dashboard.get("probe_coverage") or {}
    recurrence = dashboard.get("historical_risk_recurrence") or {}
    industry = dashboard.get("industry_business_understanding") or {}
    industry_summary = industry.get("summary") or {}
    enterprise_knowledge = dashboard.get("enterprise_business_knowledge") or {}
    enterprise_knowledge_summary = enterprise_knowledge.get("summary") or {}
    enterprise_testops = dashboard.get("enterprise_testops_control_plane") or {}
    enterprise_testops_summary = enterprise_testops.get("summary") or {}
    leak = dashboard.get("private_leak_check") or {}

    cards = "".join([
        metric_card("发布建议", decision_label, "由风险、证据、环境与门禁共同决定", decision_tone, "release"),
        metric_card("发布风险分", summary.get("release_risk_score", 0), "高分表示需要优先复核", "danger" if decision == "block_release" else "warning", "risk"),
        metric_card("已执行 Probe", coverage.get("executed_probe_count", 0), f"执行覆盖率 {float(coverage.get('execution_coverage') or 0) * 100:.0f}%", "default", "runtime"),
        metric_card("历史风险复现", recurrence.get("recurrent_issue_count", 0), f"复现比例 {float(recurrence.get('recurrence_rate') or 0) * 100:.0f}%", "warning" if (recurrence.get("recurrent_issue_count") or 0) else "success", "benchmark"),
    ])
    risk_rows = [[h(k), h(v)] for k, v in (dashboard.get("risk_distribution") or {}).items()]
    api_rows = [[
        f"<code>{h(a.get('api') or '-')}</code>", h(a.get("api_risk_score") or 0), h(a.get("issue_count") or 0), status_badge(a.get("max_severity") or "-"), h("、".join(a.get("risk_types") or []) or "-"),
    ] for a in (dashboard.get("high_risk_apis") or [])[:20]]
    module_rows = [[
        h(m.get("module") or "-"), h(m.get("module_risk_score") or 0), h(m.get("issue_count") or 0), status_badge(m.get("p0_p1_count") or 0), h(m.get("historical_count") or 0),
    ] for m in (dashboard.get("high_risk_modules") or [])[:20]]
    issue_rows = [[
        status_badge(i.get("severity") or "-"), h(i.get("title") or "-"), h(i.get("risk_type") or "-"), h(i.get("confidence") or "-"),
        f"<code>{h((i.get('request') or {}).get('method') or '')} {h((i.get('request') or {}).get('url') or '')}</code>", h(i.get("actual") or "-"),
    ] for i in (dashboard.get("top_issues") or [])[:50]]
    industry_rows = [[
        h(row.get("industry") or "-"), h(row.get("name") or "-"), h(row.get("confidence") or "-"), h("、".join(str(x) for x in row.get("matched_objects") or []) or "-"),
    ] for row in (industry.get("recognized_industries") or []) if isinstance(row, dict)]
    action_items = dashboard.get("next_actions") or []
    actions_html = ("<ul class='inline-list'>" + "".join(f"<li>{h(item)}</li>" for item in action_items) + "</ul>") if action_items else callout("暂无阻断动作", "当前没有额外的发布建议。", "success", "release")
    enterprise_body = (
        "<div class='two-col'>"
        "<div class='subtle-card'><h3>企业 TestOps 发布前置</h3>" + detail_list([
            ("目标环境", enterprise_testops_summary.get("enterprise_testops_target_environment") or "-"),
            ("环境可测", "是" if enterprise_testops_summary.get("enterprise_testops_environment_testable") else "否"),
            ("自动准备数据", f"{float(enterprise_testops_summary.get('enterprise_testops_auto_data_ratio') or 0) * 100:.0f}%"),
            ("跨系统 Journey", enterprise_testops_summary.get("enterprise_testops_journey_count") or 0),
            ("权限探针", enterprise_testops_summary.get("enterprise_testops_permission_probe_count") or 0),
        ]) + "</div>"
        "<div class='subtle-card'><h3>企业业务知识资产</h3>" + detail_list([
            ("来源资料", enterprise_knowledge_summary.get("enterprise_knowledge_source_count") or 0),
            ("业务规则", enterprise_knowledge_summary.get("enterprise_knowledge_rule_count") or 0),
            ("业务 Oracle", enterprise_knowledge_summary.get("enterprise_knowledge_oracle_count") or 0),
            ("可追溯关系", enterprise_knowledge_summary.get("enterprise_knowledge_relationship_count") or 0),
            ("私有答案泄露检查", "通过" if leak.get("passed") else "待复核"),
        ]) + "</div></div>"
    )
    industry_body = (
        "<div class='split'><div>" + table(["类别", "名称", "置信度", "匹配对象"], industry_rows, "未识别到明确业务模式时，会回退到通用业务质量模式。") + "</div>"
        "<div class='subtle-card'><h3>业务规则摘要</h3>" + detail_list([
            ("识别模式", industry.get("inference_mode") or "document_evidence"),
            ("主要对象", industry_summary.get("top_industry") or "unknown_general_business"),
            ("业务 Oracle", industry_summary.get("industry_oracle_count") or 0),
            ("P0 风险域", industry_summary.get("industry_p0_risk_domain_count") or 0),
        ]) + "</div></div>"
    )
    body = (
        f"<div class='metric-grid'>{cards}</div>"
        + section("发布决策", "发布结论必须同时考虑高价值风险、环境健康、证据可信度和安全门禁。", callout("当前建议：" + decision_label, "发布门禁不会因界面操作被绕过；环境与数据前置问题会进入去噪分流。", decision_tone, "release"), section_id="overview")
        + section("覆盖与历史风险", "覆盖率用于衡量本轮验证强度，历史风险用于识别回归与易复发区域。", "<div class='two-col'><div>" + detail_list([
            ("OpenAPI 操作", coverage.get("openapi_operation_count") or 0),
            ("入选 Probe", coverage.get("selected_probe_count") or 0),
            ("已执行 Probe", coverage.get("executed_probe_count") or 0),
            ("执行覆盖率", f"{float(coverage.get('execution_coverage') or 0) * 100:.0f}%"),
        ]) + "</div><div>" + table(["风险类型", "数量"], risk_rows, "暂无风险分布。") + "</div></div>", section_id="assets")
        + section("企业 TestOps 发布前置", "目标环境不可测或安全审计链异常会阻断发布判断。", enterprise_body, section_id="environment")
        + section("业务知识与规则", "基于企业资料自动提取业务对象、状态机、权限边界和守恒规则，不代替客户人工确认。", industry_body, section_id="knowledge")
        + section("高风险接口", "按风险分、问题数量、严重级别和风险类型排序，帮助研发优先排查。", table(["接口", "风险分", "问题数", "最高等级", "风险类型"], api_rows, "暂无高风险接口。"), section_id="risk")
        + section("高风险模块", "结合本轮问题和历史缺陷，定位最需要回归和治理的业务模块。", table(["模块", "风险分", "本轮问题", "P0/P1", "历史缺陷"], module_rows, "暂无高风险模块。"), section_id="runtime")
        + section("疑似阻断项与待复核问题", "高价值 Bug 必须同时具备业务影响、证据强度和可复现条件；环境问题不应误入阻断清单。", table(["等级", "标题", "风险", "置信度", "接口", "实际结果"], issue_rows, "暂无疑似发布阻断项。"), section_id="release")
        + section("下一轮建议", "将资源投向能提升高价值缺陷发现率与证据质量的行动。", actions_html, section_id="benchmark")
    )
    return product_shell(
        title="发布风险看板",
        project_id=str(dashboard.get("project_id") or dashboard.get("project_name") or "real_project_demo"),
        active="release",
        eyebrow="Evidence-backed release gate",
        headline="用业务证据，而不是测试数量，做发布决策。",
        description="发布风险看板把企业知识、环境健康、跨系统 Oracle、权限风险、缺陷可信度与审计门禁收敛到同一处。",
        body=body,
        payload=dashboard,
        environment_label="发布门禁已启用",
        page_hint="发布风险看板",
    )

def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    dashboard = build_release_risk_dashboard(project)
    print(json.dumps({"ok": True, "project_id": project, "summary": dashboard.get("summary"), "private_leak_check": dashboard.get("private_leak_check")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
