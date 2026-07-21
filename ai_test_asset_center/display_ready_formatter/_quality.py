"""Evidence quality scoring, completeness, display chain building."""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from ._common import *  # noqa: F401,F403
from ._evidence import *  # noqa: F401,F403


def _compute_evidence_quality(finding: dict, repro_path: str) -> dict:
    """计算证据质量评分、verified/missing 清单、curl 命令"""
    verified: list[str] = []
    missing: list[str] = []
    next_actions: list[str] = []

    method = _clean(finding.get("_api_method") or _deep_get(finding, "evidence", "method") or finding.get("method") or "GET").upper()
    has_api_target = bool(_clean(repro_path) and not _is_unresolved_path_value(repro_path))
    has_actual = bool(_clean(finding.get("actual_behavior") or finding.get("actual") or finding.get("description")))
    has_expected = bool(_clean(finding.get("expected_behavior") or finding.get("expected")))
    doc_refs = finding.get("_doc_refs") or finding.get("doc_refs") or []
    has_docs = isinstance(doc_refs, list) and len(doc_refs) > 0
    has_db_evidence = bool(_extract_verified_db_evidence(finding))
    has_db_clue = _has_db_clue(finding)
    evidence_source_file = _clean(_deep_get(finding, "evidence", "source_file") or finding.get("source"))
    has_log_signal = bool(_clean(_deep_get(finding, "investigation_guidance", "log_search") or finding.get("evidence_hint") or evidence_source_file))

    status = _clean(finding.get("status") or finding.get("verdict") or finding.get("bug_confirmation")).lower()
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    has_runtime_proof = _has_runtime_response(finding)
    # 注意：evidence.get("expected")/evidence.get("actual") 是文本描述字段，
    # 不代表真正执行过请求。只有 status_code/response/source_file/reproducibility
    # 才能证明运行时真实执行。

    # HAR 真实响应证据（状态码 + 响应体）
    has_api_response = _has_runtime_response(finding)
    # 复现验证证据
    has_reproduction = bool(_deep_get(finding, "reproducibility", "reproducible") and has_runtime_proof)

    # 失败断言证据（第10维度：需要真实异常信号，不是仅有 expected/actual 文本）
    # expected/actual 文本只是描述，不等于"已检测到失败断言"
    has_assertion = _has_anomaly_signal(finding)

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

    if has_db_evidence:
        verified.append("存在已验证的 DB 前后快照或 DB 校验证据")
    elif has_db_clue:
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
    if not has_db_evidence:
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
        (10 if has_db_evidence else 0) +
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

    upstream_quality = finding.get("evidence_quality") if isinstance(finding.get("evidence_quality"), dict) else {}
    upstream_level = _clean(upstream_quality.get("level")).lower()
    try:
        upstream_score = float(upstream_quality.get("score") or 0)
    except Exception:
        upstream_score = 0.0
    semantic_verdict = _clean(finding.get("semantic_verdict") or _deep_get(finding, "evidence_status", "semantic_verdict")).upper()
    business_evidence_status = _clean(finding.get("business_evidence_status") or _deep_get(finding, "evidence_status", "business_evidence_status")).upper()
    if (
        bool(finding.get("gate_passed"))
        and upstream_level == "validated"
        and upstream_score >= CUSTOMER_READY_MIN_EVIDENCE_SCORE
        and bool(upstream_quality.get("can_reproduce"))
        and semantic_verdict == "SEMANTIC_CONFIRMED"
        and business_evidence_status == "VALIDATED"
    ):
        merged_verified = list(dict.fromkeys([
            *[str(item) for item in verified if str(item)],
            *[str(item) for item in upstream_quality.get("verified") or [] if str(item)],
        ]))
        return {
            "level": "validated",
            "score": max(score, int(upstream_score)),
            # Once the strict customer-ready gate is satisfied, stale upstream
            # labels such as "待补强证据" must not leak into the final payload.
            "label": "可交付证据",
            "summary": "证据完整，可直接提交研发修复。",
            "verified": merged_verified,
            "missing": [],
            "next_actions": [],
            "can_reproduce": True,
            "curl_command": _clean(upstream_quality.get("curl_command")) or curl_command,
        }

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
    """提取结构化请求响应证据。

    名称保持兼容，但实际来源已统一为 canonical runtime observation：
    HAR 与 evidence.calls 都走同一个验真、路径/方法绑定和占位符过滤逻辑。
    """
    obs = _best_runtime_observation(finding)
    if not obs:
        return None
    status_code = _status_code_int(obs.get("status_code"))
    response_body = _runtime_body_excerpt(obs.get("body"), 1000)
    actor = _clean(obs.get("actor"))
    duration_ms = obs.get("duration_ms") or 0
    method = _clean(obs.get("method") or finding.get("_api_method") or finding.get("method")).upper()
    path = _clean(obs.get("path") or finding.get("_api_path") or finding.get("path"))

    if not (status_code or response_body or path):
        return None
    if path and _is_unresolved_path_value(path):
        return None
    return {
        "source": _clean(obs.get("source")) or "runtime",
        "method": method,
        "path": path,
        "status_code": status_code,
        "response_body": response_body,
        "request_body": _runtime_body_excerpt(obs.get("request_body"), 2000),
        "actor": actor,
        "duration_ms": duration_ms,
        "request_url": _clean(obs.get("request_url")),
    }


def _compute_evidence_completeness(finding: dict) -> dict:
    """计算 6 维度证据完备度，供企业用户直观判断证据是否齐全。

    维度：规则来源 / 接口请求 / 接口响应 / 数据核验 / 日志追溯 / 复现验证
    """
    har = _relevant_har_evidence(finding)
    inv = finding.get("investigation_guidance") if isinstance(finding.get("investigation_guidance"), dict) else {}
    doc_refs = finding.get("_doc_refs") or finding.get("doc_refs") or []
    has_docs = isinstance(doc_refs, list) and len(doc_refs) > 0
    path = _clean(finding.get("_api_path") or finding.get("repro_path") or finding.get("path"))

    db_ev = _extract_verified_db_evidence(finding)
    har_ev = _extract_har_response_evidence(finding)

    trace_id = _clean(inv.get("trace_id")) or _clean(_deep_get(finding, "evidence", "trace_id"))

    repro_data = finding.get("reproducibility")
    has_repro = isinstance(repro_data, dict) and repro_data.get("reproducible")
    has_real_runtime = _has_runtime_response(finding)
    has_anomaly = _has_anomaly_signal(finding)

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
            "present": bool(path and not _is_unresolved_path_value(path)),
            "detail": "可执行的接口地址",
        },
        {
            "key": "api_response",
            "label": "接口响应",
            "present": has_real_runtime,
            "detail": "真实状态码与响应体",
        },
        {
            "key": "db_evidence",
            "label": "数据核验",
            "present": bool(db_ev),
            "detail": "数据库前后快照或已验证 DB 断言",
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
            "present": bool((has_repro and has_real_runtime) or has_anomaly),
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
    if path and not _is_unresolved_path_value(path):
        chain.append({
            "tag": "api",
            "label": "触发请求",
            "content": f"{method or 'GET'} {path}",
            "detail": _strip_internal_tags(_clean(finding.get("evidence_hint"))) or "按该接口回放请求，记录参数、状态码、响应体和时间戳。",
            "source": (_best_runtime_observation(finding).get("source") or "runtime") if _has_runtime_response(finding) else "engine",
            "confidence": "high" if _has_runtime_response(finding) else "low",
            "timestamp": timestamp,
        })

    # 3. 接口响应（从 canonical runtime observation 提取真实状态码/响应体/耗时）
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
            "source": har_ev.get("source") or "runtime",
            "confidence": "high",
            "timestamp": timestamp,
            "structured": {
                "status_code": status,
                "response_body": body,
                "actor": actor,
                "duration_ms": duration,
                "source": har_ev.get("source") or "runtime",
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
    db_ev = _extract_verified_db_evidence(finding)
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
            content = "存在 DB 核验线索"
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


