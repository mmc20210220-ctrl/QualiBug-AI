"""Repro steps, investigation display, SQL generation."""
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
    from ._evidence import _best_runtime_observation, _clean, _has_runtime_response, _is_unresolved_path_value, _path_mismatch_reasons, _runtime_body_excerpt, _status_code_int  # lazy: avoid circular import
    """构建复现信息展示（基于 HAR 真实数据）"""
    ctx = enterprise_ctx or {}
    runtime_obs = _best_runtime_observation(finding)
    finding_path = finding.get("_api_path") or finding.get("repro_path") or ""
    finding_method = (finding.get("_api_method") or finding.get("repro_method") or "").upper()
    har_path = runtime_obs.get("path") or ""
    har_method = (runtime_obs.get("method") or "").upper()

    # 优先用 finding 自己的 path（更精确）
    if har_path and not _is_unresolved_path_value(har_path):
        path = har_path
    else:
        path = finding_path
    if _is_unresolved_path_value(path):
        path = ""
    method = finding_method or har_method
    if not path:
        method = ""

    # 复现步骤只有在显式执行来源可追溯时才算真实；仅有文本列表不构成执行证据。
    real_steps = finding.get("reproduction_steps") or finding.get("reproduce_steps_business") or []
    if not isinstance(real_steps, list):
        real_steps = []
    real_steps = [str(s) for s in real_steps if s]
    provenance = finding.get("reproduction_steps_provenance") if isinstance(finding.get("reproduction_steps_provenance"), dict) else {}
    reproduction_record = finding.get("reproduction") if isinstance(finding.get("reproduction"), dict) else {}
    raw_evidence = finding.get("raw_evidence") if isinstance(finding.get("raw_evidence"), dict) else {}
    request_raw = raw_evidence.get("request_raw") if isinstance(raw_evidence.get("request_raw"), dict) else {}
    response_raw = raw_evidence.get("response_raw") if isinstance(raw_evidence.get("response_raw"), dict) else {}
    captured_execution_steps = bool(
        real_steps
        and reproduction_record.get("is_synthetic") is False
        and raw_evidence.get("has_real_evidence")
        and request_raw.get("method")
        and request_raw.get("path")
        and (response_raw.get("status_code") or response_raw.get("body") or _has_runtime_response(finding))
        and (raw_evidence.get("timestamp") or finding.get("timestamp"))
    )

    is_synthetic = True
    if real_steps:
        steps = real_steps
        if provenance:
            is_synthetic = not (
                provenance.get("is_synthetic") is False
                and _clean(provenance.get("status")).lower() in {"observed", "executed", "captured"}
            )
        else:
            is_synthetic = not captured_execution_steps
    else:
        guidance = finding.get("reproduction_guidance") if isinstance(finding.get("reproduction_guidance"), dict) else {}
        guidance_steps = guidance.get("steps") if isinstance(guidance.get("steps"), list) else []
        steps = [str(step) for step in guidance_steps if str(step)] or _generate_default_repro_steps(finding, path, method, ctx)

    har = finding.get("har_evidence") if isinstance(finding.get("har_evidence"), dict) else {}
    request_body = runtime_obs.get("request_body") or request_raw.get("body") or ""
    if har.get("request_body_truncated"):
        request_body_status = "truncated"
    elif request_body not in (None, "") and (
        har.get("request_body_observed") is True or bool(request_raw.get("body"))
    ):
        request_body_status = "observed"
    else:
        request_body_status = "missing"
    curl_command = ""
    if path:
        base_url = ctx.get("base_url", "")
        full_url = f"{base_url}{path}" if path.startswith("/") and base_url else (path if path.startswith("http") else f"${{BASE_URL}}{path}")
        if method not in {"POST", "PUT", "PATCH"} or request_body_status == "observed":
            body_part = ""
            if method in {"POST", "PUT", "PATCH"}:
                body_text = _runtime_body_excerpt(request_body, 20_000)
                body_part = f" -H \"Content-Type: application/json\" -d '{body_text}'"
            curl_command = f'curl -X {method or "GET"} "{full_url}"{body_part} -v'

    har_evidence_out = None
    if runtime_obs and not _path_mismatch_reasons(finding):
        har_evidence_out = {
            "source": runtime_obs.get("source") or "runtime",
            "status_code": _status_code_int(runtime_obs.get("status_code")),
            "response_body": _runtime_body_excerpt(runtime_obs.get("body"), 2000),
            "actor": runtime_obs.get("actor") or "",
            "duration_ms": runtime_obs.get("duration_ms") or 0,
        }

    return {
        "method": method,
        "path": path,
        "steps": steps,
        "is_synthetic": is_synthetic,
        "curl_command": curl_command,
        "request_body_status": request_body_status,
        "har_evidence": har_evidence_out,
    }


def _generate_default_repro_steps(finding: dict, path: str, method: str, ctx: dict) -> list[str]:
    from ._format import _strip_internal_tags  # lazy
    from ._evidence import _best_runtime_observation, _clean, _has_runtime_response, _is_unresolved_path_value, _path_mismatch_reasons, _runtime_body_excerpt, _status_code_int  # lazy: avoid circular import
    """生成默认复现步骤指引（当后端没有真实步骤时的 fallback）。

    注意：这些是"建议操作指引"，不是"已执行的真实复现步骤"。
    调用方应通过 is_synthetic=True 标记，前端据此区分"真实复现步骤"与"建议指引"。
    """
    # 如果有运行时真实响应，说明请求确实执行过，可以基于真实数据生成指引
    runtime_obs = _best_runtime_observation(finding)
    has_real_response = _has_runtime_response(finding)

    if not path or _is_unresolved_path_value(path):
        return []

    if path and has_real_response:
        # 有真实响应 → 生成基于实际执行的指引（但仍标记为 synthetic）
        status = _status_code_int(runtime_obs.get("status_code")) if runtime_obs else 0
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
    from ._quality import _deep_get  # lazy: avoid circular import
    from ._evidence import _best_runtime_observation, _clean, _has_runtime_response, _is_unresolved_path_value, _path_mismatch_reasons, _runtime_body_excerpt, _status_code_int  # lazy: avoid circular import
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

    # 5. 从 canonical runtime request body 动态提取（通用）
    runtime_obs = _best_runtime_observation(finding)
    runtime_request_body = runtime_obs.get("request_body") if runtime_obs else None
    if runtime_request_body:
        import json as _json
        try:
            body = _json.loads(runtime_request_body) if isinstance(runtime_request_body, str) else runtime_request_body
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
    from ._evidence import _best_runtime_observation, _clean, _has_runtime_response, _is_unresolved_path_value, _path_mismatch_reasons, _runtime_body_excerpt, _status_code_int  # lazy: avoid circular import
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
    from ._evidence import _best_runtime_observation, _clean, _has_runtime_response, _is_unresolved_path_value, _path_mismatch_reasons, _runtime_body_excerpt, _status_code_int  # lazy: avoid circular import
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
    from ._quality import _deep_get  # lazy: avoid circular import
    from ._evidence import _best_runtime_observation, _clean, _has_runtime_response, _is_unresolved_path_value, _path_mismatch_reasons, _runtime_body_excerpt, _status_code_int  # lazy: avoid circular import
    """构建排查指引展示——生成具体 SQL 而非通用模板"""
    inv = finding.get("investigation_guidance") if isinstance(finding.get("investigation_guidance"), dict) else {}
    entity = _clean(finding.get("source_entity"))
    path = _clean(finding.get("_api_path") or finding.get("repro_path") or finding.get("path"))
    method = _clean(finding.get("_api_method") or finding.get("repro_method")).upper()
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
        # 从 canonical runtime observation 响应体提取
        runtime_obs = _best_runtime_observation(finding)
        runtime_body = runtime_obs.get("body") if runtime_obs else ""
        if runtime_body:
            import json as _json
            try:
                body_obj = _json.loads(runtime_body) if isinstance(runtime_body, str) else runtime_body
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

