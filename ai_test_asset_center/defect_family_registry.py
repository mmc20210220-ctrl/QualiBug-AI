from __future__ import annotations

"""Unified defect family registry for full-spectrum discovery coverage."""

from typing import Any
import signal

DEFECT_FAMILIES: dict[str, dict[str, Any]] = {
    "scenario_flow": {
        "family_id": "scenario_flow",
        "display_name": "场景流转 Bug",
        "bug_examples": ["跨页面流程断链", "关键业务路径中断", "状态流转遗漏"],
        "discovery_mode": "hybrid",
        "probe_sources": ["enterprise_business_flow_graph", "frontend_ux", "business_lifecycle_reasoning"],
        "required_evidence": ["request_response", "business_transition", "user_path"],
        "allowed_execution_modes": ["plan_only", "api_probe", "frontend_runtime"],
        "dedupe_keys": ["family_id", "title", "path", "source"],
        "confidence_policy": "needs_cross_step_evidence",
        "reporting_bucket": "functional",
    },
    "api_contract": {
        "family_id": "api_contract",
        "display_name": "接口契约 Bug",
        "bug_examples": ["schema drift", "response envelope mismatch", "operationId 冲突"],
        "discovery_mode": "contract_first",
        "probe_sources": ["api_contract_acceptance", "phase104_api_contract"],
        "required_evidence": ["openapi_contract", "runtime_response"],
        "allowed_execution_modes": ["plan_only", "contract_only", "api_probe"],
        "dedupe_keys": ["family_id", "path", "method", "title"],
        "confidence_policy": "contract_break_is_high_confidence",
        "reporting_bucket": "api",
    },
    "security_boundary": {
        "family_id": "security_boundary",
        "display_name": "安全边界 Bug",
        "bug_examples": ["越权", "租户隔离失败", "匿名访问泄露"],
        "discovery_mode": "runtime_first",
        "probe_sources": ["real_project_pattern", "consistency_isolation_reasoning", "authorization"],
        "required_evidence": ["request_response", "actor_scope", "business_data_exposure"],
        "allowed_execution_modes": ["plan_only", "api_probe"],
        "dedupe_keys": ["family_id", "risk_type", "path", "method"],
        "confidence_policy": "requires_scope_evidence",
        "reporting_bucket": "security",
    },
    "privacy_compliance": {
        "family_id": "privacy_compliance",
        "display_name": "隐私与合规 Bug",
        "bug_examples": ["敏感字段泄露", "审计缺失", "脱敏失败", "数据边界越界"],
        "discovery_mode": "hybrid",
        "probe_sources": ["audit_privacy_probe", "openapi_static_security_scan", "enterprise_testops_control_plane"],
        "required_evidence": ["data_exposure", "audit_trail", "policy_expectation"],
        "allowed_execution_modes": ["plan_only", "api_probe", "contract_only"],
        "dedupe_keys": ["family_id", "risk_type", "path", "method", "title"],
        "confidence_policy": "needs_sensitive_field_evidence",
        "reporting_bucket": "security",
    },
    "observability": {
        "family_id": "observability",
        "display_name": "可观测性 Bug",
        "bug_examples": ["关键链路无 trace", "错误码不一致", "日志缺上下文", "告警缺失"],
        "discovery_mode": "contract_first",
        "probe_sources": ["enterprise_testops_control_plane", "performance_monitor", "api_contract_acceptance"],
        "required_evidence": ["error_code_contract", "trace_or_log_context", "slo_signal"],
        "allowed_execution_modes": ["plan_only", "contract_only", "api_probe"],
        "dedupe_keys": ["family_id", "title", "path", "method"],
        "confidence_policy": "contract_break_is_high_confidence",
        "reporting_bucket": "reliability",
    },
    "configuration_drift": {
        "family_id": "configuration_drift",
        "display_name": "配置与漂移 Bug",
        "bug_examples": ["灰度开关漂移", "环境变量缺失", "配置回滚不一致", "部署策略误配"],
        "discovery_mode": "comparison_first",
        "probe_sources": ["real_project_onboarding", "deployment_config_drift", "enterprise_testops_control_plane"],
        "required_evidence": ["config_snapshot", "environment_diff", "governance_state"],
        "allowed_execution_modes": ["plan_only", "compatibility_matrix"],
        "dedupe_keys": ["family_id", "title", "comparison_key"],
        "confidence_policy": "needs_cross_environment_diff",
        "reporting_bucket": "reliability",
    },
    "data_integrity": {
        "family_id": "data_integrity",
        "display_name": "数据一致性 Bug",
        "bug_examples": ["金额守恒失败", "生命周期异常", "缓存不一致"],
        "discovery_mode": "hybrid",
        "probe_sources": [
            "business_invariant_mining",
            "business_reconciliation",
            "business_causality_conservation",
            "business_population_constraints",
        ],
        "required_evidence": ["before_after_snapshot", "business_oracle"],
        "allowed_execution_modes": ["plan_only", "api_probe", "runtime_signal"],
        "dedupe_keys": ["family_id", "risk_type", "title"],
        "confidence_policy": "needs_oracle_and_observation",
        "reporting_bucket": "data",
    },
    "performance": {
        "family_id": "performance",
        "display_name": "性能 Bug",
        "bug_examples": ["慢请求", "高 fanout", "冷启动退化"],
        "discovery_mode": "runtime_first",
        "probe_sources": ["performance_monitor", "performance_stability_adapter"],
        "required_evidence": ["latency_metrics", "threshold", "baseline_or_repeat_run"],
        "allowed_execution_modes": ["plan_only", "api_probe", "performance_oracle"],
        "dedupe_keys": ["family_id", "path", "method", "oracle"],
        "confidence_policy": "needs_threshold_breach",
        "reporting_bucket": "performance",
    },
    "stability": {
        "family_id": "stability",
        "display_name": "稳定性 Bug",
        "bug_examples": ["间歇性失败", "超时", "重试风暴", "状态抖动"],
        "discovery_mode": "runtime_first",
        "probe_sources": ["performance_stability_adapter", "loop_watchdog"],
        "required_evidence": ["repeat_execution", "error_pattern", "runtime_context"],
        "allowed_execution_modes": ["plan_only", "api_probe", "runtime_signal"],
        "dedupe_keys": ["family_id", "path", "method", "error_signature"],
        "confidence_policy": "needs_repeatability_or_burst",
        "reporting_bucket": "stability",
    },
    "compatibility": {
        "family_id": "compatibility",
        "display_name": "兼容性 Bug",
        "bug_examples": ["时区差异", "schema version break", "环境兼容失败"],
        "discovery_mode": "comparison_first",
        "probe_sources": ["compatibility_adapter", "api_contract_acceptance"],
        "required_evidence": ["environment_diff", "version_diff", "render_or_response_diff"],
        "allowed_execution_modes": ["plan_only", "contract_only", "compatibility_matrix"],
        "dedupe_keys": ["family_id", "comparison_key", "title"],
        "confidence_policy": "needs_cross_environment_diff",
        "reporting_bucket": "compatibility",
    },
    "ui": {
        "family_id": "ui",
        "display_name": "UI Bug",
        "bug_examples": ["页面空白", "路由断裂", "关键区域不渲染"],
        "discovery_mode": "frontend_first",
        "probe_sources": ["frontend_runtime", "frontend_smoke", "frontend_preview"],
        "required_evidence": ["page_state", "component_presence", "route_navigation"],
        "allowed_execution_modes": ["plan_only", "frontend_runtime"],
        "dedupe_keys": ["family_id", "route", "title"],
        "confidence_policy": "needs_render_or_route_evidence",
        "reporting_bucket": "frontend",
    },
    "uiux": {
        "family_id": "uiux",
        "display_name": "UIUX Bug",
        "bug_examples": ["主任务无法完成", "CTA 不可见", "状态反馈误导"],
        "discovery_mode": "frontend_first",
        "probe_sources": ["frontend_ux", "frontend_interaction_acceptance"],
        "required_evidence": ["task_path", "interaction_result", "feedback_state"],
        "allowed_execution_modes": ["plan_only", "frontend_runtime"],
        "dedupe_keys": ["family_id", "route", "ux_rule"],
        "confidence_policy": "needs_task_failure_evidence",
        "reporting_bucket": "ux",
    },
    "accessibility_i18n": {
        "family_id": "accessibility_i18n",
        "display_name": "可访问性与本地化 Bug",
        "bug_examples": ["文案不可读", "语言回退错误", "时区展示错误"],
        "discovery_mode": "comparison_first",
        "probe_sources": ["compatibility_adapter", "frontend_ux"],
        "required_evidence": ["render_diff", "locale_or_timezone_context"],
        "allowed_execution_modes": ["plan_only", "frontend_runtime", "compatibility_matrix"],
        "dedupe_keys": ["family_id", "route", "locale"],
        "confidence_policy": "needs_contextual_diff",
        "reporting_bucket": "ux",
    },
}

RISK_TYPE_TO_FAMILY = {
    "permission_bypass": "security_boundary",
    "idor": "security_boundary",
    "tenant_isolation": "security_boundary",
    "business_invariant": "data_integrity",
    "business_reconciliation": "data_integrity",
    "business_causality": "data_integrity",
    "consistency_integrity": "data_integrity",
    "api_contract": "api_contract",
    "api_backward_compatibility": "compatibility",
    "frontend_execution_runtime": "ui",
    "frontend_runtime": "ui",
    "frontend_ui": "ui",
    "frontend_ux": "uiux",
    "browser_ui_replay": "ui",
    "performance_regression": "performance",
    "stability_timeout": "stability",
    "compatibility": "compatibility",
    "openapi_security_static_scan": "security_boundary",
    "audit_privacy_probe": "privacy_compliance",
    "privacy_compliance": "privacy_compliance",
    "sensitive_field_leak": "privacy_compliance",
    "audit_log_missing": "privacy_compliance",
    "desensitization_failure": "privacy_compliance",
    "deployment_config_drift": "configuration_drift",
    "positive_numeric": "api_contract",
    "nonnegative_numeric": "api_contract",
    "enum_closed_set": "api_contract",
    "unique_constraint": "data_integrity",
    "date_order": "data_integrity",
    "idempotency": "data_integrity",
}


def iter_defect_families() -> list[dict[str, Any]]:
    return [dict(item) for item in DEFECT_FAMILIES.values()]


def get_defect_family(family_id: str | None) -> dict[str, Any]:
    family = DEFECT_FAMILIES.get(str(family_id or "").strip())
    if family:
        return dict(family)
    return dict(DEFECT_FAMILIES["scenario_flow"])


def resolve_defect_family(signal: dict[str, Any] | None) -> dict[str, Any]:
    signal = signal if isinstance(signal, dict) else {}
    explicit_family = str(signal.get("defect_family") or signal.get("family_id") or "").strip()
    if explicit_family in DEFECT_FAMILIES:
        return get_defect_family(explicit_family)
    risk_type = str(signal.get("risk_type") or signal.get("category") or signal.get("source") or "").strip()
    mapped = RISK_TYPE_TO_FAMILY.get(risk_type)
    if mapped:
        return get_defect_family(mapped)
    title = str(signal.get("title") or "").lower()
    if any(token in title for token in ("latency", "timeout", "slow", "performance", "性能")):
        return get_defect_family("performance")
    if any(token in title for token in ("ui", "页面", "渲染", "route", "导航")):
        return get_defect_family("ui")
    if any(token in title for token in ("ux", "体验", "cta", "反馈")):
        return get_defect_family("uiux")
    if any(token in title for token in ("compat", "兼容", "timezone", "locale", "版本")):
        return get_defect_family("compatibility")
    return get_defect_family("scenario_flow")


def list_reporting_buckets() -> list[str]:
    return sorted({str(item.get("reporting_bucket") or "") for item in DEFECT_FAMILIES.values() if item.get("reporting_bucket")})
