"""Source-bound evidence enrichment for customer-facing findings.

This module formats evidence that already exists. It never invents actors,
credentials, request bodies, business rules, table names, SQL, or impact claims.
Generated operator guidance is kept separate from captured reproduction steps and
is always marked synthetic.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .artifact_redactor import redact_artifact
from .finding_risk_grading import apply_finding_grading as _apply_finding_grading


CATEGORY_MODULE_MAP = {
    "authorization": "权限管控",
    "data_integrity": "数据一致性",
    "data_leak": "数据隔离",
    "business_rule": "业务规则",
    "financial": "资金安全",
    "idempotency": "幂等防护",
    "inventory": "库存管理",
    "state_machine": "状态流转",
    "parameter_validation": "参数校验",
    "db_verification": "数据验证",
    "e2e_flow": "端到端流程",
    "deep_test": "深度检测",
}
WRITE_METHODS = {"POST", "PUT", "PATCH"}
READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _safe_text_excerpt(value: Any, limit: int = 200) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    return text[:limit]


def _body_text(value: Any) -> str:
    redacted, _receipt = redact_artifact(value)
    if isinstance(redacted, str):
        return redacted
    return json.dumps(redacted, ensure_ascii=False, separators=(",", ":"), default=str)


def _request_identity(finding: dict[str, Any]) -> tuple[str, str, str]:
    evidence = _dict(finding.get("evidence"))
    har = _dict(finding.get("har_evidence"))
    method = _text(
        finding.get("_api_method")
        or finding.get("repro_method")
        or evidence.get("method")
        or har.get("method")
    ).upper()
    path = _text(
        finding.get("_api_path")
        or finding.get("repro_path")
        or evidence.get("path")
        or har.get("path")
    )
    if method and path:
        return method, path, "finding_or_runtime_field"

    claim = f"{_text(finding.get('title'))} {_text(finding.get('description') or finding.get('summary'))}"
    path_match = re.search(r"(/api/[\w/{}.%\-:]+|/[\w{}.%\-:]+/[\w/{}.%\-:]+)", claim)
    method_match = re.search(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b", claim, re.IGNORECASE)
    return (
        method or (method_match.group(1).upper() if method_match else ""),
        path or (path_match.group(1) if path_match else ""),
        "finding_text" if path_match or method_match else "missing",
    )


def _request_body_evidence(finding: dict[str, Any], har: dict[str, Any] | None = None) -> dict[str, Any]:
    runtime_har = har or _dict(finding.get("har_evidence"))
    if runtime_har.get("request_body_observed") is True and runtime_har.get("request_body") not in (None, ""):
        return {
            "body": _body_text(runtime_har["request_body"]),
            "status": "truncated" if runtime_har.get("request_body_truncated") else "observed",
            "source": _text(runtime_har.get("request_body_source")) or "har.request_body",
        }

    raw_evidence = _dict(finding.get("raw_evidence"))
    request_raw = _dict(raw_evidence.get("request_raw"))
    if raw_evidence.get("has_real_evidence") and request_raw.get("body") not in (None, ""):
        return {
            "body": _body_text(request_raw["body"]),
            "status": "observed",
            "source": _text(request_raw.get("body_source")) or "raw_evidence.request_raw.body",
        }

    evidence = _dict(finding.get("evidence"))
    if evidence.get("request_body_observed") is True and evidence.get("request_body") not in (None, ""):
        return {
            "body": _body_text(evidence["request_body"]),
            "status": "observed",
            "source": _text(evidence.get("request_body_source")) or "evidence.request_body",
        }
    return {"body": "", "status": "missing", "source": ""}


def _extract_request_body(finding: dict[str, Any], har: dict[str, Any]) -> str:
    """Return a redacted captured request body, never an inferred example."""
    body = _request_body_evidence(finding, har)
    return body["body"] if body["status"] == "observed" else ""


def _match_business_impact(title: str, description: str, category: str = "") -> dict[str, str]:
    observed_fact = _safe_text_excerpt(description or title, 180)
    return {
        "summary": (
            "缺少企业资料支持的业务影响结论。"
            + (f"当前仅记录技术事实：{observed_fact}" if observed_fact else "")
        ),
        "urgency": "待评估",
        "module": CATEGORY_MODULE_MAP.get(category, category or "未绑定业务域"),
        "source": "missing_enterprise_evidence",
    }


def _build_reproduction_steps(
    finding: dict[str, Any],
    enterprise_ctx: dict[str, Any] | None = None,
) -> list[str]:
    """Build synthetic operator guidance without claiming it was executed."""
    ctx = enterprise_ctx or {}
    har = _dict(finding.get("har_evidence"))
    method, path, _identity_source = _request_identity(finding)
    if not method or not path:
        return ["[指引] 缺少来源可追溯的请求方法或路径，无法生成复现命令。"]

    base_url = _text(ctx.get("base_url"))
    full_url = path if path.startswith("http") else f"{base_url}{path}" if base_url else f"${{BASE_URL}}{path}"
    body = _request_body_evidence(finding, har)
    steps: list[str] = []
    if method in WRITE_METHODS and body["status"] != "observed":
        steps.append(
            f"[指引] 已定位 {method} {path}，但同一执行证据未记录完整请求体；"
            "补齐请求回执前不得生成或执行写请求。"
        )
    else:
        command = f'curl -X {method} "{full_url}"'
        if method in WRITE_METHODS:
            command += f" -H \"Content-Type: application/json\" -d '{body['body']}'"
        steps.append(f"[指引] 基于已记录请求字段构造命令：\n   {command} -v")

    status = har.get("status_code") or _dict(finding.get("evidence")).get("status_code")
    if status not in (None, "", 0):
        steps.append(f"[运行事实] 已捕获 HTTP {status}；复验时必须保留时间戳、响应体和关联标识。")
    expected = _text(finding.get("expected") or finding.get("expected_behavior"))
    if expected:
        steps.append(f"[来源规则] {expected}")
    else:
        steps.append("[证据缺口] 缺少企业资料或 source invariant 支持的预期行为。")
    return steps


def _http_status_description(code: int) -> str:
    return {
        200: "成功",
        201: "已创建",
        400: "请求错误",
        401: "未认证",
        403: "权限禁止",
        404: "未找到",
        409: "冲突",
        422: "不可处理",
        500: "服务端内部错误",
        502: "网关错误",
        503: "服务不可用",
    }.get(code, "")


def _explicit_tables(finding: dict[str, Any], ctx: dict[str, Any]) -> list[str]:
    evidence = _dict(finding.get("evidence"))
    existing = _dict(finding.get("investigation_guidance"))
    for value in (
        existing.get("relevant_tables"),
        finding.get("relevant_tables"),
        evidence.get("relevant_tables"),
        ctx.get("relevant_tables"),
    ):
        values = _text_list(value)
        if values:
            return values
    table = _text(evidence.get("table") or evidence.get("table_name"))
    return [table] if table else []


def _build_sql_verify(finding: dict[str, Any], category: str = "", tables: list[str] | None = None) -> str:
    """Return only source-declared SQL; never synthesize table or column names."""
    evidence = _dict(finding.get("evidence"))
    guidance = _dict(finding.get("investigation_guidance"))
    return _text(
        guidance.get("sql_verify")
        or finding.get("sql_verify")
        or evidence.get("sql_verify")
        or evidence.get("verification_query")
    )


def _extract_trace_id(finding: dict[str, Any], har_body: Any) -> str:
    if isinstance(har_body, dict):
        body = har_body
    elif isinstance(har_body, str) and har_body.strip().startswith(("{", "[")):
        try:
            decoded = json.loads(har_body)
        except json.JSONDecodeError:
            decoded = None
        body = decoded if isinstance(decoded, dict) else {}
    else:
        body = {}
    evidence = _dict(finding.get("evidence"))
    return _text(
        body.get("traceId")
        or body.get("trace_id")
        or body.get("requestId")
        or body.get("request_id")
        or evidence.get("trace_id")
        or evidence.get("request_id")
    )


def _build_investigation_guidance(
    finding: dict[str, Any],
    enterprise_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build neutral guidance using only captured or source-declared fields."""
    ctx = enterprise_ctx or {}
    evidence = _dict(finding.get("evidence"))
    har = _dict(finding.get("har_evidence"))
    method, path, _identity_source = _request_identity(finding)
    source_entity = _text(finding.get("source_entity") or evidence.get("source_entity"))
    source_location = _text(evidence.get("source_file") or finding.get("source_location"))
    primary_area = source_location or source_entity or (f"{method} {path}" if method and path else "")

    relevant_apis = [f"{method} {path}"] if method and path else []
    relevant_apis.extend(_text_list(finding.get("related_endpoints")))
    relevant_apis = list(dict.fromkeys(relevant_apis))
    tables = _explicit_tables(finding, ctx)
    log_search = _text(finding.get("log_search") or evidence.get("log_search") or ctx.get("log_search"))
    sql_verify = _build_sql_verify(finding, _text(finding.get("category")), tables)
    status = har.get("status_code") or evidence.get("status_code")
    body = har.get("response_body") or evidence.get("response_body")
    response_analysis = ""
    if status not in (None, "", 0):
        response_analysis = f"运行时捕获 HTTP {status} {_http_status_description(int(status)) if str(status).isdigit() else ''}".strip()

    missing: list[str] = []
    if not primary_area:
        missing.append("source_location_or_entity")
    if not log_search:
        missing.append("source_declared_log_query")
    if not sql_verify:
        missing.append("source_declared_verification_query")
    return {
        "primary_area": primary_area,
        "relevant_apis": relevant_apis[:5],
        "relevant_tables": tables[:5],
        "log_search": log_search,
        "sql_verify": sql_verify,
        "trace_id": _extract_trace_id(finding, body),
        "har_status": status or "",
        "har_actor": har.get("actor") or "",
        "response_analysis": response_analysis,
        "missing_source_bindings": missing,
    }


def _decode_unicode_escapes(value: str) -> str:
    if not value or "\\u" not in value:
        return value
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value


def _match_business_rule_text(title: str, description: str, category: str) -> str:
    """Keyword matching is not a rule source; keep the missing state explicit."""
    return ""


def _source_rule(finding: dict[str, Any]) -> tuple[str, str]:
    expected = _text(finding.get("expected") or finding.get("expected_behavior"))
    if expected:
        return expected, _text(finding.get("expected_source") or "finding.expected")
    invariant = _dict(finding.get("source_invariant"))
    text = _text(invariant.get("text") or invariant.get("assertion"))
    if text:
        return text, _text(invariant.get("source_ref") or "source_invariant")
    return "", ""


def _build_evidence_chain(
    finding: dict[str, Any],
    enterprise_ctx: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    har = _dict(finding.get("har_evidence"))
    evidence = _dict(finding.get("evidence"))
    method, path, identity_source = _request_identity(finding)
    rule, rule_source = _source_rule(finding)
    chain: list[dict[str, str]] = []
    if rule:
        chain.append({"tag": "rule", "label": "预期规则", "content": rule, "detail": f"来源: {rule_source}"})
    else:
        chain.append({
            "tag": "gap",
            "label": "规则证据缺口",
            "content": "缺少企业资料或 source invariant 支持的预期规则。",
            "detail": "该缺口不得由关键词推断补齐。",
        })
    if method and path:
        chain.append({"tag": "api", "label": "请求身份", "content": f"{method} {path}", "detail": f"来源: {identity_source}"})
    status = har.get("status_code") or evidence.get("status_code")
    response_body = har.get("response_body") or evidence.get("response_body")
    actual = _text(finding.get("actual") or finding.get("actual_behavior") or finding.get("description") or finding.get("summary"))
    if status not in (None, "", 0) or response_body not in (None, ""):
        chain.append({
            "tag": "response",
            "label": "运行时响应",
            "content": f"HTTP {status}" if status not in (None, "", 0) else "已捕获响应体",
            "detail": _safe_text_excerpt(response_body, 180),
        })
    if actual:
        chain.append({"tag": "fact", "label": "记录的实际行为", "content": actual[:200], "detail": "来源: finding.actual/description"})
    return chain


def _has_real_reproduction(finding: dict[str, Any]) -> bool:
    reproduction = _dict(finding.get("reproduction"))
    raw = _dict(finding.get("raw_evidence"))
    request = _dict(raw.get("request_raw"))
    response = _dict(raw.get("response_raw"))
    return bool(
        reproduction.get("is_synthetic") is False
        and raw.get("has_real_evidence")
        and request.get("method")
        and request.get("path")
        and (response.get("status_code") or response.get("body") or _dict(finding.get("har_evidence")).get("status_code"))
        and _text_list(finding.get("reproduction_steps"))
    )


def _build_evidence_quality(finding: dict[str, Any]) -> dict[str, Any]:
    evidence = _dict(finding.get("evidence"))
    har = _dict(finding.get("har_evidence"))
    raw = _dict(finding.get("raw_evidence"))
    method, path, _identity_source = _request_identity(finding)
    expected, _expected_source = _source_rule(finding)
    actual = _text(finding.get("actual") or finding.get("actual_behavior") or finding.get("description"))
    doc_refs = finding.get("_doc_refs") if isinstance(finding.get("_doc_refs"), list) else []
    has_runtime_response = bool(
        har.get("status_code")
        or har.get("response_body")
        or _dict(raw.get("response_raw")).get("status_code")
        or _dict(raw.get("response_raw")).get("body")
    )
    has_db = bool(
        evidence.get("db_row")
        or _dict(raw.get("db_snapshot")).get("before")
        or _dict(raw.get("db_snapshot")).get("after")
    )
    has_reproduction = _has_real_reproduction(finding)

    verified: list[str] = []
    missing: list[str] = []
    if method and path:
        verified.append(f"请求身份: {method} {path}")
    else:
        missing.append("缺少可追溯的请求方法或路径")
    if has_runtime_response:
        verified.append("已捕获运行时响应")
    else:
        missing.append("缺少运行时响应")
    if expected:
        verified.append("已绑定来源规则")
    else:
        missing.append("缺少来自企业资料或 source invariant 的预期规则")
    if actual:
        verified.append("已记录实际行为")
    else:
        missing.append("缺少实际行为")
    if has_db:
        verified.append("已捕获数据核验证据")
    if doc_refs:
        verified.append(f"已关联 {len(doc_refs)} 份企业资料")
    if has_reproduction:
        verified.append("存在可追溯的真实复现步骤")
    else:
        missing.append("缺少与执行回执绑定的真实复现步骤")

    score = min(100, sum((
        15 if method and path else 0,
        25 if has_runtime_response else 0,
        20 if expected else 0,
        10 if actual else 0,
        10 if has_db else 0,
        10 if doc_refs else 0,
        10 if has_reproduction else 0,
    )))
    level = "validated" if score >= 90 and has_reproduction else "partial" if score >= 40 else "needs_evidence"
    labels = {"validated": "可交付证据", "partial": "待补强证据", "needs_evidence": "仅为风险线索"}
    body = _request_body_evidence(finding, har)
    curl_command = ""
    if method and path and (method in READ_METHODS or (method in WRITE_METHODS and body["status"] == "observed")):
        curl_command = f'curl -X {method} "${{BASE_URL}}{path}"'
        if method in WRITE_METHODS:
            curl_command += f" -H \"Content-Type: application/json\" -d '{body['body']}'"
    return {
        "level": level,
        "score": score,
        "label": labels[level],
        "summary": "证据来自已绑定来源。" if level == "validated" else "证据不完整，保持为内部线索。",
        "verified": verified,
        "missing": missing[:6],
        "next_actions": missing[:5],
        "can_reproduce": has_reproduction,
        "curl_command": curl_command,
    }


def _steps_provenance(finding: dict[str, Any], steps: list[str]) -> dict[str, Any]:
    existing = _dict(finding.get("reproduction_steps_provenance"))
    if existing:
        return existing
    reproduction = _dict(finding.get("reproduction"))
    if not steps:
        return {"status": "missing", "source": "", "is_synthetic": True}
    if _has_real_reproduction(finding):
        return {"status": "observed", "source": "captured_execution_receipt", "is_synthetic": False}
    if reproduction.get("is_synthetic") is True:
        return {"status": "generated_guidance", "source": "upstream", "is_synthetic": True}
    return {"status": "unverified_upstream", "source": "upstream", "is_synthetic": True}


def enrich_finding(
    finding: dict[str, Any],
    enterprise_ctx: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Add source-bound display metadata without changing evidence truth."""
    if not isinstance(finding, dict):
        raise TypeError("finding must be an object")
    enriched = dict(finding)
    ctx = enterprise_ctx or {}
    evidence = dict(_dict(finding.get("evidence")))
    method, path, identity_source = _request_identity(finding)
    if method:
        enriched.setdefault("_api_method", method)
        enriched.setdefault("repro_method", method)
        evidence.setdefault("method", method)
    if path:
        enriched.setdefault("_api_path", path)
        enriched.setdefault("repro_path", path)
        evidence.setdefault("path", path)
    enriched["request_identity_source"] = identity_source
    enriched["evidence"] = evidence

    business_impact = finding.get("business_impact")
    if isinstance(business_impact, dict):
        enriched["business_impact"] = dict(business_impact)
    elif isinstance(business_impact, str) and business_impact.strip():
        enriched["business_impact"] = {
            "summary": business_impact.strip(),
            "urgency": "待评估",
            "module": _text(finding.get("source_entity")) or "未绑定业务域",
            "source": "upstream_finding",
        }
    else:
        enriched["business_impact"] = _match_business_impact(
            _text(finding.get("title")),
            _text(finding.get("description") or finding.get("summary")),
            _text(finding.get("category") or finding.get("risk_type")),
        )

    existing_steps = _text_list(finding.get("reproduction_steps") or finding.get("repro_steps"))
    enriched["reproduction_steps_provenance"] = _steps_provenance(finding, existing_steps)
    body = _request_body_evidence(enriched)
    guidance_steps = _build_reproduction_steps(enriched, ctx)
    enriched["reproduction_guidance"] = {
        "schema_version": "qualibug.reproduction-guidance.v1",
        "steps": guidance_steps,
        "is_synthetic": True,
        "source": "evidence_enricher_v3.generated_guidance",
        "request_body_status": body["status"],
        "request_body_source": body["source"],
        "can_execute": bool(
            method
            and path
            and (method in READ_METHODS or (method in WRITE_METHODS and body["status"] == "observed"))
        ),
    }
    enriched.setdefault("investigation_guidance", _build_investigation_guidance(enriched, ctx))
    enriched["evidence_chain"] = _build_evidence_chain(enriched, ctx)

    computed_quality = _build_evidence_quality(enriched)
    existing_quality = _dict(finding.get("evidence_quality"))
    semantic_verdict = _text(finding.get("semantic_verdict") or _dict(finding.get("evidence_status")).get("semantic_verdict")).upper()
    business_status = _text(finding.get("business_evidence_status") or _dict(finding.get("evidence_status")).get("business_evidence_status")).upper()
    if (
        finding.get("gate_passed") is True
        and _text(existing_quality.get("level")).lower() == "validated"
        and float(existing_quality.get("score") or 0) >= 90
        and existing_quality.get("can_reproduce") is True
        and semantic_verdict == "SEMANTIC_CONFIRMED"
        and business_status == "VALIDATED"
        and _has_real_reproduction(enriched)
    ):
        computed_quality = {**computed_quality, **existing_quality}
    enriched["evidence_quality"] = computed_quality

    # ── Task 10: severity/confidence differentiation grading ──
    # Delivery-path re-grading with the full evidence chain (gate status,
    # occurrence multiplicity, observer depth).  Idempotent and backward
    # compatible: `severity` and `evidence_quality.level/score` are preserved.
    enriched = _apply_finding_grading(enriched)

    if not _text(enriched.get("expected")):
        enriched["expected_source_status"] = "missing"
    if not _text(enriched.get("actual")):
        enriched["actual"] = _text(finding.get("description") or finding.get("summary") or finding.get("title"))
    return enriched


def _infer_expected(title: str, description: str, category: str) -> str:
    """Expectations require a source invariant; keyword inference is forbidden."""
    return ""


def enrich_findings_batch(
    findings: list[dict[str, Any]],
    enterprise_ctx: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(findings, list):
        raise TypeError("findings must be a list")
    return [enrich_finding(finding, enterprise_ctx) for finding in findings if isinstance(finding, dict)]


def load_enterprise_context(project_id: str, root: Path) -> dict[str, Any]:
    """Load explicit connector context; absence stays empty and malformed data fails."""
    ctx: dict[str, Any] = {}
    registry_path = root / "platform_workspace" / project_id / "enterprise_pilot_runtime" / "connector_registry.json"
    if not registry_path.exists():
        return ctx
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(registry, dict):
        raise ValueError(f"connector registry must be an object: {registry_path}")
    connectors = registry.get("connectors", [])
    if not isinstance(connectors, list):
        raise ValueError(f"connector registry connectors must be a list: {registry_path}")
    for connector in connectors:
        if not isinstance(connector, dict) or connector.get("kind") not in {"api", "gateway", "http_api"}:
            continue
        url = _text(connector.get("url"))
        if url and "://" not in url:
            raise ValueError(f"API connector url must be absolute: {registry_path}")
        if url:
            ctx["base_url"] = url.rstrip("/")
            break
    return ctx
