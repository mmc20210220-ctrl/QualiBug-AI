"""Finalization, formatting, deduplication, public API."""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ._common import *  # noqa: F401,F403
from ._evidence import *  # noqa: F401,F403
from ._quality import *  # noqa: F401,F403
from ._display import *  # noqa: F401,F403
from ._scoring import *  # noqa: F401,F403
from ._display import _build_investigation_display, _build_repro_steps_display, _looks_like_api_endpoint  # noqa: F401
from ._evidence import _best_runtime_observation, _build_taxonomy, _clean, _extract_verified_db_evidence, _format_policy_text, _high_confidence_candidate_display, _is_unresolved_path_value, _normalize_severity, _policy_list, _policy_section, _runtime_body_excerpt, _runtime_identity_mismatch_reasons, _runtime_observation_supports_finding, _status_code_int, _ui_verification_display  # noqa: F401
from ._quality import _build_display_evidence_chain, _compute_evidence_completeness, _compute_evidence_quality, _deep_get  # noqa: F401


def _normalize_regression_verification_obligations(
    details: dict[str, Any],
    finding: dict[str, Any],
) -> dict[str, Any]:
    """Map legacy regression suggestion text to customer-safe verification obligations."""
    obligations = details.get("regression_verification_obligations")
    if not isinstance(obligations, list):
        suggestions = details.get("regression_suggestions")
        obligations = suggestions if isinstance(suggestions, list) else []
    endpoint = details.get("api_endpoint") if isinstance(details.get("api_endpoint"), dict) else {}
    method = str(
        endpoint.get("method")
        or finding.get("_api_method")
        or finding.get("method")
        or ""
    ).strip().upper()
    path = str(
        endpoint.get("path")
        or finding.get("_api_path")
        or finding.get("path")
        or ""
    ).strip()
    if not obligations and (method or path):
        obligations = [
            f"系统应在客户处理后回归验证 {method or 'HTTP'} {path or '<unknown>'} 是否仍可复现"
        ]
    details["regression_verification_obligations"] = [
        str(item) for item in obligations if str(item).strip()
    ]
    details.pop("regression_suggestions", None)
    return details


def _finalize_technical_details_for_customer(
    details: dict[str, Any],
    finding: dict[str, Any],
) -> dict[str, Any]:
    """Strip fix advice and attach the product responsibility boundary."""
    cleaned = strip_fix_advice_fields(details) if isinstance(details, dict) else {}
    cleaned = _normalize_regression_verification_obligations(
        cleaned if isinstance(cleaned, dict) else {},
        finding if isinstance(finding, dict) else {},
    )
    cleaned["product_responsibility_boundary"] = product_responsibility_boundary(
        DISPLAY_READY_BOUNDARY_SOURCE
    )
    return cleaned


def _finalize_display_finding_for_customer(
    formatted: dict[str, Any],
    finding: dict[str, Any],
) -> dict[str, Any]:
    """Apply customer boundary to a fully formatted display-ready finding."""
    cleaned = strip_fix_advice_fields(formatted) if isinstance(formatted, dict) else {}
    technical = cleaned.get("technical_details") if isinstance(cleaned.get("technical_details"), dict) else {}
    cleaned["technical_details"] = _finalize_technical_details_for_customer(
        technical,
        finding if isinstance(finding, dict) else {},
    )
    regression = cleaned.get("regression_suggestions")
    if isinstance(regression, list):
        cleaned["regression_verification_obligations"] = [
            str(item) for item in regression if str(item).strip()
        ]
    elif not isinstance(cleaned.get("regression_verification_obligations"), list):
        cleaned["regression_verification_obligations"] = list(
            cleaned["technical_details"].get("regression_verification_obligations") or []
        )
    cleaned.pop("regression_suggestions", None)
    cleaned.pop("recommended_fix", None)
    cleaned["product_responsibility_boundary"] = product_responsibility_boundary(
        DISPLAY_READY_BOUNDARY_SOURCE
    )
    return cleaned


def _build_technical_details(finding: dict, investigation: dict, reproduction: dict) -> dict:
    """构建研发定位证据（让研发一眼能定位问题）。"""
    runtime_obs = _best_runtime_observation(finding)
    risk_type = _clean(finding.get("risk_type") or finding.get("category") or "unknown")
    path = _clean(finding.get("_api_path") or finding.get("repro_path") or finding.get("path"))
    method = _clean(finding.get("_api_method") or finding.get("method") or "GET").upper()

    defaults = _policy_section("defaults")
    context = {"risk_type": risk_type, "method": method, "path": path}
    possible_root_cause = _policy_section("root_cause_by_risk_type").get(risk_type)
    if not possible_root_cause:
        possible_root_cause = _format_policy_text(
            _clean(defaults.get("possible_root_cause")) or "{risk_type}",
            **context,
        )
    else:
        possible_root_cause = _format_policy_text(_clean(possible_root_cause), **context)
    recommended_fix = _policy_section("fix_by_risk_type").get(risk_type)
    if not recommended_fix:
        recommended_fix = _format_policy_text(
            _clean(defaults.get("recommended_fix")) or "{risk_type}",
            **context,
        )
    else:
        recommended_fix = _format_policy_text(_clean(recommended_fix), **context)

    regression_suggestions = [
        _format_policy_text(template, **context)
        for template in _policy_list("regression_suggestion_templates")
    ]
    if not regression_suggestions:
        regression_suggestions = [_format_policy_text("{method} {path}", **context)]

    details = {
        "api_endpoint": {
            "method": method,
            "path": path,
            "actor": _clean(runtime_obs.get("actor")),
        },
        "response_status": _status_code_int(runtime_obs.get("status_code")) if runtime_obs else 0,
        "response_body_excerpt": _runtime_body_excerpt(runtime_obs.get("body"), 500) if runtime_obs else "",
        "related_tables": investigation.get("relevant_tables", []),
        "trace_id": investigation.get("trace_id", ""),
        # Built for policy/regression mapping, then stripped before return.
        "possible_root_cause": possible_root_cause,
        "recommended_fix": recommended_fix,
        "regression_suggestions": regression_suggestions,
        "code_module_hint": _clean(finding.get("source_entity")) or (path.split("/")[1] if "/" in path and len(path.split("/")) > 1 else ""),
    }
    return _finalize_technical_details_for_customer(details, finding if isinstance(finding, dict) else {})


def _build_business_summary(finding: dict, business_impact: dict, bug_status: dict) -> str:
    """生成一句话业务影响摘要。"""
    severity = _clean(finding.get("severity")) or "P2"
    biz_summary = _strip_internal_tags(_clean(business_impact.get("summary") or finding.get("actual_behavior") or finding.get("actual")))
    module = _clean(business_impact.get("module") or finding.get("source_entity") or "未绑定业务域")

    if biz_summary:
        # 截断到合理长度
        if len(biz_summary) > 150:
            biz_summary = biz_summary[:147] + "..."
        return f"【{bug_status['label']}·{severity}】{module}：{biz_summary}"
    return f"【{bug_status['label']}·{severity}】{module}：缺少企业资料支持的业务影响结论"


def _build_test_summary(finding: dict, reproduction: dict, bug_status: dict) -> str:
    """生成一句话测试复现摘要。"""
    method = reproduction.get("method", "GET")
    path = reproduction.get("path", "")
    steps_count = len(reproduction.get("steps", []))
    is_synthetic = reproduction.get("is_synthetic", False)

    if bug_status.get("is_reproducible") and path and steps_count > 0 and not is_synthetic:
        return f"通过 {method} {path} 可复现，共 {steps_count} 步操作触发该问题（{bug_status['label']}）"
    elif path and steps_count > 0 and not is_synthetic:
        return f"涉及接口 {method} {path}，已有 {steps_count} 步复现步骤，状态：{bug_status['label']}"
    elif path:
        return f"涉及接口 {method} {path}，需补充真实复现步骤验证（{bug_status['label']}）"
    else:
        return f"缺少明确复现入口，需补充测试数据和执行步骤（{bug_status['label']}）"


def _build_dev_summary(finding: dict, investigation: dict, bug_status: dict) -> str:
    """生成一句话研发定位摘要。"""
    path = _clean(finding.get("_api_path") or finding.get("repro_path") or finding.get("path"))
    method = _clean(finding.get("_api_method") or finding.get("method") or "GET").upper()
    risk_type = _clean(finding.get("risk_type") or finding.get("category") or "unknown")
    trace_id = investigation.get("trace_id", "")
    tables = investigation.get("relevant_tables", [])

    parts = [f"{method} {path}"] if path else []
    if tables:
        parts.append(f"涉及表 {','.join(tables[:3])}")
    if trace_id:
        parts.append(f"TraceID {trace_id[:16]}")
    parts.append(risk_type)

    return f"{' · '.join(parts)}（{bug_status['label']}）"


def _build_expected_actual_comparison(finding: dict) -> dict:
    """构建预期 vs 实际结构化对比。"""
    expected = _strip_internal_tags(_clean(finding.get("expected_behavior") or finding.get("expected")))
    actual = _strip_internal_tags(_clean(finding.get("actual_behavior") or finding.get("actual") or finding.get("description")))
    db_ev = _extract_verified_db_evidence(finding)
    runtime_obs = _best_runtime_observation(finding)

    comparison = {
        "expected": expected or "未关联预期规则（需上传 PRD / API 规范）",
        "actual": actual or "未采集到实际行为",
        "difference": "",
        "db_comparison": None,
        "api_comparison": None,
    }

    # DB 对比
    if db_ev:
        comparison["db_comparison"] = {
            "table": db_ev.get("table", ""),
            "column": db_ev.get("column", ""),
            "expected": "符合业务约束",
            "actual": f"当前值 {db_ev.get('value', '')}",
            "violation": db_ev.get("violation", ""),
        }

    # API 对比
    status_code = _status_code_int(runtime_obs.get("status_code")) if runtime_obs else 0
    if status_code:
        comparison["api_comparison"] = {
            "expected": "2xx 成功响应",
            "actual": f"HTTP {status_code}",
            "response_body": _runtime_body_excerpt(runtime_obs.get("body"), 500),
            "duration_ms": runtime_obs.get("duration_ms") or 0,
            "source": runtime_obs.get("source") or "runtime",
        }

    # 差异描述
    if expected and actual:
        comparison["difference"] = f"预期：{expected[:100]} → 实际：{actual[:100]}"
    elif db_ev:
        comparison["difference"] = f"数据库约束违规：{db_ev.get('violation', '')}"
    elif status_code >= 400:
        comparison["difference"] = f"HTTP 响应异常：{status_code}"

    return comparison


# ═══════════════════════════════════════════════════════════════════════
# 单条 finding 格式化
# ═══════════════════════════════════════════════════════════════════════

def _format_single_finding(finding: dict, enterprise_ctx: dict | None = None) -> dict:
    from ._scoring import _build_raw_evidence, _compute_bug_status, _compute_commercial_value, _compute_reproducibility_confidence, _compute_scores, _enforce_evidence_gate, _extract_failed_assertions  # lazy: avoid circular import
    """格式化单条 finding 为 display-ready 结构"""

    # ── Stage 0: 输入标准化 ──
    finding = finding if isinstance(finding, dict) else {}
    ctx = enterprise_ctx or {}

    title = _clean(finding.get("title") or finding.get("bug_title") or finding.get("technical_title") or "未命名缺陷")
    # 翻译为企业可读中文标题
    title = _translate_title(title)
    # 如果有合并的 Oracle 标注，追加到标题
    merged_oracles = _clean(finding.get("_merged_oracles"))
    if merged_oracles:
        title = f"{title} {merged_oracles}"
    severity = _normalize_severity(finding.get("severity"))
    risk_type = _clean(finding.get("risk_type") or finding.get("category") or "unknown")
    status = _clean(finding.get("status") or finding.get("verdict") or finding.get("bug_confirmation"))
    status_lower = status.lower()
    confirmed = any(t in status_lower for t in ("confirmed", "validated", "reproduced", "reproducible"))

    repro_path = _clean(finding.get("_api_path") or finding.get("repro_path") or _deep_get(finding, "evidence", "path") or finding.get("path"))
    if _is_unresolved_path_value(repro_path):
        repro_path = ""
    repro_method = _clean(finding.get("_api_method") or finding.get("repro_method") or _deep_get(finding, "evidence", "method") or finding.get("method")).upper()
    if not repro_path:
        repro_method = ""

    # ── Stage 1: 子分析器 ──
    taxonomy = _build_taxonomy(finding)
    ui_verification_display = _ui_verification_display({**finding, **taxonomy})
    high_confidence_display = _high_confidence_candidate_display(finding)
    evidence_quality = _compute_evidence_quality(finding, repro_path)
    evidence_chain_data = _build_display_evidence_chain(finding)
    evidence_completeness = _compute_evidence_completeness(finding)
    reproduction = _build_repro_steps_display(finding, ctx)
    investigation = _build_investigation_display(finding)

    # ── Stage 2: 状态判定 ──
    bug_status = _compute_bug_status(finding, evidence_quality, evidence_completeness)
    bug_status = _enforce_evidence_gate(finding, bug_status, evidence_completeness)
    reproducibility_confidence = _compute_reproducibility_confidence(finding, bug_status, evidence_quality)

    # ── Stage 3: 影响范围与业务影响 ──
    affected_scope_parts: list[str] = []
    if _clean(finding.get("source_entity")):
        affected_scope_parts.append(_clean(finding.get("source_entity")))
    if repro_path:
        affected_scope_parts.append(f"接口 {repro_method} {repro_path}")
    if taxonomy.get("defect_family_label"):
        affected_scope_parts.append(taxonomy["defect_family_label"])
    affected_scope = "、".join(affected_scope_parts) if affected_scope_parts else "未绑定影响范围"

    # 业务影响
    business_impact_raw = finding.get("business_impact")
    if isinstance(business_impact_raw, dict):
        business_impact = {
            "summary": _strip_internal_tags(_clean(business_impact_raw.get("summary"))) or "缺少企业资料支持的业务影响结论。",
            "urgency": _clean(business_impact_raw.get("urgency")) or "待评估",
            "module": _clean(business_impact_raw.get("module")) or _clean(finding.get("source_entity")) or "未绑定业务域",
        }
    else:
        biz_text = _strip_internal_tags(_clean(business_impact_raw))
        business_impact = {
            "summary": biz_text or "缺少企业资料支持的业务影响结论。",
            "urgency": "待评估",
            "module": _clean(finding.get("source_entity")) or "未绑定业务域",
        }

    # ── Stage 4: 证据状态 ──
    evidence_status = {
        "raw_runtime_verdict": _clean(finding.get("raw_runtime_verdict")),
        "semantic_verdict": _clean(finding.get("semantic_verdict")),
        "business_evidence_status": _clean(finding.get("business_evidence_status")),
        "final_review_status": _clean(finding.get("final_review_status") or ("VALIDATED_CANDIDATE" if bug_status.get("status") == "reproduced" else "NEEDS_MORE_EVIDENCE")),
        "missing_requirements": finding.get("missing_requirements") if isinstance(finding.get("missing_requirements"), list) else [],
    }

    # ── Stage 5: 五层证据模型 ──
    failed_assertions = _extract_failed_assertions(finding, reproduction)
    raw_evidence = _build_raw_evidence(finding, reproduction)
    technical_details = _build_technical_details(finding, investigation, reproduction)
    business_summary = _build_business_summary(finding, business_impact, bug_status)
    test_summary = _build_test_summary(finding, reproduction, bug_status)
    dev_summary = _build_dev_summary(finding, investigation, bug_status)
    expected_actual_comparison = _build_expected_actual_comparison(finding)

    # 文档引用
    doc_refs = finding.get("_doc_refs") or finding.get("doc_refs") or []
    if not isinstance(doc_refs, list):
        doc_refs = []

    # 复现率：归一化为 0-100
    confidence_score = float(finding.get("confidence_score") or finding.get("score") or finding.get("confidence") or 0.5)
    if confidence_score <= 1:
        repro_rate = min(100, int(confidence_score * 100))
    elif confidence_score < 100:
        repro_rate = min(100, int(confidence_score))
    else:
        repro_rate = 100
    if confirmed:
        repro_rate = max(repro_rate, 100)
    if bug_status.get("status") != "reproduced":
        repro_rate = 0 if bug_status.get("status") in {"risk_clue", "not_reproduced"} else min(repro_rate, 69)

    # ── Stage 6: 标识符与 ID 去噪 ──
    # 判断 risk_id 是否是误填的标题（而非真正的唯一标识）：
    # - 太长（>40字符）
    # - 包含中文（标题有中文）
    # - 包含路径标签特征（[禁止路径] [状态破坏] 等）
    # - 与标题相同或相似
    _raw_title = _clean(finding.get("title"))
    risk_id = _clean(finding.get("risk_id") or finding.get("id"))
    _looks_like_title = (
        not risk_id or
        len(risk_id) > 40 or
        bool(re.search(r'[\u4e00-\u9fff]', risk_id)) or
        bool(re.search(r'\[(禁止|边界|正常|状态|场景)', risk_id)) or
        risk_id == _raw_title or
        (_raw_title and risk_id in _raw_title) or
        (risk_id and _raw_title in risk_id)
    )
    if _looks_like_title:
        import hashlib as _hashlib
        _title_hash = _hashlib.md5(title.encode("utf-8")).hexdigest()[:8]
        if repro_path:
            # method:path + title hash 保证唯一性（避免同接口多个 bug 共用 id）
            risk_id = f"{repro_method}:{repro_path}:{_title_hash}"
        else:
            risk_id = f"RISK-{_title_hash}"

    # 时间戳
    first_seen = _clean(finding.get("first_seen_at") or finding.get("created_at_utc") or finding.get("generated_at_utc"))
    last_verified = _clean(finding.get("last_verified_at") or finding.get("updated_at_utc") or finding.get("timestamp"))
    timestamp = last_verified or first_seen or ""

    # 复现次数
    repro_count = 1
    repro_data = finding.get("reproducibility")
    if isinstance(repro_data, dict) and repro_data.get("reproducible"):
        repro_count = round(repro_data.get("reproduction_confidence", 0) * 10) if repro_data.get("reproduction_confidence") else 5

    # ── Stage 7: 组装 display-ready payload ──

    payload = {
        "id": risk_id,
        "candidate_id": _clean(finding.get("candidate_id")),
        "slice_id": _clean(finding.get("slice_id")),
        "obligation_id": _clean(finding.get("obligation_id")),
        "experiment_id": _clean(finding.get("experiment_id")),
        "execution_id": _clean(finding.get("execution_id")),
        "evidence_id": _clean(finding.get("evidence_id")),
        "finding_id": _clean(finding.get("finding_id")),
        "title": title,
        "severity": severity,
        "risk_type": risk_type,
        "_summary_only": bool(finding.get("_summary_only")),
        "verdict": "confirmed" if bug_status.get("status") == "reproduced" else "pending",

        # ── 统一 Bug 状态（四态）──
        "bug_status": bug_status["status"],
        "bug_status_label": bug_status["label"],
        "bug_status_description": bug_status["description"],
        "is_reproducible": bug_status["is_reproducible"],
        "confidence": reproducibility_confidence,
        "affected_scope": affected_scope,
        "gate_passed": bug_status.get("gate_passed", False),
        "gate_failures": bug_status.get("gate_failures", []),

        "defect_family": taxonomy["defect_family"],
        "defect_family_label": taxonomy["defect_family_label"],
        "reporting_bucket": taxonomy["reporting_bucket"],
        "reporting_bucket_label": taxonomy["reporting_bucket_label"],
        "quality_assurance_gap": taxonomy["quality_assurance_gap"],
        "verification_badge": ui_verification_display["verification_badge"],
        "verification_label": ui_verification_display["verification_label"],
        "customer_evidence_label": ui_verification_display["customer_evidence_label"],
        "verification_rank": ui_verification_display["verification_rank"],
        "high_confidence_candidate": high_confidence_display["high_confidence_candidate"],
        "candidate_tier": high_confidence_display["candidate_tier"],
        "candidate_tier_label": high_confidence_display["candidate_tier_label"],

        "evidence_quality": evidence_quality,
        "evidence_chain": evidence_chain_data["full"],
        "evidence_chain_business": evidence_chain_data["business"],
        "evidence_chain_test": evidence_chain_data["test"],
        "evidence_chain_dev": evidence_chain_data["dev"],
        "evidence_completeness": evidence_completeness,
        "reproduction": reproduction,
        "business_impact": business_impact,
        "investigation_guidance": investigation,
        "evidence_status": evidence_status,
        "doc_refs": doc_refs,

        # ── 五层证据模型 ──
        "business_summary": business_summary,
        "test_summary": test_summary,
        "dev_summary": dev_summary,
        "expected_actual_comparison": expected_actual_comparison,
        "failed_assertions": failed_assertions,
        "raw_evidence": raw_evidence,
        "before_after_snapshot": finding.get("before_after_snapshot") if isinstance(finding.get("before_after_snapshot"), dict) else {},
        "business_invariant_evaluation": (
            finding.get("business_invariant_evaluation")
            if isinstance(finding.get("business_invariant_evaluation"), dict)
            else {}
        ),
        "db_evidence": finding.get("db_evidence") if isinstance(finding.get("db_evidence"), dict) else {},
        "technical_details": technical_details,
        "regression_verification_obligations": list(
            technical_details.get("regression_verification_obligations") or []
        ),

        # 受影响的业务实例列表（去重合并后，同类缺陷的多个实例）
        "affected_instances": finding.get("_affected_instances") if isinstance(finding.get("_affected_instances"), list) else [],
        "affected_count": int(finding.get("_affected_count") or 0),

        "timestamp": timestamp,
        "reproducibility_count": repro_count,
        "proof": {
            "hash": _clean(_deep_get(finding, "evidence", "hash")) or risk_id,
            "repro_rate": repro_rate,
        },

        # 保留原始关键字段供兼容
        # 保留原始关键字段供兼容，但清理内部技术标识符（[V12 XxxOracle] 等）
        "expected": _strip_internal_tags(_clean(finding.get("expected_behavior") or finding.get("expected"))),
        "actual": _strip_internal_tags(_clean(finding.get("actual_behavior") or finding.get("actual") or finding.get("description"))),
        "repro_method": repro_method,
        "repro_path": repro_path,
        "source_entity": _clean(finding.get("source_entity")),
        "source_value": _clean(finding.get("source_value")),
        "evidence_hint": _clean(finding.get("evidence_hint")),
    }
    return _finalize_display_finding_for_customer(payload, finding if isinstance(finding, dict) else {})


# ═══════════════════════════════════════════════════════════════════════
# 业务级去重 + 标题通用清理
# ═══════════════════════════════════════════════════════════════════════

# Oracle/引擎内部标识 → 中文（通用，非业务概念）
_ORACLE_CN = {
    "HttpStatusOracle": "HTTP状态码", "ErrorCodeOracle": "错误码",
    "RequiredFieldOracle": "必填字段", "SchemaOracle": "数据结构",
    "FieldTypeOracle": "字段类型", "DataIntegrityOracle": "数据完整性",
    "ConsistencyOracle": "一致性", "TransactionOracle": "事务",
    "CacheConsistencyOracle": "缓存一致性", "IdempotencyOracle": "幂等性",
    "StateTransitionOracle": "状态转移", "ConcurrencyOracle": "并发安全",
    "BusinessRuleOracle": "业务规则", "SecurityOracle": "安全",
    "PrivacyOracle": "隐私合规", "PerformanceOracle": "性能",
}

# 路径类型标签通用翻译（通用，非业务概念）
_PATH_TAG_CN = {
    "[禁止路径]": "[状态机违规]", "[边界路径]": "[边界条件]",
    "[正常路径]": "[正常流程]", "[状态破坏]": "[状态跳转违规]",
    "[异常路径]": "[异常场景]",
}


def _strip_internal_tags(text: str) -> str:
    """清理文本中的所有方括号内部标识符，保留业务内容。

    通用方案：移除所有 [xxx] 格式的方括号标签（1-20字符），
    因为方括号标签都是内部技术标识（引擎名/路径分类/验证来源等），
    企业用户不需要看。业务分类信息已通过 defect_family 字段结构化输出。
    """
    if not text:
        return ""
    # 移除 [V12 XxxOracle] 前缀（先处理，因为格式含空格）
    text = re.sub(r"\[V12\s+[^\]]*\]\s*", "", text)
    # 通用移除所有方括号标签（1-20字符的中文/英文/数字内容）
    # 覆盖：[DB]/[DB验证]/[权限]/[优惠券]/[资金]/[退款]/[购物车]/[数据隔离]/
    #       [并发]/[边界]/[报表]/[安全]/[前端]/[E2E]/[权限穿透]/[场景执行]/
    #       [禁止路径]/[资金安全]/[数据安全]/[状态机] 等所有内部标签
    text = re.sub(r"\[[^\]]{1,20}\]\s*", "", text)
    return text.strip()


def _translate_title(title: str) -> str:
    """
    通用标题清理：去除所有内部技术标识符，不硬编码任何业务术语。
    业务术语（如 order→订单、cancelled→已取消）取决于具体项目，
    不在此处翻译——应由 LLM 引擎在生成 finding 时直接用中文，
    或从企业资料动态学习术语词典。
    """
    if not title:
        return "未命名缺陷"

    text = title

    # 1. 先处理 DB 类标题（需要从 [DB] 标签提取表名/字段名/值）
    #    通用方案：提取业务字段信息（业务字段由 deep_verifier 动态发现）
    #    支持整数和小数值（-1, -6999.00）
    #    如果业务主键值是 UUID 格式，用前8位简写（用户看不懂完整UUID）
    _UUID_RE = r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
    db_match = re.match(r"^\[DB\]\s+(\w+)\.(\w+)为负:\s*(.+?)（值=(-?\d+\.?\d*)）\s*$", text)
    if db_match:
        table, col, biz_info, value = db_match.groups()
        # 如果 biz_info 是 uuid=value 格式，用前8位简写
        uuid_m = re.match(r'^([0-9a-fA-F]{8})-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}=(.+)$', biz_info)
        if uuid_m:
            biz_info = f"主键{uuid_m.group(1)}={uuid_m.group(2)}"
        elif re.match(_UUID_RE, biz_info.strip()):
            biz_info = f"主键{biz_info.strip()[:8]}"
        text = f"数据一致性: {table}.{col} 为负值（{biz_info}，当前值 {value}）"
    else:
        db_match2 = re.match(r"^\[DB\]\s+(\w+)\.(\w+)为负:\s*(.+)=(-?\d+\.?\d*)\s*$", text)
        if db_match2:
            table, col, biz_info, value = db_match2.groups()
            # biz_info 格式: uuid=value，提取 uuid 前8位
            uuid_m = re.match(r'^([0-9a-fA-F]{8})-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}=(.+)$', biz_info)
            if uuid_m:
                biz_info = f"主键{uuid_m.group(1)}={uuid_m.group(2)}"
            elif re.match(_UUID_RE, biz_info.strip()):
                biz_info = f"主键{biz_info.strip()[:8]}"
            text = f"数据一致性: {table}.{col} 为负值（{biz_info}，当前值 {value}）"

    # 2. 通用移除所有方括号标签（与 _strip_internal_tags 一致的通用方案）
    text = re.sub(r"\[V12\s+[^\]]*\]\s*", "", text)
    text = re.sub(r"\[[^\]]{1,20}\]\s*", "", text)

    # 3. 不做业务术语翻译——order→订单、cancelled→已取消等取决于具体项目
    #    只做通用技术术语清理
    general_tech_map = [
        (r"\boperationId\b", "接口标识"),
        (r"\bOpenAPI\b", "接口规范"),
        (r"\bidempotency\b", "幂等性", re.IGNORECASE),
        (r"\bIdempotency-Key\b", "幂等键"),
    ]
    for item in general_tech_map:
        pattern, cn = item[0], item[1]
        flags = item[2] if len(item) > 2 else 0
        text = re.sub(pattern, cn, text, flags=flags)

    return text


def _extract_business_key_for_dedupe(finding: dict) -> str:
    """提取业务级去重 key：去掉所有引擎/Oracle 前缀和路径标签后的核心描述 + API path。

    业务主键值（单引号包裹的ID、记录编号等）归一化为占位符，
    使同一业务缺陷的不同实例被识别为同一条。
    """
    title = _clean(finding.get("title") or finding.get("bug_title") or "")

    # 先用 _strip_internal_tags 清理所有方括号内部标签（通用，覆盖所有标签格式）
    core = _strip_internal_tags(title)

    # ── 业务主键值归一化（通用，不硬编码业务概念）──
    # 1. 单引号包裹的值：'RF1783157672785319' → '<ID>'
    core = re.sub(r"'[^']+'", "'<ID>'", core)
    # 2. 双引号包裹的值："RF1783157672785319" → "<ID>"
    core = re.sub(r'"[^"]+"', '"<ID>"', core)
    # 3. 标准 UUID 格式（通用 RFC 4122 格式）：4321e175-6ad3-4303-9cee-b8b9acd61ca9 → <ID>
    core = re.sub(r'\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b', '<ID>', core)
    # 4. 纯数字ID（独立出现的6位以上数字）：1783157672785319 → <ID>
    core = re.sub(r'\b\d{6,}\b', '<ID>', core)
    # 5. 常见业务编号格式（字母前缀+连字符+数字）：ORD-001, SKU-PHONE-001 → <ID>
    # 纯大写状态词（如 PENDING_PAYMENT）不是业务实例主键，不能在去重时折叠。
    def _normalize_prefixed_identifier(match: re.Match[str]) -> str:
        token = match.group(0)
        return '<ID>' if any(ch.isdigit() for ch in token) else token

    core = re.sub(r'\b[A-Z]{2,}[-_][A-Z0-9][-_A-Z0-9]*\b', _normalize_prefixed_identifier, core)
    # 6. =号后的值：amount=100, amount=-6999.00 → amount=<V>
    core = re.sub(r'=\s*\S+', '=<V>', core)

    # API path
    path = _clean(finding.get("_api_path") or finding.get("repro_path") or finding.get("path"))

    return f"{core.strip().lower()}|{path.lower()}"


_SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "critical": 0, "high": 1, "medium": 2, "low": 3}
_BUG_STATUS_RANK = {"reproduced": 0, "suspected": 1, "risk_clue": 2, "not_reproduced": 3}


def _verification_rank_value(item: dict[str, Any]) -> int:
    try:
        value = int(item.get("verification_rank") or 0)
        if value:
            return value
    except Exception:
        pass
    verification = item.get("ui_verification") if isinstance(item.get("ui_verification"), dict) else {}
    gate = item.get("ui_candidate_gate") if isinstance(item.get("ui_candidate_gate"), dict) else {}
    badge = _clean(item.get("verification_badge") or verification.get("status"))
    if badge in {"ui_verified", "verified"}:
        return 3
    if badge == "ui_candidate" or gate.get("passed") is True:
        return 2
    if badge == "ui_signal":
        return 1
    return 0


def _sort_display_findings(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def _severity_rank(item: dict[str, Any]) -> int:
        return _SEVERITY_RANK.get(str(item.get("severity", "P2")).lower(), 2)

    def _bug_status_rank(item: dict[str, Any]) -> int:
        return _BUG_STATUS_RANK.get(str(item.get("bug_status") or "").lower(), 4)

    def _confidence(item: dict[str, Any]) -> float:
        try:
            return float(item.get("confidence") or item.get("confidence_score") or 0.0)
        except Exception:
            return 0.0

    return sorted(
        [item for item in items if isinstance(item, dict)],
        key=lambda item: (
            _severity_rank(item),
            -_verification_rank_value(item),
            _bug_status_rank(item),
            -_confidence(item),
            _clean(item.get("title")).lower(),
        ),
    )


def _dedupe_by_business_semantics(risks: list[dict]) -> list[dict]:
    """
    业务级去重：同一业务缺陷被多个引擎/Oracle 重复报告时，只保留最高严重度的。

    通用逻辑：去掉所有引擎前缀和路径标签后，核心描述+API path 相同的视为同一缺陷。
    """
    if not risks:
        return []

    def severity_rank(item: dict) -> int:
        return _SEVERITY_RANK.get(str(item.get("severity", "P2")).lower(), 2)

    # 按业务 key 分组
    groups: dict[str, list[dict]] = {}
    for item in risks:
        if not isinstance(item, dict):
            continue
        key = _extract_business_key_for_dedupe(item)
        groups.setdefault(key, []).append(item)

    # 每组只保留最高严重度的（P0 < P1 < P2）
    deduped: list[dict] = []
    for group in groups.values():
        group.sort(
            key=lambda item: (
                severity_rank(item),
                -_verification_rank_value(item),
                -float(item.get("confidence_score") or item.get("confidence") or 0.0),
            )
        )
        best = group[0]

        # 如果有多个，合并它们的 evidence 信息到保留的那条
        if len(group) > 1:
            merged_engines = []
            all_evidence = best.get("evidence") if isinstance(best.get("evidence"), dict) else {}
            # 收集所有受影响的业务主键实例（通用，不硬编码业务概念）
            affected_instances: list[str] = []
            for item in group:
                # 收集引擎/Oracle 名称（通用正则匹配）
                title = _clean(item.get("title"))
                engine_match = re.search(r"\[V12\s+(\w+)\]", title)
                if engine_match:
                    merged_engines.append(engine_match.group(1))
                # 合并 evidence
                item_ev = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
                for k, v in item_ev.items():
                    if k not in all_evidence and v:
                        all_evidence[k] = v
                # 收集业务主键值（从 source_value、evidence.db_row、标题中的单引号值）
                sv = _clean(item.get("source_value"))
                if sv and not _looks_like_api_endpoint(sv):
                    affected_instances.append(sv)
                else:
                    db_row = item_ev.get("db_row") if isinstance(item_ev.get("db_row"), dict) else {}
                    for v in (db_row.values() if db_row else []):
                        v_str = _clean(v)
                        if v_str and not _looks_like_api_endpoint(v_str):
                            affected_instances.append(v_str)
                            break
                    if not affected_instances or not affected_instances[-1]:
                        # 从标题提取单引号包裹的值（通用）
                        quoted = re.findall(r"'([^']+)'", title)
                        if quoted:
                            affected_instances.append(quoted[0])
            if merged_engines or affected_instances:
                best = dict(best)  # 浅拷贝避免修改原始
                best["evidence"] = all_evidence
                if affected_instances:
                    # 去重保留受影响实例（最多20个，避免标题过长）
                    seen_inst = set()
                    unique_inst = []
                    for inst in affected_instances:
                        if inst and inst not in seen_inst:
                            seen_inst.add(inst)
                            unique_inst.append(inst)
                    best["_affected_instances"] = unique_inst[:20]
                    best["_affected_count"] = len(unique_inst)
                    if len(unique_inst) > 1:
                        inst_preview = "、".join(unique_inst[:3])
                        suffix = f"等{len(unique_inst)}个" if len(unique_inst) > 3 else ""
                        best["_merged_oracles"] = (best.get("_merged_oracles", "") + f"（影响{len(unique_inst)}个实例：{inst_preview}{suffix}）").strip()

        deduped.append(best)

    return _sort_display_findings(deduped)




def _safe_report_total(raw: dict | None) -> int:
    """Return raw candidate count from legacy report fields without changing display count."""
    raw = raw or {}
    candidates: list[Any] = [
        raw.get("risk_total"), raw.get("risk_count"), raw.get("total_findings"),
        raw.get("total_bugs_found"), raw.get("total_found"), raw.get("raw_total"),
    ]
    exec_summary = raw.get("executive_summary") if isinstance(raw.get("executive_summary"), dict) else {}
    candidates.extend([
        exec_summary.get("total_findings"), exec_summary.get("total_bugs_found"), exec_summary.get("total_found"),
    ])
    for value in candidates:
        try:
            number = int(float(value))
        except Exception:
            continue
        if number >= 0:
            return number
    return 0


def _build_display_contract(display_findings: list[dict], raw_report: dict | None = None) -> dict:
    """Build a single source-of-truth contract consumed by the frontend.

    This is the audit boundary: every number that implies customer-facing bugs must
    be derived from the already formatted ``risks`` list, not from raw/cached report
    totals. Raw report totals are kept only as candidate/reference counts.
    """
    statuses = {"reproduced": 0, "suspected": 0, "risk_clue": 0, "not_reproduced": 0}
    evidence_levels = {"validated": 0, "partial": 0, "suspected": 0, "needs_evidence": 0}
    severities = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}

    for item in display_findings:
        if not isinstance(item, dict):
            continue
        status = str(item.get("bug_status") or "risk_clue")
        statuses[status] = statuses.get(status, 0) + 1
        quality = item.get("evidence_quality") if isinstance(item.get("evidence_quality"), dict) else {}
        level = str(quality.get("level") or "needs_evidence")
        evidence_levels[level] = evidence_levels.get(level, 0) + 1
        severity = str(item.get("severity") or "P2")
        severities[severity] = severities.get(severity, 0) + 1

    ready_bug_count = sum(
        1 for item in display_findings
        if isinstance(item, dict) and item.get("bug_status") == "reproduced" and bool(item.get("gate_passed"))
    )
    needs_validation_count = statuses.get("suspected", 0) + statuses.get("risk_clue", 0)
    not_reproduced_count = statuses.get("not_reproduced", 0)
    materialized_count = len(display_findings)
    raw_candidate_count = max(_safe_report_total(raw_report), materialized_count)

    projection = []
    for item in display_findings:
        if not isinstance(item, dict):
            continue
        quality = item.get("evidence_quality") if isinstance(item.get("evidence_quality"), dict) else {}
        completeness = item.get("evidence_completeness") if isinstance(item.get("evidence_completeness"), dict) else {}
        projection.append({
            "id": item.get("id"),
            "title": item.get("title"),
            "severity": item.get("severity"),
            "bug_status": item.get("bug_status"),
            "gate_passed": bool(item.get("gate_passed")),
            "repro_method": item.get("repro_method"),
            "repro_path": item.get("repro_path"),
            "evidence_level": quality.get("level"),
            "evidence_score": quality.get("score"),
            "completeness_present": completeness.get("present_count"),
            "failed_assertion_count": len(item.get("failed_assertions") or []),
        })
    integrity_hash = hashlib.sha256(
        json.dumps(projection, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:24]

    return {
        "schema_version": "display-ready-v2026-07-05",
        "source_of_truth": "backend.display_ready_formatter.format_findings_display_ready",
        "display_key": "risks",
        "materialized_risk_count": materialized_count,
        "raw_candidate_risk_count": raw_candidate_count,
        "raw_to_display_delta": max(0, raw_candidate_count - materialized_count),
        "ready_bug_count": ready_bug_count,
        "needs_validation_count": needs_validation_count,
        "not_reproduced_count": not_reproduced_count,
        "status_counts": statuses,
        "evidence_level_counts": evidence_levels,
        "severity_counts": severities,
        "customer_delivery_mode": "只把 reproduced + gate_passed 作为已复现 Bug；suspected/risk_clue 作为待补证据线索；not_reproduced 不进入客户缺陷交付。",
        "integrity_hash": integrity_hash,
    }

# ═══════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════


def sanitize_customer_evidence_payload(value: Any) -> Any:
    """Recursively clean customer-facing evidence payloads on the current main path.

    This is a final response guard for stale cache or legacy rows that may already
    have been materialized before display-ready formatting was tightened. It is
    intentionally placed in display_ready_formatter instead of historical Phase104
    adapters, because the active commercial route is private_pilot_service ->
    format_findings_display_ready -> frontend/src/api/client.ts.
    """
    if isinstance(value, list):
        return [sanitize_customer_evidence_payload(item) for item in value]
    if not isinstance(value, dict):
        return value
    sanitized = {str(key): sanitize_customer_evidence_payload(item) for key, item in value.items()}
    if _looks_like_display_risk_payload(sanitized):
        return _sanitize_display_risk_payload(sanitized)
    return sanitized


def _looks_like_display_risk_payload(item: dict[str, Any]) -> bool:
    return (
        "title" in item
        and ("raw_evidence" in item or "reproduction" in item or "evidence_quality" in item)
        and ("bug_status" in item or "verdict" in item)
    )


def _sanitize_display_risk_payload(risk: dict[str, Any]) -> dict[str, Any]:
    raw_evidence = risk.get("raw_evidence") if isinstance(risk.get("raw_evidence"), dict) else {}
    response_raw = raw_evidence.get("response_raw") if isinstance(raw_evidence.get("response_raw"), dict) else {}
    if not response_raw:
        return risk

    request_raw = raw_evidence.get("request_raw") if isinstance(raw_evidence.get("request_raw"), dict) else {}
    obs = {
        "source": response_raw.get("source") or "runtime",
        "method": request_raw.get("method") or risk.get("repro_method"),
        "path": request_raw.get("path") or risk.get("repro_path"),
        "status_code": response_raw.get("status_code"),
        "body": response_raw.get("body"),
        "request_body": request_raw.get("body"),
        "duration_ms": response_raw.get("duration_ms") or 0,
        "actor": request_raw.get("actor"),
    }
    if _runtime_observation_supports_finding(risk, obs) and not _runtime_identity_mismatch_reasons(risk, obs):
        return risk

    cleaned = dict(risk)
    cleaned["bug_status"] = "not_reproduced"
    cleaned["bug_status_label"] = "未复现"
    cleaned["bug_status_description"] = EVIDENCE_RELEVANCE_FAILURE
    cleaned["verdict"] = "pending"
    cleaned["is_reproducible"] = False
    cleaned["gate_passed"] = False
    failures = [str(item) for item in cleaned.get("gate_failures") or [] if str(item)]
    if EVIDENCE_RELEVANCE_FAILURE not in failures:
        failures.append(EVIDENCE_RELEVANCE_FAILURE)
    cleaned["gate_failures"] = failures
    cleaned["confidence"] = 0.0

    cleaned_raw = dict(raw_evidence)
    cleaned_raw["response_raw"] = {}
    cleaned_raw["has_real_evidence"] = bool(
        cleaned_raw.get("db_snapshot")
        or cleaned_raw.get("logs")
        or cleaned_raw.get("execution_trace")
    )
    cleaned["raw_evidence"] = cleaned_raw

    reproduction = dict(cleaned.get("reproduction") or {})
    reproduction["har_evidence"] = None
    reproduction["is_synthetic"] = True
    reproduction["steps"] = [
        "当前运行响应与缺陷描述不匹配，已拒绝作为复现证据；请重新执行与该缺陷场景一致的复现步骤。"
    ]
    cleaned["reproduction"] = reproduction

    quality = dict(cleaned.get("evidence_quality") or {})
    quality["can_reproduce"] = False
    quality["score"] = min(int(quality.get("score") or 0), 40)
    quality["level"] = "needs_evidence"
    quality["label"] = "风险线索"
    quality["summary"] = EVIDENCE_RELEVANCE_FAILURE
    quality["verified"] = [
        item for item in quality.get("verified") or []
        if "接口响应" not in str(item) and "复现" not in str(item)
    ]
    missing = [str(item) for item in quality.get("missing") or [] if str(item)]
    if "缺少与缺陷描述匹配的真实接口响应" not in missing:
        missing.insert(0, "缺少与缺陷描述匹配的真实接口响应")
    quality["missing"] = missing[:6]
    cleaned["evidence_quality"] = quality

    comparison = dict(cleaned.get("expected_actual_comparison") or {})
    comparison["api_comparison"] = None
    cleaned["expected_actual_comparison"] = comparison
    cleaned["failed_assertions"] = [
        assertion for assertion in cleaned.get("failed_assertions") or []
        if isinstance(assertion, dict) and assertion.get("type") not in {"http_status_error", "response_error_field", "behavior_mismatch"}
    ]
    proof = dict(cleaned.get("proof") or {})
    proof["repro_rate"] = 0
    cleaned["proof"] = proof
    return cleaned

def format_findings_display_ready(
    risks: list[dict],
    enterprise_ctx: dict | None = None,
    raw_report: dict | None = None,
) -> tuple[list[dict], dict]:
    from ._scoring import _build_raw_evidence, _compute_bug_status, _compute_commercial_value, _compute_reproducibility_confidence, _compute_scores, _enforce_evidence_gate, _extract_failed_assertions  # lazy: avoid circular import
    """
    主入口：对统一汇聚后的 risks 列表做整体格式化。

    Args:
        risks: _build_command_center() 中统一汇聚+去重+HAR注入+证据富化后的 risks 列表
        enterprise_ctx: 企业上下文（base_url, test_email 等）
        raw_report: 原始报告数据（用于计算评分）

    Returns:
        (display_ready_risks, display_ready_metrics)
        - display_ready_risks: 格式化后的 finding 列表
        - display_ready_metrics: 包含 scores, commercial_value 等展示指标
    """
    if not isinstance(risks, list):
        risks = []

    # 业务级去重：同一业务缺陷被多个 Oracle/引擎重复报告时，只保留最高严重度的
    risks = _dedupe_by_business_semantics(risks)

    display_findings = [_format_single_finding(r, enterprise_ctx) for r in risks if isinstance(r, dict)]
    display_findings = _sort_display_findings(display_findings)

    scores = _compute_scores(display_findings, raw_report)
    commercial_value = _compute_commercial_value(display_findings, raw_report)

    display_contract = _build_display_contract(display_findings, raw_report)

    metrics = {
        "scores": scores,
        "commercial_value": commercial_value,
        "display_contract": display_contract,
    }
    return display_findings, metrics
