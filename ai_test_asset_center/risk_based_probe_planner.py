from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _html_escape, _load_json, _safe_project_id, _write_json, config_paths, load_real_project_config
from .real_project_defect_discovery import generate_real_project_probes, generate_history_informed_probes, _path_keywords, DESTRUCTIVE_RISK_TYPES
from .business_adaptation_layer import build_business_adaptation_profile, generate_business_adaptive_probes
from .enterprise_strategy_learning import load_enterprise_strategy_learning, weights_by_key
from .enterprise_test_knowledge import build_enterprise_test_knowledge, generate_enterprise_knowledge_probes, load_enterprise_test_knowledge
from .business_flow_graph import build_business_flow_graph, generate_business_flow_probes, load_business_flow_graph
from .business_flow_execution import load_business_flow_execution_result
from .replay_evidence_sandbox import load_replay_evidence_sandbox
from .universal_defect_mining import build_universal_defect_mining_profile, generate_universal_defect_probes, load_universal_defect_mining
from .counterexample_discovery import build_counterexample_discovery_profile, generate_counterexample_probes, load_counterexample_discovery
from .business_outcome_validation import build_business_outcome_profile, generate_business_outcome_probes, load_business_outcome_profile
from .business_reconciliation import build_business_reconciliation_profile, generate_business_reconciliation_probes, load_business_reconciliation_profile
from .business_invariant_mining import build_business_invariant_profile, generate_business_invariant_probes, load_business_invariant_profile
from .multisource_reasoning import build_multi_source_reasoning_profile, generate_multi_source_reasoning_probes, load_multi_source_reasoning_profile
from .business_lifecycle_reasoning import build_business_lifecycle_profile, generate_business_lifecycle_probes, load_business_lifecycle_profile
from .consistency_isolation_reasoning import build_consistency_isolation_profile, generate_consistency_isolation_probes, load_consistency_isolation_profile
from .metamorphic_differential_reasoning import build_metamorphic_differential_profile, generate_metamorphic_differential_probes, load_metamorphic_differential_profile
from .temporal_data_regression_reasoning import build_temporal_data_regression_profile, generate_temporal_data_regression_probes, load_temporal_data_regression_profile
from .business_causality_conservation import build_business_causality_profile, generate_business_causality_probes, load_business_causality_profile
from .business_population_constraints import build_business_population_constraint_profile, generate_business_population_constraint_probes, load_business_population_constraint_profile
from .business_event_chain_reasoning import build_business_event_chain_profile, generate_business_event_chain_probes, load_business_event_chain_profile
from .business_saga_compensation_reasoning import build_business_saga_compensation_profile, generate_business_saga_compensation_probes, load_business_saga_compensation_profile
from .confirmed_bug_flywheel import build_confirmed_bug_flywheel, annotate_probes_with_confirmed_learning
from .business_world_model import build_business_world_model_profile, generate_business_world_model_probes, load_business_world_model_profile
from .cross_industry_confirmed_learning import annotate_probes_with_cross_industry_learning, build_cross_industry_confirmed_learning
from .business_assurance_coverage import build_business_assurance_coverage_profile, generate_business_assurance_coverage_probes, load_business_assurance_coverage_profile
from .multi_industry_business_reasoning import build_multi_industry_business_profile, generate_multi_industry_business_probes, load_multi_industry_business_profile
from .enterprise_knowledge_center import build_enterprise_business_knowledge_asset, build_enterprise_knowledge_evidence_bundle, generate_enterprise_business_knowledge_probes, load_enterprise_business_knowledge_asset
from .enterprise_testops_control_plane import build_enterprise_testops_control_plane, generate_enterprise_testops_probes

SEVERITY_WEIGHT = {"P0": 1.0, "P1": 0.78, "P2": 0.45, "P3": 0.2}
RISK_BASE_WEIGHT = {
    "permission_bypass": 0.95,
    "idor": 0.92,
    "tenant_isolation": 0.94,
    "money_consistency": 0.86,
    "coupon_abuse": 0.82,
    "stock_consistency": 0.88,
    "payment": 0.96,
    "refund": 0.94,
    "idempotency": 0.84,
    "order_state": 0.78,
    "state_consistency": 0.8,
    "data_consistency": 0.75,
    "concurrency": 0.88,
    "business_rule": 0.55,
    "export_data_quality": 0.84,
    "business_reconciliation": 0.90,
    "business_invariant": 0.93,
    "cross_system_oracle": 0.96,
    "page_api_oracle": 0.84,
    "exception_path": 0.88,
    "concurrency_path": 0.92,
    "historical_data_path": 0.86,
    "lifecycle_temporal_order": 0.94,
    "lifecycle_state_evidence": 0.93,
    "lifecycle_soft_delete": 0.90,
    "lifecycle_effective_window": 0.86,
    "lifecycle_history": 0.96,
    "lifecycle_transition": 0.95,
    "lifecycle_contract_gap": 0.42,
    "async_result_consistency": 0.94,
    "async_idempotency": 0.95,
    "read_model_consistency": 0.93,
    "read_model_staleness": 0.89,
    "read_stability": 0.82,
    "consistency_contract_gap": 0.42,
    "metamorphic_filter_relation": 0.94,
    "metamorphic_pagination_relation": 0.91,
    "metamorphic_sort_relation": 0.83,
    "metamorphic_detail_relation": 0.92,
    "metamorphic_equivalence_relation": 0.87,
    "metamorphic_temporal_relation": 0.95,
    "metamorphic_contract_gap": 0.42,
    "temporal_field_presence_regression": 0.93,
    "temporal_type_regression": 0.84,
    "temporal_immutable_drift": 0.95,
    "temporal_numeric_scale_regression": 0.94,
    "temporal_identity_regression": 0.96,
    "temporal_contract_regression": 0.86,
    "temporal_contract_gap": 0.42,
    "business_causality_missing": 0.97,
    "business_causality_duplicate": 0.98,
    "business_causality_orphan": 0.94,
    "business_amount_conservation": 0.97,
    "business_causality_idempotency": 0.96,
    "business_causality_contract_gap": 0.42,
    "inventory_reservation_conservation": 0.98,
    "inventory_stock_conservation": 0.98,
    "inventory_oversell_risk": 0.99,
    "inventory_reservation_contract_gap": 0.42,
    "business_cohort_limit": 0.98,
    "business_cohort_limit_bypass": 0.99,
    "business_interval_overlap": 0.97,
    "business_interval_race": 0.98,
    "business_composite_duplicate": 0.96,
    "business_batch_integrity": 0.95,
    "business_approval_threshold": 0.99,
    "business_population_contract_gap": 0.42,
    "event_delivery_missing": 0.98,
    "event_duplicate_publish": 0.99,
    "event_retry_race": 0.98,
    "event_replay_idempotency": 0.98,
    "event_ordering_violation": 0.96,
    "event_consumer_missing": 0.98,
    "event_consumer_idempotency": 0.97,
    "event_orphan": 0.95,
    "event_dead_letter_diagnostics": 0.88,
    "event_chain_contract_gap": 0.42,
    "saga_compensation_missing": 0.99,
    "saga_compensation_duplicate": 0.99,
    "saga_compensation_amount": 0.99,
    "saga_compensation_orphan": 0.96,
    "saga_residual_effect": 0.99,
    "saga_terminal_state": 0.97,
    "saga_compensation_stale": 0.91,
    "saga_compensation_retry_idempotency": 0.98,
    "saga_compensation_contract_gap": 0.42,
    "assurance_coverage_gap": 0.99,
    "industry_financial_conservation": 0.99,
    "industry_account_ownership": 0.99,
    "industry_limit_enforcement": 0.96,
    "industry_sensitive_data_access": 0.99,
    "industry_appointment_capacity": 0.95,
    "industry_prescription_authorization": 0.99,
    "industry_grade_integrity": 0.99,
    "industry_enrollment_capacity": 0.96,
    "industry_tenant_isolation": 0.99,
    "industry_entitlement_enforcement": 0.95,
    "industry_payment_idempotency": 0.99,
    "industry_inventory_conservation": 0.99,
    "industry_approval_matrix": 0.99,
    "industry_three_way_match": 0.96,
    "industry_ownership_boundary": 0.93,
    "industry_quote_discount": 0.92,
    "industry_contract_state": 0.91,
    "industry_state_transition": 0.92,
    "industry_subscription_state": 0.90,
    "industry_coupon_policy": 0.92,
    "industry_order_state": 0.91,
    "industry_exam_state": 0.90,
}
MODE_BUDGETS = {
    "safe": {"max_probe_count": 40, "allow_destructive": False, "risk_budget": {"permission_bypass": 12, "idor": 10, "tenant_isolation": 8, "business_rule": 10, "export_data_quality": 8, "business_reconciliation": 10, "business_invariant": 14, "cross_system_oracle": 10, "exception_path": 10, "concurrency_path": 8, "historical_data_path": 8, "page_api_oracle": 6, "lifecycle_temporal_order": 10, "lifecycle_state_evidence": 10, "lifecycle_soft_delete": 8, "lifecycle_effective_window": 6, "lifecycle_history": 10, "lifecycle_transition": 8, "async_result_consistency": 10, "async_idempotency": 6, "read_model_consistency": 10, "read_model_staleness": 8, "read_stability": 6, "consistency_contract_gap": 4, "business_causality_missing": 10, "business_causality_duplicate": 8, "business_causality_orphan": 8, "business_amount_conservation": 10, "business_causality_idempotency": 6, "business_causality_contract_gap": 4, "inventory_reservation_conservation": 10, "inventory_stock_conservation": 10, "inventory_oversell_risk": 10, "inventory_reservation_contract_gap": 4, "business_cohort_limit": 10, "business_interval_overlap": 10, "business_composite_duplicate": 8, "business_batch_integrity": 8, "business_approval_threshold": 10, "business_population_contract_gap": 4, "saga_compensation_missing": 10, "saga_compensation_duplicate": 8, "saga_compensation_amount": 10, "saga_compensation_orphan": 8, "saga_residual_effect": 10, "saga_terminal_state": 8, "saga_compensation_stale": 6, "saga_compensation_retry_idempotency": 6, "saga_compensation_contract_gap": 4, "assurance_coverage_gap": 12}},
    "standard": {"max_probe_count": 100, "allow_destructive": False, "risk_budget": {"permission_bypass": 15, "idor": 14, "tenant_isolation": 10, "coupon_abuse": 12, "stock_consistency": 14, "money_consistency": 10, "order_state": 10, "business_rule": 15, "export_data_quality": 12, "business_reconciliation": 16, "business_invariant": 22, "cross_system_oracle": 16, "exception_path": 16, "concurrency_path": 12, "historical_data_path": 12, "page_api_oracle": 10, "lifecycle_temporal_order": 18, "lifecycle_state_evidence": 18, "lifecycle_soft_delete": 12, "lifecycle_effective_window": 10, "lifecycle_history": 18, "lifecycle_transition": 14, "async_result_consistency": 16, "async_idempotency": 10, "read_model_consistency": 16, "read_model_staleness": 12, "read_stability": 10, "consistency_contract_gap": 6, "business_causality_missing": 18, "business_causality_duplicate": 14, "business_causality_orphan": 14, "business_amount_conservation": 18, "business_causality_idempotency": 10, "business_causality_contract_gap": 6, "inventory_reservation_conservation": 18, "inventory_stock_conservation": 18, "inventory_oversell_risk": 18, "inventory_reservation_contract_gap": 6, "business_cohort_limit": 18, "business_interval_overlap": 18, "business_composite_duplicate": 14, "business_batch_integrity": 14, "business_approval_threshold": 18, "business_population_contract_gap": 6, "saga_compensation_missing": 18, "saga_compensation_duplicate": 14, "saga_compensation_amount": 18, "saga_compensation_orphan": 14, "saga_residual_effect": 18, "saga_terminal_state": 14, "saga_compensation_stale": 10, "saga_compensation_retry_idempotency": 10, "saga_compensation_contract_gap": 6, "assurance_coverage_gap": 24}},
    "aggressive": {"max_probe_count": 180, "allow_destructive": True, "risk_budget": {"permission_bypass": 18, "idor": 16, "tenant_isolation": 12, "coupon_abuse": 16, "stock_consistency": 18, "payment": 18, "refund": 16, "idempotency": 14, "order_state": 12, "business_rule": 20, "export_data_quality": 20, "business_reconciliation": 24, "business_invariant": 30, "cross_system_oracle": 24, "exception_path": 24, "concurrency_path": 16, "historical_data_path": 18, "page_api_oracle": 14, "lifecycle_temporal_order": 24, "lifecycle_state_evidence": 24, "lifecycle_soft_delete": 18, "lifecycle_effective_window": 16, "lifecycle_history": 24, "lifecycle_transition": 18, "async_result_consistency": 24, "async_idempotency": 16, "read_model_consistency": 24, "read_model_staleness": 18, "read_stability": 14, "consistency_contract_gap": 8, "business_causality_missing": 24, "business_causality_duplicate": 20, "business_causality_orphan": 20, "business_amount_conservation": 24, "business_causality_idempotency": 16, "business_causality_contract_gap": 8, "inventory_reservation_conservation": 24, "inventory_stock_conservation": 24, "inventory_oversell_risk": 24, "inventory_reservation_contract_gap": 8, "business_cohort_limit": 24, "business_interval_overlap": 24, "business_composite_duplicate": 20, "business_batch_integrity": 20, "business_approval_threshold": 24, "business_population_contract_gap": 8, "saga_compensation_missing": 24, "saga_compensation_duplicate": 20, "saga_compensation_amount": 24, "saga_compensation_orphan": 20, "saga_residual_effect": 24, "saga_terminal_state": 20, "saga_compensation_stale": 16, "saga_compensation_retry_idempotency": 16, "saga_compensation_contract_gap": 8, "assurance_coverage_gap": 36}},
}
PRIVATE_MARKERS = {"private_ground_truth", "ground_truth_bugs", "bug_sets", "enabled_bugs", "current_bug_set", "bug_instance_id"}


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return default
    return default


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _counter(items: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        key = str(item or "unknown")
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _severity_score(value: Any) -> float:
    return SEVERITY_WEIGHT.get(str(value or "P2").upper(), 0.35)


def _risk_score(value: Any) -> float:
    return RISK_BASE_WEIGHT.get(str(value or "business_rule"), 0.55)


def _load_enterprise_patterns(project_id: str, root: Path | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _read_json(root / "platform_workspace" / project / "defect_discovery" / "enterprise_bug_pattern_library.json", {})
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return [x for x in data["items"] if isinstance(x, dict)]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _load_risk_profile(project_id: str, root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    path = root / "platform_workspace" / project / "defect_discovery" / "real_project_risk_profile.json"
    data = _read_json(path, {})
    return data if isinstance(data, dict) else {}


def _openapi_paths(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, methods in (openapi.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, spec in methods.items():
            method_u = str(method).upper()
            if method_u not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            keys = sorted(_path_keywords(str(path), method_u))
            rows.append({"method": method_u, "path": str(path), "operation_id": (spec or {}).get("operationId") if isinstance(spec, dict) else None, "keywords": keys})
    return rows


def _risk_from_probe(probe: dict[str, Any]) -> str:
    return str(probe.get("risk_type") or "business_rule")


def _path_risk_boost(probe: dict[str, Any], risk_profile: dict[str, Any], patterns: list[dict[str, Any]]) -> tuple[float, list[str]]:
    reasons: list[str] = []
    boost = 0.0
    path = str(probe.get("path") or "").lower()
    risk = _risk_from_probe(probe)
    risk_dist = risk_profile.get("risk_distribution") or risk_profile.get("risk_type_distribution") or {}
    if isinstance(risk_dist, dict) and risk in risk_dist:
        count = float(risk_dist.get(risk) or 0)
        boost += min(0.25, count * 0.025)
        reasons.append(f"历史高发风险 {risk}={int(count)}")
    modules = risk_profile.get("module_distribution") or risk_profile.get("high_risk_modules") or {}
    if isinstance(modules, dict):
        for module, count in modules.items():
            m = str(module).lower()
            if m and len(m) >= 3 and m in path:
                boost += min(0.18, float(count or 0) * 0.02)
                reasons.append(f"历史高发模块 {module}")
    for p in patterns:
        prisk = str(p.get("risk_type") or "")
        if prisk == risk:
            boost += min(0.12, float(p.get("confidence_prior") or 0.5) * 0.12)
            reasons.append("匹配企业历史缺陷模式")
            break
    # OpenAPI path semantic boost.
    keys = set(_path_keywords(str(probe.get("path") or ""), str(probe.get("method") or "GET")))
    if risk == "permission_bypass" and "admin" in keys:
        boost += 0.2
        reasons.append("管理员接口高风险")
    if risk == "idor" and "order" in keys:
        boost += 0.18
        reasons.append("订单资源越权高风险")
    if risk == "tenant_isolation" and "tenant" in keys:
        boost += 0.18
        reasons.append("租户隔离高风险")
    if risk in {"payment", "refund"}:
        boost += 0.2
        reasons.append("资金链路高风险")
    if risk in {"stock_consistency", "coupon_abuse"}:
        boost += 0.12
        reasons.append("交易一致性高风险")
    return min(0.6, boost), reasons[:6]


def _is_destructive(probe: dict[str, Any]) -> bool:
    if probe.get("destructive") is True:
        return True
    risk = _risk_from_probe(probe)
    method = str(probe.get("method") or "GET").upper()
    return risk in DESTRUCTIVE_RISK_TYPES or risk in {"payment", "refund", "idempotency"} or method in {"POST", "PUT", "PATCH", "DELETE"}


def _strategy_learning_boost(probe: dict[str, Any], strategy: dict[str, Any] | None) -> tuple[float, list[str]]:
    if not isinstance(strategy, dict) or not strategy:
        return 0.0, []
    boost = 0.0
    reasons: list[str] = []
    risk = _risk_from_probe(probe)
    domain = str(probe.get("business_domain") or "unknown")
    endpoint = f"{str(probe.get('method') or 'GET').upper()} {str(probe.get('path') or '/')}"
    source = str(probe.get("source") or "unknown")
    module = str(probe.get("path") or "/").strip("/").split("/")[0].lower() or "root"

    maps = [
        (weights_by_key(strategy.get("risk_type_weights") or [], "risk_type"), risk, 0.22, "QA学习风险权重"),
        (weights_by_key(strategy.get("business_domain_weights") or [], "business_domain"), domain, 0.12, "QA学习业务域权重"),
        (weights_by_key(strategy.get("endpoint_weights") or [], "endpoint"), endpoint, 0.18, "QA学习接口权重"),
        (weights_by_key(strategy.get("source_weights") or [], "source"), source, 0.08, "QA学习来源权重"),
        (weights_by_key(strategy.get("module_weights") or [], "module"), module, 0.10, "QA学习模块权重"),
    ]
    for weight_map, key, scale, label in maps:
        row = weight_map.get(str(key))
        if not row:
            continue
        weight = float(row.get("weight") or 1.0)
        delta = (weight - 1.0) * scale
        boost += delta
        if abs(delta) >= 0.01:
            direction = "提升" if delta > 0 else "降低"
            reasons.append(f"{label}{direction} {key}={weight}")
    return max(-0.35, min(0.45, boost)), reasons[:5]


def _flow_execution_boost(probe: dict[str, Any], flow_execution: dict[str, Any] | None) -> tuple[float, list[str]]:
    if not isinstance(flow_execution, dict) or not flow_execution:
        return 0.0, []
    flow_id = str(probe.get("flow_id") or "")
    risk = _risk_from_probe(probe)
    boost = 0.0
    reasons: list[str] = []
    for assertion in flow_execution.get("assertions") or []:
        if flow_id and str(assertion.get("flow_id") or "") != flow_id:
            continue
        if str(assertion.get("risk_type") or "") != risk:
            continue
        status = str(assertion.get("status") or "")
        if status == "failed":
            boost += 0.22
            reasons.append("链路执行断言已失败")
        elif status in {"needs_evidence", "needs_replay"}:
            boost += 0.08
            reasons.append("链路执行断言待补证据/回放")
    return min(0.28, boost), reasons[:3]


def _replay_evidence_boost(probe: dict[str, Any], replay_sandbox: dict[str, Any] | None) -> tuple[float, list[str]]:
    if not isinstance(replay_sandbox, dict) or not replay_sandbox:
        return 0.0, []
    probe_id = str(probe.get("probe_id") or "")
    flow_id = str(probe.get("flow_id") or "")
    risk = _risk_from_probe(probe)
    boost = 0.0
    reasons: list[str] = []
    for packet in replay_sandbox.get("evidence_packets") or []:
        if probe_id and str(packet.get("probe_id") or "") != probe_id:
            continue
        if flow_id and str(packet.get("flow_id") or "") != flow_id:
            continue
        if str(packet.get("risk_type") or "") != risk:
            continue
        assertion = packet.get("assertion") or {}
        status = str(assertion.get("status") or "")
        completeness = float(packet.get("evidence_completeness") or 0)
        if status == "failed":
            boost += 0.18 + min(0.08, completeness * 0.08)
            reasons.append("Phase40证据包包含失败断言")
        elif status == "needs_replay":
            boost += 0.10 + min(0.05, completeness * 0.05)
            reasons.append("Phase40证据包等待安全回放补证")
        elif status == "needs_evidence":
            boost += 0.06
            reasons.append("Phase40证据包等待状态快照补证")
    return min(0.3, boost), reasons[:3]


def _avg_score(rows: list[dict[str, Any]], field: str) -> float:
    if not rows:
        return 0.0
    total = sum(float(row.get(field) or 0.0) for row in rows)
    return round(total / len(rows), 6)


def _validated_yield_signal_level(score: float) -> str:
    if score >= 0.16:
        return "strong"
    if score >= 0.04:
        return "moderate"
    return "weak"


def _validated_yield_priority(
    probe: dict[str, Any],
    *,
    flow_exec_boost: float,
    replay_boost: float,
) -> tuple[float, list[str], dict[str, Any]]:
    risk = _risk_from_probe(probe)
    source = str(probe.get("source") or "unknown")
    execution_policy = str(probe.get("execution_policy") or "direct")
    confirmed_learning_bonus = max(0.0, min(0.25, float(probe.get("learning_bonus") or 0.0)))
    strict_validation_ready = execution_policy != "candidate_only" and not risk.endswith("contract_gap")
    repro_ready_signal = execution_policy != "candidate_only" or flow_exec_boost >= 0.08
    evidence_ready_signal = replay_boost >= 0.1 or confirmed_learning_bonus >= 0.08
    score = 0.0
    reasons: list[str] = []

    source_prior = {
        "business_assurance_coverage": 0.06,
        "business_world_model": 0.06,
        "enterprise_business_knowledge_asset": 0.06,
        "multi_industry_business_reasoning": 0.05,
        "business_saga_compensation_reasoning": 0.05,
        "business_event_chain_reasoning": 0.05,
        "business_population_constraints": 0.05,
        "business_causality_conservation": 0.05,
        "temporal_data_regression_reasoning": 0.04,
        "metamorphic_differential_reasoning": 0.04,
        "consistency_isolation_reasoning": 0.04,
        "business_lifecycle_reasoning": 0.04,
        "multi_source_business_reasoning": 0.04,
        "business_invariant_mining": 0.03,
        "business_reconciliation": 0.03,
        "business_outcome_validation": 0.03,
    }.get(source, 0.0)
    if source_prior > 0:
        score += source_prior
        reasons.append(f"来源 validated-yield 先验 {source_prior:.2f}")

    if strict_validation_ready:
        score += 0.08
        reasons.append("可直接进入严格验证")
    elif execution_policy == "candidate_only":
        score -= 0.14
        reasons.append("仅候选信号，严格验证产出弱")
    else:
        score += 0.02
        reasons.append("需要沙箱/补充步骤后验证")

    if flow_exec_boost > 0:
        delta = min(0.08, flow_exec_boost * 0.4)
        score += delta
        reasons.append("已有失败断言/复现线索")
    if replay_boost > 0:
        delta = min(0.1, replay_boost * 0.45)
        score += delta
        reasons.append("已有证据包可收敛验证")
    if confirmed_learning_bonus > 0:
        delta = min(0.08, confirmed_learning_bonus * 0.35)
        score += delta
        reasons.append("确认缺陷记忆支持更快闭环")
    if risk.endswith("contract_gap"):
        score -= 0.1
        reasons.append("合同缺口类信号更难形成严格验证闭环")
    if _is_destructive(probe) and execution_policy == "candidate_only":
        score -= 0.05
        reasons.append("高副作用且仅候选，预算性价比低")

    bounded_score = round(max(-0.25, min(0.3, score)), 6)
    return (
        bounded_score,
        reasons[:6],
        {
            "strict_validation_ready": strict_validation_ready,
            "repro_ready_signal": repro_ready_signal,
            "evidence_ready_signal": evidence_ready_signal,
            "signal_level": _validated_yield_signal_level(bounded_score),
            "execution_policy": execution_policy,
        },
    )


def _summarize_validated_yield_priority(candidates: list[dict[str, Any]], selected: list[dict[str, Any]]) -> dict[str, Any]:
    def _count(rows: list[dict[str, Any]], predicate: Any) -> int:
        return sum(1 for row in rows if predicate(row))

    candidate_signal_distribution = _counter([str(row.get("validated_yield_signal_level") or "weak") for row in candidates])
    selected_signal_distribution = _counter([str(row.get("validated_yield_signal_level") or "weak") for row in selected])
    strong_candidate_count = _count(candidates, lambda row: row.get("validated_yield_signal_level") == "strong")
    moderate_candidate_count = _count(candidates, lambda row: row.get("validated_yield_signal_level") == "moderate")
    weak_candidate_count = _count(candidates, lambda row: row.get("validated_yield_signal_level") == "weak")
    strong_selected_count = _count(selected, lambda row: row.get("validated_yield_signal_level") == "strong")
    moderate_selected_count = _count(selected, lambda row: row.get("validated_yield_signal_level") == "moderate")
    weak_selected_count = _count(selected, lambda row: row.get("validated_yield_signal_level") == "weak")
    candidate_only_candidate_count = _count(candidates, lambda row: row.get("execution_policy") == "candidate_only")
    candidate_only_selected_count = _count(selected, lambda row: row.get("execution_policy") == "candidate_only")
    strict_candidate_count = _count(candidates, lambda row: bool(row.get("strict_validation_ready")))
    strict_selected_count = _count(selected, lambda row: bool(row.get("strict_validation_ready")))
    evidence_ready_candidate_count = _count(candidates, lambda row: bool(row.get("evidence_ready_signal")))
    evidence_ready_selected_count = _count(selected, lambda row: bool(row.get("evidence_ready_signal")))
    candidate_avg = _avg_score(candidates, "validated_yield_priority_score")
    selected_avg = _avg_score(selected, "validated_yield_priority_score")
    strong_selection_rate = round(strong_selected_count / strong_candidate_count, 3) if strong_candidate_count else 0.0
    moderate_selection_rate = round(moderate_selected_count / moderate_candidate_count, 3) if moderate_candidate_count else 0.0
    weak_selection_rate = round(weak_selected_count / weak_candidate_count, 3) if weak_candidate_count else 0.0
    candidate_only_selection_rate = round(candidate_only_selected_count / candidate_only_candidate_count, 3) if candidate_only_candidate_count else 0.0
    strict_selection_rate = round(strict_selected_count / strict_candidate_count, 3) if strict_candidate_count else 0.0
    evidence_ready_selection_rate = round(evidence_ready_selected_count / evidence_ready_candidate_count, 3) if evidence_ready_candidate_count else 0.0
    preference_proven = (
        selected_avg >= candidate_avg
        and strong_selection_rate >= weak_selection_rate
        and strict_selection_rate >= candidate_only_selection_rate
    )
    return {
        "reporting_basis": "validated_bug",
        "preference_target": "validated_yield",
        "deprioritized_proxy": "candidate_scale",
        "candidate_average_score": candidate_avg,
        "selected_average_score": selected_avg,
        "selection_gain": round(selected_avg - candidate_avg, 6),
        "candidate_signal_distribution": candidate_signal_distribution,
        "selected_signal_distribution": selected_signal_distribution,
        "signal_level_selection_rate": {
            "strong": strong_selection_rate,
            "moderate": moderate_selection_rate,
            "weak": weak_selection_rate,
        },
        "strict_validation_ready_candidate_count": strict_candidate_count,
        "strict_validation_ready_selected_count": strict_selected_count,
        "strict_validation_ready_selection_rate": strict_selection_rate,
        "candidate_only_candidate_count": candidate_only_candidate_count,
        "candidate_only_selected_count": candidate_only_selected_count,
        "candidate_only_selection_rate": candidate_only_selection_rate,
        "evidence_ready_candidate_count": evidence_ready_candidate_count,
        "evidence_ready_selected_count": evidence_ready_selected_count,
        "evidence_ready_selection_rate": evidence_ready_selection_rate,
        "selection_prefers_strictly_verifiable_output": preference_proven,
    }


def _select_probes_by_budget(
    combined: list[dict[str, Any]],
    *,
    mode: str,
    allow_destructive: bool,
    budget: dict[str, Any],
    max_count: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    per_risk_used: dict[str, int] = {}
    selected: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for probe in sorted(combined, key=lambda p: (-float(p.get("priority_score") or 0), -float(p.get("validated_yield_priority_score") or 0), str(p.get("risk_type")), str(p.get("path")))):
        risk = _risk_from_probe(probe)
        candidate_only_flow = mode != "safe" and probe.get("execution_policy") == "candidate_only" and probe.get("source") == "enterprise_business_flow_graph"
        sandbox_reasoning_candidate = probe.get("source") in {"multi_source_business_reasoning", "business_lifecycle_reasoning", "consistency_isolation_reasoning", "business_causality_conservation", "business_population_constraints", "business_event_chain_reasoning", "business_saga_compensation_reasoning", "business_assurance_coverage", "multi_industry_business_reasoning", "enterprise_business_knowledge_asset", "enterprise_testops_journey", "enterprise_testops_permission", "enterprise_testops_system_state", "enterprise_testops_data"} and probe.get("execution_policy") == "sandbox_required"
        if _is_destructive(probe) and (mode == "safe" or not allow_destructive) and not candidate_only_flow and not sandbox_reasoning_candidate:
            skipped.append({"probe_id": probe.get("probe_id"), "reason": "destructive_blocked_by_mode", "risk_type": risk})
            continue
        risk_limit = (budget.get("risk_budget") or {}).get(risk)
        if risk_limit is not None and per_risk_used.get(risk, 0) >= int(risk_limit):
            skipped.append({"probe_id": probe.get("probe_id"), "reason": "risk_budget_exceeded", "risk_type": risk})
            continue
        selected.append({**probe, "plan_rank": len(selected) + 1, "selected_by": "risk_based_probe_planner"})
        per_risk_used[risk] = per_risk_used.get(risk, 0) + 1
        if len(selected) >= max_count:
            break
    return selected, skipped


def _score_probe(probe: dict[str, Any], risk_profile: dict[str, Any], patterns: list[dict[str, Any]], strategy: dict[str, Any] | None = None, flow_execution: dict[str, Any] | None = None, replay_sandbox: dict[str, Any] | None = None) -> dict[str, Any]:
    risk = _risk_from_probe(probe)
    severity = str(probe.get("severity") or "P2")
    source = str(probe.get("source") or "unknown")
    base = _risk_score(risk) * 0.45 + _severity_score(severity) * 0.25
    source_bonus = {"enterprise_business_flow_graph": 0.24, "enterprise_knowledge_rag": 0.2, "enterprise_history_rag": 0.18, "universal_spec_behavior": 0.19, "counterexample_relation_mining": 0.28, "business_outcome_validation": 0.32, "business_reconciliation": 0.36, "business_invariant_mining": 0.40, "multi_source_business_reasoning": 0.46, "business_lifecycle_reasoning": 0.50, "consistency_isolation_reasoning": 0.54, "metamorphic_differential_reasoning": 0.58, "temporal_data_regression_reasoning": 0.62, "business_causality_conservation": 0.66, "business_population_constraints": 0.70, "business_event_chain_reasoning": 0.74, "business_saga_compensation_reasoning": 0.78, "business_assurance_coverage": 0.80, "multi_industry_business_reasoning": 0.82, "enterprise_business_knowledge_asset": 0.84, "business_world_model": 0.86, "business_adaptation_layer": 0.10, "real_project_pattern": 0.06, "feedback_adjusted_policy": 0.14, "rag_enhanced": 0.12}.get(source, 0.04)
    boost, reasons = _path_risk_boost(probe, risk_profile, patterns)
    learning_boost, learning_reasons = _strategy_learning_boost(probe, strategy)
    flow_exec_boost, flow_exec_reasons = _flow_execution_boost(probe, flow_execution)
    replay_boost, replay_reasons = _replay_evidence_boost(probe, replay_sandbox)
    confirmed_learning_bonus = max(0.0, min(0.25, float(probe.get("learning_bonus") or 0.0)))
    confirmed_learning_reasons = [f"确认缺陷记忆加分 {confirmed_learning_bonus:.2f}"] if confirmed_learning_bonus >= 0.01 else []
    validated_yield_boost, validated_yield_reasons, validated_yield_flags = _validated_yield_priority(
        probe,
        flow_exec_boost=flow_exec_boost,
        replay_boost=replay_boost,
    )
    destructive_penalty = 0.12 if _is_destructive(probe) else 0.0
    candidate_penalty = 0.08 if probe.get("execution_policy") == "candidate_only" else 0.0
    score = max(0.05, min(1.0, base + source_bonus + boost + learning_boost + flow_exec_boost + replay_boost + confirmed_learning_bonus + validated_yield_boost - destructive_penalty - candidate_penalty))
    reason_list = [*validated_yield_reasons, *reasons, *learning_reasons, *flow_exec_reasons, *replay_reasons, *confirmed_learning_reasons] or ["OpenAPI 风险语义匹配"]
    return {
        "priority_score": round(score, 6),
        "priority_reasons": reason_list[:8],
        "strategy_learning_boost": round(learning_boost, 6),
        "business_flow_execution_boost": round(flow_exec_boost, 6),
        "replay_evidence_boost": round(replay_boost, 6),
        "confirmed_bug_learning_bonus": round(confirmed_learning_bonus, 6),
        "validated_yield_priority_score": validated_yield_boost,
        "validated_yield_priority_reasons": validated_yield_reasons,
        "validated_yield_signal_level": validated_yield_flags["signal_level"],
        "strict_validation_ready": validated_yield_flags["strict_validation_ready"],
        "repro_ready_signal": validated_yield_flags["repro_ready_signal"],
        "evidence_ready_signal": validated_yield_flags["evidence_ready_signal"],
        "destructive": _is_destructive(probe),
    }


def build_risk_based_probe_plan(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    paths = config_paths(project, root)
    cfg = load_real_project_config(project, root)
    mode = str(cfg.get("discovery_mode") or "standard").lower()
    if mode not in MODE_BUDGETS:
        mode = "standard"
    openapi = _read_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _read_json(paths["input_dir"] / "openapi.json", {})
    if not isinstance(openapi, dict):
        openapi = {}
    patterns = _load_enterprise_patterns(project, root)
    risk_profile = _load_risk_profile(project, root)
    business_adaptation_profile = build_business_adaptation_profile(project, root)
    multi_industry_business_profile = load_multi_industry_business_profile(project, root) or build_multi_industry_business_profile(project, root)
    enterprise_business_knowledge_asset = load_enterprise_business_knowledge_asset(project, root) or build_enterprise_business_knowledge_asset(project, root)
    enterprise_business_knowledge_evidence_bundle = build_enterprise_knowledge_evidence_bundle(project, root)
    enterprise_testops_control_plane = build_enterprise_testops_control_plane(project, root, {"target_environment": cfg.get("target_environment")})
    strategy_learning = load_enterprise_strategy_learning(project, root)
    enterprise_knowledge = load_enterprise_test_knowledge(project, root) or build_enterprise_test_knowledge(project, root, options={"skip_probe_preview": True})
    business_flow_graph = load_business_flow_graph(project, root) or build_business_flow_graph(project, root, options={"skip_knowledge_build": True, "scenario_probe_count": max(120, int(cfg.get("max_probe_count") or 100))})
    business_flow_execution = load_business_flow_execution_result(project, root)
    replay_evidence_sandbox = load_replay_evidence_sandbox(project, root)
    universal_defect_mining = load_universal_defect_mining(project, root) or build_universal_defect_mining_profile(project, root, options={"preview_probe_count": max(160, int(cfg.get("max_probe_count") or 100) * 2)})
    counterexample_discovery = load_counterexample_discovery(project, root) or build_counterexample_discovery_profile(project, root, options={"preview_relation_count": max(180, int(cfg.get("max_probe_count") or 100) * 2)})
    business_outcome_profile = load_business_outcome_profile(project, root) or build_business_outcome_profile(project, root, options={"preview_probe_count": max(80, int(cfg.get("max_probe_count") or 100) * 2)})
    business_reconciliation_profile = load_business_reconciliation_profile(project, root) or build_business_reconciliation_profile(project, root, options={"preview_probe_count": max(80, int(cfg.get("max_probe_count") or 100) * 2)})
    business_invariant_profile = load_business_invariant_profile(project, root) or build_business_invariant_profile(project, root, options={"preview_probe_count": max(140, int(cfg.get("max_probe_count") or 100) * 2)})
    multi_source_reasoning_profile = load_multi_source_reasoning_profile(project, root) or build_multi_source_reasoning_profile(project, root, options={"preview_probe_count": max(160, int(cfg.get("max_probe_count") or 100) * 2)})
    business_lifecycle_profile = load_business_lifecycle_profile(project, root) or build_business_lifecycle_profile(project, root, options={"preview_probe_count": max(180, int(cfg.get("max_probe_count") or 100) * 2)})
    consistency_isolation_profile = load_consistency_isolation_profile(project, root) or build_consistency_isolation_profile(project, root, options={"preview_probe_count": max(180, int(cfg.get("max_probe_count") or 100) * 2)})
    metamorphic_differential_profile = load_metamorphic_differential_profile(project, root) or build_metamorphic_differential_profile(project, root, options={"preview_probe_count": max(180, int(cfg.get("max_probe_count") or 100) * 2)})
    temporal_data_regression_profile = load_temporal_data_regression_profile(project, root) or build_temporal_data_regression_profile(project, root, options={"preview_probe_count": max(180, int(cfg.get("max_probe_count") or 100) * 2)})
    business_causality_profile = load_business_causality_profile(project, root) or build_business_causality_profile(project, root, options={"preview_probe_count": max(180, int(cfg.get("max_probe_count") or 100) * 2)})
    business_population_profile = load_business_population_constraint_profile(project, root) or build_business_population_constraint_profile(project, root, options={"preview_probe_count": max(180, int(cfg.get("max_probe_count") or 100) * 2)})
    business_event_chain_profile = load_business_event_chain_profile(project, root) or build_business_event_chain_profile(project, root, options={"preview_probe_count": max(180, int(cfg.get("max_probe_count") or 100) * 2)})
    business_saga_compensation_profile = load_business_saga_compensation_profile(project, root) or build_business_saga_compensation_profile(project, root, options={"preview_probe_count": max(180, int(cfg.get("max_probe_count") or 100) * 2)})
    business_assurance_coverage_profile = load_business_assurance_coverage_profile(project, root) or build_business_assurance_coverage_profile(project, root, options={"inventory_limit": max(80, int(cfg.get("max_probe_count") or 100) * 2)})
    confirmed_bug_flywheel = build_confirmed_bug_flywheel(project, root)
    business_world_model_profile = load_business_world_model_profile(project, root) or build_business_world_model_profile(project, root)
    cross_industry_confirmed_learning = build_cross_industry_confirmed_learning(root)
    flow_probes = generate_business_flow_probes(openapi, cfg, project, root, max_count=max(120, int(cfg.get("max_probe_count") or 100)))
    knowledge_probes = generate_enterprise_knowledge_probes(openapi, cfg, project, root, max_count=max(120, int(cfg.get("max_probe_count") or 100)))
    adaptive = generate_business_adaptive_probes(openapi, cfg, project, root, max_count=max(200, int(cfg.get("max_probe_count") or 100) * 2))
    base = generate_real_project_probes(openapi, cfg, max_count=max(200, int(cfg.get("max_probe_count") or 100) * 2))
    history = generate_history_informed_probes(openapi, cfg, project, root, max_count=max(100, int(cfg.get("max_probe_count") or 100)))
    universal = generate_universal_defect_probes(openapi, cfg, project, root, max_count=max(180, int(cfg.get("max_probe_count") or 100) * 2))
    counterexamples = generate_counterexample_probes(openapi, cfg, project, root, max_count=max(180, int(cfg.get("max_probe_count") or 100) * 2))
    business_outcomes = generate_business_outcome_probes(openapi, cfg, project, root, max_count=max(80, int(cfg.get("max_probe_count") or 100) * 2))
    business_reconciliations = generate_business_reconciliation_probes(openapi, cfg, project, root, max_count=max(80, int(cfg.get("max_probe_count") or 100) * 2))
    business_invariants = generate_business_invariant_probes(openapi, cfg, project, root, max_count=max(140, int(cfg.get("max_probe_count") or 100) * 2))
    multi_source_probes = generate_multi_source_reasoning_probes(openapi, cfg, project, root, max_count=max(160, int(cfg.get("max_probe_count") or 100) * 2))
    lifecycle_probes = generate_business_lifecycle_probes(openapi, cfg, project, root, max_count=max(180, int(cfg.get("max_probe_count") or 100) * 2))
    consistency_isolation_probes = generate_consistency_isolation_probes(openapi, cfg, project, root, max_count=max(180, int(cfg.get("max_probe_count") or 100) * 2))
    metamorphic_differential_probes = generate_metamorphic_differential_probes(openapi, cfg, project, root, max_count=max(180, int(cfg.get("max_probe_count") or 100) * 2))
    temporal_data_regression_probes = generate_temporal_data_regression_probes(openapi, cfg, project, root, max_count=max(180, int(cfg.get("max_probe_count") or 100) * 2))
    business_causality_probes = generate_business_causality_probes(openapi, cfg, project, root, max_count=max(180, int(cfg.get("max_probe_count") or 100) * 2))
    business_population_probes = generate_business_population_constraint_probes(openapi, cfg, project, root, max_count=max(180, int(cfg.get("max_probe_count") or 100) * 2))
    business_event_chain_probes = generate_business_event_chain_probes(openapi, cfg, project, root, max_count=max(180, int(cfg.get("max_probe_count") or 100) * 2))
    business_saga_compensation_probes = generate_business_saga_compensation_probes(openapi, cfg, project, root, max_count=max(180, int(cfg.get("max_probe_count") or 100) * 2))
    business_assurance_coverage_probes = generate_business_assurance_coverage_probes(openapi, cfg, project, root, max_count=max(80, int(cfg.get("max_probe_count") or 100) * 2))
    multi_industry_business_probes = generate_multi_industry_business_probes(openapi, cfg, project, root, max_count=max(120, int(cfg.get("max_probe_count") or 100) * 2))
    enterprise_business_knowledge_probes = generate_enterprise_business_knowledge_probes(openapi, cfg, project, root, max_count=max(140, int(cfg.get("max_probe_count") or 100) * 2))
    enterprise_testops_probes = generate_enterprise_testops_probes(openapi, cfg, project, root, max_count=max(120, int(cfg.get("max_probe_count") or 100) * 2))
    business_world_model_probes = generate_business_world_model_probes(openapi, cfg, project, root, max_count=max(60, int(cfg.get("max_probe_count") or 100)))
    combined: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str, str, str]] = set()
    for probe in [*business_assurance_coverage_probes, *enterprise_testops_probes, *multi_industry_business_probes, *enterprise_business_knowledge_probes, *business_world_model_probes, *business_saga_compensation_probes, *business_event_chain_probes, *business_population_probes, *business_causality_probes, *temporal_data_regression_probes, *metamorphic_differential_probes, *consistency_isolation_probes, *lifecycle_probes, *multi_source_probes, *business_invariants, *business_reconciliations, *business_outcomes, *counterexamples, *universal, *flow_probes, *knowledge_probes, *adaptive, *history, *base]:
        key = (str(probe.get("risk_type")), str(probe.get("method")), str(probe.get("path")), str(probe.get("source")), str(probe.get("history_pattern_id")), str(probe.get("universal_risk_type")), str(probe.get("counterexample_type")), str(probe.get("business_outcome_type")), str(probe.get("business_reconciliation_type")), str(probe.get("business_invariant_type")), str(probe.get("reasoning_type")), str(probe.get("lifecycle_type")), str(probe.get("metamorphic_relation")), str(probe.get("temporal_regression_type")), str(probe.get("business_causality_type")), str(probe.get("business_population_type")), str(probe.get("business_event_chain_type")), str(probe.get("business_saga_compensation_type")), str(probe.get("quality_assurance_mutation")), str(probe.get("contract_id")))
        if key in seen:
            continue
        seen.add(key)
        flywheel_enriched = annotate_probes_with_confirmed_learning([probe], project, root, profile=confirmed_bug_flywheel)[0]
        cross_industry_enriched = annotate_probes_with_cross_industry_learning([flywheel_enriched], root)[0]
        score_data = _score_probe(cross_industry_enriched, risk_profile, patterns, strategy_learning, business_flow_execution, replay_evidence_sandbox)
        combined.append({**cross_industry_enriched, **score_data})
    allow_destructive = bool(cfg.get("allow_destructive_tests")) or bool(MODE_BUDGETS[mode].get("allow_destructive"))
    budget = dict(MODE_BUDGETS[mode])
    max_count = int(cfg.get("max_probe_count") or budget["max_probe_count"])
    max_count = min(max_count, int(budget["max_probe_count"])) if mode == "safe" else max_count
    selected, skipped = _select_probes_by_budget(
        combined,
        mode=mode,
        allow_destructive=allow_destructive,
        budget=budget,
        max_count=max_count,
    )
    # Phase90: coverage memory biases the next run toward unexplored business
    # surfaces and evidence gaps, while suppressing already-confirmed/rejected
    # duplicates. It changes ranking only; existing safety/risk budgets remain
    # authoritative below.
    try:
        from .business_risk_coverage_map import BusinessRiskCoverageMap
        coverage_map = BusinessRiskCoverageMap(project, root)
        selected, coverage_summary = coverage_map.prioritize(selected)
        for index, probe in enumerate(selected, start=1):
            probe["plan_rank"] = index
    except Exception as exc:
        coverage_summary = {"error": str(exc)[:300]}
    validated_yield_summary = _summarize_validated_yield_priority(combined, selected)

    summary = {
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "mode": mode,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "openapi_operation_count": len(_openapi_paths(openapi)),
        "enterprise_pattern_count": len(patterns),
        "candidate_probe_count": len(combined),
        "selected_probe_count": len(selected),
        "skipped_probe_count": len(skipped),
        "business_risk_coverage_map": coverage_summary,
        "risk_distribution": _counter([_risk_from_probe(p) for p in selected]),
        "source_distribution": _counter([str(p.get("source") or "unknown") for p in selected]),
        "severity_distribution": _counter([str(p.get("severity") or "P2") for p in selected]),
        "business_domains": [item.get("domain") for item in business_adaptation_profile.get("selected_domains", [])],
        "multi_industry_business_understanding_enabled": bool(multi_industry_business_profile),
        "multi_industry_recognized_industries": ((multi_industry_business_profile or {}).get("summary") or {}).get("recognized_industries", []),
        "multi_industry_top_industry": ((multi_industry_business_profile or {}).get("summary") or {}).get("top_industry", "unknown_general_business"),
        "multi_industry_object_count": ((multi_industry_business_profile or {}).get("summary") or {}).get("business_object_count", 0),
        "multi_industry_state_machine_count": ((multi_industry_business_profile or {}).get("summary") or {}).get("state_machine_count", 0),
        "multi_industry_oracle_count": ((multi_industry_business_profile or {}).get("summary") or {}).get("oracle_count", 0),
        "multi_industry_p0_risk_domain_count": ((multi_industry_business_profile or {}).get("summary") or {}).get("p0_risk_domain_count", 0),
        "multi_industry_business_probe_count": len([p for p in selected if p.get("source") == "multi_industry_business_reasoning"]),
        "business_world_model_enabled": bool(business_world_model_profile),
        "business_world_model_candidate_relation_count": ((business_world_model_profile or {}).get("summary") or {}).get("candidate_relation_count", 0),
        "business_world_model_candidate_state_machine_count": ((business_world_model_profile or {}).get("summary") or {}).get("candidate_state_machine_count", 0),
        "business_world_model_confirmed_contract_count": ((business_world_model_profile or {}).get("summary") or {}).get("confirmed_contract_count", 0),
        "business_world_model_probe_count": len([p for p in selected if p.get("source") == "business_world_model"]),
        "cross_industry_confirmed_learning_enabled": bool(cross_industry_confirmed_learning),
        "cross_industry_confirmed_transfer_pattern_count": ((cross_industry_confirmed_learning or {}).get("summary") or {}).get("cross_industry_transfer_pattern_count", 0),
        "enterprise_business_knowledge_center_enabled": bool(((enterprise_business_knowledge_asset or {}).get("summary") or {}).get("active_source_count")),
        "enterprise_business_knowledge_source_count": ((enterprise_business_knowledge_asset or {}).get("summary") or {}).get("active_source_count", 0),
        "enterprise_business_knowledge_rule_count": ((enterprise_business_knowledge_asset or {}).get("summary") or {}).get("rule_count", 0),
        "enterprise_business_knowledge_oracle_count": ((enterprise_business_knowledge_asset or {}).get("summary") or {}).get("oracle_count", 0),
        "enterprise_business_knowledge_relationship_count": ((enterprise_business_knowledge_asset or {}).get("summary") or {}).get("relationship_count", 0),
        "enterprise_business_knowledge_probe_count": len([p for p in selected if p.get("source") == "enterprise_business_knowledge_asset"]),
        "enterprise_testops_control_plane_enabled": bool(enterprise_testops_control_plane),
        "enterprise_testops_target_environment": ((enterprise_testops_control_plane.get("environment_health") or {}).get("target_environment") if isinstance(enterprise_testops_control_plane, dict) else ""),
        "enterprise_testops_environment_testable": bool((enterprise_testops_control_plane.get("environment_health") or {}).get("target_testable")) if isinstance(enterprise_testops_control_plane, dict) else False,
        "enterprise_testops_data_automatic_preparation_ratio": ((enterprise_testops_control_plane.get("test_data") or {}).get("automatic_preparation_ratio") if isinstance(enterprise_testops_control_plane, dict) else 0.0),
        "enterprise_testops_journey_count": len(((enterprise_testops_control_plane.get("journey_graph") or {}).get("journeys") or [])) if isinstance(enterprise_testops_control_plane, dict) else 0,
        "enterprise_testops_permission_probe_count": len([p for p in selected if str(p.get("source") or "").startswith("enterprise_testops_permission")]),
        "enterprise_testops_probe_count": len([p for p in selected if str(p.get("source") or "").startswith("enterprise_testops_")]),
        "universal_defect_mining_enabled": bool(universal_defect_mining),
        "universal_requirement_rule_count": ((universal_defect_mining or {}).get("summary") or {}).get("requirement_rule_count", 0),
        "universal_structure_finding_count": ((universal_defect_mining or {}).get("summary") or {}).get("structure_finding_count", 0),
        "universal_probe_count": len([p for p in selected if p.get("source") == "universal_spec_behavior"]),
        "counterexample_discovery_enabled": bool(counterexample_discovery),
        "counterexample_resource_count": ((counterexample_discovery or {}).get("summary") or {}).get("resource_count", 0),
        "counterexample_relation_count": ((counterexample_discovery or {}).get("summary") or {}).get("relation_count", 0),
        "counterexample_probe_count": len([p for p in selected if p.get("source") == "counterexample_relation_mining"]),
        "business_outcome_validation_enabled": bool(business_outcome_profile),
        "business_outcome_export_contract_count": ((business_outcome_profile or {}).get("summary") or {}).get("export_contract_count", 0),
        "business_outcome_probe_count": len([p for p in selected if p.get("source") == "business_outcome_validation"]),
        "business_reconciliation_enabled": bool(business_reconciliation_profile),
        "business_reconciliation_contract_count": ((business_reconciliation_profile or {}).get("summary") or {}).get("reconciliation_contract_count", 0),
        "business_reconciliation_metric_count": ((business_reconciliation_profile or {}).get("summary") or {}).get("metric_count", 0),
        "business_reconciliation_probe_count": len([p for p in selected if p.get("source") == "business_reconciliation"]),
        "business_invariant_mining_enabled": bool(business_invariant_profile),
        "business_invariant_contract_count": ((business_invariant_profile or {}).get("summary") or {}).get("invariant_contract_count", 0),
        "business_invariant_filter_count": ((business_invariant_profile or {}).get("summary") or {}).get("filter_invariant_count", 0),
        "business_invariant_relation_count": ((business_invariant_profile or {}).get("summary") or {}).get("referential_invariant_count", 0),
        "business_invariant_probe_count": len([p for p in selected if p.get("source") == "business_invariant_mining"]),
        "multi_source_reasoning_enabled": bool(multi_source_reasoning_profile),
        "multi_source_reasoning_contract_count": ((multi_source_reasoning_profile or {}).get("summary") or {}).get("total_contract_count", 0),
        "multi_source_cross_system_oracle_count": ((multi_source_reasoning_profile or {}).get("summary") or {}).get("cross_system_oracle_count", 0),
        "multi_source_confirmed_bug_memory_count": ((multi_source_reasoning_profile or {}).get("summary") or {}).get("confirmed_bug_memory_count", 0),
        "multi_source_reasoning_probe_count": len([p for p in selected if p.get("source") == "multi_source_business_reasoning"]),
        "business_lifecycle_reasoning_enabled": bool(business_lifecycle_profile),
        "business_lifecycle_contract_count": ((business_lifecycle_profile or {}).get("summary") or {}).get("lifecycle_contract_count", 0),
        "business_lifecycle_history_contract_count": ((business_lifecycle_profile or {}).get("summary") or {}).get("history_contract_count", 0),
        "business_lifecycle_probe_count": len([p for p in selected if p.get("source") == "business_lifecycle_reasoning"]),
        "consistency_isolation_reasoning_enabled": bool(consistency_isolation_profile),
        "consistency_isolation_contract_count": ((consistency_isolation_profile or {}).get("summary") or {}).get("consistency_contract_count", 0),
        "consistency_isolation_tenant_contract_count": ((consistency_isolation_profile or {}).get("summary") or {}).get("tenant_isolation_contract_count", 0),
        "consistency_isolation_role_access_contract_count": ((consistency_isolation_profile or {}).get("summary") or {}).get("role_access_contract_count", 0),
        "consistency_isolation_probe_count": len([p for p in selected if p.get("source") == "consistency_isolation_reasoning"]),
        "metamorphic_differential_reasoning_enabled": bool(metamorphic_differential_profile),
        "metamorphic_differential_contract_count": ((metamorphic_differential_profile or {}).get("summary") or {}).get("metamorphic_contract_count", 0),
        "metamorphic_differential_filter_relation_count": ((metamorphic_differential_profile or {}).get("summary") or {}).get("filter_relation_count", 0),
        "metamorphic_differential_temporal_partition_relation_count": ((metamorphic_differential_profile or {}).get("summary") or {}).get("temporal_partition_relation_count", 0),
        "metamorphic_differential_probe_count": len([p for p in selected if p.get("source") == "metamorphic_differential_reasoning"]),
        "temporal_data_regression_reasoning_enabled": bool(temporal_data_regression_profile),
        "temporal_data_regression_contract_count": ((temporal_data_regression_profile or {}).get("summary") or {}).get("temporal_contract_count", 0),
        "temporal_data_regression_immutable_field_count": ((temporal_data_regression_profile or {}).get("summary") or {}).get("temporal_immutable_field_count", 0),
        "temporal_data_regression_probe_count": len([p for p in selected if p.get("source") == "temporal_data_regression_reasoning"]),
        "business_causality_conservation_enabled": bool(business_causality_profile),
        "business_causality_contract_count": ((business_causality_profile or {}).get("summary") or {}).get("causality_contract_count", 0),
        "business_causality_side_effect_contract_count": ((business_causality_profile or {}).get("summary") or {}).get("side_effect_contract_count", 0),
        "business_causality_journal_balance_contract_count": ((business_causality_profile or {}).get("summary") or {}).get("journal_balance_contract_count", 0),
        "business_causality_period_rollforward_contract_count": ((business_causality_profile or {}).get("summary") or {}).get("period_rollforward_contract_count", 0),
        "business_causality_inventory_reservation_contract_count": ((business_causality_profile or {}).get("summary") or {}).get("inventory_reservation_contract_count", 0),
        "business_causality_probe_count": len([p for p in selected if p.get("source") == "business_causality_conservation"]),
        "business_population_constraints_enabled": bool(business_population_profile),
        "business_population_contract_count": ((business_population_profile or {}).get("summary") or {}).get("population_contract_count", 0),
        "business_population_group_limit_contract_count": ((business_population_profile or {}).get("summary") or {}).get("group_limit_contract_count", 0),
        "business_population_probe_count": len([p for p in selected if p.get("source") == "business_population_constraints"]),
        "business_saga_compensation_reasoning_enabled": bool(business_saga_compensation_profile),
        "business_saga_compensation_contract_count": ((business_saga_compensation_profile or {}).get("summary") or {}).get("saga_compensation_contract_count", 0),
        "business_saga_compensation_coverage_contract_count": ((business_saga_compensation_profile or {}).get("summary") or {}).get("compensation_coverage_contract_count", 0),
        "business_saga_compensation_probe_count": len([p for p in selected if p.get("source") == "business_saga_compensation_reasoning"]),
        "business_assurance_coverage_enabled": bool(business_assurance_coverage_profile),
        "business_assurance_score": ((business_assurance_coverage_profile or {}).get("summary") or {}).get("assurance_score", 0),
        "business_assurance_mutation_kill_rate": ((business_assurance_coverage_profile or {}).get("summary") or {}).get("modeled_mutation_kill_rate", 0),
        "business_assurance_critical_gap_count": ((business_assurance_coverage_profile or {}).get("summary") or {}).get("critical_uncovered_gap_count", 0),
        "business_assurance_coverage_probe_count": len([p for p in selected if p.get("source") == "business_assurance_coverage"]),
        "confirmed_bug_flywheel_enabled": bool(confirmed_bug_flywheel),
        "confirmed_bug_flywheel_pattern_count": int((confirmed_bug_flywheel.get("summary") or {}).get("learning_pattern_count") or 0),
        "confirmed_bug_flywheel_pending_promotion_count": int((confirmed_bug_flywheel.get("summary") or {}).get("pending_promotion_count") or 0),
        "confirmed_bug_flywheel_regression_candidate_count": int((confirmed_bug_flywheel.get("summary") or {}).get("approved_regression_candidate_count") or 0),
        "business_event_chain_reasoning_enabled": bool(business_event_chain_profile),
        "business_event_chain_contract_count": ((business_event_chain_profile or {}).get("summary") or {}).get("event_chain_contract_count", 0),
        "business_event_chain_delivery_contract_count": ((business_event_chain_profile or {}).get("summary") or {}).get("event_delivery_contract_count", 0),
        "business_event_chain_probe_count": len([p for p in selected if p.get("source") == "business_event_chain_reasoning"]),
        "business_adaptive_probe_count": len([p for p in selected if p.get("source") == "business_adaptation_layer"]),
        "enterprise_knowledge_enabled": bool(enterprise_knowledge),
        "enterprise_knowledge_document_count": ((enterprise_knowledge or {}).get("summary") or {}).get("document_count", 0),
        "enterprise_knowledge_probe_count": len([p for p in selected if p.get("source") == "enterprise_knowledge_rag"]),
        "business_flow_graph_enabled": bool(business_flow_graph),
        "business_flow_count": ((business_flow_graph or {}).get("summary") or {}).get("flow_count", 0),
        "business_flow_scenario_probe_count": len([p for p in selected if p.get("source") == "enterprise_business_flow_graph"]),
        "business_flow_execution_enabled": bool(business_flow_execution),
        "business_flow_execution_assertion_count": ((business_flow_execution or {}).get("summary") or {}).get("assertion_count", 0),
        "business_flow_execution_failed_assertion_count": ((business_flow_execution or {}).get("summary") or {}).get("failed_assertion_count", 0),
        "replay_evidence_sandbox_enabled": bool(replay_evidence_sandbox),
        "replay_evidence_packet_count": ((replay_evidence_sandbox or {}).get("summary") or {}).get("evidence_packet_count", 0),
        "replay_evidence_enhanced_issue_count": ((replay_evidence_sandbox or {}).get("summary") or {}).get("enhanced_candidate_issue_count", 0),
        "strategy_learning_enabled": bool(strategy_learning),
        "strategy_learning_feedback_rows": ((strategy_learning or {}).get("summary") or {}).get("feedback_rows", 0),
        "strategy_learning_weight_count": len((strategy_learning or {}).get("risk_type_weights") or []) + len((strategy_learning or {}).get("endpoint_weights") or []),
        "risk_budget": budget.get("risk_budget") or {},
        "allow_destructive_effective": allow_destructive,
        "validated_yield_priority_signal_summary": validated_yield_summary,
        "budget_selection_bias_summary": {
            "preferred_target": "validated_yield",
            "deprioritized_proxy": "candidate_scale",
            "selection_prefers_strictly_verifiable_output": validated_yield_summary.get("selection_prefers_strictly_verifiable_output"),
            "selection_gain": validated_yield_summary.get("selection_gain"),
            "strict_validation_ready_selection_rate": validated_yield_summary.get("strict_validation_ready_selection_rate"),
            "candidate_only_selection_rate": validated_yield_summary.get("candidate_only_selection_rate"),
            "strong_signal_selection_rate": ((validated_yield_summary.get("signal_level_selection_rate") or {}).get("strong")),
            "weak_signal_selection_rate": ((validated_yield_summary.get("signal_level_selection_rate") or {}).get("weak")),
        },
    }
    plan = {
        "phase": "phase26_risk_based_probe_planner",
        "summary": summary,
        "risk_profile_digest": _digest_risk_profile(risk_profile),
        "confirmed_bug_flywheel": {"summary": confirmed_bug_flywheel.get("summary") or {}, "ledger_check": confirmed_bug_flywheel.get("ledger_check") or {}},
        "business_world_model": {"summary": (business_world_model_profile or {}).get("summary") or {}, "governance": (business_world_model_profile or {}).get("governance") or {}},
        "cross_industry_confirmed_learning": {"summary": (cross_industry_confirmed_learning or {}).get("summary") or {}, "governance": (cross_industry_confirmed_learning or {}).get("governance") or {}},
        "business_adaptation_profile": {
            "selected_domains": business_adaptation_profile.get("selected_domains", []),
            "operation_count": business_adaptation_profile.get("operation_count", 0),
            "private_leak_check": business_adaptation_profile.get("private_leak_check", {}),
        },
        "multi_industry_business_profile": {
            "summary": (multi_industry_business_profile or {}).get("summary", {}),
            "recognized_industries": (multi_industry_business_profile or {}).get("recognized_industries", []),
            "risk_domains": (multi_industry_business_profile or {}).get("risk_domains", []),
            "private_leak_check": (multi_industry_business_profile or {}).get("private_leak_check", {}),
        },
        "enterprise_business_knowledge_asset": {
            "summary": (enterprise_business_knowledge_asset or {}).get("summary", {}),
            "asset_id": (enterprise_business_knowledge_asset or {}).get("asset_id"),
            "evidence_bundle": enterprise_business_knowledge_evidence_bundle,
        },
        "enterprise_testops_control_plane": {
            "target_environment": (enterprise_testops_control_plane.get("environment_health") or {}).get("target_environment") if isinstance(enterprise_testops_control_plane, dict) else "",
            "environment_health": {"status": (enterprise_testops_control_plane.get("environment_health") or {}).get("target_health_status"), "testable": (enterprise_testops_control_plane.get("environment_health") or {}).get("target_testable")} if isinstance(enterprise_testops_control_plane, dict) else {},
            "test_data": {"automatic_preparation_ratio": (enterprise_testops_control_plane.get("test_data") or {}).get("automatic_preparation_ratio"), "manual_gap_count": len((enterprise_testops_control_plane.get("test_data") or {}).get("manual_gaps") or [])} if isinstance(enterprise_testops_control_plane, dict) else {},
            "journey_coverage": (enterprise_testops_control_plane.get("journey_graph") or {}).get("coverage", {}) if isinstance(enterprise_testops_control_plane, dict) else {},
            "security": {"production_write_blocked": ((enterprise_testops_control_plane.get("security_audit_report") or {}).get("production_protection") or {}).get("destructive_operations_blocked")} if isinstance(enterprise_testops_control_plane, dict) else {},
        },
        "selected_probes": selected,
        "skipped_probes": skipped[:100],
        "business_risk_coverage_map": coverage_summary,
        "governance": {
            "real_project_mode": True,
            "uses_only_real_project_public_inputs": True,
            "uses_no_benchmark_answer_files": True,
            "planner_inputs": ["openapi", "real_project_config", "universal_spec_behavior_mining", "semantic_counterexample_discovery", "business_outcome_validation", "business_reconciliation", "business_invariant_mining", "multi_source_business_reasoning", "business_lifecycle_reasoning", "consistency_isolation_reasoning", "metamorphic_differential_reasoning", "temporal_data_regression_reasoning", "business_causality_conservation", "business_population_constraints", "business_event_chain_reasoning", "business_saga_compensation_reasoning", "business_assurance_coverage", "confirmed_bug_flywheel", "business_world_model_confirmed_contracts", "cross_industry_confirmed_metadata", "business_adaptation_profile", "multi_industry_business_reasoning", "enterprise_knowledge_unified_ingestion", "enterprise_business_knowledge_asset", "enterprise_testops_control_plane", "enterprise_bug_pattern_library", "real_project_risk_profile", "enterprise_strategy_learning", "enterprise_test_knowledge", "business_flow_graph", "business_flow_execution_assertions", "replay_evidence_sandbox"],
        },
    }
    leak = _private_leak_check(plan)
    plan["private_leak_check"] = leak
    out_dir = root / "platform_outputs" / project / "risk_based_planning"
    ws_dir = root / "platform_workspace" / project / "defect_discovery"
    _write_json(out_dir / "risk_based_probe_plan.json", plan)
    _write_json(out_dir / "risk_based_probe_plan_summary.json", {"summary": summary, "private_leak_check": leak})
    _write_json(ws_dir / "risk_based_probe_plan.json", plan)
    _write_text(out_dir / "risk_based_probe_plan_report.html", render_risk_based_probe_plan_report(plan))
    return plan


def _digest_risk_profile(profile: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, dict):
        return {}
    return {
        "risk_distribution": profile.get("risk_distribution") or profile.get("risk_type_distribution") or {},
        "module_distribution": profile.get("module_distribution") or {},
        "high_risk_modules": profile.get("high_risk_modules") or {},
        "historical_bug_count": profile.get("historical_bug_count") or profile.get("bug_count") or 0,
    }


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False).lower()
    leaks = sorted([m for m in PRIVATE_MARKERS if m.lower() in text])
    return {"passed": not leaks, "leak_terms": leaks}


def load_risk_based_probe_plan(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    path = root / "platform_workspace" / project / "defect_discovery" / "risk_based_probe_plan.json"
    if not path.exists():
        return None
    data = _read_json(path, {})
    return data if isinstance(data, dict) else None


def render_risk_based_probe_plan_report(plan: dict[str, Any]) -> str:
    summary = plan.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(k)}</span><b>{_html_escape(v)}</b></div>" for k, v in summary.items() if k not in {"risk_budget", "risk_distribution", "source_distribution", "severity_distribution", "validated_yield_priority_signal_summary", "budget_selection_bias_summary"})
    risk_rows = "".join(f"<tr><td>{_html_escape(k)}</td><td>{_html_escape(v)}</td></tr>" for k, v in (summary.get("risk_distribution") or {}).items())
    validated_yield_rows = "".join(
        f"<tr><td>{_html_escape(k)}</td><td>{_html_escape(v)}</td></tr>"
        for k, v in (summary.get("validated_yield_priority_signal_summary") or {}).items()
        if k not in {"candidate_signal_distribution", "selected_signal_distribution", "signal_level_selection_rate"}
    )
    validated_yield_candidate_rows = "".join(f"<tr><td>{_html_escape(k)}</td><td>{_html_escape(v)}</td></tr>" for k, v in ((summary.get("validated_yield_priority_signal_summary") or {}).get("candidate_signal_distribution") or {}).items())
    validated_yield_selected_rows = "".join(f"<tr><td>{_html_escape(k)}</td><td>{_html_escape(v)}</td></tr>" for k, v in ((summary.get("validated_yield_priority_signal_summary") or {}).get("selected_signal_distribution") or {}).items())
    validated_yield_rate_rows = "".join(f"<tr><td>{_html_escape(k)}</td><td>{_html_escape(v)}</td></tr>" for k, v in ((summary.get("validated_yield_priority_signal_summary") or {}).get("signal_level_selection_rate") or {}).items())
    bias_rows = "".join(f"<tr><td>{_html_escape(k)}</td><td>{_html_escape(v)}</td></tr>" for k, v in (summary.get("budget_selection_bias_summary") or {}).items())
    rows = []
    for p in (plan.get("selected_probes") or [])[:100]:
        rows.append(f"<tr><td>{_html_escape(p.get('plan_rank'))}</td><td>{_html_escape(p.get('priority_score'))}</td><td>{_html_escape(p.get('severity'))}</td><td>{_html_escape(p.get('risk_type'))}</td><td>{_html_escape(p.get('method'))} {_html_escape(p.get('path'))}</td><td>{_html_escape(p.get('source'))}</td><td>{_html_escape('; '.join(p.get('priority_reasons') or []))}</td></tr>")
    leak = plan.get("private_leak_check") or {}
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>Risk-based Probe Plan</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#eef2ff;color:#3730a3}}</style></head><body>
<section class='hero'><span class='badge'>Phase26</span><h1>真实项目 Risk-based Probe Planner</h1><p>根据企业历史 Bug、风险画像、OpenAPI 和发现模式，为真实项目生成高价值探针优先级计划。</p><p>私有数据泄露检查：<b>{_html_escape('passed' if leak.get('passed') else 'failed')}</b></p></section>
<section class='panel'><h2>规划概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>风险分布</h2><table><tbody>{risk_rows or '<tr><td>暂无</td><td>0</td></tr>'}</tbody></table></section>
<section class='panel'><h2>Validated Yield 优先信号</h2><table><tbody>{validated_yield_rows or '<tr><td>暂无</td><td>0</td></tr>'}</tbody></table><h3>候选信号分布</h3><table><tbody>{validated_yield_candidate_rows or '<tr><td>暂无</td><td>0</td></tr>'}</tbody></table><h3>入选信号分布</h3><table><tbody>{validated_yield_selected_rows or '<tr><td>暂无</td><td>0</td></tr>'}</tbody></table><h3>各信号层级入选率</h3><table><tbody>{validated_yield_rate_rows or '<tr><td>暂无</td><td>0</td></tr>'}</tbody></table></section>
<section class='panel'><h2>预算偏好证据</h2><table><tbody>{bias_rows or '<tr><td>暂无</td><td>0</td></tr>'}</tbody></table></section>
<section class='panel'><h2>Top 探针计划</h2><table><thead><tr><th>#</th><th>Score</th><th>等级</th><th>风险</th><th>接口</th><th>来源</th><th>原因</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="7">暂无探针</td></tr>'}</tbody></table></section>
</body></html>"""


# ══════════════════════════════════════════════════════════════════════════════
# Phase92A: Evidence Supplementation Strategy
# ══════════════════════════════════════════════════════════════════════════════

EVIDENCE_SUPPLEMENTATION_STRATEGIES = {
    "ENTITY_BINDING_MISSING": {
        "priority": 0.95,
        "action": "query_entity_binding",
        "description": "Execute read-only query to establish entity binding",
        "probe_template": "GET /api/{entity_type}?id={entity_candidate}",
        "risk_level": "low",
    },
    "BEFORE_SNAPSHOT_MISSING": {
        "priority": 0.90,
        "action": "capture_before_snapshot",
        "description": "Execute read-only snapshot flow before mutation",
        "probe_template": "GET /api/{entity_type}/{entity_id}",
        "risk_level": "low",
    },
    "AFTER_SNAPSHOT_MISSING": {
        "priority": 0.90,
        "action": "capture_after_snapshot",
        "description": "Execute read-only snapshot flow after mutation",
        "probe_template": "GET /api/{entity_type}/{entity_id}",
        "risk_level": "low",
    },
    "CLEANUP_PENDING": {
        "priority": 0.85,
        "action": "verify_cleanup_status",
        "description": "Verify cleanup execution and status",
        "probe_template": "GET /api/cleanup/{run_id}",
        "risk_level": "low",
        "block_high_risk": True,
    },
    "CLEANUP_FAILED": {
        "priority": 0.99,
        "action": "manual_cleanup_required",
        "description": "Manual cleanup required before replay",
        "probe_template": None,
        "risk_level": "high",
        "block_replay": True,
    },
    "OBSERVER_CONFLICT": {
        "priority": 0.80,
        "action": "reconcile_observer_consensus",
        "description": "Additional observer probes to reconcile conflict",
        "probe_template": "GET /api/{entity_type}/{entity_id}?role={role}",
        "risk_level": "low",
    },
    "ASYNC_WINDOW_OPEN": {
        "priority": 0.75,
        "action": "poll_async_result",
        "description": "Poll for async operation completion",
        "probe_template": "GET /api/jobs/{job_id}",
        "risk_level": "low",
        "poll_strategy": "exponential_backoff",
    },
    "FIXTURE_AMBIGUOUS": {
        "priority": 0.70,
        "action": "clarify_fixture_binding",
        "description": "Query fixture registry for clarification",
        "probe_template": "GET /api/fixtures/{fixture_id}",
        "risk_level": "low",
    },
    "REPRODUCTION_INCOMPLETE": {
        "priority": 0.65,
        "action": "complete_reproduction_flow",
        "description": "Complete missing steps in reproduction flow",
        "probe_template": "GET /api/reproduction/{flow_id}",
        "risk_level": "low",
    },
    "INVARIANT_REF_MISSING": {
        "priority": 0.60,
        "action": "identify_invariant_rule",
        "description": "Identify violated invariant from business rules",
        "probe_template": None,
        "risk_level": "low",
    },
}


def generate_evidence_supplementation_probes(
    finding: dict[str, Any],
    *,
    project_id: str = "",
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Phase92A: Generate probes to supplement missing evidence for a finding.

    For findings with business_evidence_status = PENDING_*, this function
    generates specific probes to fill the evidence gaps, rather than
    creating new random hypotheses.

    Returns a list of probe specifications that can be executed to
    gather the missing evidence.
    """
    probes = []
    missing = finding.get("missing_requirements", [])
    entity_binding = finding.get("entity_binding", {})
    entity_type = entity_binding.get("entity_type", "")
    entity_id = entity_binding.get("entity_id", "")
    
    for req in missing:
        strategy = EVIDENCE_SUPPLEMENTATION_STRATEGIES.get(req, {})
        if not strategy:
            continue
        
        priority = strategy.get("priority", 0.5)
        action = strategy.get("action", "unknown")
        template = strategy.get("probe_template")
        
        if template and entity_type:
            # Substitute entity placeholders
            probe_path = template.replace("{entity_type}", entity_type)
            if entity_id:
                probe_path = probe_path.replace("{entity_id}", entity_id)
                probe_path = probe_path.replace("{entity_candidate}", entity_id)
            else:
                probe_path = probe_path.replace("{entity_id}", "unknown")
                probe_path = probe_path.replace("{entity_candidate}", "unknown")
            
            probes.append({
                "probe_id": f"supplement_{action}_{finding.get('finding_id', 'unknown')}",
                "method": "GET",
                "path": probe_path,
                "purpose": strategy.get("description", ""),
                "priority_score": priority,
                "risk_type": "evidence_supplementation",
                "source": "phase92a_evidence_supplement",
                "parent_finding_id": finding.get("finding_id", ""),
                "parent_hypothesis_id": finding.get("hypothesis_id", ""),
                "supplementation_target": req,
                "risk_level": strategy.get("risk_level", "low"),
                "block_high_risk": strategy.get("block_high_risk", False),
                "block_replay": strategy.get("block_replay", False),
            })
        
        # Non-probe actions (manual cleanup, invariant identification)
        if not template:
            probes.append({
                "probe_id": f"supplement_{action}_{finding.get('finding_id', 'unknown')}",
                "method": "NONE",
                "path": "",
                "purpose": strategy.get("description", ""),
                "priority_score": priority,
                "risk_type": "evidence_supplementation",
                "source": "phase92a_manual_action",
                "parent_finding_id": finding.get("finding_id", ""),
                "parent_hypothesis_id": finding.get("hypothesis_id", ""),
                "supplementation_target": req,
                "manual_action": action,
                "risk_level": strategy.get("risk_level", "low"),
                "block_replay": strategy.get("block_replay", False),
            })
    
    return probes


def prioritize_evidence_supplementation(
    findings: list[dict[str, Any]],
    *,
    project_id: str = "",
    root: Path | None = None,
) -> dict[str, Any]:
    """Phase92A: Prioritize evidence supplementation for PENDING findings.

    Takes findings with business_evidence_status starting with PENDING_
    and generates a prioritized list of supplementation probes.

    This prevents the Risk Frontier from just generating new random
    hypotheses when there are existing findings that need evidence.
    """
    from .evidence_models import BUSINESS_EVIDENCE_STATUS
    
    pending_findings = [
        f for f in findings
        if f.get("business_evidence_status", "").startswith("PENDING_")
        or f.get("final_review_status") == "NEEDS_MORE_EVIDENCE"
    ]
    
    all_probes = []
    for f in pending_findings:
        probes = generate_evidence_supplementation_probes(f, project_id=project_id, root=root)
        all_probes.extend(probes)
    
    # Sort by priority score descending
    all_probes.sort(key=lambda p: p.get("priority_score", 0), reverse=True)
    
    summary = {
        "pending_findings_count": len(pending_findings),
        "supplementation_probes_count": len(all_probes),
        "top_missing_requirements": _counter([
            req for f in pending_findings
            for req in (f.get("missing_requirements") or [])
        ]),
        "block_high_risk_count": sum(1 for p in all_probes if p.get("block_high_risk")),
        "block_replay_count": sum(1 for p in all_probes if p.get("block_replay")),
    }
    
    return {
        "phase": "phase92a_evidence_supplementation",
        "project_id": project_id,
        "pending_findings": pending_findings[:50],  # Top 50
        "supplementation_probes": all_probes[:100],  # Top 100
        "summary": summary,
        "generated_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
    }


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    plan = build_risk_based_probe_plan(project)
    print(json.dumps({"ok": True, "project_id": project, "summary": plan.get("summary"), "private_leak_check": plan.get("private_leak_check")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
