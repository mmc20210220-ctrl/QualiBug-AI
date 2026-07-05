"""
Display-Ready Formatter — 统一成果展示格式化引擎。

在 _build_command_center() 的 risks 列表统一汇聚完成后
（所有挖掘能力已 .extend() + 去重 + HAR注入 + 证据富化），
对统一的 risks 列表做整体格式化，输出前端零加工可渲染的 display-ready JSON。

所有函数处理 missing/partial 数据，输出保证有值有标签。
不区分挖掘来源，成果统一展示。
"""
from __future__ import annotations

import re
from typing import Any


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


def _infer_family_from_title(title: str, repro_path: str) -> str:
    """从标题推断缺陷族（fallback）"""
    text = (title or "").lower()
    if any(k in text for k in ("401", "403", "权限", "越权", "tenant", "idor", "鉴权")):
        return "security_boundary"
    if any(k in text for k in ("隐私", "合规", "脱敏", "敏感", "audit", "审计", "泄露")):
        return "privacy_compliance"
    if any(k in text for k in ("trace", "日志", "观测", "告警", "error code", "operationid")):
        return "observability"
    if any(k in text for k in ("配置", "环境", "开关", "部署", "config", "env")):
        return "configuration_drift"
    if any(k in text for k in ("db verified", "数据", "一致性", "幂等", "idempot", "integrity", "constraint", "约束")):
        return "data_integrity"
    if any(k in text for k in ("性能", "latency", "slow", "吞吐", "内存", "fanout")):
        return "performance"
    if any(k in text for k in ("timeout", "超时", "重试", "抖动", "间歇", "storm")):
        return "stability"
    if any(k in text for k in ("兼容", "compat", "版本")):
        return "compatibility"
    if any(k in text for k in ("本地化", "i18n", "locale", "timezone", "时区", "无障碍")):
        return "accessibility_i18n"
    if any(k in text for k in ("ui", "页面", "渲染", "route", "导航", "空白")):
        return "ui"
    if any(k in text for k in ("ux", "体验", "反馈", "cta", "交互", "可用性")):
        return "uiux"
    if repro_path or any(k in text for k in ("openapi", "schema", "contract", "spec")):
        return "api_contract"
    return "scenario_flow"


def _build_taxonomy(finding: dict) -> dict:
    """构建分类标签（从前端 resolveFindingTaxonomy 迁移）"""
    explicit_family = str(finding.get("defect_family") or "").strip()
    risk_type = str(finding.get("risk_type") or finding.get("category") or "").strip()
    title = str(finding.get("title") or "")
    repro_path = str(finding.get("_api_path") or finding.get("repro_path") or finding.get("path") or "")
    raw_bucket = str(finding.get("reporting_bucket") or "").strip()
    quality_gap = bool(finding.get("quality_assurance_gap"))

    if explicit_family in DEFECT_FAMILY_META:
        family = explicit_family
    elif risk_type in RISK_TYPE_TO_FAMILY:
        family = RISK_TYPE_TO_FAMILY[risk_type]
    else:
        family = _infer_family_from_title(title, repro_path)

    meta = DEFECT_FAMILY_META.get(family, DEFECT_FAMILY_META["scenario_flow"])
    return {
        "defect_family": family,
        "defect_family_label": meta["label"],
        "reporting_bucket": raw_bucket or meta["reporting_bucket"],
        "reporting_bucket_label": meta["bucket_label"],
        "quality_assurance_gap": quality_gap,
    }


# ═══════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════

def _clean(value: Any) -> str:
    return str(value or "").strip()


def _has_any_value(value: Any) -> bool:
    if isinstance(value, list):
        return any(_has_any_value(v) for v in value)
    if value and isinstance(value, dict):
        return any(_has_any_value(v) for v in value.values())
    return _clean(value).strip() != ""


def _normalize_severity(value: Any) -> str:
    v = str(value or "").lower()
    if v in ("critical", "p0"):
        return "P0"
    if v in ("high", "p1"):
        return "P1"
    return "P2"


# ═══════════════════════════════════════════════════════════════════════
# 证据质量评分（从前端 buildEvidenceQuality 迁移）
# ═══════════════════════════════════════════════════════════════════════

def _compute_evidence_quality(finding: dict, repro_path: str) -> dict:
    """计算证据质量评分、verified/missing 清单、curl 命令"""
    verified: list[str] = []
    missing: list[str] = []
    next_actions: list[str] = []

    method = _clean(finding.get("_api_method") or _deep_get(finding, "evidence", "method") or finding.get("method") or "GET").upper()
    has_api_target = bool(_clean(repro_path))
    has_actual = bool(_clean(finding.get("actual_behavior") or finding.get("actual") or finding.get("description")))
    has_expected = bool(_clean(finding.get("expected_behavior") or finding.get("expected")))
    doc_refs = finding.get("_doc_refs") or finding.get("doc_refs") or []
    has_docs = isinstance(doc_refs, list) and len(doc_refs) > 0
    has_db_signal = bool(_clean(finding.get("source_entity") or finding.get("source_value"))) or _has_any_value(_deep_get(finding, "investigation_guidance", "relevant_tables"))
    evidence_source_file = _clean(_deep_get(finding, "evidence", "source_file") or finding.get("source"))
    has_log_signal = bool(_clean(_deep_get(finding, "investigation_guidance", "log_search") or finding.get("evidence_hint") or evidence_source_file))

    status = _clean(finding.get("status") or finding.get("verdict") or finding.get("bug_confirmation")).lower()
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    has_runtime_proof = bool(
        _deep_get(finding, "reproducibility", "reproducible")
        or "confirmed" in status
        or "validated" in status
        or "reproduced" in status
        or _has_any_value(evidence.get("response"))
        or _has_any_value(evidence.get("responses"))
        or _has_any_value(evidence.get("status_code"))
        or _has_any_value(evidence.get("response_status"))
        or _has_any_value(evidence.get("source_file"))
    )
    # 注意：evidence.get("expected")/evidence.get("actual") 是文本描述字段，
    # 不代表真正执行过请求。只有 status_code/response/source_file/reproducibility
    # 才能证明运行时真实执行。

    # HAR 真实响应证据（状态码 + 响应体）
    has_api_response = bool(
        _deep_get(finding, "har_evidence", "status_code")
        or _deep_get(finding, "har_evidence", "response_body")
    )
    # 复现验证证据
    has_reproduction = bool(_deep_get(finding, "reproducibility", "reproducible"))

    # 失败断言证据（第10维度：需要真实异常信号，不是仅有 expected/actual 文本）
    # expected/actual 文本只是描述，不等于"已检测到失败断言"
    has_assertion = bool(
        _extract_db_evidence(finding)
        or (_deep_get(finding, "har_evidence", "status_code") or 0) >= 400
    )

    if has_api_target:
        verified.append(f"接口目标：{method} {repro_path}")
    else:
        missing.append("缺少可执行接口地址 / 页面地址")

    if has_runtime_proof:
        verified.append(f"存在运行时证据文件：{evidence_source_file}" if evidence_source_file else "存在运行时验证结果")
    else:
        missing.append("缺少真实请求响应、状态码或浏览器执行结果")

    if has_actual:
        verified.append("已记录实际行为")
    else:
        missing.append("缺少实际行为截图、响应体或异常日志")

    if has_expected:
        verified.append("已记录预期行为")
    else:
        missing.append("缺少来自 PRD / API 规范的预期规则")

    if has_db_signal:
        verified.append("存在业务数据核验线索")
    else:
        missing.append("缺少 DB 前后快照或业务主键")

    if has_docs:
        verified.append("已关联企业资料出处")
    else:
        missing.append("缺少 PRD / API / 业务规则文档出处")

    if has_log_signal:
        verified.append("存在日志检索线索")
    else:
        missing.append("缺少 traceId、时间窗口或日志关键词")

    if has_api_response:
        verified.append("已捕获真实接口响应（状态码/响应体）")
    else:
        missing.append("缺少真实接口响应状态码与响应体")

    if has_reproduction:
        verified.append("已通过复现验证")
    else:
        missing.append("缺少可重复执行的复现结果")

    if has_assertion:
        verified.append("已识别失败断言（预期/实际不一致或约束违规）")
    else:
        missing.append("缺少明确的失败断言（预期 vs 实际对比）")

    if not has_api_target:
        next_actions.append("在客户设置中配置可访问的测试地址，并重新执行扫描")
    if not has_runtime_proof:
        next_actions.append("补跑一次真实请求 / 浏览器用例，保存状态码、响应体、截图和时间戳")
    if not has_db_signal:
        next_actions.append("补充业务主键（如资源ID、记录编号等），并导出请求前后 DB 快照")
    if not has_docs:
        next_actions.append("上传 PRD、API 规范或验收规则，让缺陷结论能回链到需求出处")
    if not has_log_signal:
        next_actions.append("接入应用日志或 traceId，形成请求、日志、数据三方闭环")

    score = min(100, round(
        (10 if has_api_target else 0) +
        (15 if has_runtime_proof else 0) +
        (10 if has_actual else 0) +
        (10 if has_expected else 0) +
        (10 if has_db_signal else 0) +
        (8 if has_docs else 0) +
        (7 if has_log_signal else 0) +
        (15 if has_api_response else 0) +
        (10 if has_reproduction else 0) +
        (5 if has_assertion else 0)
    ))

    # 四级评分（与 Bug 状态对齐）
    if score >= 90 and has_runtime_proof:
        level = "validated"
        label = "可交付证据"
        summary = "证据完整，可直接提交研发修复。"
    elif score >= 70:
        level = "partial"
        label = "较完整证据"
        summary = "证据较完整，可人工确认后提交研发。"
    elif score >= 40:
        level = "suspected"
        label = "疑似问题"
        summary = "已有部分证据，但缺少关键复现证据或断言，需补充后才能作为已复现 Bug 交付。"
    else:
        level = "needs_evidence"
        label = "风险线索"
        summary = "当前更像检测线索，缺少真实复现、数据核验或文档出处，不能算 Bug。"

    curl_command = ""
    if has_api_target:
        curl_command = f'curl -X {method} "${{BASE_URL}}{repro_path}" -H "Content-Type: application/json" -v'

    return {
        "level": level,
        "score": score,
        "label": label,
        "summary": summary,
        "verified": verified,
        "missing": missing[:6],
        "next_actions": next_actions[:5],
        "can_reproduce": has_api_target and has_runtime_proof,
        "curl_command": curl_command,
    }


def _deep_get(d: dict, *keys, default: Any = None) -> Any:
    """安全嵌套取值"""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


# ═══════════════════════════════════════════════════════════════════════
# 证据链构建（从前端 buildEvidenceChain 迁移）
# ═══════════════════════════════════════════════════════════════════════

def _extract_db_evidence(finding: dict) -> dict | None:
    """从 finding 提取结构化 DB 证据（通用，不硬编码表名/字段名）。

    优先从 title 正则解析 deep_verifier 的输出格式，
    其次从 source_entity/source_value 提取。
    """
    title = _clean(finding.get("title"))
    # 匹配 [DB] table.col为负: biz_info（值=-1）格式（deep_verifier 输出）
    m = re.match(r"^\[DB\]\s+(\w+)\.(\w+)为负:\s*(.+?)（值=(-?\d+)）\s*$", title)
    if not m:
        # 兼容旧格式：[DB] table.col为负: key=value
        m = re.match(r"^\[DB\]\s+(\w+)\.(\w+)为负:\s*(.+)=(-?\d+)\s*$", title)
    if m:
        table, col, biz_info, value = m.groups()
        return {
            "table": table,
            "column": col,
            "business_key": biz_info,
            "value": value,
            "violation": "字段值为负，违反业务约束",
            "raw": title,
        }

    # 从 source_entity + source_value 提取
    entity = _clean(finding.get("source_entity"))
    value = _clean(finding.get("source_value"))
    if entity and value and not _looks_like_api_endpoint(value):
        return {
            "table": entity,
            "column": "",
            "business_key": value,
            "value": "",
            "violation": _strip_internal_tags(_clean(finding.get("actual_behavior") or finding.get("actual") or "")),
            "raw": title,
        }
    return None


def _extract_har_response_evidence(finding: dict) -> dict | None:
    """从 har_evidence 提取结构化请求响应证据（通用，不硬编码业务概念）。"""
    har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
    status_code = har.get("status_code") or 0
    response_body = _clean(har.get("response_body"))
    actor = _clean(har.get("actor"))
    duration_ms = har.get("duration_ms") or 0
    method = _clean(har.get("method") or finding.get("_api_method") or finding.get("method") or "GET").upper()
    path = _clean(har.get("path") or finding.get("_api_path") or finding.get("path"))

    if not (status_code or response_body or path):
        return None
    return {
        "method": method or "GET",
        "path": path,
        "status_code": status_code,
        "response_body": response_body[:1000],
        "actor": actor,
        "duration_ms": duration_ms,
    }


def _compute_evidence_completeness(finding: dict) -> dict:
    """计算 6 维度证据完备度，供企业用户直观判断证据是否齐全。

    维度：规则来源 / 接口请求 / 接口响应 / 数据核验 / 日志追溯 / 复现验证
    """
    har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
    inv = finding.get("investigation_guidance") if isinstance(finding.get("investigation_guidance"), dict) else {}
    doc_refs = finding.get("_doc_refs") or finding.get("doc_refs") or []
    has_docs = isinstance(doc_refs, list) and len(doc_refs) > 0
    path = _clean(finding.get("_api_path") or finding.get("repro_path") or finding.get("path"))

    db_ev = _extract_db_evidence(finding)
    har_ev = _extract_har_response_evidence(finding)

    trace_id = _clean(inv.get("trace_id")) or _clean(_deep_get(finding, "evidence", "trace_id"))

    repro_data = finding.get("reproducibility")
    has_repro = isinstance(repro_data, dict) and repro_data.get("reproducible")

    has_expected = bool(_clean(finding.get("expected_behavior") or finding.get("expected")))

    dimensions = [
        {
            "key": "rule_source",
            "label": "规则来源",
            "present": has_docs or has_expected,
            "detail": "PRD / API 规范 / 业务规则文档",
        },
        {
            "key": "api_request",
            "label": "接口请求",
            "present": bool(path),
            "detail": "可执行的接口地址",
        },
        {
            "key": "api_response",
            "label": "接口响应",
            "present": bool(har_ev and (har_ev.get("status_code") or har_ev.get("response_body"))),
            "detail": "真实状态码与响应体",
        },
        {
            "key": "db_evidence",
            "label": "数据核验",
            "present": bool(db_ev or inv.get("sql_verify")),
            "detail": "数据库快照或 SQL 核验",
        },
        {
            "key": "log_evidence",
            "label": "日志追溯",
            "present": bool(trace_id or inv.get("log_search")),
            "detail": "TraceID 或日志检索线索",
        },
        {
            "key": "reproduction",
            "label": "复现验证",
            "present": bool(has_repro or (har_ev and (har_ev.get("status_code") or 0) >= 400)),
            "detail": "可重复执行的复现结果（需触发异常响应或明确复现标记）",
        },
    ]

    present_count = sum(1 for d in dimensions if d["present"])
    score = round(present_count / len(dimensions) * 100)

    return {
        "score": score,
        "present_count": present_count,
        "total": len(dimensions),
        "dimensions": dimensions,
    }


def _build_display_evidence_chain(finding: dict) -> dict:
    """构建多源数据驱动的企业级证据链。

    从文档/HAR/DB/日志/复现多源条件式提取真实数据，
    每步标注来源(source)、可信度(confidence)、时间戳(timestamp)，
    并生成三视角预过滤链（business/test/dev）。
    """
    chain: list[dict] = []

    method = _clean(finding.get("_api_method") or _deep_get(finding, "evidence", "method") or finding.get("method") or "GET").upper()
    path = _clean(finding.get("_api_path") or _deep_get(finding, "evidence", "path") or finding.get("path"))
    source_file = _clean(_deep_get(finding, "evidence", "source_file") or finding.get("source"))

    doc_refs = finding.get("_doc_refs") or finding.get("doc_refs") or []
    doc_name = ""
    if isinstance(doc_refs, list) and doc_refs:
        first = doc_refs[0] if isinstance(doc_refs[0], dict) else {}
        doc_name = _clean(first.get("display_name") or first.get("source_id"))

    timestamp = _clean(finding.get("last_verified_at") or finding.get("timestamp") or finding.get("first_seen_at"))
    inv = finding.get("investigation_guidance") if isinstance(finding.get("investigation_guidance"), dict) else {}

    # 1. 规则来源
    rule_content = doc_name or finding.get("business_rule_source") or finding.get("source") or "系统行为模型 / 企业资料"
    rule_detail = _strip_internal_tags(_clean(finding.get("expected_behavior") or finding.get("expected"))) or "缺少明确预期规则时，将标记为待补强证据。"
    chain.append({
        "tag": "rule",
        "label": "规则来源",
        "content": rule_content,
        "detail": rule_detail,
        "source": "document" if doc_name else "engine",
        "confidence": "high" if doc_name else "medium",
        "timestamp": timestamp,
    })

    # 2. 触发请求（接口地址）
    if path:
        chain.append({
            "tag": "api",
            "label": "触发请求",
            "content": f"{method or 'GET'} {path}",
            "detail": _strip_internal_tags(_clean(finding.get("evidence_hint"))) or "按该接口回放请求，记录参数、状态码、响应体和时间戳。",
            "source": "har" if finding.get("har_evidence") else "engine",
            "confidence": "high",
            "timestamp": timestamp,
        })

    # 3. 接口响应（从 HAR 提取真实状态码/响应体/耗时）
    har_ev = _extract_har_response_evidence(finding)
    if har_ev and (har_ev.get("status_code") or har_ev.get("response_body")):
        status = har_ev.get("status_code") or 0
        body = har_ev.get("response_body") or ""
        actor = har_ev.get("actor") or ""
        duration = har_ev.get("duration_ms") or 0
        # 状态码语义化（通用 HTTP 标准，非业务概念）
        status_label = ""
        if status >= 500:
            status_label = "（服务端错误）"
        elif status >= 400:
            status_label = "（客户端错误）"
        elif status >= 300:
            status_label = "（重定向）"
        elif status >= 200:
            status_label = "（成功）"

        content_parts = [f"状态码 {status}{status_label}"]
        if duration:
            content_parts.append(f"耗时 {duration}ms")
        if actor:
            content_parts.append(f"操作者 {actor}")

        detail = f"响应体摘要：{body[:200]}" if body else "未捕获响应体"
        chain.append({
            "tag": "response",
            "label": "接口响应",
            "content": " · ".join(content_parts),
            "detail": detail,
            "source": "har",
            "confidence": "high",
            "timestamp": timestamp,
            "structured": {
                "status_code": status,
                "response_body": body,
                "actor": actor,
                "duration_ms": duration,
            },
        })

    # 4. 实际结果（文本描述）
    actual_text = _strip_internal_tags(_clean(finding.get("actual_behavior") or finding.get("actual") or finding.get("description")))
    if actual_text:
        chain.append({
            "tag": "fact",
            "label": "实际结果",
            "content": actual_text,
            "detail": f"证据文件：{source_file}" if source_file else (_clean(finding.get("risk_type")) or ""),
            "source": "engine",
            "confidence": "medium",
            "timestamp": timestamp,
        })

    # 5. DB 数据核验（结构化）
    db_ev = _extract_db_evidence(finding)
    if db_ev or inv.get("sql_verify"):
        if db_ev:
            table = db_ev.get("table", "")
            col = db_ev.get("column", "")
            biz_key = db_ev.get("business_key", "")
            value = db_ev.get("value", "")
            violation = db_ev.get("violation", "")
            content = f"表 {table}"
            if col:
                content += f".{col}"
            if value:
                content += f" 当前值 {value}"
            if biz_key:
                content += f"（{biz_key}）"
            detail = violation or "数据库字段值违反业务约束"
            structured = {
                "table": table,
                "column": col,
                "business_key": biz_key,
                "value": value,
                "violation": violation,
            }
        else:
            content = "已生成 SQL 核验语句"
            detail = _clean(inv.get("sql_verify"))[:200]
            structured = None

        chain.append({
            "tag": "db",
            "label": "数据核验",
            "content": content,
            "detail": detail,
            "source": "db",
            "confidence": "high",
            "timestamp": timestamp,
            "structured": structured,
        })

    # 6. 日志追溯（TraceID）
    trace_id = _clean(inv.get("trace_id"))
    if trace_id:
        chain.append({
            "tag": "log",
            "label": "日志追溯",
            "content": f"TraceID: {trace_id}",
            "detail": "在企业日志系统中搜索此 TraceID 可定位完整请求链路与异常堆栈。",
            "source": "log",
            "confidence": "high",
            "timestamp": timestamp,
            "structured": {"trace_id": trace_id},
        })

    # 7. 缺陷判定
    severity = finding.get("severity") or "P2"
    risk_type = finding.get("risk_type") or finding.get("category") or "待分类"
    verdict = _clean(finding.get("bug_confirmation") or finding.get("validation_verdict") or finding.get("verdict") or "pending")
    chain.append({
        "tag": "judgment",
        "label": "缺陷判定",
        "content": f"{severity} · {risk_type}",
        "detail": verdict,
        "source": "engine",
        "confidence": "high",
        "timestamp": timestamp,
    })

    # 三视角预过滤链
    business_chain = _filter_chain_for_business(chain)
    test_chain = _filter_chain_for_test(chain)
    dev_chain = chain  # 研发视角看完整链

    return {
        "full": chain,
        "business": business_chain,
        "test": test_chain,
        "dev": dev_chain,
    }


def _filter_chain_for_business(chain: list[dict]) -> list[dict]:
    """业务视角：只看规则来源、接口响应(简略)、数据核验(简略)、缺陷判定。"""
    keep_tags = {"rule", "response", "db", "judgment"}
    filtered = []
    for step in chain:
        if step.get("tag") in keep_tags:
            biz_step = dict(step)
            if step.get("tag") == "response":
                biz_step["detail"] = ""  # 业务领导不看响应体摘要
            if step.get("tag") == "db" and step.get("structured"):
                s = step["structured"]
                biz_step["detail"] = s.get("violation", "")
            filtered.append(biz_step)
    return filtered


def _filter_chain_for_test(chain: list[dict]) -> list[dict]:
    """测试视角：看规则来源、触发请求、接口响应、实际结果、数据核验、缺陷判定。"""
    keep_tags = {"rule", "api", "response", "fact", "db", "judgment"}
    return [step for step in chain if step.get("tag") in keep_tags]


# ═══════════════════════════════════════════════════════════════════════
# 复现步骤展示（基于 HAR 真实数据，非前端编造）
# ═══════════════════════════════════════════════════════════════════════

def _build_repro_steps_display(finding: dict, enterprise_ctx: dict | None = None) -> dict:
    """构建复现信息展示（基于 HAR 真实数据）"""
    ctx = enterprise_ctx or {}
    har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
    finding_path = finding.get("_api_path") or finding.get("repro_path") or ""
    finding_method = (finding.get("_api_method") or finding.get("repro_method") or "").upper()
    har_path = har.get("path") or ""
    har_method = (har.get("method") or "").upper()

    # 优先用 finding 自己的 path（更精确）
    if finding_path and har_path and finding_path != har_path and len(finding_path) > len(har_path):
        path = finding_path
    elif har_path:
        path = har_path
    else:
        path = finding_path
    method = finding_method or har_method

    # 复现步骤（优先用已有的 reproduction_steps）
    real_steps = finding.get("reproduction_steps") or finding.get("reproduce_steps_business") or []
    if not isinstance(real_steps, list):
        real_steps = []
    real_steps = [str(s) for s in real_steps if s]

    # 如果有真实步骤，用真实步骤；否则用合成指引（但标记为 synthetic）
    is_synthetic = False
    if real_steps:
        steps = real_steps
    else:
        steps = _generate_default_repro_steps(finding, path, method, ctx)
        is_synthetic = True  # 标记为合成指引，非真实执行步骤

    curl_command = ""
    if path:
        base_url = ctx.get("base_url", "")
        full_url = f"{base_url}{path}" if path.startswith("/") and base_url else (path if path.startswith("http") else f"${{BASE_URL}}{path}")
        body_part = ""
        if method in ("POST", "PUT", "PATCH"):
            body_part = ' -H "Content-Type: application/json" -d \'{"...":"根据业务场景填写"}\''
        curl_command = f'curl -X {method or "GET"} "{full_url}" -H "Authorization: Bearer <TOKEN>"{body_part} -v'

    har_evidence_out = None
    if har:
        har_evidence_out = {
            "status_code": har.get("status_code") or 0,
            "response_body": str(har.get("response_body") or "")[:2000],
            "actor": har.get("actor") or "",
            "duration_ms": har.get("duration_ms") or 0,
        }

    return {
        "method": method or "GET",
        "path": path,
        "steps": steps,
        "is_synthetic": is_synthetic,
        "curl_command": curl_command,
        "har_evidence": har_evidence_out,
    }


def _generate_default_repro_steps(finding: dict, path: str, method: str, ctx: dict) -> list[str]:
    """生成默认复现步骤指引（当后端没有真实步骤时的 fallback）。

    注意：这些是"建议操作指引"，不是"已执行的真实复现步骤"。
    调用方应通过 is_synthetic=True 标记，前端据此区分"真实复现步骤"与"建议指引"。
    """
    # 如果有 HAR 真实响应，说明请求确实执行过，可以基于真实数据生成指引
    har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
    has_real_response = bool(har.get("status_code") or har.get("response_body"))

    if path and has_real_response:
        # 有真实响应 → 生成基于实际执行的指引（但仍标记为 synthetic）
        status = har.get("status_code") or 0
        return [
            f"[指引] 已执行 {method or 'GET'} {path}，实际响应 {status}。请对比响应与预期规则。",
            f"[指引] 预期规则：{_strip_internal_tags(_clean(finding.get('expected_behavior') or finding.get('expected'))) or '需上传企业资料明确预期'}",
            f"[指引] 实际结果：{_strip_internal_tags(_clean(finding.get('actual_behavior') or finding.get('actual'))) or '需记录系统实际响应'}",
        ]
    elif path:
        # 有接口地址但无真实响应 → 只能建议执行，不能声称已执行
        return [
            f"[指引] 建议执行 {method or 'GET'} {path}，记录请求参数、响应状态码、响应体、时间戳",
            f"[指引] 预期规则：{_strip_internal_tags(_clean(finding.get('expected_behavior') or finding.get('expected'))) or '需上传企业资料明确预期'}",
            f"[指引] 实际结果：{_strip_internal_tags(_clean(finding.get('actual_behavior') or finding.get('actual'))) or '执行后记录系统实际响应'}",
        ]
    # 无接口地址 → 无法生成有意义的指引
    return []


# ═══════════════════════════════════════════════════════════════════════
# 排查指引（通用方案：不硬编码表名/字段名，从 finding 数据动态获取）
# ═══════════════════════════════════════════════════════════════════════


def _looks_like_api_endpoint(value: str) -> bool:
    """判断值是否像接口路径而非业务主键（如 'POST /api/orders'、'/api/users/123'）"""
    if not value:
        return False
    v = value.strip()
    # 包含 HTTP method 前缀
    if re.match(r"^(GET|POST|PUT|PATCH|DELETE)\s", v, re.IGNORECASE):
        return True
    # 包含 /api/ 路径
    if "/api/" in v or v.startswith("/"):
        return True
    return False


def _extract_business_keys(finding: dict) -> list[tuple[str, str]]:
    """
    通用业务主键提取：从 finding 数据的多个位置动态提取业务主键。
    不硬编码任何业务概念（如 order_id/sku/email），
    而是从 source_value、API path 参数、evidence 请求体中动态发现。
    """
    keys: list[tuple[str, str]] = []

    # 1. 从 source_value 提取（通用，任何项目都有）
    source_value = _clean(finding.get("source_value"))
    # 排除接口路径误填为 source_value 的情况（如 "POST /api/orders"）
    if source_value and not _looks_like_api_endpoint(source_value):
        source_entity = _clean(finding.get("source_entity"))
        key_name = f"{source_entity}_id" if source_entity else "source_value"
        keys.append((key_name, source_value))

    # 2. 从 source_entity 提取
    source_entity = _clean(finding.get("source_entity"))
    if source_entity:
        keys.append(("source_entity", source_entity))

    # 3. 从 API path 提取路径参数（通用：路径段中像 ID 的段）
    path = _clean(finding.get("_api_path") or finding.get("repro_path") or finding.get("path"))
    if path:
        segments = [s for s in path.split("/") if s and s not in ("api", "v1", "v2", "admin", "public")]
        for i, seg in enumerate(segments):
            # 跳过纯单词段（可能是资源名），只取像主键的段（含数字或连字符）
            if i > 0 and seg and not seg.startswith("{") and any(c.isdigit() or c == "-" for c in seg):
                prev = segments[i - 1].lower().rstrip("s")  # 去复数
                keys.append((f"{prev}_id", seg))

    # 4. 从 evidence 请求体动态提取所有字段（通用，不硬编码字段名）
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    for field in ("email", "phone", "sku", "order_id", "user_id", "payment_id",
                  "refund_id", "coupon_code", "invoice_id", "contract_id",
                  "ticket_id", "account_id", "product_id"):
        val = _clean(evidence.get(field) or _deep_get(finding, "evidence", "request_body", field))
        if val:
            keys.append((field, val))

    # 5. 从 HAR evidence 请求体动态提取（通用）
    har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
    if har.get("request_body"):
        import json as _json
        try:
            body = _json.loads(har["request_body"]) if isinstance(har["request_body"], str) else har["request_body"]
            if isinstance(body, dict):
                # 动态提取所有看起来像主键的字段（通用：含 _id 后缀或常见主键名）
                for field, val in body.items():
                    if any(kw in field.lower() for kw in ("_id", "id", "email", "phone", "sku", "code", "number")):
                        val_str = _clean(val)
                        if val_str:
                            keys.append((field, val_str))
        except Exception:
            pass

    # 6. 从 title/description 中用通用正则提取常见主键格式（通用，不硬编码具体前缀）
    title = _clean(finding.get("title"))
    actual = _clean(finding.get("actual_behavior") or finding.get("actual") or finding.get("description"))
    full_text = f"{title} {actual}"
    # 通用 ID 格式：大写字母前缀+连字符+数字（如 ORD-001, SKU-PHONE-001, USR-123）
    for m in re.finditer(r"\b[A-Z]{2,}[-_][A-Z0-9][-_A-Z0-9]*\b", full_text):
        keys.append(("entity_id", m.group()))
    # 邮箱（通用格式）
    for m in re.finditer(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", full_text):
        keys.append(("email", m.group()))

    # 去重
    seen = set()
    unique = []
    for name, val in keys:
        key = f"{name}={val}"
        if key not in seen and val:
            seen.add(key)
            unique.append((name, val))
    return unique


def _infer_tables_from_finding(finding: dict) -> list[str]:
    """
    通用表名推断：从 finding 的 source_entity、investigation_guidance.relevant_tables、
    API path 动态获取相关表名。不硬编码任何表名映射。
    """
    tables: list[str] = []
    # 用于去重的单数形式集合
    seen_singular: set[str] = set()

    def _add_table(name: str):
        name = name.strip().lower()
        if not name or len(name) < 2:
            return
        singular = name.rstrip("s")  # 去复数
        # 如果单数形式已在集合中，跳过（避免 contracts 和 contract 重复）
        if singular in seen_singular:
            return
        seen_singular.add(singular)
        tables.append(name)

    # 1. 从 source_entity 获取（最直接）
    source_entity = _clean(finding.get("source_entity"))
    if source_entity:
        _add_table(source_entity)

    # 2. 从 investigation_guidance.relevant_tables 获取（后端已有）
    inv = finding.get("investigation_guidance") if isinstance(finding.get("investigation_guidance"), dict) else {}
    for t in (inv.get("relevant_tables") or []):
        _add_table(_clean(t))

    # 3. 从 API path 资源段推断表名（通用：path 段 → 表名）
    path = _clean(finding.get("_api_path") or finding.get("repro_path") or finding.get("path"))
    if path:
        segments = [s for s in path.split("/") if s and s not in ("api", "v1", "v2", "admin", "public")]
        for seg in segments:
            # 只取纯字母段作为资源名/表名，跳过 ID 段（含数字/连字符/@/./_等）
            if seg and seg.isalpha() and not seg.startswith("{") and len(seg) > 2:
                _add_table(seg)

    return tables[:5]  # 最多 5 张表


def _build_specific_sql(finding: dict, tables: list[str], business_keys: list[tuple[str, str]], entity: str = "") -> str:
    """
    通用 SQL 核验语句生成：基于 finding 动态提取的表名和业务主键生成 SQL。
    不硬编码任何表结构——如果没有表名信息，生成通用模板。
    """
    if not tables:
        return (
            "-- 当前缺少业务主键或表字段，无法形成可审计 SQL 证据\n"
            "-- 请补充业务主键（如资源ID、记录编号等），并导出请求前后 DB 快照"
        )

    lines: list[str] = []
    lines.append("-- ═══ 企业核验 SQL（基于缺陷业务主键生成）═══")
    lines.append(f"-- 缺陷：{_clean(finding.get('title'))[:80]}")
    lines.append(f"-- 严重度：{_clean(finding.get('severity')) or 'P2'}")
    if business_keys:
        lines.append(f"-- 业务主键：{', '.join(f'{k}={v}' for k, v in business_keys[:5])}")
    lines.append("")

    for table in tables[:3]:
        lines.append(f"-- ── 表 {table} ──")

        # 通用主键列名推断（不硬编码，用常见主键名模式）
        pk_col = "id"  # 默认主键列名

        # 将业务主键匹配到当前表的 WHERE 条件（通用逻辑）
        where_clauses: list[str] = []
        for key_name, key_value in business_keys:
            if key_name == "source_entity":
                continue  # source_entity 是表名不是值
            # {entity}_id 格式的主键匹配表的主键列
            if key_name == f"{table}_id":
                where_clauses.append(f"{pk_col} = '{key_value}'")
            # source_value 只匹配 source_entity 对应表的主键
            elif key_name == f"{entity}_id" and entity == table:
                where_clauses.append(f"{pk_col} = '{key_value}'")
            # 通用：key_name 本身就是列名（如 email, phone, sku, order_id 等）
            elif key_name in ("email", "phone", "sku", "code"):
                where_clauses.append(f"{key_name} = '{key_value}'")
            # 通用：key_name 以 _id 结尾，可能是外键
            elif key_name.endswith("_id") and key_name != f"{table}_id":
                # 如果 key_name 是 table 的外键（如 orders 表的 user_id）
                where_clauses.append(f"{key_name} = '{key_value}'")
            # 通用：entity_id 格式的主键
            elif key_name == "entity_id" and entity == table:
                where_clauses.append(f"{pk_col} = '{key_value}'")

        # 去重 WHERE 条件（按值去重：相同值只保留第一个条件）
        seen_where_values: set[str] = set()
        unique_where: list[str] = []
        for w in where_clauses:
            # 提取条件中的值部分用于去重（如 id = 'CT-001' → CT-001）
            val_match = re.search(r"=\s*'([^']*)'", w)
            val = val_match.group(1) if val_match else w
            if val not in seen_where_values:
                seen_where_values.add(val)
                unique_where.append(w)

        if unique_where:
            where = " AND ".join(unique_where[:2])
            lines.append(f"-- 请求前快照：")
            lines.append(f"SELECT * FROM {table} WHERE {where};")
            lines.append(f"-- 请求后快照（复现动作执行后再次查询，对比差异）：")
            lines.append(f"SELECT * FROM {table} WHERE {where};")
        else:
            # 没有匹配主键，给出通用查询模板
            lines.append(f"-- 未匹配到精确主键，请替换 <主键值> 后执行：")
            lines.append(f"SELECT * FROM {table} WHERE {pk_col} = '<主键值>';")

        lines.append("")

    lines.append("-- ═══ 核验要点 ═══")
    lines.append("-- 1. 对比请求前后的状态/金额/数量等关键字段是否发生预期外变化")
    lines.append("-- 2. 检查数据守恒：关联金额、数量是否一致")
    lines.append("-- 3. 检查状态流转：是否符合业务规则定义的状态机")
    lines.append("-- 4. 检查权限归属：数据是否归属于正确的用户/租户")

    return "\n".join(lines)


def _build_investigation_display(finding: dict) -> dict:
    """构建排查指引展示——生成具体 SQL 而非通用模板"""
    inv = finding.get("investigation_guidance") if isinstance(finding.get("investigation_guidance"), dict) else {}
    entity = _clean(finding.get("source_entity"))
    path = _clean(finding.get("_api_path") or finding.get("repro_path") or finding.get("path"))
    method = _clean(finding.get("_api_method") or finding.get("repro_method") or "GET").upper()
    primary_area = inv.get("primary_area") or entity or ""

    # 提取业务主键和推断相关表
    business_keys = _extract_business_keys(finding)
    tables = _infer_tables_from_finding(finding)

    # SQL 核验建议：优先用已有的，否则生成具体 SQL
    if inv.get("sql_verify") and not business_keys:
        sql_verify = inv["sql_verify"]
    else:
        sql_verify = _build_specific_sql(finding, tables, business_keys, entity)

    # 日志排查建议
    if inv.get("log_search"):
        log_search = inv["log_search"]
    elif path:
        log_search = (
            f"# 按接口路径检索相关日志\n"
            f"# 关键词：{method} {path}\n"
            "# 必须补齐：请求时间窗口、traceId / requestId、状态码、业务主键\n"
            "# 与响应体、DB 快照交叉验证后再标记为已验证缺陷"
        )
    else:
        log_search = (
            "# 当前缺少可检索接口路径或页面地址\n"
            "# 请先补跑真实请求 / 浏览器用例，记录时间戳、traceId、状态码和错误摘要"
        )

    relevant_apis = inv.get("relevant_apis") or ([f"{method} {path}"] if path else [])
    relevant_tables = tables or (inv.get("relevant_tables") or [])

    # TraceID 提取：从多个位置动态获取（通用，不硬编码）
    # 只提取真实的 traceId/requestId，不要 fallback 到 risk_id（那是标题不是 traceId）
    trace_id = _clean(inv.get("trace_id"))
    if not trace_id:
        trace_id = _clean(_deep_get(finding, "evidence", "trace_id"))
    if not trace_id:
        trace_id = _clean(_deep_get(finding, "evidence", "request_id"))
    if not trace_id:
        # 从 evidence_hint 提取（discovery_engine 存的格式："Trace ID: xxx"）
        hint = _clean(finding.get("evidence_hint"))
        if hint:
            m = re.search(r"[Tt]race\s*[Ii][Dd][:：]\s*(\S+)", hint)
            if m:
                trace_id = _clean(m.group(1))
    if not trace_id:
        # 从 har_evidence 响应体提取
        har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
        har_body = har.get("response_body") or ""
        if har_body:
            import json as _json
            try:
                body_obj = _json.loads(har_body) if isinstance(har_body, str) else har_body
                if isinstance(body_obj, dict):
                    trace_id = _clean(
                        body_obj.get("traceId") or body_obj.get("trace_id") or
                        body_obj.get("requestId") or body_obj.get("request_id") or
                        body_obj.get("correlationId") or body_obj.get("correlation_id") or ""
                    )
            except Exception:
                pass
    # 不做 fallback 到 risk_id——那不是 traceId，展示空比展示错误信息更好

    return {
        "primary_area": primary_area or (f"{method} {path}" if path else _clean(finding.get("title"))),
        "relevant_apis": relevant_apis,
        "relevant_tables": relevant_tables,
        "log_search": log_search,
        "sql_verify": sql_verify,
        "trace_id": trace_id,
    }


# ═══════════════════════════════════════════════════════════════════════
# 评分计算（从前端 computeBEI/BDS/BCS 迁移）
# ═══════════════════════════════════════════════════════════════════════

def _compute_scores(findings: list[dict], raw: dict | None = None) -> dict:
    """计算 BEI / BDS / BCS 评分"""
    raw = raw or {}
    if not findings:
        return {"bei": 0, "bds": "0.0", "bcs": 0, "evidence_trust_score": 0}

    # BEI
    base = 50
    p0_weight, p1_weight, p2_weight = 10, 5, 2
    max_weight = max(len(findings), 1) * p0_weight
    total_weight = sum(
        p0_weight if f.get("severity") == "P0"
        else p1_weight if f.get("severity") == "P1"
        else p2_weight
        for f in findings
    )
    bei = round(max(5, min(95, base + (total_weight / max_weight) * 45)), 1)

    # BDS
    p0p1 = sum(1 for f in findings if f.get("severity") in ("P0", "P1"))
    exec_summary = raw.get("executive_summary") or {}
    oracles = exec_summary.get("oracle_count") or len(exec_summary.get("recommended_oracles") or []) or len(findings)
    paths = oracles * 8
    bds = f"{((p0p1 / max(paths, 1)) * 1000):.1f}"

    # BCS
    db_hit_rate = _deep_get(raw, "db_verification", "hit_rate", default=0) or _deep_get(raw, "value_metrics", "evidence_trust_score", default=0) or 0
    bcs = round(min(98, 60 + db_hit_rate + len(findings) * 1.5), 1)

    # Evidence trust — already 0-100 from _evidence_trust_score(), no *100 needed
    exec = raw.get("executive_summary") or {}
    value_metrics = raw.get("value_metrics") or {}
    raw_trust = _deep_get(raw, "value_metrics", "evidence_trust_score", default=0) or 0
    # 如果值 <= 1 说明是 0-1 的小数比例，需要 * 100；否则直接用
    evidence_trust = round(raw_trust * 100) if raw_trust <= 1 else round(raw_trust)
    evidence_trust = evidence_trust or bcs

    return {"bei": bei, "bds": bds, "bcs": bcs, "evidence_trust_score": max(0, min(100, evidence_trust))}


# ═══════════════════════════════════════════════════════════════════════
# 商业价值计算（从前端 computeCommercialValue 迁移）
# ═══════════════════════════════════════════════════════════════════════

def _compute_commercial_value(findings: list[dict], raw: dict | None = None) -> dict:
    """计算商业价值指标和决策卡片"""
    raw = raw or {}
    exec = raw.get("executive_summary") or {}
    runtime = raw.get("runtime_verification") or {}
    value_metrics = raw.get("value_metrics") or {}
    discovery_funnel = raw.get("discovery_funnel") or {}
    capability_matrix = raw.get("full_spectrum_capability_matrix") or {}
    family_coverage = raw.get("bug_family_coverage") or {}

    p0 = int(exec.get("critical_bugs") or sum(1 for f in findings if f.get("severity") == "P0") or 0)
    p1 = int(exec.get("high_priority_bugs") or sum(1 for f in findings if f.get("severity") == "P1") or 0)
    raw_trust = value_metrics.get("evidence_trust_score") or 0
    # 如果值 <= 1 说明是 0-1 的小数比例，需要 * 100；否则直接用
    evidence_trust = round(raw_trust * 100) if raw_trust <= 1 else round(raw_trust)
    evidence_trust = max(0, min(100, evidence_trust)) or _compute_scores(findings, raw)["bcs"]
    ai_test_points = value_metrics.get("ai_equivalent_test_points") or exec.get("llm_powered_analyses") or runtime.get("total_probes") or len(findings)
    explored = discovery_funnel.get("explored_paths") or discovery_funnel.get("total_candidates") or runtime.get("total_probes") or ai_test_points
    capability_families = capability_matrix.get("total_capabilities") or capability_matrix.get("covered_capabilities") or len(capability_matrix) or 0
    bug_families = family_coverage.get("covered_families") or family_coverage.get("total_families") or len(family_coverage) or 0
    blocked_risk_count = p0 + p1

    return {
        "executive_message": (
            f"已提前暴露 {blocked_risk_count} 个会影响收入、履约或上线验收的高优先级风险。"
            if blocked_risk_count > 0
            else "当前没有确认的 P0/P1 阻断项，可作为上线评审的正向证据。"
        ),
        "ai_equivalent_test_points": int(ai_test_points or 0),
        "evidence_trust_score": max(0, min(100, int(evidence_trust) or 0)),
        "explored_behavior_paths": int(explored) or 0,
        "blocked_risk_count": blocked_risk_count,
        "capability_families": int(capability_families) or 0,
        "bug_families": int(bug_families) or 0,
        "decision_cards": [
            {
                "role": "管理层",
                "title": "用证据把风险前置" if blocked_risk_count > 0 else "用证据降低上线不确定性",
                "value": f"{blocked_risk_count} 个高优先级风险" if blocked_risk_count > 0 else f"{len(findings)} 个已验证发现",
                "detail": "把上线争议转为可复现证据与可分派整改清单，减少返工与客户投诉。",
            },
            {
                "role": "业务负责人",
                "title": "把业务规则沉淀为持续验证",
                "value": f"{int(ai_test_points or 0):,} 个验证覆盖点",
                "detail": "PRD、接口、DB 与权限规则沉淀为检测基线，变更后自动回归核验。",
            },
            {
                "role": "技术负责人",
                "title": "证据链可复现、可追溯",
                "value": f"{max(0, min(100, int(evidence_trust) or 0))}% 证据可信度",
                "detail": "每条结论关联请求链路、关键状态与数据一致性证据，便于快速定位与复现。",
            },
        ],
    }


# ═══════════════════════════════════════════════════════════════════════
# 统一 Bug 状态机（四态）+ 证据完备度门控
# ═══════════════════════════════════════════════════════════════════════

BUG_STATUS_META = {
    "reproduced": {"label": "已复现", "description": "有完整证据链，可重复复现，可直接交付研发修复"},
    "suspected": {"label": "疑似", "description": "有异常信号和部分证据，但缺少关键复现证据或断言，需人工确认"},
    "risk_clue": {"label": "风险线索", "description": "规则/模型/静态分析发现潜在风险，未真实复现，需继续验证"},
    "not_reproduced": {"label": "未复现", "description": "执行过但未触发异常，或证据不足以证明问题存在"},
}


def _compute_bug_status(
    finding: dict,
    evidence_quality: dict,
    evidence_completeness: dict,
) -> dict:
    """计算统一 Bug 状态（四态：已复现/疑似/风险线索/未复现）。

    状态判定逻辑（从高到低优先级）：
    1. reproduced: 有真实执行 + 完备度≥4/6 + 预期实际对比 + API响应或DB证据
    2. suspected: 有部分运行时证据，但缺少关键复现证据或断言
    3. risk_clue: 规则/模型发现潜在风险，无真实复现
    4. not_reproduced: 执行过但未触发异常，或被明确标记为 falsified/rejected
    """
    quality_level = evidence_quality.get("level", "needs_evidence")
    can_reproduce = evidence_quality.get("can_reproduce", False)
    present_count = evidence_completeness.get("present_count", 0)

    dims = {d["key"]: d["present"] for d in evidence_completeness.get("dimensions", [])}
    has_api_response = dims.get("api_response", False)
    has_db_evidence = dims.get("db_evidence", False)
    has_reproduction = dims.get("reproduction", False)
    has_rule = dims.get("rule_source", False)

    has_expected = bool(_clean(finding.get("expected_behavior") or finding.get("expected")))
    has_actual = bool(_clean(finding.get("actual_behavior") or finding.get("actual") or finding.get("description")))

    raw_status = _clean(finding.get("status") or finding.get("verdict") or finding.get("bug_confirmation")).lower()

    # 明确被标记为未复现/被驳回
    if any(t in raw_status for t in ("falsified", "not_reproduced", "rejected", "false_positive")):
        status = "not_reproduced"
    # 有真实运行时证据 → 可能是 reproduced 或 suspected
    elif can_reproduce or has_api_response or has_reproduction:
        if present_count >= 4 and has_expected and has_actual and (has_api_response or has_db_evidence):
            status = "reproduced"
        else:
            status = "suspected"
    elif present_count >= 2:
        status = "suspected"
    elif has_rule or quality_level == "needs_evidence":
        status = "risk_clue"
    else:
        status = "risk_clue"

    return {
        "status": status,
        "label": BUG_STATUS_META[status]["label"],
        "description": BUG_STATUS_META[status]["description"],
        "is_reproducible": status == "reproduced",
        "gate_passed": status == "reproduced",
    }


def _check_claim_evidence_consistency(finding: dict) -> list[str]:
    """声明-证据一致性检查：finding 声称的异常与实际运行证据是否矛盾。

    检查维度（通用，全行业适用）：
    1. HTTP 状态码矛盾：声称 4xx/5xx 但实际 2xx
    2. 响应体矛盾：声称"错误/失败/异常"但响应体是正常业务数据（无 error/code 字段）
    3. 声称"可复现/已确认"但无任何异常信号（2xx 响应 + 无错误字段 + 无 DB 违规）
    4. 认证错误被误判为 Bug：401/403 是认证/授权失败，如果 finding 声称的不是
       认证类问题，则证据不支持该结论——可能是扫描器遇到认证墙
    5. 认证端点返回 401 是预期行为：/auth/login 等端点返回 401 表示系统正确
       拒绝无效凭证，不构成 Bug

    返回矛盾原因列表（空列表表示无矛盾）。
    """
    contradictions: list[str] = []

    har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
    actual_status = har.get("status_code") or 0
    actual_body = _clean(har.get("response_body"))

    import re as _re
    import json as _json

    claim_text = " ".join([
        str(finding.get("title") or ""),
        str(finding.get("description") or ""),
        str(finding.get("actual_behavior") or finding.get("actual") or ""),
    ])

    # ── 1. HTTP 状态码矛盾检查 ──
    if actual_status and actual_status > 0:
        claimed_codes = set(int(m) for m in _re.findall(r'(?:服务端|返回|HTTP\s*|状态码\s*)?(\d{3})', claim_text)
                            if 400 <= int(m) <= 599)
        if claimed_codes and 200 <= actual_status < 300:
            contradictions.append(
                f"声明-证据矛盾：声称服务端{'/'.join(str(c) for c in sorted(claimed_codes))}，"
                f"但实际响应 {actual_status}（成功），异常未复现"
            )

    # ── 2. 响应体矛盾检查：声称错误但响应体无错误标识 ──
    if actual_status and 200 <= actual_status < 300 and actual_body:
        # 声称文本中有错误关键词（通用，非业务概念）
        error_keywords = ("错误", "失败", "异常", "崩溃", "error", "fail", "crash", "500", "exception")
        claims_error = any(kw in claim_text.lower() for kw in error_keywords)
        if claims_error:
            # 检查响应体是否真的包含错误标识
            body_has_error = False
            try:
                body_obj = _json.loads(actual_body) if isinstance(actual_body, str) else actual_body
                if isinstance(body_obj, dict):
                    body_has_error = bool(
                        body_obj.get("error") or body_obj.get("error_code")
                        or body_obj.get("message") and any(kw in str(body_obj.get("message", "")).lower() for kw in ("error", "fail", "错误", "失败"))
                    )
            except Exception:
                # 非 JSON 响应体，检查是否包含错误关键词
                body_has_error = any(kw in actual_body.lower() for kw in ("error", "fail", "exception", "错误", "失败"))
            if not body_has_error:
                contradictions.append(
                    f"声明-证据矛盾：声称存在错误/异常，但实际响应 {actual_status} 成功且响应体无错误标识"
                )

    # ── 3. 声称"可复现/已确认"但无异常信号 ──
    raw_status = _clean(finding.get("status") or finding.get("verdict") or finding.get("bug_confirmation")).lower()
    claims_confirmed = any(t in raw_status for t in ("confirmed", "reproduced", "validated", "已复现", "已确认"))
    if claims_confirmed and actual_status and 200 <= actual_status < 300:
        # 检查是否有 DB 违规或其他异常信号
        db_ev = _extract_db_evidence(finding)
        has_anomaly_signal = bool(db_ev and db_ev.get("violation"))
        if not has_anomaly_signal:
            contradictions.append(
                f"声明-证据矛盾：状态标记为'{raw_status}'（已复现），"
                f"但实际响应 {actual_status} 成功且无 DB 违规或其他异常信号"
            )

    # ── 4. 认证错误被误判为 Bug ──
    # 401/403 是认证/授权失败的标准 HTTP 状态码（通用，非业务概念）。
    # 如果 finding 声称的不是认证/授权类问题，但实际响应是 401/403，
    # 说明证据不支持该结论——可能是扫描器遇到认证墙，把认证失败误当 Bug。
    if actual_status in (401, 403):
        auth_keywords = (
            "auth", "login", "permission", "权限", "认证", "授权", "登录",
            "越权", "unauthorized", "forbidden", "credential", "token",
            "session", "会话", "身份",
        )
        claim_is_auth = any(kw in claim_text.lower() for kw in auth_keywords)
        if not claim_is_auth:
            path = _clean(har.get("path") or finding.get("_api_path") or finding.get("path") or "")
            contradictions.append(
                f"声明-证据矛盾：实际响应 {actual_status}（认证/授权失败），"
                f"但 finding 声称的是非认证类问题"
                f"{'（接口 ' + path + '）' if path else ''}——"
                f"证据可能是扫描器遇到认证墙，不代表所声称的缺陷存在"
            )

    # ── 5. 认证端点返回 401 是预期行为 ──
    # /auth/login、/signin、/token 等端点返回 401 表示系统正确拒绝无效凭证，
    # 这是正常的安全行为，不构成 Bug。
    if actual_status == 401:
        path = _clean(har.get("path") or finding.get("_api_path") or finding.get("path") or "")
        if path:
            # 通用 HTTP 认证端点路径模式（非业务概念）
            auth_endpoint_patterns = ("/auth/", "/login", "/signin", "/token", "/oauth")
            is_auth_endpoint = any(p in path.lower() for p in auth_endpoint_patterns)
            if is_auth_endpoint:
                # 检查响应体是否确实是"无效凭证"类错误
                is_credential_rejection = False
                if actual_body:
                    credential_keywords = (
                        "invalid", "incorrect", "wrong", "expired", "missing",
                        "credentials", "password", "token", "unauthorized",
                        "无效", "错误", "过期", "缺失", "凭证", "密码",
                    )
                    is_credential_rejection = any(kw in actual_body.lower() for kw in credential_keywords)
                if is_credential_rejection:
                    contradictions.append(
                        f"声明-证据矛盾：{path} 返回 401 是认证端点的预期行为"
                        f"（系统正确拒绝了无效凭证），不构成 Bug"
                    )

    return contradictions


def _enforce_evidence_gate(
    finding: dict,
    bug_status: dict,
    evidence_completeness: dict,
) -> dict:
    """证据门控：声明-证据一致性 + 证据完备度双重检查。

    1. 声明-证据一致性（所有状态都检查）：
       如果 finding 声称 HTTP 错误但实际响应是 2xx 成功 → 降级为 not_reproduced
    2. 证据完备度（仅 reproduced 状态检查）：
       不满足"已复现"门槛的自动降级为"疑似"。
    """
    # ── 1. 声明-证据一致性检查（所有状态都执行）──
    contradictions = _check_claim_evidence_consistency(finding)
    if contradictions:
        return {
            "status": "not_reproduced",
            "label": BUG_STATUS_META["not_reproduced"]["label"],
            "description": f"声明与证据矛盾：{'；'.join(contradictions)}",
            "is_reproducible": False,
            "gate_passed": False,
            "gate_failures": contradictions,
        }

    # ── 2. 证据完备度门控（仅 reproduced 状态检查）──
    if bug_status["status"] != "reproduced":
        return bug_status

    dims = {d["key"]: d["present"] for d in evidence_completeness.get("dimensions", [])}
    has_api_response = dims.get("api_response", False)
    has_db_evidence = dims.get("db_evidence", False)
    has_expected = bool(_clean(finding.get("expected_behavior") or finding.get("expected")))
    has_actual = bool(_clean(finding.get("actual_behavior") or finding.get("actual") or finding.get("description")))
    present_count = evidence_completeness.get("present_count", 0)

    gate_failures: list[str] = []
    if present_count < 4:
        gate_failures.append(f"证据完备度不足（{present_count}/6，需≥4）")
    if not has_expected:
        gate_failures.append("缺少预期结果")
    if not has_actual:
        gate_failures.append("缺少实际结果")
    if not (has_api_response or has_db_evidence):
        gate_failures.append("缺少API响应或DB证据")

    if gate_failures:
        return {
            "status": "suspected",
            "label": BUG_STATUS_META["suspected"]["label"],
            "description": f"证据门控未通过：{'；'.join(gate_failures)}",
            "is_reproducible": False,
            "gate_passed": False,
            "gate_failures": gate_failures,
        }

    return bug_status


def _compute_reproducibility_confidence(finding: dict, bug_status: dict, evidence_quality: dict) -> float:
    """计算复现置信度（0-1）。

    基于：bug状态 + 证据质量分 + 复现率 + 运行时证据
    """
    if bug_status["status"] == "not_reproduced":
        return 0.0
    if bug_status["status"] == "risk_clue":
        return 0.1

    score = evidence_quality.get("score", 0) / 100.0
    can_reproduce = evidence_quality.get("can_reproduce", False)

    # 有运行时证据加权
    har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
    if har.get("status_code"):
        score = min(1.0, score + 0.1)
    if can_reproduce:
        score = min(1.0, score + 0.1)

    if bug_status["status"] == "suspected":
        score = min(score, 0.69)  # 疑似上限
    elif bug_status["status"] == "reproduced":
        score = max(score, 0.7)  # 已复现下限

    return round(score, 2)


# ═══════════════════════════════════════════════════════════════════════
# 五层证据模型：摘要 / 业务 / 测试 / 研发 / 原始证据
# ═══════════════════════════════════════════════════════════════════════

def _extract_failed_assertions(finding: dict, reproduction: dict) -> list[dict]:
    """从 finding 提取失败断言列表（通用，不硬编码业务概念）。

    断言来源（均需真实异常信号，非仅有文本描述）：
    1. DB 字段值违反业务约束
    2. API 响应状态码异常（4xx/5xx）
    3. 响应体含错误标识（通用 error/code 字段）
    4. 行为不一致（仅当存在上述异常信号 + expected/actual 对比时才生成）
    """
    assertions: list[dict] = []

    expected = _strip_internal_tags(_clean(finding.get("expected_behavior") or finding.get("expected")))
    actual = _strip_internal_tags(_clean(finding.get("actual_behavior") or finding.get("actual") or finding.get("description")))

    # 2. DB 约束违规
    db_ev = _extract_db_evidence(finding)
    if db_ev and db_ev.get("violation"):
        assertions.append({
            "type": "db_constraint_violation",
            "label": f"数据库字段 {db_ev.get('table', '')}.{db_ev.get('column', '')} 违反约束",
            "expected": "字段值应符合业务约束",
            "actual": f"当前值 {db_ev.get('value', '')}（{db_ev.get('violation', '')}）",
            "detail": db_ev.get("raw", ""),
        })

    # 3. API 响应状态码异常
    har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
    status_code = har.get("status_code") or 0
    if status_code and status_code >= 400:
        assertions.append({
            "type": "http_status_error",
            "label": f"HTTP {status_code} 响应异常",
            "expected": "2xx 成功响应",
            "actual": f"状态码 {status_code}",
            "detail": _clean(har.get("response_body"))[:200] or "响应体未捕获",
        })

    # 4. 响应体含通用错误标识
    body = _clean(har.get("response_body"))
    if body:
        import json as _json
        try:
            body_obj = _json.loads(body) if isinstance(body, str) else body
            if isinstance(body_obj, dict):
                error_msg = _clean(body_obj.get("error") or body_obj.get("message") or body_obj.get("error_message"))
                error_code = _clean(body_obj.get("code") or body_obj.get("error_code"))
                if error_msg or error_code:
                    assertions.append({
                        "type": "response_error_field",
                        "label": "响应体包含错误信息",
                        "expected": "响应体不应包含错误标识",
                        "actual": f"code={error_code or 'N/A'}, message={error_msg or 'N/A'}",
                        "detail": body[:300],
                    })
        except Exception:
            pass

    # 4. 行为不一致断言（仅当存在真实异常信号 + expected/actual 对比时才生成）
    # 注意：assertions 非空说明已有 DB 违规 / HTTP 错误 / 响应错误等真实异常信号
    if assertions and expected and actual:
        assertions.append({
            "type": "behavior_mismatch",
            "label": "行为不符合预期",
            "expected": expected[:300],
            "actual": actual[:300],
            "detail": "系统实际行为与预期规则不一致（有运行时异常信号佐证）",
        })

    # 不伪造断言——如果没有真实失败断言，返回空列表
    # （预期/实际文本只是描述，不等于"已检测到失败"）
    return assertions


def _build_raw_evidence(finding: dict, reproduction: dict) -> dict:
    """构建原始证据结构（机器可追溯）。

    从 HAR/DB/日志/执行记录中提取原始证据，不伪造。
    """
    har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
    inv = finding.get("investigation_guidance") if isinstance(finding.get("investigation_guidance"), dict) else {}
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    db_ev = _extract_db_evidence(finding)

    trace_id = _clean(inv.get("trace_id")) or _clean(_deep_get(finding, "evidence", "trace_id"))
    source_file = _clean(_deep_get(finding, "evidence", "source_file") or finding.get("source"))

    # 请求原始数据
    request_raw: dict[str, Any] = {}
    method = _clean(har.get("method") or finding.get("_api_method") or "GET").upper()
    path = _clean(har.get("path") or finding.get("_api_path") or finding.get("path"))
    if method or path:
        request_raw["method"] = method
        request_raw["path"] = path
        request_raw["actor"] = _clean(har.get("actor"))
        if har.get("request_body"):
            request_raw["body"] = str(har.get("request_body"))[:2000]

    # 响应原始数据
    response_raw: dict[str, Any] = {}
    status_code = har.get("status_code") or 0
    if status_code or har.get("response_body"):
        response_raw["status_code"] = status_code
        response_raw["body"] = str(har.get("response_body") or "")[:2000]
        response_raw["duration_ms"] = har.get("duration_ms") or 0

    # DB 快照
    db_snapshot: dict[str, Any] = {}
    if db_ev:
        db_snapshot["table"] = db_ev.get("table", "")
        db_snapshot["column"] = db_ev.get("column", "")
        db_snapshot["value"] = db_ev.get("value", "")
        db_snapshot["violation"] = db_ev.get("violation", "")
    elif inv.get("sql_verify"):
        db_snapshot["sql_verify"] = _clean(inv.get("sql_verify"))[:500]

    # 日志
    logs: dict[str, Any] = {}
    if trace_id:
        logs["trace_id"] = trace_id
    if inv.get("log_search"):
        logs["search_hint"] = _clean(inv.get("log_search"))[:300]

    # 执行轨迹
    execution_trace: dict[str, Any] = {}
    if source_file:
        execution_trace["source_file"] = source_file
    if evidence.get("hash"):
        execution_trace["evidence_hash"] = _clean(evidence.get("hash"))

    return {
        "request_raw": request_raw,
        "response_raw": response_raw,
        "db_snapshot": db_snapshot,
        "logs": logs,
        "execution_trace": execution_trace,
        "timestamp": _clean(finding.get("last_verified_at") or finding.get("timestamp") or finding.get("first_seen_at")),
        "has_real_evidence": bool(request_raw or response_raw or db_snapshot or trace_id or source_file),
    }


def _build_technical_details(finding: dict, investigation: dict, reproduction: dict) -> dict:
    """构建研发定位证据（让研发一眼能定位问题）。"""
    har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
    risk_type = _clean(finding.get("risk_type") or finding.get("category") or "unknown")
    path = _clean(finding.get("_api_path") or finding.get("repro_path") or finding.get("path"))
    method = _clean(finding.get("_api_method") or finding.get("method") or "GET").upper()

    # 可能的根因方向（通用，基于 risk_type 映射，不硬编码业务概念）
    root_cause_map = {
        "permission_bypass": "权限校验逻辑未覆盖该场景，可能缺少角色/资源归属检查",
        "idor": "对象级授权缺失，接口未校验当前用户对该资源的访问权限",
        "business_invariant": "业务约束校验缺失，数据写入前未验证不变量",
        "data_integrity": "数据一致性校验缺失，可能缺少事务保护或并发控制",
        "positive_numeric": "数值范围校验缺失，未拒绝非法负值",
        "nonnegative_numeric": "非负约束校验缺失，未在写入前验证",
        "idempotency": "幂等控制缺失，重复请求导致数据重复写入",
        "state_machine": "状态流转校验缺失，允许了非法状态跳转",
        "api_contract": "接口契约实现与规范不一致",
        "sensitive_field_leak": "响应体未脱敏，敏感字段直接返回",
    }
    possible_root_cause = root_cause_map.get(risk_type, f"检测到 {risk_type} 类型风险，需排查相关校验逻辑")

    # 修复建议（通用，基于 risk_type）
    fix_map = {
        "permission_bypass": "在该接口增加权限校验中间件，验证当前用户角色是否有权操作目标资源",
        "idor": "在接口层增加对象级授权检查，验证请求者对目标资源的归属关系",
        "business_invariant": "在数据写入前增加业务不变量校验，违反约束时拒绝写入并返回错误",
        "data_integrity": "增加事务边界保护，确保关联数据原子性写入；补充并发控制机制",
        "positive_numeric": "在入参校验层增加数值范围检查，拒绝负值；在数据库层增加 CHECK 约束",
        "nonnegative_numeric": "增加非负校验，在模型层和数据库层双重保护",
        "idempotency": "引入幂等键机制，相同请求在有效期内返回缓存结果而非重复执行",
        "state_machine": "在状态变更接口增加状态机校验，拒绝非法跳转",
        "api_contract": "对齐接口实现与 OpenAPI 规范，补充缺失的校验或字段",
        "sensitive_field_leak": "在响应序列化层增加脱敏过滤，移除敏感字段或替换为掩码",
    }
    recommended_fix = fix_map.get(risk_type, "根据缺陷类型排查相关校验逻辑，补充缺失的边界检查和约束保护")

    # 回归测试建议（通用）
    regression_suggestions = [
        f"编写针对 {method} {path} 的回归测试，覆盖该缺陷场景",
        "增加边界值测试用例，验证修复后不再出现同类问题",
        "增加关联场景的集成测试，确保修复不影响正常业务流程",
    ]

    return {
        "api_endpoint": {
            "method": method,
            "path": path,
            "actor": _clean(har.get("actor")),
        },
        "response_status": har.get("status_code") or 0,
        "response_body_excerpt": _clean(har.get("response_body"))[:500],
        "related_tables": investigation.get("relevant_tables", []),
        "trace_id": investigation.get("trace_id", ""),
        "possible_root_cause": possible_root_cause,
        "recommended_fix": recommended_fix,
        "regression_suggestions": regression_suggestions,
        "code_module_hint": _clean(finding.get("source_entity")) or (path.split("/")[1] if "/" in path and len(path.split("/")) > 1 else ""),
    }


def _build_business_summary(finding: dict, business_impact: dict, bug_status: dict) -> str:
    """生成一句话业务影响摘要。"""
    severity = _clean(finding.get("severity")) or "P2"
    biz_summary = _strip_internal_tags(_clean(business_impact.get("summary") or finding.get("actual_behavior") or finding.get("actual")))
    module = _clean(business_impact.get("module") or finding.get("source_entity") or "核心业务")

    if biz_summary:
        # 截断到合理长度
        if len(biz_summary) > 150:
            biz_summary = biz_summary[:147] + "..."
        return f"【{bug_status['label']}·{severity}】{module}：{biz_summary}"
    return f"【{bug_status['label']}·{severity}】{module}存在潜在风险，需进一步确认业务影响"


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
    db_ev = _extract_db_evidence(finding)
    har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}

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
    status_code = har.get("status_code") or 0
    if status_code:
        comparison["api_comparison"] = {
            "expected": "2xx 成功响应",
            "actual": f"HTTP {status_code}",
            "response_body": _clean(har.get("response_body"))[:500],
            "duration_ms": har.get("duration_ms") or 0,
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
    """格式化单条 finding 为 display-ready 结构"""
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
    repro_method = _clean(finding.get("_api_method") or finding.get("repro_method") or _deep_get(finding, "evidence", "method") or finding.get("method") or "GET").upper()

    taxonomy = _build_taxonomy(finding)
    evidence_quality = _compute_evidence_quality(finding, repro_path)
    evidence_chain_data = _build_display_evidence_chain(finding)
    evidence_completeness = _compute_evidence_completeness(finding)
    reproduction = _build_repro_steps_display(finding, ctx)
    investigation = _build_investigation_display(finding)

    # 统一 Bug 状态（四态）+ 证据门控
    bug_status = _compute_bug_status(finding, evidence_quality, evidence_completeness)
    bug_status = _enforce_evidence_gate(finding, bug_status, evidence_completeness)
    reproducibility_confidence = _compute_reproducibility_confidence(finding, bug_status, evidence_quality)

    # 影响范围
    affected_scope_parts: list[str] = []
    if _clean(finding.get("source_entity")):
        affected_scope_parts.append(_clean(finding.get("source_entity")))
    if repro_path:
        affected_scope_parts.append(f"接口 {repro_method} {repro_path}")
    if taxonomy.get("defect_family_label"):
        affected_scope_parts.append(taxonomy["defect_family_label"])
    affected_scope = "、".join(affected_scope_parts) if affected_scope_parts else "核心业务"

    # 业务影响
    business_impact_raw = finding.get("business_impact")
    if isinstance(business_impact_raw, dict):
        business_impact = {
            "summary": _strip_internal_tags(_clean(business_impact_raw.get("summary") or finding.get("actual_behavior") or finding.get("actual"))) or "该缺陷可能导致业务流程异常，影响用户体验和业务数据一致性。",
            "urgency": _clean(business_impact_raw.get("urgency")) or ("高" if severity == "P0" else "中高" if severity == "P1" else "中"),
            "module": _clean(business_impact_raw.get("module")) or _clean(finding.get("source_entity")) or "核心业务",
        }
    else:
        biz_text = _strip_internal_tags(_clean(business_impact_raw) or _clean(finding.get("actual_behavior") or finding.get("actual") or finding.get("description")))
        business_impact = {
            "summary": biz_text or "该缺陷可能导致业务流程异常，影响用户体验和业务数据一致性。",
            "urgency": "高" if severity == "P0" else "中高" if severity == "P1" else "中",
            "module": _clean(finding.get("source_entity")) or "核心业务",
        }

    # 四层证据状态（透传）
    evidence_status = {
        "raw_runtime_verdict": _clean(finding.get("raw_runtime_verdict")),
        "semantic_verdict": _clean(finding.get("semantic_verdict")),
        "business_evidence_status": _clean(finding.get("business_evidence_status")),
        "final_review_status": _clean(finding.get("final_review_status") or ("VALIDATED_CANDIDATE" if confirmed else "NEEDS_MORE_EVIDENCE")),
        "missing_requirements": finding.get("missing_requirements") if isinstance(finding.get("missing_requirements"), list) else [],
    }

    # ── 五层证据模型 ──
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

    risk_id = _clean(finding.get("risk_id") or finding.get("finding_id") or finding.get("bug_id") or finding.get("issue_id"))
    # 判断 risk_id 是否是误填的标题（而非真正的唯一标识）：
    # - 太长（>40字符）
    # - 包含中文（标题有中文）
    # - 包含路径标签特征（[禁止路径] [状态破坏] 等）
    # - 与标题相同或相似
    _raw_title = _clean(finding.get("title"))
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

    return {
        "id": risk_id,
        "title": title,
        "severity": severity,
        "risk_type": risk_type,
        "verdict": "confirmed" if confirmed else "pending",

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
        "technical_details": technical_details,
        "recommended_fix": technical_details["recommended_fix"],
        "regression_suggestions": technical_details["regression_suggestions"],

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
    core = re.sub(r'\b[A-Z]{2,}[-_][A-Z0-9][-_A-Z0-9]*\b', '<ID>', core)
    # 6. =号后的值：amount=100, amount=-6999.00 → amount=<V>
    core = re.sub(r'=\s*\S+', '=<V>', core)

    # API path
    path = _clean(finding.get("_api_path") or finding.get("repro_path") or finding.get("path"))

    return f"{core.strip().lower()}|{path.lower()}"


def _dedupe_by_business_semantics(risks: list[dict]) -> list[dict]:
    """
    业务级去重：同一业务缺陷被多个引擎/Oracle 重复报告时，只保留最高严重度的。

    通用逻辑：去掉所有引擎前缀和路径标签后，核心描述+API path 相同的视为同一缺陷。
    """
    if not risks:
        return []

    SEVERITY_RANK = {"P0": 0, "P1": 1, "P2": 2, "P3": 3, "critical": 0, "high": 1, "medium": 2, "low": 3}

    def severity_rank(item: dict) -> int:
        return SEVERITY_RANK.get(str(item.get("severity", "P2")).lower(), 2)

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
        group.sort(key=severity_rank)
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

    return deduped


# ═══════════════════════════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════════════════════════

def format_findings_display_ready(
    risks: list[dict],
    enterprise_ctx: dict | None = None,
    raw_report: dict | None = None,
) -> tuple[list[dict], dict]:
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

    scores = _compute_scores(display_findings, raw_report)
    commercial_value = _compute_commercial_value(display_findings, raw_report)

    metrics = {
        "scores": scores,
        "commercial_value": commercial_value,
    }
    return display_findings, metrics
