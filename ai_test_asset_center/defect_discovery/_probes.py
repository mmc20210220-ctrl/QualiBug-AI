"""Defect probe generation: invariants, patterns, knowledge, memory, gaps."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_test_asset_center.adaptive_probe_optimizer import build_learned_probe_policy

from ._common import *  # noqa: F401,F403
from ._model import *  # noqa: F401,F403
from ._scenarios import *  # noqa: F401,F403
from ._model import _FULFILLMENT_PARENT_LIKE, _INVENTORY_LIKE, _PAYMENT_LIKE, _REFUND_LIKE, _resource_in, resource_name  # noqa: F401
from ._scenarios import scenario  # noqa: F401


_INVARIANT_TEMPLATES: dict[str, str] = {
    "permission_bypass": "非授权角色不得执行 {method} {path}",
    "idor": "用户不得读取或变更不属于自己的 {resource} 资源",
    "tenant_isolation": "不同租户之间的 {resource} 数据必须隔离",
    "money_consistency": "{resource} 的金额、支付、退款和汇总字段必须一致",
    "quantity_consistency": "{resource} 的数量、库存或额度不能越界",
    "state_flow": "{resource} 状态流转必须满足前置状态和后置一致性",
    "idempotency": "{method} {path} 重复提交或回调必须幂等",
    "benefit_abuse": "{resource} 的优惠、权益或折扣必须校验归属、门槛和次数",
    "approval_bypass": "{resource} 审批/复核流程不能跳过必要节点",
    "audit_log_missing": "{resource} 的关键操作必须留下可追踪审计记录",
    "file_upload_validation": "{resource} 文件上传或导入必须校验格式、大小和内容",
    "bulk_operation_partial_failure": "{resource} 批量操作必须处理部分成功、部分失败和回滚",
    "export_permission": "{resource} 导出必须遵守角色、租户和字段权限",
    "report_aggregation_error": "{resource} 报表统计口径必须和明细数据一致",
    "search_scope_leak": "{resource} 搜索和筛选不能越权返回其他组织或用户数据",
    "notification_wrong_recipient": "{resource} 通知不能发送给错误用户或泄露模板变量",
    "feature_flag_scope": "{resource} 配置开关必须按租户、组织和角色隔离",
    "soft_delete_visibility": "{resource} 删除、禁用或归档后不能在普通查询中可见",
    "callback_trust": "{resource} 外部回调不能无签名、无状态校验地改变业务状态",
    "race_condition": "{resource} 并发操作不能造成重复处理、丢失更新或资源越界",
    "time_window_boundary": "{resource} 时间窗口、过期和边界日期必须被严格校验",
}


def invariant_statement(risk: str, op: dict, roles: list[str], tenants: list[str]) -> str:
    template = _INVARIANT_TEMPLATES.get(risk)
    if template:
        return template.format(
            method=op.get("method", ""), path=op.get("path", ""), resource=op.get("resource", "")
        )
    return f"{op['resource']} 必须满足业务不变量"


def keyword_hits(domain: str, path: str) -> bool:
    """Match domain keywords against path resource tokens (industry-agnostic)."""
    path_l = str(path or "").lower()
    mapping = {
        "permission": ["admin", "login", "role", "permission", "auth"],
        "idor": ["{", "owner", "me", "self", "patient", "account", "member", "order", "booking", "claim", "record"],
        "tenant": ["tenant", "org", "organization", "workspace"],
        "order": list(_FULFILLMENT_PARENT_LIKE),
        "stock": list(_INVENTORY_LIKE | _FULFILLMENT_PARENT_LIKE),
        "coupon": ["coupon", "voucher", "promo", "benefit", "discount"],
        "payment": list(_PAYMENT_LIKE),
        "refund": list(_REFUND_LIKE),
        "idempotency": list(_FULFILLMENT_PARENT_LIKE) + ["callback", "idempotency", "retry"],
        "money": list(_PAYMENT_LIKE | _REFUND_LIKE | _FULFILLMENT_PARENT_LIKE) + ["coupon", "voucher", "ledger"],
    }
    tokens = mapping.get(domain, [])
    return any(word in path_l for word in tokens)


def build_invariants(rules: list[dict]) -> list[dict]:
    templates = {
        "permission": "非授权角色不能访问管理或受保护资源",
        "idor": "用户只能访问和变更自己的业务对象",
        "tenant": "租户数据必须按 tenant_id 强隔离",
        "order": "履约单状态创建、取消、支付、退款必须可查询且一致",
        "stock": "容量/库存不能超卖，交易状态变化必须同步库存",
        "coupon": "优惠/权益必须校验归属、有效期、门槛和使用次数",
        "payment": "支付/结算金额必须等于应付金额且状态流转合法",
        "refund": "退款必须基于已支付单据且金额不能超过支付金额",
        "idempotency": "重复请求不能重复创建、扣减容量或入账",
        "money": "金额计算不能为负，不能出现履约单和支付不一致",
    }
    return [{"invariant_id": f"INV_{r['domain'].upper()}", "domain": r["domain"], "statement": templates[r["domain"]], "paths": r["paths"]} for r in rules]


KNOWLEDGE_RISK_ALIASES = {
    "risk_permission_bypass": "permission_bypass",
    "permission_bypass": "permission_bypass",
    "auth_bypass": "auth_bypass",
    "idor": "idor",
    "tenant_isolation": "tenant_isolation",
    "risk_data_consistency": "state_consistency",
    "data_consistency": "state_consistency",
    "state_consistency": "state_consistency",
    "risk_state_transition": "state_flow",
    "state_transition": "state_flow",
    "state_flow": "state_flow",
    "risk_boundary_validation": "boundary_validation",
    "boundary_validation": "boundary_validation",
    "risk_financial_rule": "money_consistency",
    "financial_rule": "money_consistency",
    "money_consistency": "money_consistency",
    "quantity_consistency": "quantity_consistency",
    "stock_consistency": "quantity_consistency",
    "coupon_abuse": "benefit_abuse",
    "benefit_abuse": "benefit_abuse",
    "approval_bypass": "approval_bypass",
    "audit_log_missing": "audit_log_missing",
    "search_scope_leak": "search_scope_leak",
    "report_aggregation_error": "report_aggregation_error",
    "file_upload_validation": "file_upload_validation",
    "notification_wrong_recipient": "notification_wrong_recipient",
    "feature_flag_scope": "feature_flag_scope",
    "callback_trust": "callback_trust",
    "race_condition": "race_condition",
    "idempotency": "idempotency",
}


def normalize_knowledge_risk(value: str) -> str:
    raw = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "").replace("RISK_", "risk_")).strip("_")
    while "__" in raw:
        raw = raw.replace("__", "_")
    return KNOWLEDGE_RISK_ALIASES.get(raw, raw)


_RISK_KEYWORD_MAP: dict[str, tuple[str, ...]] = {
    "permission_bypass": ("admin", "permission", "role", "unauthorized", "forbidden", "权限", "角色", "越权", "未授权"),
    "idor": ("owner", "ownership", "他人", "归属", "本人", "idor"),
    "tenant_isolation": ("tenant", "租户", "组织", "门店", "机构"),
    "money_consistency": ("amount", "total", "payment", "refund", "discount", "金额", "费用", "支付", "退款", "折扣"),
    "quantity_consistency": ("stock", "quantity", "quota", "inventory", "库存", "数量", "额度", "限额"),
    "state_flow": ("status", "state", "transition", "cancel", "approve", "状态", "流转", "取消", "审批"),
    "idempotency": ("idempot", "duplicate", "重复", "幂等"),
    "benefit_abuse": ("coupon", "benefit", "promotion", "优惠", "权益", "券"),
    "boundary_validation": ("required", "invalid", "empty", "range", "missing", "必填", "非法", "为空", "边界"),
    "audit_log_missing": ("audit", "log", "审计", "日志", "留痕"),
}


def risks_from_text(text: str) -> set[str]:
    lower = str(text or "").lower()
    risks: set[str] = set()
    for risk_type, keywords in _RISK_KEYWORD_MAP.items():
        if any(k in lower for k in keywords):
            risks.add(risk_type)
    return risks


def operation_matches_any_knowledge_risk(op: dict, risks: set[str]) -> bool:
    method = op.get("method")
    hints = set(op.get("risk_hints", []))
    text = f"{op.get('path', '')} {op.get('summary', '')} {op.get('resource', '')}".lower()
    if hints & risks:
        return True
    if {"money_consistency", "benefit_abuse"} & risks and any(k in text for k in ["pay", "amount", "total", "refund", "coupon", "discount"]):
        return True
    if {"quantity_consistency"} & risks and any(k in text for k in ["stock", "quantity", "product", "inventory"]):
        return True
    if {"state_flow", "state_consistency"} & risks and any(k in text for k in ["status", "state", "cancel", "approve", "refund", "payment", "order", "状态", "流转", "审批", "取消"]):
        return True
    if {"permission_bypass", "auth_bypass"} & risks and any(k in text for k in ["admin", "permission", "role"]):
        return True
    if {"idor", "tenant_isolation"} & risks and ("{" in op.get("path", "") or "tenant" in text):
        return True
    if {"boundary_validation", "audit_log_missing"} & risks and method in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    if {"idempotency"} & risks and method in {"POST", "PUT", "PATCH", "DELETE"} and "login" not in text:
        return True
    return False


def normalize_discovery_mode(mode: str | None) -> str:
    normalized = (mode or "blind").strip().lower()
    if normalized in {"demo", "demo_mode", "compat"}:
        return "demo"
    return "blind"


def find_oracle_fallback_operation(operations: list[dict], risk_type: str) -> dict | None:
    risk = str(risk_type or "")
    ranked_patterns = {
        "coupon_abuse": ["apply-coupon", "coupon", "benefit", "promotion", "discount"],
        "money_consistency": ["payment", "pay", "refund", "apply-coupon", "amount", "total"],
        "refund_abuse": ["refund"],
        "payment_callback": ["callback", "payment"],
        "stock_consistency": ["stock", "inventory", "product", "order"],
        "state_flow": ["status", "state", "cancel", "approve", "order", "refund"],
        "state_consistency": ["status", "state", "cancel", "approve", "order", "refund", "payment"],
        "idempotency": ["order", "payment", "refund", "callback"],
    }.get(risk, [])
    best: tuple[int, dict] | None = None
    for op in operations:
        text = f"{op.get('path', '')} {op.get('summary', '')} {op.get('resource', '')}".lower()
        score = 0
        for index, pattern in enumerate(ranked_patterns):
            if pattern in text:
                score = max(score, len(ranked_patterns) - index)
        if risk in {business_adaptation_executable_risk(r) for r in op.get("risk_hints", []) or []}:
            score += 5
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, op)
    return best[1] if best else None


def _attach_source_request_contracts(probes: list[dict], business_model: dict) -> list[dict]:
    operations = {
        (
            str(op.get("method") or "").upper(),
            str(op.get("path") or "").split("?", 1)[0],
        ): op
        for op in business_model.get("operations", [])
        if isinstance(op, dict)
    }
    bound: list[dict] = []
    for probe_item in probes:
        item = dict(probe_item)
        key = (
            str(item.get("method") or "").upper(),
            str(item.get("path") or "").split("?", 1)[0],
        )
        operation = operations.get(key) or {}
        example = operation.get("request_example")
        schema = operation.get("request_schema")
        if isinstance(example, dict) and example:
            item["request_example"] = dict(example)
            item["request_contract_source"] = "openapi_documented_example"
        if isinstance(schema, dict) and schema:
            item["request_schema"] = dict(schema)
        bound.append(item)
    return bound


def generate_defect_probes(invariants: list[dict], business_model: dict | None = None, discovery_mode: str = "blind") -> list[dict]:
    model = business_model or {}
    mode = normalize_discovery_mode(discovery_mode)
    policy_profile = normalize_probe_policy_profile(os.environ.get("PROBE_POLICY_PROFILE"), mode)
    generic = generate_generic_defect_probes(model)
    pattern_library = generate_high_value_pattern_probes(model)
    business_knowledge = generate_business_knowledge_probes(model)
    business_adaptation = generate_business_adaptation_probes(model)
    high_value_memory = generate_high_value_memory_probes(model)
    risk_learning_profile = generate_risk_learning_profile_probes(model)
    high_value_attack_plan = generate_high_value_attack_plan_probes(model)
    capability_gap = generate_capability_gap_probes(model, [*business_knowledge, *business_adaptation, *high_value_memory, *risk_learning_profile, *high_value_attack_plan])
    oracle_gap = generate_oracle_gap_probes(model, [*business_knowledge, *business_adaptation, *high_value_memory, *risk_learning_profile, *high_value_attack_plan, *capability_gap])
    feedback_learning = generate_feedback_learning_probes(model)
    adaptive_policy = generate_adaptive_policy_probes(model)
    feedback_adjusted = generate_feedback_adjusted_policy_probes(model)
    journeys = generate_journey_defect_probes(model)
    combined = [*generic, *pattern_library, *business_knowledge, *business_adaptation, *high_value_memory, *risk_learning_profile, *high_value_attack_plan, *capability_gap, *oracle_gap, *feedback_learning, *adaptive_policy, *feedback_adjusted, *journeys]
    combined = _attach_source_request_contracts(combined, model)
    combined = upgrade_high_value_oracle_probes(combined, model)
    combined = filter_probes_by_policy(combined, policy_profile, mode)
    seen = set()
    probes = []
    for item in combined:
        key = item["probe_id"]
        if key in seen:
            continue
        seen.add(key)
        probes.append(item)
    return enrich_probe_business_context(apply_probe_budget_policy(probes), model)


def inferred_business_context_for_probe(item: dict, business_model: dict) -> dict:
    from ._reporting import business_risk_domain_for  # lazy
    from ._runner import business_object_for_api, operation_for_method  # lazy
    api_template = str(item.get("api_template") or f"{item.get('method', '')} {item.get('path', '')}".strip())
    operations = business_model.get("operations", []) or []
    op = find_operation_by_api_template(operations, api_template)
    if not op and item.get("path"):
        op = find_operation_by_api_template(operations, f"{item.get('method')} {str(item.get('path')).split('?')[0]}")
    risk = str(item.get("risk_type") or "business_risk")
    domain, domain_label = business_risk_domain_for(risk)
    module = str((op or {}).get("resource") or business_object_for_api(api_template) or "business")
    operation = operation_for_method(str(item.get("method") or (op or {}).get("method") or ""), str(item.get("path") or (op or {}).get("path") or api_template))
    expected = knowledge_expected_statement(risk, op or {"resource": module, "method": item.get("method"), "path": item.get("path")})
    return {
        "knowledge_source": "auto_semantic_business_context",
        "industry": business_model.get("industry") or "generic_enterprise_software",
        "module": module,
        "domains": [module, domain],
        "risk_domain": domain,
        "risk_domain_label": domain_label,
        "business_object": business_object_for_api(api_template),
        "operation": operation,
        "business_rule": expected,
        "why_high_value": risk_business_impact_statement(risk),
        "api_template": api_template,
        "source_probe": item.get("source"),
    }


def risk_business_impact_statement(risk_type: str) -> str:
    risk = str(risk_type or "")
    mapping = {
        "permission_bypass": "权限绕过会导致非授权用户访问或修改关键业务数据。",
        "auth_bypass": "未登录访问会绕过身份边界，影响所有受保护业务流程。",
        "idor": "对象级越权会导致跨用户读取或操作订单、资产和业务对象。",
        "tenant_isolation": "跨租户隔离失败会造成企业客户数据泄露和合规风险。",
        "money_consistency": "金额不一致会直接造成资损、错账、退款和对账失败。",
        "stock_consistency": "库存容量不一致会造成超卖、额度透支和履约失败。",
        "state_flow": "非法状态流转会破坏订单、支付、审批等核心业务生命周期。",
        "state_consistency": "动作后状态不一致会导致前后台、异步任务和下游系统判断错误。",
        "coupon_abuse": "权益优惠滥用会造成重复抵扣、超额优惠和营销资损。",
        "idempotency": "幂等缺失会导致重复下单、重复扣款、重复退款或重复回调处理。",
        "refund_abuse": "退款滥用会造成超额退款、重复退款和跨单退款。",
        "payment_callback": "支付回调可信校验缺失会造成伪造支付、重复入账或状态污染。",
    }
    return mapping.get(risk, "该风险会破坏 PRD/OpenAPI 中隐含的业务规则和数据一致性。")


def enrich_probe_business_context(probes: list[dict], business_model: dict) -> list[dict]:
    enriched = []
    eligible_sources = {
        "generic_auto",
        "pattern_library",
        "feedback_learning",
        "feedback_adjusted",
        "adaptive_policy",
        "rag_enhanced",
        "high_value_memory",
        "business_knowledge",
        "business_adaptation_layer",
        "risk_learning_profile",
        "high_value_attack_plan",
        "capability_gap",
        "oracle_gap",
    }
    for item in probes:
        if item.get("business_context") or item.get("source") not in eligible_sources:
            enriched.append(item)
            continue
        next_item = dict(item)
        next_item["business_context"] = inferred_business_context_for_probe(next_item, business_model)
        evidence = list(next_item.get("evidence_required") or [])
        if "business_context" not in evidence:
            next_item["evidence_required"] = ["business_context", *evidence]
        enriched.append(next_item)
    return enriched


def upgrade_high_value_oracle_probes(probes: list[dict], business_model: dict) -> list[dict]:
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    eligible_sources = {
        "business_knowledge",
        "business_adaptation_layer",
        "risk_learning_profile",
        "high_value_attack_plan",
        "capability_gap",
        "oracle_gap",
        "high_value_memory",
        "pattern_library",
        "feedback_learning",
        "adaptive_policy",
        "feedback_adjusted",
        "rag_enhanced",
    }
    upgraded: list[dict] = []
    for item in probes:
        risk = str(item.get("risk_type") or "")
        if item.get("cross_step_oracle") or risk not in ORACLE_REQUIRED_RISKS or item.get("source") not in eligible_sources:
            upgraded.append(item)
            continue
        op = find_operation_by_api_template(operations, item.get("api_template") or f"{item.get('method')} {item.get('path')}")
        if not op:
            upgraded.append(item)
            continue
        steps = build_business_knowledge_steps(risk, op, operations)
        fallback_op = None
        if not steps:
            fallback_op = find_oracle_fallback_operation(operations, risk)
            if fallback_op:
                steps = build_business_knowledge_steps(risk, fallback_op, operations)
        if not steps:
            upgraded.append(item)
            continue
        oracle_op = fallback_op or op
        next_item = dict(item)
        next_item["steps"] = steps
        next_item["probe_type"] = f"{item.get('probe_type', 'probe')}_journey"
        if fallback_op:
            next_item["method"] = oracle_op.get("method")
            next_item["path"] = concrete_path(oracle_op.get("path", ""))
            next_item["api_template"] = operation_api_template(oracle_op)
            next_item["expected_status"] = expected_status_for_knowledge_risk(risk, oracle_op.get("method", "GET"))
        next_item["cross_step_oracle"] = build_cross_step_oracle(risk, "high_value_oracle_upgrade")
        next_item["evidence_required"] = ["actor_role", "step_requests", "step_responses", "cross_step_assertion"]
        next_item["oracle_upgrade_context"] = {
            "source": item.get("source"),
            "reason": "高价值探针自动升级为跨步骤业务 Oracle，避免只依赖单接口状态码",
            "api_template": next_item.get("api_template"),
            "original_api_template": item.get("api_template"),
            "used_semantic_fallback": bool(fallback_op),
            "risk_type": risk,
        }
        upgraded.append(next_item)
    return upgraded


def apply_probe_budget_policy(probes: list[dict]) -> list[dict]:
    """Optionally reduce probes by a learned ROI budget policy.

    This is Phase10 governance: it is driven by workspace/output evaluator
    feedback, not by private ground truth or enabled bug sets. It is disabled by
    default and only activates when PROBE_EXECUTION_BUDGET or
    PROBE_BUDGET_POLICY_PATH is provided.
    """
    raw_budget = os.environ.get("PROBE_EXECUTION_BUDGET", "").strip()
    raw_policy_path = os.environ.get("PROBE_BUDGET_POLICY_PATH", "").strip()
    policy_path = Path(raw_policy_path or "platform_workspace/enterprise_shop/defect_discovery/budgeted_probe_policy.json")
    budget = 0
    try:
        budget = int(raw_budget) if raw_budget else 0
    except Exception:
        budget = 0
    if not raw_budget and not raw_policy_path:
        return probes
    policy = {}
    if policy_path.exists():
        try:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
        except Exception:
            policy = {}
    selected_ids = set(str(x) for x in policy.get("selected_probe_ids", []) if x)
    scores = {str(k): float(v) for k, v in (policy.get("score_by_probe_id", {}) or {}).items()}
    blocked_sources = set(policy.get("blocked_sources", []))
    filtered = [p for p in probes if p.get("source") not in blocked_sources]
    if selected_ids:
        selected = [p for p in filtered if str(p.get("probe_id")) in selected_ids]
        # Include mandatory P0 pattern probes if the policy was generated before
        # a new endpoint appeared, but still respect the top-level budget below.
        mandatory = [p for p in filtered if p.get("severity") in {"P0", "P1"} and p.get("source") in {"pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "feedback_adjusted", "rag_enhanced"}]
        by_id = {p["probe_id"]: p for p in [*selected, *mandatory]}
        filtered = list(by_id.values())
    if budget <= 0:
        budget = int(policy.get("budget") or 0)
    if budget <= 0:
        return filtered

    def rank(p: dict) -> tuple:
        pid = str(p.get("probe_id"))
        source_rank = {"business_knowledge": 13, "business_adaptation_layer": 12, "risk_learning_profile": 11, "high_value_attack_plan": 10, "capability_gap": 9, "oracle_gap": 8, "high_value_memory": 7, "pattern_library": 6, "feedback_adjusted": 5, "feedback_learning": 4, "adaptive_policy": 3, "rag_enhanced": 3, "journey_auto": 1, "generic_auto": 0}.get(p.get("source"), 0)
        severity_rank = {"P0": 3, "P1": 2, "P2": 1}.get(p.get("severity"), 0)
        return (scores.get(pid, 0.0), severity_rank, source_rank, pid)

    return sorted(filtered, key=rank, reverse=True)[:budget]


def generate_high_value_pattern_probes(business_model: dict) -> list[dict]:
    """Generate blind-mode probes from public OpenAPI/PRD semantics.

    Patterns activate only when matching endpoints exist. Paths and titles are
    derived from the real operations, never from a benchmark endpoint map.
    """
    operations = [op for op in (business_model.get("operations") or []) if isinstance(op, dict)]
    probes: list[dict] = []
    seen: set[str] = set()

    def add(
        probe_id: str,
        title: str,
        probe_type: str,
        risk_type: str,
        severity: str,
        actor: str,
        method: str,
        path: str,
        expected_status: int,
        api_template: str | None = None,
    ) -> None:
        if probe_id in seen:
            return
        seen.add(probe_id)
        probes.append(
            probe(
                probe_id,
                title,
                probe_type,
                risk_type,
                severity,
                actor,
                method,
                path,
                expected_status,
                api_template or f"{method} {str(path).split('?')[0]}",
                source="pattern_library",
            )
        )

    def _slug(path: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", str(path or "").strip("/")).strip("_").upper()
        return (cleaned or "RESOURCE")[:48]

    def _text(op: dict) -> str:
        return f"{op.get('path') or ''} {op.get('summary') or ''} {op.get('resource') or ''}".lower()

    login_ops = [
        op for op in operations
        if str(op.get("method") or "").upper() == "POST"
        and any(tok in str(op.get("path") or "").lower() for tok in ("/login", "/auth/login", "/signin", "/sign-in"))
    ]
    for op in login_ops[:1]:
        path = str(op.get("path"))
        pid = f"PATTERN_LOCKED_ACCOUNT_LOGIN_{_slug(path)}"
        add(pid, "锁定账号不得登录", "account_state_probe", "locked_account_bypass", "P1", "locked_user", "POST", path, 403, f"POST {path}")

    for op in operations:
        method = str(op.get("method") or "").upper()
        path = str(op.get("path") or "")
        if not path or path in {"/reset", "/health", "/openapi.json"}:
            continue
        text = _text(op)
        template = f"{method} {path}"
        slug = _slug(path)
        resource = str(op.get("resource") or path.strip("/").split("/")[0] or "resource")
        is_admin = any(tok in path.lower() for tok in ("/admin/", "/manage/", "/manager/", "/console/"))
        has_path_param = "{" in path and "}" in path
        is_collection_post = method == "POST" and not has_path_param
        is_terminal_action = method in {"POST", "PUT", "PATCH"} and any(
            tok in path.lower() for tok in ("/cancel", "/close", "/void", "/revoke", "/reject", "/withdraw")
        )
        is_benefit = method in {"POST", "PUT", "PATCH"} and any(
            tok in text for tok in ("coupon", "voucher", "promo", "promotion", "benefit", "discount", "补贴", "优惠", "券")
        )
        is_payment = method == "POST" and any(tok in text for tok in ("payment", "pay", "settlement", "charge", "支付", "结算"))
        is_callback = is_payment and any(tok in text for tok in ("callback", "webhook", "notify", "回调", "通知"))
        is_refund = method == "POST" and any(tok in text for tok in ("refund", "chargeback", "退款"))
        is_capacity = is_collection_post and any(
            tok in text
            for tok in (
                "order", "booking", "reservation", "enrollment", "appointment", "inventory",
                "stock", "seat", "capacity", "订单", "预约", "选课", "库存", "名额",
            )
        )
        is_tenant_surface = "tenant" in path.lower() or "tenant" in text

        if is_admin and method == "GET":
            pid_user = f"PATTERN_ADMIN_READ_FORBIDDEN_TO_USER_{slug}"
            pid_anon = f"PATTERN_ADMIN_READ_FORBIDDEN_TO_ANON_{slug}"
            add(pid_user, f"普通用户不得读取管理端资源 {path}", "permission_probe", "permission_bypass", "P0", "normal_user", method, path, 403, template)
            add(pid_anon, f"未登录用户不得读取管理端资源 {path}", "auth_probe", "auth_bypass", "P0", "anonymous", method, path, 401, template)
        if is_admin and method in {"POST", "PUT", "PATCH", "DELETE"}:
            pid_user = f"PATTERN_ADMIN_WRITE_FORBIDDEN_TO_USER_{slug}"
            pid_anon = f"PATTERN_ADMIN_WRITE_FORBIDDEN_TO_ANON_{slug}"
            add(pid_user, f"普通用户不得修改管理端资源 {path}", "permission_probe", "privilege_escalation", "P0", "normal_user", method, path, 403, template)
            add(pid_anon, f"未登录用户不得修改管理端资源 {path}", "auth_probe", "auth_bypass", "P0", "anonymous", method, path, 401, template)

        if has_path_param and method == "GET" and not is_admin:
            pid = f"PATTERN_IDOR_READ_{slug}"
            add(pid, f"用户不得查看他人{resource}", "idor_probe", "idor", "P0", "normal_user", method, path, 403, template)

        if is_terminal_action and not is_admin:
            pid_idor = f"PATTERN_IDOR_ACTION_{slug}"
            pid_state = f"PATTERN_STATE_ACTION_{slug}"
            add(pid_idor, f"用户不得越权执行 {path}", "idor_probe", "idor", "P1", "normal_user", method, path, 403, template)
            add(pid_state, f"终止/取消后状态必须一致 {path}", "state_transition_probe", "state_flow", "P1", "normal_user", method, path, 200, template)

        if is_tenant_surface and method == "GET":
            tenant_path = path if "tenant_id=" in path else (f"{path}{'&' if '?' in path else '?'}tenant_id=tenant_b")
            pid = f"PATTERN_TENANT_ISOLATION_{slug}"
            add(pid, f"租户用户不得读取其他租户数据 {path}", "tenant_probe", "tenant_isolation", "P0", "normal_user", method, tenant_path, 403, template)

        if is_capacity:
            pid_over = f"PATTERN_CAPACITY_OVERSELL_{slug}"
            pid_deduct = f"PATTERN_CAPACITY_DEDUCT_{slug}"
            pid_read = f"PATTERN_CREATE_READ_{slug}"
            pid_idem = f"PATTERN_CREATE_IDEMPOTENCY_{slug}"
            pid_tamper = f"PATTERN_CREATE_OWNER_TAMPER_{slug}"
            add(pid_over, f"容量/库存不足时不能创建 {path}", "stock_probe", "stock_consistency", "P0", "normal_user", method, path, 409, template)
            add(pid_deduct, f"创建成功后容量/库存必须扣减 {path}", "stock_probe", "stock_consistency", "P1", "normal_user", method, path, 200, template)
            add(pid_read, f"创建后必须可查询 {path}", "state_consistency_probe", "state_consistency", "P1", "normal_user", method, path, 200, template)
            add(pid_idem, f"同一幂等键不能重复创建 {path}", "idempotency_probe", "idempotency", "P1", "normal_user", method, path, 200, template)
            add(pid_tamper, f"创建时不能篡改租户或归属 {path}", "idor_probe", "idor", "P1", "normal_user", method, path, 403, template)

        if is_benefit:
            add(f"PATTERN_BENEFIT_REUSE_{slug}", "同一权益/优惠不能重复抵扣", "coupon_probe", "coupon_abuse", "P1", "normal_user", method, path, 409, template)
            add(f"PATTERN_BENEFIT_EXPIRED_{slug}", "过期权益/优惠不能使用", "coupon_probe", "coupon_abuse", "P1", "normal_user", method, path, 400, template)
            add(f"PATTERN_BENEFIT_THRESHOLD_{slug}", "不满足门槛不能使用权益/优惠", "coupon_probe", "coupon_abuse", "P1", "normal_user", method, path, 400, template)
            add(f"PATTERN_BENEFIT_OTHER_USER_{slug}", "用户不能使用他人权益/优惠", "coupon_probe", "coupon_abuse", "P0", "normal_user", method, path, 403, template)
            add(f"PATTERN_BENEFIT_OVER_DISCOUNT_{slug}", "优惠金额不能超过应付金额", "money_probe", "money_consistency", "P0", "normal_user", method, path, 400, template)

        if is_payment and not is_callback:
            add(f"PATTERN_PAYMENT_AMOUNT_MATCH_{slug}", "支付/结算金额必须匹配应付金额", "payment_probe", "money_consistency", "P0", "normal_user", method, path, 409, template)
            add(f"PATTERN_PAYMENT_STATUS_UPDATE_{slug}", "支付成功后业务状态必须更新", "payment_probe", "state_consistency", "P1", "normal_user", method, path, 200, template)
            add(f"PATTERN_PAYMENT_TERMINAL_STATE_{slug}", "已终止业务单不能再支付/结算", "payment_probe", "state_flow", "P0", "normal_user", method, path, 409, template)

        if is_callback:
            add(f"PATTERN_CALLBACK_IDEMPOTENT_{slug}", "支付/结算回调必须幂等", "payment_callback_probe", "idempotency", "P1", "system", method, path, 200, template)
            add(f"PATTERN_CALLBACK_AMOUNT_{slug}", "回调金额必须匹配应付金额", "payment_callback_probe", "money_consistency", "P0", "system", method, path, 409, template)
            add(f"PATTERN_CALLBACK_TERMINAL_STATE_{slug}", "已终止业务单不能被回调改为已支付", "payment_callback_probe", "state_flow", "P0", "system", method, path, 409, template)

        if is_refund:
            add(f"PATTERN_REFUND_DUPLICATE_{slug}", "同一业务单不能重复退款", "refund_probe", "refund_abuse", "P1", "normal_user", method, path, 409, template)
            add(f"PATTERN_REFUND_UNPAID_{slug}", "未支付业务单不能退款", "refund_probe", "refund_abuse", "P1", "normal_user", method, path, 409, template)
            add(f"PATTERN_REFUND_OVER_AMOUNT_{slug}", "退款金额不能超过已支付金额", "refund_probe", "money_consistency", "P0", "normal_user", method, path, 409, template)
            add(f"PATTERN_REFUND_STATE_{slug}", "退款后状态与库存/额度必须一致", "refund_probe", "state_consistency", "P1", "normal_user", method, path, 200, template)

    return probes


def generate_business_knowledge_probes(business_model: dict) -> list[dict]:
    knowledge = business_model.get("business_knowledge_model") or {}
    if not knowledge:
        return []
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    if not operations:
        return []
    risk_items = collect_knowledge_risk_items(knowledge)
    module_by_domain = build_module_context_by_domain(knowledge)
    probes: list[dict] = []
    seq = 1
    for op in operations:
        op_risks = set(op.get("risk_hints", [])) | risks_from_text(f"{op.get('path')} {op.get('summary')} {op.get('resource')}")
        matched = [item for item in risk_items if item["risk_type"] in op_risks or operation_matches_any_knowledge_risk(op, {item["risk_type"]})]
        if not matched:
            continue
        for item in matched[:4]:
            if not risk_operation_compatible(item["risk_type"], op):
                continue
            if op["method"] == "GET" and item["risk_type"] in {"idempotency", "boundary_validation"}:
                continue
            probe_item = probe(
                f"BK_{item['risk_type'].upper()}_{seq:03d}",
                knowledge_probe_title(item["risk_type"], op, item),
                "business_knowledge_probe",
                executable_risk_type(item["risk_type"]),
                knowledge_probe_severity(item["risk_type"], item.get("priority")),
                knowledge_probe_actor(item["risk_type"]),
                op["method"],
                concrete_path(op["path"]),
                expected_status_for_knowledge_risk(item["risk_type"], op["method"]),
                f"{op['method']} {op['path']}",
                source="business_knowledge",
            )
            domains = set(item.get("domains", []))
            domains.add(op.get("resource", ""))
            probe_item["business_context"] = {
                "knowledge_source": knowledge.get("_source_path", ""),
                "module": choose_module_for_operation(op, module_by_domain),
                "rule_ids": item.get("rule_ids", [])[:5],
                "risk_ids": item.get("risk_ids", [])[:5],
                "scenario_ids": item.get("scenario_ids", [])[:5],
                "evidence": item.get("evidence", [])[:5],
                "domains": sorted(x for x in domains if x),
                "why_high_value": why_high_value(item["risk_type"]),
            }
            probe_item["expected"] = knowledge_expected_statement(item["risk_type"], op)
            probe_item["bug_signal"] = knowledge_bug_signal(item["risk_type"])
            knowledge_steps = build_business_knowledge_steps(item["risk_type"], op, operations)
            if knowledge_steps:
                probe_item["steps"] = knowledge_steps
                probe_item["probe_type"] = "business_knowledge_journey_probe"
                probe_item["evidence_required"] = ["business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
            probes.append(probe_item)
            seq += 1
            if len(probes) >= 96:
                return probes
    return probes


def parse_api_template(api: str) -> tuple[str, str]:
    parts = str(api or "").strip().split(" ", 1)
    if len(parts) == 2 and parts[0].upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return parts[0].upper(), parts[1].strip()
    return "", str(api or "").strip()


def find_operation_by_api_template(operations: list[dict], api_template: str) -> dict | None:
    method, path = parse_api_template(api_template)
    if not method or not path:
        return None
    for op in operations:
        if op.get("method") == method and op.get("path") == path:
            return op
    for op in operations:
        if op.get("method") == method and concrete_path(op.get("path", "")) == concrete_path(path):
            return op
    return None


def operation_api_template(op: dict) -> str:
    return f"{op.get('method')} {op.get('path')}"


def memory_risk_aliases(risk_type: str) -> set[str]:
    return {
        "stock_consistency": {"stock_consistency", "quantity_consistency", "inventory_consistency", "quota_limit"},
        "coupon_abuse": {"coupon_abuse", "benefit_abuse", "money_consistency"},
        "refund_abuse": {"refund_abuse", "money_consistency", "state_flow", "state_consistency"},
        "payment_callback": {"payment_callback", "money_consistency", "idempotency", "callback_trust"},
        "state_consistency": {"state_consistency", "state_flow"},
    }.get(risk_type, {risk_type})


def related_resources_for_memory_pattern(business_model: dict, pattern: dict) -> set[str]:
    base = str(pattern.get("business_object") or "").lower()
    related = {base} if base else set()
    for edge in business_model.get("semantic_graph", {}).get("edges", []) or []:
        src = str(edge.get("from") or "").lower()
        dst = str(edge.get("to") or "").lower()
        if src == base and dst:
            related.add(dst)
        if dst == base and src:
            related.add(src)
    for lineage in business_model.get("data_lineage", []) or []:
        producer = str(lineage.get("producer") or "")
        consumers = [str(x) for x in lineage.get("consumers", []) or []]
        if base and (base == str(lineage.get("resource") or "").lower() or base in producer.lower() or any(base in c.lower() for c in consumers)):
            related.add(str(lineage.get("resource") or "").lower())
            for api in consumers:
                _, path = parse_api_template(api)
                if path:
                    related.add(resource_name(path).lower())
    return {item for item in related if item}


def score_memory_related_operation(op: dict, pattern: dict, related_resources: set[str]) -> int:
    risk = str(pattern.get("risk_type") or "")
    aliases = memory_risk_aliases(risk)
    op_risks = set(op.get("risk_hints", []))
    text = f"{op.get('path', '')} {op.get('summary', '')} {op.get('resource', '')}".lower()
    score = 0
    if str(op.get("resource") or "").lower() in related_resources:
        score += 4
    if op_risks & aliases:
        score += 5
    if operation_matches_any_knowledge_risk(op, aliases):
        score += 3
    if risk in {"money_consistency", "refund_abuse", "payment_callback"} and any(k in text for k in ["payment", "pay", "refund", "coupon", "amount", "total"]):
        score += 3
    if risk in {"state_flow", "state_consistency"} and any(k in text for k in ["order", "status", "state", "cancel", "payment", "refund"]):
        score += 3
    if risk in {"idor", "permission_bypass", "tenant_isolation", "auth_bypass"} and any(k in text for k in ["admin", "tenant", "{", "owner", "permission"]):
        score += 3
    if risk in {"stock_consistency", "quantity_consistency"} and any(k in text for k in ["stock", "quantity", "product", "inventory", "order"]):
        score += 3
    if op.get("method") in {"POST", "PUT", "PATCH", "DELETE"}:
        score += 1
    return score


def related_operations_for_memory_pattern(business_model: dict, operations: list[dict], pattern: dict, exact_op: dict | None) -> list[dict]:
    related_resources = related_resources_for_memory_pattern(business_model, pattern)
    exact_template = operation_api_template(exact_op) if exact_op else ""
    ranked: list[tuple[int, str, dict]] = []
    for op in operations:
        template = operation_api_template(op)
        if template == exact_template:
            continue
        score = score_memory_related_operation(op, pattern, related_resources)
        if score >= 5:
            ranked.append((score, template, op))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [op for _, _, op in ranked[:3]]


def generate_high_value_memory_probes(business_model: dict) -> list[dict]:
    memory = business_model.get("high_value_pattern_memory") or {}
    patterns = memory.get("top_patterns") or []
    if not patterns:
        return []
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    probes: list[dict] = []
    seq = 1
    seen: set[tuple[str, str, str]] = set()
    for pattern in patterns[:24]:
        api_template = pattern.get("api_template") or pattern.get("affected_api") or ""
        exact_op = find_operation_by_api_template(operations, api_template)
        candidate_ops = ([exact_op] if exact_op else []) + related_operations_for_memory_pattern(business_model, operations, pattern, exact_op)
        if not candidate_ops:
            continue
        risk_type = executable_risk_type(str(pattern.get("risk_type") or "boundary_validation"))
        for idx, op in enumerate(candidate_ops):
            variant = "exact_replay" if idx == 0 and exact_op and operation_api_template(op) == operation_api_template(exact_op) else "semantic_expansion"
            key = (str(pattern.get("pattern_id")), operation_api_template(op), risk_type)
            if key in seen:
                continue
            seen.add(key)
            probe_item = probe(
                f"HVM_{risk_type.upper()}_{seq:03d}",
                f"{'历史高价值缺陷模式复测' if variant == 'exact_replay' else '历史高价值缺陷模式扩散'}：{pattern.get('title') or risk_type}",
                "high_value_memory_probe",
                risk_type,
                "P0" if pattern.get("value_tier") == "S" else "P1",
                knowledge_probe_actor(risk_type),
                op["method"],
                concrete_path(op["path"]),
                expected_status_for_knowledge_risk(risk_type, op["method"]),
                f"{op['method']} {op['path']}",
                source="high_value_memory",
            )
            reasons = [str(x) for x in pattern.get("reasons", []) if x]
            probe_item["memory_context"] = {
                "pattern_id": pattern.get("pattern_id"),
                "memory_variant": variant,
                "previous_api": api_template,
                "expanded_api": f"{op['method']} {op['path']}",
                "previous_value_tier": pattern.get("value_tier"),
                "previous_bug_value_score": pattern.get("bug_value_score"),
                "previous_probe_source": pattern.get("probe_source"),
                "reasons": reasons[:5],
            }
            probe_item["business_context"] = {
                "knowledge_source": memory.get("source", "discovered_high_value_findings"),
                "module": pattern.get("business_object") or op.get("resource") or "历史高价值缺陷模式",
                "domains": sorted(set(x for x in [pattern.get("business_object"), op.get("resource")] if x)),
                "why_high_value": ("历史 S/A 级缺陷模式复测：" if variant == "exact_replay" else "历史 S/A 级缺陷模式语义扩散：") + ("；".join(reasons[:3]) if reasons else str(pattern.get("risk_type") or "")),
            }
            probe_item["expected"] = f"历史高价值风险 {risk_type} 不应在 {op['method']} {op['path']} 或相邻业务链路再次出现"
            probe_item["bug_signal"] = "历史高价值缺陷模式再次命中" if variant == "exact_replay" else "历史高价值缺陷模式在相邻业务链路扩散命中"
            memory_steps = build_business_knowledge_steps(risk_type, op)
            if memory_steps:
                probe_item["steps"] = memory_steps
                probe_item["probe_type"] = "high_value_memory_journey_probe"
                probe_item["cross_step_oracle"] = build_cross_step_oracle(risk_type, variant)
                probe_item["evidence_required"] = ["memory_context", "business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
            probes.append(probe_item)
            seq += 1
            if len(probes) >= 72:
                return probes
    return probes


def build_cross_step_oracle(risk_type: str, variant: str = "exact_replay") -> dict:
    assertions = {
        "money_consistency": ["reject_mismatched_amount", "paid_amount_must_match_order_total", "refund_amount_must_not_exceed_paid_amount"],
        "stock_consistency": ["reject_quantity_boundary", "stock_must_not_go_negative", "stock_delta_must_match_order_quantity"],
        "state_flow": ["terminal_state_must_not_be_reopened", "cancelled_order_must_not_be_paid", "state_action_must_persist"],
        "state_consistency": ["state_action_must_persist", "query_state_must_match_action_result"],
        "idempotency": ["duplicate_submit_must_not_create_new_resource", "duplicate_callback_must_not_double_count_amount"],
        "coupon_abuse": ["benefit_amount_must_not_exceed_payable_amount", "duplicate_benefit_must_be_rejected"],
        "refund_abuse": ["refund_amount_must_not_exceed_paid_amount", "duplicate_refund_must_be_rejected"],
        "tenant_isolation": ["cross_scope_read_must_be_rejected", "cross_scope_write_must_be_rejected"],
        "idor": ["cross_scope_read_must_be_rejected", "cross_scope_write_must_be_rejected"],
    }.get(risk_type, ["business_invariant_must_hold"])
    return {
        "version": "cross_step_oracle_v1",
        "risk_type": risk_type,
        "variant": variant,
        "assertions": assertions,
    }


def profile_item_names(items: list[dict]) -> list[str]:
    return [str(item.get("name") or "") for item in items if item.get("name")]


def risk_learning_profile_risk_for_operation(op: dict, priority_risks: list[str]) -> str | None:
    op_risks = set(op.get("risk_hints", []))
    for risk in priority_risks:
        aliases = memory_risk_aliases(executable_risk_type(risk))
        if risk in op_risks or op_risks & aliases or operation_matches_any_knowledge_risk(op, aliases):
            return executable_risk_type(risk)
    return None


def generate_risk_learning_profile_probes(business_model: dict) -> list[dict]:
    profile = business_model.get("risk_learning_profile") or {}
    if not profile:
        return []
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    priority_risks = profile_item_names(profile.get("priority_risks") or [])
    priority_apis = profile_item_names(profile.get("priority_apis") or [])
    priority_modules = set(profile_item_names(profile.get("priority_modules") or []))
    oracle_risks = set(profile_item_names(profile.get("oracle_priority_risks") or []))
    probes: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add_probe(op: dict, risk_type: str, reason: str) -> None:
        key = (operation_api_template(op), risk_type)
        if key in seen:
            return
        seen.add(key)
        seq = len(probes) + 1
        severity = "P0" if risk_type in {"permission_bypass", "auth_bypass", "idor", "tenant_isolation", "money_consistency", "stock_consistency"} else "P1"
        item = probe(
            f"RLP_{risk_type.upper()}_{seq:03d}",
            f"风险学习画像驱动探针：{risk_type} {op['method']} {op['path']}",
            "risk_learning_profile_probe",
            risk_type,
            severity,
            knowledge_probe_actor(risk_type),
            op["method"],
            concrete_path(op["path"]),
            expected_status_for_knowledge_risk(risk_type, op["method"]),
            f"{op['method']} {op['path']}",
            source="risk_learning_profile",
        )
        item["risk_learning_context"] = {
            "profile_version": profile.get("version"),
            "reason": reason,
            "priority_risks": priority_risks[:5],
            "priority_modules": list(priority_modules)[:5],
            "learned_from_findings": profile.get("learned_from_findings"),
            "semantic_expansion_probe_count": profile.get("semantic_expansion_probe_count"),
        }
        item["business_context"] = {
            "knowledge_source": "enterprise_high_value_risk_learning_profile",
            "module": op.get("resource") or "风险学习画像",
            "domains": [op.get("resource") or ""],
            "why_high_value": f"由企业高价值缺陷学习画像推荐：{reason}",
        }
        steps = build_business_knowledge_steps(risk_type, op)
        if steps:
            item["steps"] = steps
            item["probe_type"] = "risk_learning_profile_journey_probe"
            if risk_type in oracle_risks or risk_type in {"money_consistency", "stock_consistency", "state_flow", "state_consistency", "idempotency"}:
                item["cross_step_oracle"] = build_cross_step_oracle(risk_type, "risk_learning_profile")
            item["evidence_required"] = ["risk_learning_context", "business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
        probes.append(item)

    for api in priority_apis[:12]:
        op = find_operation_by_api_template(operations, api)
        if not op:
            continue
        risk = risk_learning_profile_risk_for_operation(op, priority_risks) or (priority_risks[0] if priority_risks else "boundary_validation")
        add_probe(op, executable_risk_type(risk), f"重点接口 {api} 在历史高价值画像中权重靠前")
    for op in operations:
        if len(probes) >= 64:
            break
        risk = risk_learning_profile_risk_for_operation(op, priority_risks)
        if not risk:
            continue
        module = str(op.get("resource") or "")
        if module in priority_modules or operation_api_template(op) in priority_apis or len(probes) < 24:
            add_probe(op, risk, f"重点风险 {risk} 与接口 {op['method']} {op['path']} 匹配")
    return probes


def generate_high_value_attack_plan_probes(business_model: dict) -> list[dict]:
    from ._reporting import safe_pattern_token  # lazy
    plan = business_model.get("high_value_attack_plan") or {}
    focus = plan.get("top_focus") or []
    if not focus:
        return []
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    probes: list[dict] = []
    seen: set[tuple[str, str]] = set()
    total_budget = max(4, min(64, int(plan.get("next_run_probe_budget") or 24)))

    for focus_item in focus[:10]:
        if len(probes) >= total_budget:
            break
        risk = executable_risk_type(str(focus_item.get("risk_type") or ""))
        if risk not in ORACLE_REQUIRED_RISKS:
            continue
        priority = str(focus_item.get("priority") or "P1")
        if priority not in {"P0", "P1"}:
            continue
        per_risk_budget = max(1, min(8, int(focus_item.get("recommended_next_probe_count") or 3)))
        matched = [op for op in operations if risk_operation_compatible(risk, op)]
        matched.sort(key=lambda op: (operation_api_template(op) not in {str(api.get("name") or "") for api in (plan.get("priority_apis") or [])}, op.get("method") not in {"POST", "PUT", "PATCH"}, operation_api_template(op)))
        added = 0
        for op in matched:
            if len(probes) >= total_budget or added >= per_risk_budget:
                break
            template = operation_api_template(op)
            key = (template, risk)
            if key in seen:
                continue
            steps = build_business_knowledge_steps(risk, op)
            if not steps:
                continue
            seen.add(key)
            added += 1
            seq = len(probes) + 1
            item = probe(
                f"HVAP_{safe_pattern_token(risk)}_{seq:03d}",
                f"高价值攻击计划探针 {risk} {template}",
                "high_value_attack_plan_journey_probe",
                risk,
                "P0" if priority == "P0" or risk in {"money_consistency", "stock_consistency", "state_flow", "idor", "tenant_isolation"} else "P1",
                knowledge_probe_actor(risk),
                op["method"],
                concrete_path(op["path"]),
                expected_status_for_knowledge_risk(risk, op["method"]),
                template,
                source="high_value_attack_plan",
            )
            item["steps"] = steps
            item["cross_step_oracle"] = build_cross_step_oracle(risk, "high_value_attack_plan")
            item["attack_plan_context"] = {
                "plan_version": plan.get("version"),
                "priority": priority,
                "priority_score": focus_item.get("priority_score"),
                "gap_types": focus_item.get("gap_types") or [],
                "oracle_coverage_rate": focus_item.get("oracle_coverage_rate"),
                "recommended_next_probe_count": focus_item.get("recommended_next_probe_count"),
            }
            item["business_context"] = {
                "knowledge_source": "high_value_attack_plan",
                "module": op.get("resource") or "attack_plan",
                "domains": [op.get("resource") or ""],
                "why_high_value": f"上一轮攻击计划将 {risk} 识别为 {priority} 风险热点，自动转化为跨步骤 Oracle 探针",
            }
            item["evidence_required"] = ["attack_plan_context", "business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
            probes.append(item)
    return probes


def business_adaptation_executable_risk(risk_type: str) -> str:
    return {
        "payment": "money_consistency",
        "refund": "refund_abuse",
        "state_transition": "state_flow",
        "account_ownership": "idor",
        "ownership_boundary": "idor",
        "privacy_leak": "permission_bypass",
        "limit_bypass": "boundary_validation",
        "amount_limit": "money_consistency",
        "benefit_abuse": "coupon_abuse",
        "coupon_abuse": "coupon_abuse",
        "quote_discount": "money_consistency",
        "contract_state": "state_flow",
        "appointment_conflict": "state_consistency",
        "prescription_rule": "permission_bypass",
        "score_tampering": "permission_bypass",
        "enrollment_capacity": "stock_consistency",
        "tracking_tampering": "state_flow",
        "delivery_state": "state_flow",
        "moderation_bypass": "state_flow",
        "author_ownership": "idor",
        "audit_trace": "audit_log_missing",
    }.get(str(risk_type or ""), str(risk_type or "boundary_validation"))


def generate_business_adaptation_probes(business_model: dict) -> list[dict]:
    profile = business_model.get("business_adaptation_profile") or {}
    if not profile:
        return []
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    op_by_template = {operation_api_template(op): op for op in operations}
    probes: list[dict] = []
    seen: set[tuple[str, str]] = set()
    selected_domains = [str(item.get("domain") or "") for item in profile.get("selected_domains", []) if item.get("domain")]
    risk_matrix = profile.get("adaptive_risk_matrix") or []

    def add(op: dict, raw_risk: str, row: dict, reason: str) -> None:
        risk_type = business_adaptation_executable_risk(raw_risk)
        key = (operation_api_template(op), risk_type)
        if key in seen:
            return
        seen.add(key)
        seq = len(probes) + 1
        item = probe(
            f"BA_{risk_type.upper()}_{seq:03d}",
            f"业务适配画像探针：{row.get('title') or raw_risk} {op['method']} {op['path']}",
            "business_adaptation_probe",
            risk_type,
            str(row.get("severity") or "P1"),
            knowledge_probe_actor(risk_type),
            op["method"],
            concrete_path(op["path"]),
            expected_status_for_knowledge_risk(risk_type, op["method"]),
            f"{op['method']} {op['path']}",
            source="business_adaptation_layer",
        )
        item["business_adaptation_context"] = {
            "profile_phase": profile.get("phase"),
            "business_domains": selected_domains,
            "raw_risk_type": raw_risk,
            "adapted_risk_type": risk_type,
            "reason": reason,
            "expected": row.get("expected"),
            "bug_signal": row.get("bug_signal"),
        }
        item["business_context"] = {
            "knowledge_source": "business_adaptation_profile",
            "module": op.get("resource") or row.get("domain") or "业务适配画像",
            "domains": sorted(set([str(row.get("domain") or ""), *selected_domains, str(op.get("resource") or "")]) - {""}),
            "why_high_value": f"由 PRD/OpenAPI 自动识别业务域 {','.join(selected_domains) or 'auto'} 后生成：{reason}",
        }
        if row.get("expected"):
            item["expected"] = str(row.get("expected"))
        if row.get("bug_signal"):
            item["bug_signal"] = str(row.get("bug_signal"))
        steps = build_business_knowledge_steps(risk_type, op)
        if steps:
            item["steps"] = steps
            item["probe_type"] = "business_adaptation_journey_probe"
            item["cross_step_oracle"] = build_cross_step_oracle(risk_type, "business_adaptation_layer")
            item["evidence_required"] = ["business_adaptation_context", "business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
        probes.append(item)

    for row in risk_matrix[:32]:
        raw_risk = str(row.get("risk_type") or "")
        matched = [str(api) for api in row.get("matched_operations", []) or []]
        for api in matched[:6]:
            op = op_by_template.get(api) or find_operation_by_api_template(operations, api)
            if op:
                add(op, raw_risk, row, f"业务域风险矩阵命中 {raw_risk}")
        if len(probes) >= 96:
            return probes
    endpoint_map = profile.get("endpoint_domain_map") or []
    for item in endpoint_map:
        if len(probes) >= 96:
            break
        op = find_operation_by_api_template(operations, f"{item.get('method')} {item.get('path')}")
        if not op:
            continue
        for raw_risk in item.get("risk_types", [])[:4]:
            row = next((r for r in risk_matrix if r.get("risk_type") == raw_risk), {"risk_type": raw_risk, "severity": "P1", "title": f"{raw_risk} 业务风险"})
            add(op, str(raw_risk), row, f"接口风险映射命中 {raw_risk}")
    return probes


ORACLE_REQUIRED_RISKS = {
    "money_consistency",
    "stock_consistency",
    "state_flow",
    "state_consistency",
    "idempotency",
    "coupon_abuse",
    "refund_abuse",
    "payment_callback",
}


def generate_oracle_gap_probes(business_model: dict, existing_probes: list[dict]) -> list[dict]:
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    covered = {
        (p.get("api_template"), p.get("risk_type"))
        for p in existing_probes
        if p.get("cross_step_oracle") and p.get("api_template") and p.get("risk_type")
    }
    candidates: list[tuple[int, dict, str, str]] = []
    for op in operations:
        template = operation_api_template(op)
        op_risks = {business_adaptation_executable_risk(r) for r in op.get("risk_hints", []) or []}
        text = f"{op.get('path', '')} {op.get('summary', '')} {op.get('resource', '')}".lower()
        if any(k in text for k in ["amount", "payment", "pay", "refund", "coupon", "discount", "price", "金额", "支付", "退款", "优惠"]):
            op_risks.add("money_consistency")
        if any(k in text for k in ["coupon", "voucher", "benefit", "promotion", "discount", "优惠", "权益", "券", "折扣"]):
            op_risks.add("coupon_abuse")
        if any(k in text for k in ["stock", "quantity", "inventory", "product", "库存", "数量"]):
            op_risks.add("stock_consistency")
        if any(k in text for k in ["status", "state", "cancel", "approve", "状态", "取消", "审批"]):
            op_risks.add("state_flow")
        if op.get("method") in {"POST", "PUT", "PATCH", "DELETE"} and "login" not in text:
            op_risks.add("idempotency")
        for risk in sorted(op_risks & ORACLE_REQUIRED_RISKS):
            if (template, risk) in covered:
                continue
            steps = build_business_knowledge_steps(risk, op)
            if not steps:
                continue
            score = 10
            if risk in {"money_consistency", "stock_consistency", "state_flow"}:
                score += 4
            if op.get("method") in {"POST", "PUT", "PATCH"}:
                score += 2
            candidates.append((score, op, risk, template))
    candidates.sort(key=lambda item: (item[0], item[3]), reverse=True)
    probes: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for _, op, risk, template in candidates[:48]:
        key = (template, risk)
        if key in seen:
            continue
        seen.add(key)
        seq = len(probes) + 1
        item = probe(
            f"ORACLE_GAP_{risk.upper()}_{seq:03d}",
            f"跨步骤 Oracle 覆盖缺口探针：{risk} {template}",
            "oracle_gap_probe",
            risk,
            "P0" if risk in {"money_consistency", "stock_consistency"} else "P1",
            knowledge_probe_actor(risk),
            op["method"],
            concrete_path(op["path"]),
            expected_status_for_knowledge_risk(risk, op["method"]),
            template,
            source="oracle_gap",
        )
        item["steps"] = build_business_knowledge_steps(risk, op)
        item["cross_step_oracle"] = build_cross_step_oracle(risk, "oracle_gap")
        item["oracle_gap_context"] = {
            "gap_type": "missing_cross_step_oracle",
            "api_template": template,
            "risk_type": risk,
            "reason": "高价值风险接口缺少跨步骤业务断言覆盖",
        }
        item["business_context"] = {
            "knowledge_source": "oracle_coverage_gap_analysis",
            "module": op.get("resource") or "Oracle覆盖缺口",
            "domains": [op.get("resource") or ""],
            "why_high_value": "高价值风险接口必须具备跨步骤 Oracle，避免只靠状态码漏检业务一致性问题",
        }
        item["evidence_required"] = ["oracle_gap_context", "business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
        probes.append(item)
    assessment = business_model.get("high_value_capability_assessment") or {}
    low_coverage_gaps = [
        item for item in assessment.get("capability_gaps") or []
        if item.get("gap_type") == "oracle_coverage_gap" and item.get("risk_type") in ORACLE_REQUIRED_RISKS
    ]
    if low_coverage_gaps:
        gap_priority = {
            str(item.get("risk_type")): max(0.0, float(item.get("target") or 0.9) - float(item.get("current") or 0))
            for item in low_coverage_gaps
        }
        closure_candidates: list[tuple[float, dict, str, str]] = []
        existing_oracle_count_by_risk: dict[str, int] = {}
        for p in existing_probes:
            if p.get("cross_step_oracle") and p.get("risk_type") in gap_priority:
                risk = str(p.get("risk_type"))
                existing_oracle_count_by_risk[risk] = existing_oracle_count_by_risk.get(risk, 0) + 1
        for op in operations:
            template = operation_api_template(op)
            text = f"{op.get('path', '')} {op.get('summary', '')} {op.get('resource', '')}".lower()
            hinted = {business_adaptation_executable_risk(r) for r in op.get("risk_hints", []) or []}
            for risk, gap_size in gap_priority.items():
                steps = build_business_knowledge_steps(risk, op)
                if not steps:
                    continue
                relevance = 0.0
                if risk in hinted:
                    relevance += 5
                if risk == "money_consistency" and any(k in text for k in ["amount", "payment", "pay", "refund", "coupon", "discount", "price", "金额", "支付", "退款", "优惠"]):
                    relevance += 4
                if risk == "stock_consistency" and any(k in text for k in ["stock", "quantity", "inventory", "product", "库存", "数量"]):
                    relevance += 4
                if risk in {"state_flow", "state_consistency"} and any(k in text for k in ["status", "state", "cancel", "approve", "状态", "取消", "审批"]):
                    relevance += 4
                if risk == "coupon_abuse" and any(k in text for k in ["coupon", "voucher", "benefit", "promotion", "discount", "优惠", "权益", "券"]):
                    relevance += 4
                if risk == "idempotency" and op.get("method") in {"POST", "PUT", "PATCH", "DELETE"}:
                    relevance += 3
                if relevance <= 0:
                    continue
                scarcity = max(0, 8 - existing_oracle_count_by_risk.get(risk, 0))
                closure_candidates.append((gap_size * 20 + relevance + scarcity, op, risk, template))
        closure_candidates.sort(key=lambda item: (item[0], item[3]), reverse=True)
        per_risk_count: dict[str, int] = {}
        closure_seen = set(seen)
        for _, op, risk, template in closure_candidates:
            if len(probes) >= 96:
                break
            if per_risk_count.get(risk, 0) >= 6:
                continue
            key = (template, risk)
            if key in closure_seen:
                continue
            closure_seen.add(key)
            per_risk_count[risk] = per_risk_count.get(risk, 0) + 1
            seq = len(probes) + 1
            gap = next((item for item in low_coverage_gaps if item.get("risk_type") == risk), {})
            item = probe(
                f"ORACLE_GAP_CLOSURE_{risk.upper()}_{seq:03d}",
                f"低覆盖 Oracle 补强探针：{risk} {template}",
                "oracle_gap_closure_probe",
                risk,
                "P0" if risk in {"money_consistency", "stock_consistency", "state_flow", "state_consistency"} else "P1",
                knowledge_probe_actor(risk),
                op["method"],
                concrete_path(op["path"]),
                expected_status_for_knowledge_risk(risk, op["method"]),
                template,
                source="oracle_gap",
            )
            item["steps"] = build_business_knowledge_steps(risk, op)
            item["cross_step_oracle"] = build_cross_step_oracle(risk, "oracle_gap_closure")
            item["oracle_gap_context"] = {
                "gap_type": "low_oracle_coverage",
                "api_template": template,
                "risk_type": risk,
                "current": gap.get("current"),
                "target": gap.get("target", 0.9),
                "source_assessment_version": assessment.get("version"),
                "reason": "上一轮能力评估显示该高价值风险 Oracle 覆盖低于企业级 90% 目标，需要继续增加跨步骤业务断言。",
            }
            item["business_context"] = {
                "knowledge_source": "high_value_capability_assessment",
                "module": op.get("resource") or "Oracle覆盖补强",
                "domains": [op.get("resource") or ""],
                "why_high_value": "基于上一轮低覆盖风险自动生成更深的跨步骤 Oracle 探针，提升真实业务一致性缺陷发现能力。",
            }
            item["evidence_required"] = ["oracle_gap_context", "business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
            probes.append(item)
    return probes


def generate_capability_gap_probes(business_model: dict, existing_probes: list[dict]) -> list[dict]:
    assessment = business_model.get("high_value_capability_assessment") or {}
    gaps = [
        item for item in assessment.get("capability_gaps") or []
        if item.get("gap_type") == "oracle_coverage_gap" and item.get("risk_type") in ORACLE_REQUIRED_RISKS
    ]
    if not gaps:
        return []
    gap_priority = {
        str(item.get("risk_type")): float(item.get("target") or 0.9) - float(item.get("current") or 0)
        for item in gaps
    }
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    candidates: list[tuple[float, dict, str, str]] = []
    for op in operations:
        template = operation_api_template(op)
        text = f"{op.get('path', '')} {op.get('summary', '')} {op.get('resource', '')}".lower()
        hinted = {business_adaptation_executable_risk(r) for r in op.get("risk_hints", []) or []}
        for risk, gap_size in gap_priority.items():
            steps = build_business_knowledge_steps(risk, op)
            if not steps:
                continue
            relevance = 0.0
            if risk in hinted:
                relevance += 5
            if risk == "money_consistency" and any(k in text for k in ["amount", "payment", "pay", "refund", "coupon", "discount", "price", "金额", "支付", "退款", "优惠"]):
                relevance += 4
            if risk == "stock_consistency" and any(k in text for k in ["stock", "quantity", "inventory", "product", "库存", "数量"]):
                relevance += 4
            if risk in {"state_flow", "state_consistency"} and any(k in text for k in ["status", "state", "cancel", "approve", "状态", "取消", "审批"]):
                relevance += 4
            if risk == "coupon_abuse" and any(k in text for k in ["coupon", "voucher", "benefit", "promotion", "discount", "优惠", "权益", "券"]):
                relevance += 4
            if risk == "idempotency" and op.get("method") in {"POST", "PUT", "PATCH", "DELETE"}:
                relevance += 3
            if relevance <= 0:
                continue
            candidates.append((gap_size * 10 + relevance, op, risk, template))
    candidates.sort(key=lambda item: (item[0], item[3]), reverse=True)
    probes: list[dict] = []
    seen: set[tuple[str, str]] = set()
    per_risk_count: dict[str, int] = {}
    for _, op, risk, template in candidates:
        if len(probes) >= 48:
            break
        key = (template, risk)
        if key in seen:
            continue
        if per_risk_count.get(risk, 0) >= 8:
            continue
        seen.add(key)
        per_risk_count[risk] = per_risk_count.get(risk, 0) + 1
        seq = len(probes) + 1
        item = probe(
            f"CAP_GAP_{risk.upper()}_{seq:03d}",
            f"能力短板补强探针：{risk} {template}",
            "capability_gap_oracle_probe",
            risk,
            "P0" if risk in {"money_consistency", "stock_consistency", "state_flow"} else "P1",
            knowledge_probe_actor(risk),
            op["method"],
            concrete_path(op["path"]),
            expected_status_for_knowledge_risk(risk, op["method"]),
            template,
            source="capability_gap",
        )
        item["steps"] = build_business_knowledge_steps(risk, op)
        item["cross_step_oracle"] = build_cross_step_oracle(risk, "capability_gap")
        item["capability_gap_context"] = {
            "gap_type": "oracle_coverage_gap",
            "risk_type": risk,
            "current": next((g.get("current") for g in gaps if g.get("risk_type") == risk), None),
            "target": next((g.get("target") for g in gaps if g.get("risk_type") == risk), 0.9),
            "source_assessment_version": assessment.get("version"),
        }
        item["business_context"] = {
            "knowledge_source": "high_value_capability_assessment",
            "module": op.get("resource") or "capability_gap",
            "domains": [op.get("resource") or ""],
            "why_high_value": "基于上一轮能力评估自动补强低覆盖高价值风险的跨步骤 Oracle。",
        }
        item["evidence_required"] = ["capability_gap_context", "business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
        probes.append(item)
    return probes


def build_business_knowledge_steps(
    risk_type: str,
    op: dict,
    operations: list[dict] | None = None,
) -> list[dict]:
    method = op.get("method")
    path = op.get("path", "")
    concrete = concrete_path(path)
    text = f"{path} {op.get('summary', '')} {op.get('resource', '')}".lower()
    resource = str(op.get("resource") or "resource")
    ops_ctx = operations if isinstance(operations, list) else None
    if ops_ctx is None and isinstance(op.get("_operations_context"), list):
        ops_ctx = op.get("_operations_context")
    read_path = concrete if method == "GET" else read_path_for_resource(resource, operations=ops_ctx)
    create_path = None
    cancel_method = "POST"
    cancel_path = None
    if isinstance(ops_ctx, list):
        for candidate in ops_ctx:
            if str(candidate.get("resource") or "") != resource:
                # Also allow fulfillment parent create/cancel when probing payment-like ops.
                cand_resource = str(candidate.get("resource") or "")
                if not (
                    _resource_in(resource, _PAYMENT_LIKE)
                    and _resource_in(cand_resource, _FULFILLMENT_PARENT_LIKE)
                ):
                    continue
            cand_method = str(candidate.get("method") or "").upper()
            cand_path = str(candidate.get("path") or "")
            cand_op = str(candidate.get("operation") or "")
            if create_path is None and cand_method == "POST" and (
                cand_op in {"create_or_action", ""} or cand_op == "create_or_action"
            ):
                if cand_op in {"create_or_action"} or (
                    cand_op == "" and not any(tok in cand_path.lower() for tok in ("cancel", "pay", "refund", "callback"))
                ):
                    create_path = concrete_path(cand_path)
            if cancel_path is None and (
                cand_op == "state_cancel"
                or any(tok in cand_path.lower() for tok in ("cancel", "void", "close", "terminate"))
            ):
                cancel_method = cand_method or "POST"
                cancel_path = concrete_path(cand_path)
    if risk_type in {"quantity_consistency", "stock_consistency"} and any(
        k in text for k in ["product", "stock", "inventory", "cart", "order", "booking", "seat", "capacity", "material", "预约", "库存", "名额"]
    ):
        action_method = method if method in {"POST", "PUT", "PATCH"} else "POST"
        action_path = concrete if method in {"POST", "PUT", "PATCH"} else path
        if risk_type == "stock_consistency":
            return [
                step("read_stock_before", "GET", read_path, body_hint="read"),
                step("submit_quantity_change", action_method, action_path, body_hint="create"),
                step("read_stock_after", "GET", read_path, body_hint="read"),
                step("attempt_boundary_quantity_change", action_method, action_path, body_hint="quantity"),
                step("read_stock_after_boundary", "GET", read_path, body_hint="read"),
            ]
        return [
            step("read_quantity_before", "GET", read_path, body_hint="read"),
            step("attempt_boundary_quantity_change", action_method, action_path, body_hint="quantity"),
            step("read_quantity_after", "GET", read_path, body_hint="read"),
        ]
    if risk_type in {"coupon_abuse", "benefit_abuse"} and any(
        k in text for k in ["coupon", "voucher", "promo", "benefit", "discount", "优惠", "券"]
    ):
        return [
            step("apply_benefit_first_time", method, concrete, body_hint="coupon_valid"),
            step("apply_benefit_duplicate", method, concrete, body_hint="coupon_valid"),
            step("apply_abusive_benefit", method, concrete, body_hint="coupon_over_discount"),
        ]
    if risk_type == "money_consistency" and any(k in text for k in ["coupon", "voucher", "promo", "benefit", "discount", "优惠", "券"]):
        return [step("apply_abusive_benefit", method, concrete, body_hint="coupon_over_discount")]
    if risk_type == "money_consistency" and any(k in text for k in ["payment", "pay", "settlement", "支付", "结算"]):
        return [
            step("submit_mismatched_payment", method, concrete, body_hint="payment_mismatch"),
            step("read_after_payment", "GET", read_path, body_hint="read"),
        ]
    if risk_type == "money_consistency" and "refund" in text:
        return [
            step("submit_over_refund", method, concrete, body_hint="refund_over"),
            step("read_after_refund", "GET", read_path, body_hint="read"),
        ]
    # Terminal-state mutation: create → cancel/close → attempt payment/settlement (not mall-only).
    if risk_type == "state_flow" and any(k in text for k in ["payment", "pay", "callback", "settlement", "支付", "回调", "结算"]):
        steps = []
        if create_path:
            steps.append(step("seed_fulfillment_resource", "POST", create_path, body_hint="create"))
        if cancel_path:
            steps.append(step("move_to_terminal_state", cancel_method, cancel_path, body_hint="cancel"))
        steps.append(step("attempt_payment_after_terminal_state", method, concrete, body_hint="payment"))
        steps.append(step("read_after_terminal_action", "GET", read_path, body_hint="read"))
        return steps
    if risk_type == "state_consistency" and any(k in text for k in ["payment", "pay", "callback", "settlement", "支付", "回调"]):
        return [
            step("execute_state_sync_action", method, concrete, body_hint="payment"),
            step("read_after_state_sync", "GET", read_path, body_hint="read"),
        ]
    if risk_type == "state_consistency" and "refund" in text:
        return [
            step("execute_refund_state_action", method, concrete, body_hint="refund"),
            step("read_after_refund_state", "GET", read_path, body_hint="read"),
        ]
    if risk_type in {"state_flow", "state_consistency", "approval_bypass"} and method in {"POST", "PUT", "PATCH"}:
        return [
            step("execute_business_state_action", method, concrete, body_hint="state"),
            step("read_business_state_after_action", "GET", read_path, body_hint="read"),
            step("repeat_business_state_action", method, concrete, body_hint="state"),
        ]
    if risk_type in {"state_flow", "state_consistency"} and method == "GET":
        seed = create_path or (path if not has_path_param_like(path) else read_path_for_resource(resource, operations=ops_ctx))
        return [
            step("execute_business_action_before_read", "POST", seed, body_hint="create"),
            step("read_business_state", method, concrete, body_hint="read"),
        ]
    if risk_type == "money_consistency" and method in {"POST", "PUT", "PATCH"}:
        return [
            step("submit_financial_boundary_value", method, concrete, body_hint="payment_mismatch"),
            step("repeat_financial_boundary_value", method, concrete, body_hint="payment_mismatch"),
        ]
    if risk_type == "idempotency" and method in {"POST", "PUT", "PATCH"}:
        return [
            step("first_submit", method, concrete, body_hint="create"),
            step("duplicate_submit", method, concrete, body_hint="create"),
        ]
    return []


def has_path_param_like(path: str) -> bool:
    return "{" in str(path or "") and "}" in str(path or "")


_RISK_COMPAT_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...], bool]] = {
    # (text_keywords, path_keywords, methods, path_excludes, text_or_method)
    # text_or_method=True means match if text keywords OR method matches (not AND)
    "auth_bypass":           (("login", "admin"), (), ("POST", "PUT", "PATCH", "DELETE"), (), True),
    "permission_bypass":     (("admin", "permission", "role", "approve", "export", "import", "管理", "权限", "审批", "导出"), (), (), (), False),
    "idor":                  ((), ("{",), (), ("product_id", "sku_id"), False),
    "tenant_isolation":      (("tenant", "租户", "组织", "org"), (), (), (), False),
    "money_consistency":     (("amount", "price", "total", "pay", "payment", "refund", "coupon", "discount", "金额", "支付", "退款", "优惠", "折扣"), (), (), (), False),
    "quantity_consistency":  (("stock", "quantity", "quota", "inventory", "product", "cart", "库存", "数量", "额度"), (), (), (), False),
    "state_flow":            (("status", "state", "cancel", "approve", "refund", "payment", "order", "状态", "流转", "取消", "审批"), (), (), (), False),
    "state_consistency":     (("status", "state", "cancel", "approve", "refund", "payment", "order", "状态", "流转", "取消", "审批"), (), (), (), False),
    "idempotency":           ((), (), ("POST", "PUT", "PATCH", "DELETE"), ("login",), False),
    "coupon_abuse":          (("coupon", "benefit", "promotion", "discount", "优惠", "权益", "券", "折扣"), (), (), (), False),
    "benefit_abuse":         (("coupon", "benefit", "promotion", "discount", "优惠", "权益", "券", "折扣"), (), (), (), False),
    "boundary_validation":   ((), (), ("POST", "PUT", "PATCH", "DELETE"), ("login",), False),
    "audit_log_missing":     (("admin", "approve", "order", "payment", "refund", "export", "import", "delete", "cancel", "审批", "订单", "支付", "退款", "导出", "导入", "删除", "取消"), (), (), ("login",), False),
}


def risk_operation_compatible(risk_type: str, op: dict) -> bool:
    method = str(op.get("method") or "").upper()
    text = f"{op.get('path', '')} {op.get('summary', '')} {op.get('resource', '')} {op.get('operation', '')}".lower()
    path = str(op.get("path", "")).lower()

    rule = _RISK_COMPAT_RULES.get(risk_type)
    if rule is not None:
        text_kw, path_kw, methods, excludes, text_or_method = rule
        if text_or_method:
            if any(k in text for k in text_kw) or method in methods:
                return True
            return False
        if text_kw and not any(k in text for k in text_kw):
            return False
        if path_kw and not any(k in path for k in path_kw):
            return False
        if methods and method not in methods:
            return False
        if excludes and any(k in path for k in excludes):
            return False
        return True

    return method in {"POST", "PUT", "PATCH", "DELETE"}


def collect_knowledge_risk_items(knowledge: dict) -> list[dict]:
    items: dict[str, dict] = {}

    def ensure(risk_type: str) -> dict:
        risk_type = normalize_knowledge_risk(risk_type)
        return items.setdefault(risk_type, {"risk_type": risk_type, "priority": "P1", "risk_ids": [], "rule_ids": [], "scenario_ids": [], "evidence": [], "domains": set()})

    for risk in knowledge.get("risk_matrix", []) or []:
        risk_type = normalize_knowledge_risk(risk.get("risk") or risk.get("risk_id") or "")
        if not risk_type:
            continue
        item = ensure(risk_type)
        item["priority"] = min_priority(item.get("priority"), risk.get("priority") or "P1")
        if risk.get("risk_id"):
            item["risk_ids"].append(risk["risk_id"])
        item["evidence"].extend(str(x) for x in risk.get("evidence", []) or [])
    for rule in knowledge.get("business_rules", []) or []:
        rule_risks = {normalize_knowledge_risk(r) for r in rule.get("risk_tags", []) or []} | risks_from_text(rule.get("text", ""))
        if not rule_risks:
            rule_risks = {normalize_knowledge_risk(rule.get("domain", ""))}
        for risk_type in rule_risks:
            item = ensure(risk_type)
            if rule.get("rule_id"):
                item["rule_ids"].append(rule["rule_id"])
            if rule.get("domain"):
                item["domains"].add(str(rule["domain"]))
            if rule.get("text"):
                item["evidence"].append(str(rule["text"])[:180])
    for scenario in knowledge.get("business_scenarios", []) or []:
        text = " ".join(str(scenario.get(k, "")) for k in ["name", "domain", "business_value"])
        scenario_risks = risks_from_text(text) or {normalize_knowledge_risk(scenario.get("domain", ""))}
        for risk_type in scenario_risks:
            item = ensure(risk_type)
            if scenario.get("scenario_id"):
                item["scenario_ids"].append(scenario["scenario_id"])
            if scenario.get("domain"):
                item["domains"].add(str(scenario["domain"]))
    for module in knowledge.get("module_knowledge_map", []) or []:
        for risk_type in module.get("risks", []) or []:
            item = ensure(risk_type)
            item["domains"].update(str(x) for x in module.get("domains", []) or [])
            if module.get("module"):
                item["evidence"].append(f"模块 {module['module']} 暴露 {risk_type} 风险")
    result = []
    for item in items.values():
        item["domains"] = sorted(item["domains"])
        item["risk_ids"] = sorted(set(item["risk_ids"]))
        item["rule_ids"] = sorted(set(item["rule_ids"]))
        item["scenario_ids"] = sorted(set(item["scenario_ids"]))
        item["evidence"] = list(dict.fromkeys(item["evidence"]))
        result.append(item)
    return sorted(result, key=lambda item: (priority_rank(item.get("priority")), item["risk_type"]), reverse=True)


def build_module_context_by_domain(knowledge: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for module in knowledge.get("module_knowledge_map", []) or []:
        name = str(module.get("module") or "")
        for domain in module.get("domains", []) or []:
            for alias in domain_aliases(str(domain)):
                result.setdefault(alias, name)
        for risk in module.get("risks", []) or []:
            result.setdefault(normalize_knowledge_risk(str(risk)), name)
    return result


def choose_module_for_operation(op: dict, module_by_domain: dict[str, str]) -> str:
    keys: list[str] = []
    for key in [op.get("resource"), *op.get("risk_hints", [])]:
        keys.extend(domain_aliases(str(key)))
    for key in keys:
        if key in module_by_domain:
            return module_by_domain[key]
    return "通用业务模块"


def domain_aliases(value: str) -> list[str]:
    raw = str(value or "").lower().strip()
    aliases = {raw}
    if raw.endswith("s"):
        aliases.add(raw[:-1])
    aliases.update(
        {
            "orders": "order",
            "order": "orders",
            "products": "inventory",
            "product": "inventory",
            "inventory": "products",
            "stock": "inventory",
            "payments": "payment",
            "payment": "payments",
            "refunds": "refund",
            "refund": "refunds",
            "cart": "coupon",
            "coupon": "cart",
        }.get(raw, raw).split("|")
    )
    return [item for item in aliases if item]


def priority_rank(priority: str | None) -> int:
    return {"P0": 3, "P1": 2, "P2": 1}.get(str(priority or "P1").upper(), 1)


def min_priority(left: str | None, right: str | None) -> str:
    return left if priority_rank(left) >= priority_rank(right) else right


def executable_risk_type(risk_type: str) -> str:
    return {
        "state_consistency": "state_consistency",
        "quantity_consistency": "stock_consistency",
        "benefit_abuse": "coupon_abuse",
        "boundary_validation": "boundary_validation",
    }.get(risk_type, risk_type)


def knowledge_probe_severity(risk_type: str, priority: str | None = None) -> str:
    if priority in {"P0", "P1", "P2"}:
        return priority
    if risk_type in {"permission_bypass", "auth_bypass", "idor", "tenant_isolation", "money_consistency", "quantity_consistency", "state_flow"}:
        return "P0"
    return "P1"


def knowledge_probe_actor(risk_type: str) -> str:
    if risk_type in {"auth_bypass"}:
        return "anonymous"
    if risk_type in {"callback_trust"}:
        return "system"
    return "normal_user"


def expected_status_for_knowledge_risk(risk_type: str, method: str) -> int:
    if risk_type == "auth_bypass":
        return 401
    if risk_type in {"permission_bypass", "idor", "tenant_isolation", "approval_bypass", "search_scope_leak"}:
        return 403
    if risk_type in {"boundary_validation", "benefit_abuse", "coupon_abuse", "file_upload_validation"}:
        return 400
    if risk_type in {"money_consistency", "quantity_consistency", "state_flow", "idempotency", "callback_trust", "race_condition"}:
        return 409 if method in {"POST", "PUT", "PATCH", "DELETE"} else 200
    return 200


def knowledge_probe_title(risk_type: str, op: dict, item: dict) -> str:
    labels = {
        "permission_bypass": "业务知识要求非授权角色不得执行",
        "auth_bypass": "业务知识要求未登录用户不得执行",
        "idor": "业务知识要求不得越权访问或变更",
        "tenant_isolation": "业务知识要求跨租户数据必须隔离",
        "money_consistency": "业务知识要求金额/费用/支付必须一致",
        "quantity_consistency": "业务知识要求数量/库存/额度不能越界",
        "state_flow": "业务知识要求状态流转必须合法",
        "state_consistency": "业务知识要求上下游状态必须一致",
        "idempotency": "业务知识要求重复提交必须幂等",
        "benefit_abuse": "业务知识要求权益/优惠不能被滥用",
        "boundary_validation": "业务知识要求输入边界必须校验",
    }
    return f"{labels.get(risk_type, '业务知识要求校验高风险行为')} {op['method']} {op['path']}"


def knowledge_expected_statement(risk_type: str, op: dict) -> str:
    return f"根据企业业务知识，{op['method']} {op['path']} 必须满足 {risk_type} 业务不变量"


def knowledge_bug_signal(risk_type: str) -> str:
    return f"响应、状态或跨接口证据违反企业业务知识中的 {risk_type} 风险规则"


def why_high_value(risk_type: str) -> str:
    return {
        "permission_bypass": "直接影响权限边界和数据安全",
        "idor": "容易造成用户间数据越权",
        "tenant_isolation": "直接影响多租户企业隔离",
        "money_consistency": "影响资金、账务或费用正确性",
        "quantity_consistency": "影响库存、额度、容量等核心资产",
        "state_flow": "影响业务流程合法性和状态闭环",
        "state_consistency": "影响跨模块数据一致性",
        "idempotency": "容易导致重复创建、重复扣减或重复入账",
        "benefit_abuse": "容易造成权益、优惠、折扣损失",
        "boundary_validation": "容易绕过必填、枚举、范围和非法输入校验",
    }.get(risk_type, "来自企业业务知识资产中的高风险规则")


# Learned templates bind to OpenAPI by role — never emit synthetic mall IDs.
# Tuple: template_id, title, probe_type, risk_type, severity, actor, method, role, expected_status, variants
LEARNED_TEMPLATE_SPECS = [
    ("STOCK_NEGATIVE_QUANTITY", "容量/库存不足时不能创建", "learned_stock_probe", "stock_consistency", "P0", "normal_user", "POST", "capacity_create", 409, 2),
    ("ORDER_DUPLICATE_SUBMIT", "重复提交不能生成多个业务单", "learned_idempotency_probe", "idempotency", "P1", "normal_user", "POST", "capacity_create", 200, 2),
    ("IDEMPOTENCY_DUPLICATE_STOCK_DEDUCT", "重复提交不能重复扣减容量/库存", "learned_stock_idempotency_probe", "stock_consistency", "P1", "normal_user", "POST", "capacity_create", 200, 2),
    ("STOCK_NOT_DECREASED", "创建成功后容量/库存必须扣减", "learned_stock_probe", "stock_consistency", "P1", "normal_user", "POST", "capacity_create", 200, 2),
    ("STOCK_NOT_ROLLBACK", "取消后容量/库存必须回滚", "learned_stock_probe", "stock_consistency", "P1", "normal_user", "POST", "capacity_cancel", 200, 2),
    ("ORDER_CREATE_MISSING", "创建成功后必须可查询", "learned_state_probe", "state_consistency", "P1", "normal_user", "POST", "capacity_create", 200, 2),
    ("ORDER_CANCEL_STATE", "取消后状态必须进入终止态", "learned_state_probe", "state_flow", "P1", "normal_user", "POST", "capacity_cancel", 200, 2),
    ("IDOR_ORDER_ACCESS", "用户不能查看他人资源", "learned_idor_probe", "idor", "P0", "normal_user", "GET", "owned_detail", 403, 2),
    ("IDOR_ORDER_CANCEL", "用户不能越权取消他人资源", "learned_idor_probe", "idor", "P1", "normal_user", "POST", "capacity_cancel", 403, 2),
    ("IDOR_ADDRESS_MODIFY", "用户创建时不能篡改租户或归属", "learned_idor_probe", "idor", "P1", "normal_user", "POST", "capacity_create", 403, 2),
    ("TENANT_DATA_LEAK", "租户数据必须隔离", "learned_tenant_probe", "tenant_isolation", "P0", "normal_user", "GET", "tenant_list", 403, 2),
    ("AUTH_ROLE_DOWNGRADE_CACHE", "低权限用户不能访问管理端", "learned_permission_probe", "permission_bypass", "P1", "normal_user", "GET", "admin_read", 403, 1),
    ("PAYMENT_STATUS_NOT_UPDATED", "支付/结算成功后业务状态必须更新", "learned_payment_probe", "state_consistency", "P1", "normal_user", "POST", "payment_create", 200, 2),
    ("PAYMENT_CANCELLED_ORDER_ALLOWED", "已终止业务单不能继续支付/结算", "learned_payment_probe", "state_flow", "P0", "normal_user", "POST", "payment_create", 409, 2),
    ("ORDER_PAY_CANCELLED", "已终止业务单不能被回调改为已支付", "learned_callback_probe", "state_flow", "P0", "system", "POST", "payment_callback", 409, 2),
    ("PAYMENT_DUPLICATE_CALLBACK", "支付/结算回调必须幂等", "learned_callback_probe", "payment_callback", "P1", "system", "POST", "payment_callback", 200, 2),
    ("MONEY_PAY_TOTAL_DIFF", "回调金额必须匹配应付金额", "learned_money_probe", "money_consistency", "P0", "system", "POST", "payment_callback", 409, 2),
    ("REFUND_UNPAID_ORDER", "未支付业务单不能退款", "learned_refund_probe", "refund_abuse", "P1", "normal_user", "POST", "refund_create", 409, 2),
    ("REFUND_DUPLICATE", "同一退款请求不能重复处理", "learned_refund_probe", "refund_abuse", "P1", "normal_user", "POST", "refund_create", 409, 2),
    ("REFUND_OVER_AMOUNT", "退款金额不能超过已支付剩余金额", "learned_refund_probe", "money_consistency", "P0", "normal_user", "POST", "refund_create", 409, 2),
    ("REFUND_STATE_INCONSISTENCY", "退款后状态与库存/额度必须一致", "learned_refund_probe", "state_consistency", "P1", "normal_user", "POST", "refund_create", 200, 2),
    ("COUPON_THRESHOLD_BYPASS", "不满足门槛不能使用权益/优惠", "learned_coupon_probe", "coupon_abuse", "P1", "normal_user", "POST", "benefit_apply", 400, 1),
    ("MONEY_DISCOUNT_OVER_TOTAL", "优惠金额不能超过应付金额", "learned_money_probe", "money_consistency", "P0", "normal_user", "POST", "benefit_apply", 400, 1),
]

_CAPACITY_HINTS = (
    "order", "booking", "reservation", "enrollment", "appointment", "inventory",
    "stock", "seat", "capacity", "ticket", "claim", "requisition", "prescription",
    "订单", "预约", "选课", "库存", "名额",
)
_BENEFIT_HINTS = ("coupon", "voucher", "promo", "promotion", "benefit", "discount", "补贴", "优惠", "券")
_PAYMENT_HINTS = ("payment", "pay", "settlement", "charge", "支付", "结算")
_REFUND_HINTS = ("refund", "chargeback", "退款")
_CALLBACK_HINTS = ("callback", "webhook", "notify", "回调", "通知")


def normalize_synthetic_probe_path(path: str) -> str:
    """Return the source-bound path unchanged; synthetic IDs are forbidden."""
    return str(path or "")


def _operation_match_text(op: dict) -> str:
    return f"{op.get('path') or ''} {op.get('summary') or ''} {op.get('resource') or ''}".lower()


def _operations_for_learned_role(operations: list[dict], role: str, method: str) -> list[dict]:
    method_u = str(method or "").upper()
    rows: list[dict] = []
    for op in operations:
        if not isinstance(op, dict):
            continue
        op_method = str(op.get("method") or "").upper()
        if method_u and op_method != method_u:
            continue
        path = str(op.get("path") or "")
        if not path or path in {"/reset", "/health", "/openapi.json"}:
            continue
        text = _operation_match_text(op)
        has_param = "{" in path and "}" in path
        is_admin = any(tok in path.lower() for tok in ("/admin/", "/manage/", "/manager/", "/console/"))
        if role == "capacity_create":
            if has_param or not any(tok in text for tok in _CAPACITY_HINTS):
                continue
        elif role == "capacity_cancel":
            if not any(tok in path.lower() for tok in ("/cancel", "/close", "/void", "/revoke", "/withdraw")):
                continue
            if not any(tok in text for tok in _CAPACITY_HINTS):
                continue
        elif role == "owned_detail":
            if not has_param or op_method != "GET" or is_admin:
                continue
        elif role == "benefit_apply":
            if not any(tok in text for tok in _BENEFIT_HINTS):
                continue
        elif role == "payment_create":
            if any(tok in text for tok in _CALLBACK_HINTS) or not any(tok in text for tok in _PAYMENT_HINTS):
                continue
        elif role == "payment_callback":
            if not (any(tok in text for tok in _PAYMENT_HINTS) and any(tok in text for tok in _CALLBACK_HINTS)):
                continue
        elif role == "refund_create":
            if not any(tok in text for tok in _REFUND_HINTS):
                continue
        elif role == "admin_read":
            if not is_admin or op_method != "GET":
                continue
        elif role == "tenant_list":
            if "tenant" not in path.lower() and "tenant" not in text:
                continue
        else:
            continue
        rows.append(op)
    # Prefer shorter/more specific collection paths first for stable binding.
    return sorted(rows, key=lambda op: (len(str(op.get("path") or "")), str(op.get("path") or "")))


def _openapi_paths(business_model: dict) -> set[str]:
    return {
        str(op.get("path") or "")
        for op in (business_model.get("operations") or [])
        if isinstance(op, dict) and op.get("path")
    }


def _path_available_in_model(path: str, paths: set[str]) -> bool:
    if not path:
        return False
    candidates = {
        path.split("?", 1)[0],
        normalize_synthetic_probe_path(path).split("?", 1)[0],
    }
    return any(candidate in paths for candidate in candidates)


def _bind_policy_path_to_source(paths: set[str], *candidates: str) -> str:
    for candidate in candidates:
        base = str(candidate or "").split("?", 1)[0]
        if base in paths:
            return base
    return ""


def generate_feedback_learning_probes(business_model: dict) -> list[dict]:
    """Generate probes learned from prior miss analysis without reading hidden ground truth.

    Templates bind to real OpenAPI operations by semantic role and preserve the
    source-declared path.
    """
    operations = [op for op in (business_model.get("operations") or []) if isinstance(op, dict)]
    probes: list[dict] = []
    for template_id, title, probe_type, risk_type, severity, actor, method, role, expected_status, variants in LEARNED_TEMPLATE_SPECS:
        matched = _operations_for_learned_role(operations, role, method)
        if not matched:
            continue
        op = matched[0]
        path = str(op.get("path") or "")
        if role == "tenant_list" and "tenant_id=" not in path:
            path = f"{path}{'&' if '?' in path else '?'}tenant_id=tenant_b"
        api_template = f"{method} {str(op.get('path') or path).split('?')[0]}"
        resource = str(op.get("resource") or path.strip("/").split("/")[0] or "resource")
        bound_title = title if "{resource}" not in title else title.format(resource=resource)
        for idx in range(1, variants + 1):
            item = probe(
                f"LEARN_{template_id}_V{idx}",
                f"{bound_title}（反馈学习变体 {idx}）",
                probe_type,
                risk_type,
                severity,
                actor,
                method,
                path,
                expected_status,
                api_template,
                source="feedback_learning",
            )
            item["predicted_template_id"] = template_id
            item["learned_from"] = "phase3_missed_bug_analysis"
            item["learning_strategy"] = "template_level_probe_expansion"
            item["learned_role"] = role
            item["bound_operation"] = api_template
            item["variant_index"] = idx
            probes.append(item)
    return probes


def generate_adaptive_policy_probes(business_model: dict) -> list[dict]:
    """Generate probes from sanitized adaptive policy learned from evaluator feedback.

    The policy is created from benchmark evaluator outputs, not from private ground
    truth files. It stores only template-level strategy and priority, so it can be
    reused in blind mode without exposing bug instances or enabled bug sets.
    """
    paths = _openapi_paths(business_model)
    operations = [op for op in business_model.get("operations", []) if isinstance(op, dict)]

    try:
        policy = build_learned_probe_policy(Path("."))
    except Exception:
        return []
    probes: list[dict] = []
    for row in policy.get("template_policies", []):
        if row.get("priority_score", 0) < 0.45:
            continue
        method = str(row.get("method") or "GET").upper()
        raw_path = str(row.get("path") or (row.get("api_template") or " ").split(" ", 1)[-1]).strip()
        path = normalize_synthetic_probe_path(raw_path)
        api_template = str(row.get("api_template") or f"{method} {path.split('?')[0]}")
        api_path = normalize_synthetic_probe_path(api_template.split(" ", 1)[-1])
        bound_path = _bind_policy_path_to_source(paths, api_path, path)
        if not bound_path:
            risk_type = str(row.get("risk_type") or "")
            matches = [
                op for op in operations
                if str(op.get("method") or "").upper() == method
                and risk_type in {
                    str(risk) for risk in op.get("risk_hints", [])
                }
            ]
            if matches:
                bound_path = str(sorted(matches, key=lambda op: str(op.get("path") or ""))[0].get("path") or "")
        if not bound_path:
            continue
        path = bound_path
        variants = int(row.get("recommended_variants") or 1)
        for idx in range(1, variants + 1):
            item = probe(
                f"ADAPT_{row['template_id']}_V{idx}",
                f"{row.get('strategy', row['template_id'])}（自适应策略变体 {idx}）",
                row["probe_type"],
                row["risk_type"],
                row["severity"],
                row["actor"],
                method,
                path,
                int(row["expected_status"]),
                f"{method} {path.split('?')[0]}",
                source="adaptive_policy",
            )
            item["predicted_template_id"] = row["template_id"]
            item["adaptive_priority_score"] = row.get("priority_score", 0)
            item["learning_strategy"] = row.get("strategy")
            item["learned_from"] = "phase5_adaptive_probe_policy"
            item["variant_index"] = idx
            probes.append(item)
    return probes


def generate_feedback_adjusted_policy_probes(business_model: dict) -> list[dict]:
    """Generate Phase23 probes from QA human feedback policy.

    The policy is produced by ai_test_asset_center.feedback_policy_update from
    human_feedback.jsonl and human_feedback_policy_update.json. It is template
    level only, so blind discovery can use it without reading hidden ground truth,
    bug sets, or enabled bug switches.
    """
    paths = _openapi_paths(business_model)

    policy_path = Path(os.environ.get("FEEDBACK_ADJUSTED_POLICY_PATH", "platform_workspace/enterprise_shop/defect_discovery/feedback_adjusted_probe_policy.json"))
    if not policy_path.exists():
        return []
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not (policy.get("private_leak_check") or {}).get("passed", True):
        return []
    probes: list[dict] = []
    for row in policy.get("template_policies", []) or []:
        if float(row.get("priority_score") or 0) < 0.45:
            continue
        raw_path = str(row.get("path") or (row.get("api_template") or f"{row.get('method','GET')} /").split(" ", 1)[-1])
        path = normalize_synthetic_probe_path(raw_path)
        api_template = f"{row.get('method') or 'GET'} {path.split('?')[0]}"
        bound_path = _bind_policy_path_to_source(paths, api_template.split(" ", 1)[-1], path)
        if not bound_path:
            continue
        path = bound_path
        api_template = f"{row.get('method') or 'GET'} {path}"
        variants = max(1, min(5, int(row.get("recommended_variants") or 1)))
        for idx in range(1, variants + 1):
            item = probe(
                f"HFADJ_{row['template_id']}_V{idx}",
                f"{row.get('title') or row['template_id']}（人工反馈增强变体 {idx}）",
                row.get("probe_type") or "feedback_adjusted_probe",
                row.get("risk_type") or "unknown",
                row.get("severity") or row.get("human_priority") or "P1",
                row.get("actor") or "normal_user",
                row.get("method") or "GET",
                path,
                int(row.get("expected_status") or 200),
                api_template,
                source="feedback_adjusted",
            )
            item["predicted_template_id"] = row["template_id"]
            item["feedback_priority_score"] = row.get("priority_score", 0)
            item["human_feedback_count"] = row.get("human_feedback_count", 0)
            item["learning_strategy"] = "feedback_driven_policy_update"
            item["learned_from"] = "phase23_human_feedback_policy_update"
            item["variant_index"] = idx
            probes.append(item)
    return probes

def generate_journey_defect_probes(business_model: dict) -> list[dict]:
    probes: list[dict] = []
    seq = 1
    operations = list(business_model.get("operations") or [])
    for dep in business_model.get("entity_dependencies", []):
        if dep.get("dependency") == "create_then_read":
            setup = parse_operation_ref(dep["setup"])
            assertion = parse_operation_ref(dep["assert"])
            if setup and assertion:
                probes.append(
                    journey_probe(
                        f"JOURNEY_CREATE_READ_{seq:03d}",
                        f"{dep['resource']} 创建后必须可查询且字段一致",
                        "journey_consistency_probe",
                        "state_consistency",
                        "P1",
                        [
                            step("create_resource", setup[0], concrete_path(setup[1]), body_hint="create"),
                            step("read_resource", assertion[0], concrete_path(assertion[1]), body_hint="read"),
                        ],
                        api_template=dep["setup"],
                    )
                )
                seq += 1
        if dep.get("dependency") == "state_action_requires_existing_resource":
            action = parse_operation_ref(dep["action"])
            if action:
                # Prefer documented create/setup path over invented POST /{resource}.
                setup_ref = parse_operation_ref(str(dep.get("setup") or ""))
                if not setup_ref:
                    create_ops = [
                        op for op in operations
                        if str(op.get("resource") or "") == str(dep.get("resource") or "")
                        and str(op.get("method") or "").upper() == "POST"
                    ]
                    if create_ops:
                        setup_ref = (str(create_ops[0]["method"]).upper(), str(create_ops[0]["path"]))
                seed_method = setup_ref[0] if setup_ref else "POST"
                seed_path = concrete_path(setup_ref[1]) if setup_ref else f"/{dep['resource']}"
                read_path = read_path_for_resource(dep["resource"], operations=operations)
                probes.append(
                    journey_probe(
                        f"JOURNEY_STATE_ACTION_{seq:03d}",
                        f"{dep['resource']} 状态动作后必须可复核",
                        "journey_state_probe",
                        "state_flow",
                        "P1",
                        [
                            step("seed_resource", seed_method, seed_path, body_hint="create"),
                            step("state_action", action[0], concrete_path(action[1]), body_hint="state"),
                            step("read_after_action", "GET", read_path, body_hint="read"),
                        ],
                        api_template=dep["action"],
                    )
                )
                seq += 1
    for lineage in business_model.get("data_lineage", []):
        consumers = lineage.get("consumers") or []
        if not consumers:
            continue
        producer = parse_operation_ref(lineage["producer"])
        consumer = parse_operation_ref(consumers[0])
        if not producer or not consumer:
            continue
        probes.append(
            journey_probe(
                f"JOURNEY_LINEAGE_{seq:03d}",
                f"{lineage['resource']} {lineage['field_family']} 数据血缘必须跨接口一致",
                "journey_lineage_probe",
                lineage_risk_type(lineage["field_family"]),
                "P1",
                [
                    step("produce_value", producer[0], concrete_path(producer[1]), body_hint=lineage["field_family"]),
                    step("consume_value", consumer[0], concrete_path(consumer[1]), body_hint="read"),
                ],
                api_template=lineage["producer"],
            )
        )
        seq += 1
    return probes


def parse_operation_ref(ref: str) -> tuple[str, str] | None:
    parts = ref.split(" ", 1)
    if len(parts) != 2:
        return None
    return parts[0].upper(), parts[1]


def read_path_for_resource(resource: str, operations: list[dict] | None = None) -> str:
    """Pick a documented GET detail path when available; else generic /{resource}/{id}."""
    name = str(resource or "").strip().strip("/")
    if not name:
        return "/"
    ops = list(operations or [])
    detail_candidates: list[str] = []
    collection_candidates: list[str] = []
    for op in ops:
        if str(op.get("method") or "").upper() != "GET":
            continue
        if str(op.get("resource") or "") != name and name not in str(op.get("path") or ""):
            continue
        path = str(op.get("path") or "")
        if not path:
            continue
        if "{" in path and "}" in path:
            detail_candidates.append(path)
        else:
            collection_candidates.append(path)
    if detail_candidates:
        # Prefer paths whose final segment is a placeholder (true detail reads).
        detail_candidates.sort(
            key=lambda p: (
                0 if p.rstrip("/").endswith("}") else 1,
                len(p),
            )
        )
        return concrete_path(detail_candidates[0])
    if collection_candidates:
        return concrete_path(collection_candidates[0])
    return f"/{name}/{{id}}"


def lineage_risk_type(field_family: str) -> str:
    return {"money": "money_consistency", "quantity": "stock_consistency", "scope": "tenant_isolation"}.get(field_family, "state_consistency")


def step(name: str, method: str, path: str, body_hint: str = "") -> dict:
    return {"name": name, "method": method, "path": path, "body_hint": body_hint}


def journey_probe(probe_id: str, title: str, probe_type: str, risk_type: str, severity: str, steps: list[dict], api_template: str) -> dict:
    return {
        "probe_id": probe_id,
        "title": title,
        "probe_type": probe_type,
        "risk_type": risk_type,
        "severity": severity,
        "actor": "normal_user",
        "method": steps[-1]["method"],
        "path": steps[-1]["path"],
        "api_template": api_template,
        "source": "journey_auto",
        "steps": steps,
        "expected_status": 200,
        "evidence_required": ["actor_role", "step_requests", "step_responses", "cross_step_assertion"],
        "expected": "多接口链路执行后，资源状态、字段和上下游数据保持一致",
        "bug_signal": "链路执行结果违反语义图、状态机或数据血缘不变量",
    }


def generate_generic_defect_probes(business_model: dict) -> list[dict]:
    probes: list[dict] = []
    operations = business_model.get("operations", [])
    roles = business_model.get("roles", [])
    has_normal_user = "normal_user" in roles or bool(roles)
    seq = 1
    for op in operations:
        method = op["method"]
        path = op["path"]
        if path in {"/reset", "/health", "/openapi.json"}:
            continue
        concrete = concrete_path(path)
        template = f"{method} {path}"
        risks = set(op.get("risk_hints", []))
        if "permission_bypass" in risks:
            probes.append(probe(f"GEN_PERMISSION_{seq:03d}", f"非授权角色不能执行 {template}", "generic_permission_probe", "permission_bypass", "P0", "normal_user" if has_normal_user else "anonymous", method, concrete, 403, template, source="generic_auto"))
            seq += 1
            probes.append(probe(f"GEN_ANON_PROTECTED_{seq:03d}", f"未登录用户不能执行受保护操作 {template}", "generic_auth_probe", "auth_bypass", "P0", "anonymous", method, concrete, 401, template, source="generic_auto"))
            seq += 1
        if "idor" in risks:
            probes.append(probe(f"GEN_IDOR_{seq:03d}", f"用户不能访问或变更他人资源 {template}", "generic_idor_probe", "idor", "P0", "normal_user", method, concrete, 403, template, source="generic_auto"))
            seq += 1
        if "tenant_isolation" in risks:
            tenant_path = concrete if "?" in concrete else f"{concrete}?tenant_id=tenant_b"
            probes.append(probe(f"GEN_TENANT_{seq:03d}", f"跨租户请求必须被隔离 {template}", "generic_tenant_probe", "tenant_isolation", "P0", "normal_user", method, tenant_path, 403, template, source="generic_auto"))
            seq += 1
        if "idempotency" in risks and method in {"POST", "PUT", "PATCH"}:
            probes.append(probe(f"GEN_IDEMPOTENCY_{seq:03d}", f"重复提交必须幂等 {template}", "generic_idempotency_probe", "idempotency", "P1", "normal_user", method, concrete, 200, template, source="generic_auto"))
            seq += 1
        if "money_consistency" in risks and method in {"POST", "PUT", "PATCH"}:
            probes.append(probe(f"GEN_MONEY_{seq:03d}", f"金额字段不能绕过一致性校验 {template}", "generic_money_probe", "money_consistency", "P0", "normal_user", method, concrete, 409, template, source="generic_auto"))
            seq += 1
        if "quantity_consistency" in risks and method in {"POST", "PUT", "PATCH"}:
            probes.append(probe(f"GEN_QUANTITY_{seq:03d}", f"数量或额度不能越界 {template}", "generic_quantity_probe", "stock_consistency", "P0", "normal_user", method, concrete, 409, template, source="generic_auto"))
            seq += 1
        if "state_flow" in risks and method in {"POST", "PUT", "PATCH"}:
            probes.append(probe(f"GEN_STATE_{seq:03d}", f"状态流转必须满足前置条件 {template}", "generic_state_probe", "state_flow", "P1", "normal_user", method, concrete, 409, template, source="generic_auto"))
            seq += 1
        if "benefit_abuse" in risks and method in {"POST", "PUT", "PATCH"}:
            probes.append(probe(f"GEN_BENEFIT_{seq:03d}", f"权益/优惠不能被滥用 {template}", "generic_benefit_probe", "coupon_abuse", "P1", "normal_user", method, concrete, 400, template, source="generic_auto"))
            seq += 1
        if risks & {"approval_bypass", "step_skip"}:
            probes.append(probe(f"GEN_APPROVAL_{seq:03d}", f"审批流程不能跳过必要节点 {template}", "generic_workflow_probe", "approval_bypass", "P0", "normal_user", method, concrete, 403, template, source="generic_auto"))
            seq += 1
        if risks & {"audit_log_missing", "sensitive_data_exposure", "privacy_scope", "export_permission"}:
            probes.append(probe(f"GEN_COMPLIANCE_{seq:03d}", f"关键操作必须满足审计、隐私和导出权限 {template}", "generic_compliance_probe", "audit_compliance", "P1", "normal_user", method, concrete, 403, template, source="generic_auto"))
            seq += 1
        if risks & {"file_upload_validation", "duplicate_import", "bulk_operation_partial_failure", "large_payload_limit"}:
            probes.append(probe(f"GEN_BATCH_IMPORT_{seq:03d}", f"批量导入/文件上传必须校验和处理部分失败 {template}", "generic_batch_probe", "batch_import", "P1", "normal_user", method, concrete, 400, template, source="generic_auto"))
            seq += 1
        if risks & {"report_aggregation_error", "search_scope_leak", "pagination_consistency", "sorting_filter_consistency", "export_consistency"}:
            probes.append(probe(f"GEN_REPORT_SEARCH_{seq:03d}", f"搜索、分页、报表和导出必须遵守范围和一致性 {template}", "generic_report_probe", "search_report", "P1", "normal_user", method, concrete, 403 if "search_scope_leak" in risks else 200, template, source="generic_auto"))
            seq += 1
        if risks & {"notification_wrong_recipient", "notification_duplicate", "template_variable_leak"}:
            probes.append(probe(f"GEN_NOTIFICATION_{seq:03d}", f"通知不能错发、重复发送或泄露模板变量 {template}", "generic_notification_probe", "notification_risk", "P1", "normal_user", method, concrete, 400, template, source="generic_auto"))
            seq += 1
        if risks & {"feature_flag_scope", "tenant_config_isolation", "pricing_config", "workflow_config", "default_value_risk"}:
            probes.append(probe(f"GEN_CONFIG_{seq:03d}", f"配置、规则和开关必须按租户/组织隔离 {template}", "generic_config_probe", "configuration_risk", "P1", "normal_user", method, concrete, 403, template, source="generic_auto"))
            seq += 1
        if risks & {"callback_trust", "webhook_replay", "third_party_status_mapping", "message_ordering", "eventual_consistency"}:
            probes.append(probe(f"GEN_INTEGRATION_{seq:03d}", f"外部回调、事件和集成状态必须可信且幂等 {template}", "generic_integration_probe", "integration_risk", "P1", "system", method, concrete, 409, template, source="generic_auto"))
            seq += 1
        if risks & {"race_condition", "concurrent_update_lost", "time_window_boundary", "timezone_boundary", "sla_timeout"}:
            probes.append(probe(f"GEN_TIME_CONCURRENCY_{seq:03d}", f"并发、时间窗口和时区边界必须一致 {template}", "generic_time_concurrency_probe", "time_concurrency", "P1", "normal_user", method, concrete, 409, template, source="generic_auto"))
            seq += 1
    return probes


def concrete_path(path: str) -> str:
    """Keep source-declared parameter placeholders; never invent resource IDs."""
    return str(path or "")


def probe(probe_id: str, title: str, probe_type: str, risk_type: str, severity: str, actor: str, method: str, path: str, expected_status: int, api_template: str | None = None, source: str = "generic_auto") -> dict:
    return {"probe_id": probe_id, "title": title, "probe_type": probe_type, "risk_type": risk_type, "severity": severity, "actor": actor, "method": method, "path": path, "api_template": api_template or f"{method} {path.split('?')[0]}", "source": source, "expected_status": expected_status, "evidence_required": ["actor_role", "request", "response_status", "response_body"], "expected": f"HTTP {expected_status} 或业务状态保持一致", "bug_signal": "实际响应违反业务不变量"}


def load_high_value_pattern_memory(workspace: Path) -> dict:
    path = workspace / "high_value_pattern_memory.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict) and data.get("version") == "high_value_pattern_memory_v1":
        return data
    return {}


def load_risk_learning_profile(workspace: Path, output: Path | None = None) -> dict:
    for path in [workspace / "risk_learning_profile.json", *(([output / "risk_learning_profile.json"] if output else []))]:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("version") == "enterprise_high_value_risk_learning_v1":
            return data
    return {}


def load_high_value_attack_plan(workspace: Path, output: Path | None = None) -> dict:
    for path in [workspace / "high_value_attack_plan.json", *(([output / "high_value_attack_plan.json"] if output else []))]:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("version") == "high_value_attack_plan_v1":
            return data
    return {}


def load_high_value_capability_assessment(workspace: Path, output: Path | None = None) -> dict:
    for path in [workspace / "high_value_capability_assessment.json", *(([output / "high_value_capability_assessment.json"] if output else []))]:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("version") == "high_value_capability_assessment_v1":
            return data
    return {}


def load_high_value_capability_trend(workspace: Path, output: Path | None = None) -> dict:
    for path in [workspace / "high_value_capability_trend.json", *(([output / "high_value_capability_trend.json"] if output else []))]:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("version") == "high_value_capability_trend_v1":
            return data
    return {}


def load_business_adaptation_profile(workspace: Path, output_root: Path, project: str) -> dict:
    candidates = [
        workspace / "business_adaptation_profile.json",
        output_root / project / "business_adaptation" / "business_adaptation_profile.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("adaptive_risk_matrix"):
            return data
    return {}


def build_lightweight_business_adaptation_profile(business_model: dict) -> dict:
    industry = str(business_model.get("industry") or "generic_enterprise_software")
    operations = business_model.get("operations", []) or []
    domain_name = {
        "ecommerce": "电商 / 交易履约",
        "finance": "金融 / 资金账户",
        "crm": "CRM / 销售客户",
        "erp": "ERP / 供应链",
        "healthcare": "医疗 / 患者诊疗",
        "education": "教育 / 课程考试",
    }.get(industry, "通用企业软件")
    risk_rows: dict[str, dict] = {}
    for op in operations:
        for raw_risk in op.get("risk_hints", []) or []:
            risk = business_adaptation_executable_risk(str(raw_risk))
            row = risk_rows.setdefault(
                risk,
                {
                    "risk_type": risk,
                    "domain": industry,
                    "severity": knowledge_probe_severity(risk),
                    "title": f"{risk} 行业业务风险",
                    "expected": knowledge_expected_statement(risk, op),
                    "bug_signal": knowledge_bug_signal(risk),
                    "matched_operations": [],
                },
            )
            row["matched_operations"].append(operation_api_template(op))
    matrix = []
    for row in risk_rows.values():
        row["matched_operations"] = sorted(set(row["matched_operations"]))[:12]
        row["matched_operation_count"] = len(row["matched_operations"])
        matrix.append(row)
    matrix.sort(key=lambda item: (item.get("severity") != "P0", -item.get("matched_operation_count", 0), item.get("risk_type", "")))
    return {
        "phase": "lightweight_business_adaptation_from_defect_discovery",
        "business_domain_mode": "auto_from_single_input",
        "selected_domains": [{"domain": industry, "name": domain_name, "score": max(1, len(matrix)), "risks": [row["risk_type"] for row in matrix[:12]]}],
        "operation_count": len(operations),
        "endpoint_domain_map": [
            {
                "method": op.get("method"),
                "path": op.get("path"),
                "matched_domains": [industry],
                "risk_types": [business_adaptation_executable_risk(r) for r in op.get("risk_hints", []) or []],
            }
            for op in operations
        ],
        "adaptive_risk_matrix": matrix,
        "governance": {"uses_only_current_prd_openapi_accounts": True, "manual_domain_pack_required": False},
    }


