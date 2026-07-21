"""
Display-Ready Formatter — 统一成果展示格式化引擎。

在 _build_command_center() 的 risks 列表统一汇聚完成后
（所有挖掘能力已 .extend() + 去重 + HAR注入 + 证据富化），
对统一的 risks 列表做整体格式化，输出前端零加工可渲染的 display-ready JSON。

所有函数处理 missing/partial 数据，输出保证有值有标签。
不区分挖掘来源，成果统一展示。
"""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ..customer_delivery_gate import CUSTOMER_READY_MIN_EVIDENCE_SCORE
from ..customer_report_boundary import (
    product_responsibility_boundary,
    strip_fix_advice_fields,
)
from ..real_id_resolver import normalize_path_placeholders


DISPLAY_READY_POLICY_PATH = Path(__file__).resolve().parent / "policies" / "display_ready_policy.json"
DISPLAY_READY_BOUNDARY_SOURCE = "ai_test_asset_center.display_ready_formatter"


# ═══════════════════════════════════════════════════════════════════════
# 缺陷族分类映射（从前端 finding-taxonomy.ts 迁移）
# ═══════════════════════════════════════════════════════════════════════

DEFECT_FAMILY_ORDER = [
    "scenario_flow", "api_contract", "security_boundary", "privacy_compliance",
    "data_integrity", "performance", "stability", "compatibility",
    "ui", "uiux", "accessibility_i18n", "observability", "configuration_drift",
]

DEFECT_FAMILY_META = {
    "scenario_flow": {"label": "场景流转", "reporting_bucket": "functional", "bucket_label": "功能"},
    "api_contract": {"label": "接口契约", "reporting_bucket": "api", "bucket_label": "接口"},
    "security_boundary": {"label": "安全边界", "reporting_bucket": "security", "bucket_label": "安全"},
    "privacy_compliance": {"label": "隐私合规", "reporting_bucket": "security", "bucket_label": "安全"},
    "observability": {"label": "可观测性", "reporting_bucket": "reliability", "bucket_label": "可靠性"},
    "configuration_drift": {"label": "配置漂移", "reporting_bucket": "reliability", "bucket_label": "可靠性"},
    "data_integrity": {"label": "数据一致性", "reporting_bucket": "data", "bucket_label": "数据"},
    "performance": {"label": "性能", "reporting_bucket": "performance", "bucket_label": "性能"},
    "stability": {"label": "稳定性", "reporting_bucket": "stability", "bucket_label": "稳定性"},
    "compatibility": {"label": "兼容性", "reporting_bucket": "compatibility", "bucket_label": "兼容性"},
    "ui": {"label": "界面呈现", "reporting_bucket": "frontend", "bucket_label": "前端"},
    "uiux": {"label": "交互体验", "reporting_bucket": "ux", "bucket_label": "体验"},
    "accessibility_i18n": {"label": "可访问性/本地化", "reporting_bucket": "ux", "bucket_label": "体验"},
}

EVIDENCE_RELEVANCE_FAILURE = "运行时响应与当前缺陷描述不匹配，已拒绝作为复现证据"

RISK_TYPE_TO_FAMILY = {
    "permission_bypass": "security_boundary", "idor": "security_boundary",
    "tenant_isolation": "security_boundary", "openapi_security_static_scan": "security_boundary",
    "audit_privacy_probe": "privacy_compliance", "privacy_compliance": "privacy_compliance",
    "sensitive_field_leak": "privacy_compliance", "audit_log_missing": "privacy_compliance",
    "desensitization_failure": "privacy_compliance",
    "business_invariant": "data_integrity", "business_reconciliation": "data_integrity",
    "business_causality": "data_integrity", "consistency_integrity": "data_integrity",
    "unique_constraint": "data_integrity", "date_order": "data_integrity",
    "idempotency": "data_integrity", "stock_consistency": "data_integrity",
    "metamorphic_relation": "data_integrity", "temporal_data_regression": "data_integrity",
    "business_population_constraint": "data_integrity", "payment": "data_integrity",
    "refund": "data_integrity", "db_verification": "data_integrity", "db_snapshot": "data_integrity",
    "lifecycle_integrity": "scenario_flow", "business_reasoning": "scenario_flow",
    "event_chain_integrity": "scenario_flow", "saga_compensation": "scenario_flow",
    "coupon_abuse": "scenario_flow", "e2e_flow": "scenario_flow", "business_flow": "scenario_flow",
    "state_machine": "scenario_flow",
    "api_contract": "api_contract", "positive_numeric": "api_contract",
    "nonnegative_numeric": "api_contract", "enum_closed_set": "api_contract",
    "api_backward_compatibility": "compatibility", "compatibility": "compatibility",
    "performance_regression": "performance",
    "stability_timeout": "stability",
    "frontend_execution_runtime": "ui", "frontend_runtime": "ui", "frontend_ui": "ui",
    "browser_ui_replay": "ui", "frontend_ux": "uiux",
    "assurance_coverage_gap": "observability", "quality_assurance_gap": "observability",
    "deployment_config_drift": "configuration_drift",
    "deep_verifier": "scenario_flow", "deep_test": "scenario_flow",
    "multi_layer": "scenario_flow",
}


