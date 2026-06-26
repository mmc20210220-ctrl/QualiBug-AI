from __future__ import annotations

"""Phase41: domain-agnostic specification and behavior defect mining.

This module intentionally does not rely on an industry taxonomy.  It turns PRD /
requirements / OpenAPI into reusable test oracles, mutation probes and safe
read-only contract checks.  Business-domain playbooks still add value, but this
layer is always available for internal systems, emerging products and projects
with sparse domain terminology.
"""

import copy
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .real_project_onboarding import (
    ROOT,
    _html_escape,
    _join_url,
    _load_json,
    _read_text,
    _safe_project_id,
    _write_json,
    config_paths,
    load_real_project_config,
)

MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PRIVATE_MARKERS = {"private_ground_truth", "ground_truth_bugs", "bug_sets", "enabled_bugs", "current_bug_set", "bug_instance_id"}

RISK_META: dict[str, dict[str, Any]] = {
    "api_contract": {"severity": "P1", "title": "接口契约偏差", "destructive": False},
    "response_schema": {"severity": "P1", "title": "响应结构与文档不一致", "destructive": False},
    "input_validation": {"severity": "P1", "title": "输入校验缺失", "destructive": False},
    "boundary_validation": {"severity": "P1", "title": "边界条件校验缺失", "destructive": False},
    "enum_validation": {"severity": "P2", "title": "枚举约束未生效", "destructive": False},
    "resource_authorization": {"severity": "P1", "title": "资源授权边界风险", "destructive": False},
    "pagination_consistency": {"severity": "P2", "title": "分页一致性风险", "destructive": False},
    "read_consistency": {"severity": "P2", "title": "只读接口结果不稳定", "destructive": False},
    "error_contract": {"severity": "P2", "title": "错误响应契约不一致", "destructive": False},
    "idempotency_generic": {"severity": "P1", "title": "通用重复提交风险", "destructive": True},
    "state_transition_generic": {"severity": "P1", "title": "通用状态转换风险", "destructive": True},
    "concurrency_generic": {"severity": "P1", "title": "通用并发竞态风险", "destructive": True},
    "spec_invariant": {"severity": "P1", "title": "需求约束未被后端强制", "destructive": False},
    "spec_coverage_gap": {"severity": "P2", "title": "需求与接口可验证性缺口", "destructive": False},
    "spec_structure": {"severity": "P2", "title": "接口定义结构矛盾", "destructive": False},
}

RISK_ALIASES = {
    "api_contract": "data_consistency",
    "response_schema": "data_consistency",
    "input_validation": "business_rule",
    "boundary_validation": "business_rule",
    "enum_validation": "business_rule",
    "resource_authorization": "permission_bypass",
    "pagination_consistency": "data_consistency",
    "read_consistency": "state_consistency",
    "error_contract": "data_consistency",
    "idempotency_generic": "idempotency",
    "state_transition_generic": "state_consistency",
    "concurrency_generic": "concurrency",
    "spec_invariant": "business_rule",
    "spec_coverage_gap": "business_rule",
    "spec_structure": "data_consistency",
}

DYNAMIC_FIELD_RE = re.compile(r"(?:^|[_\-.])(time|timestamp|updated|created|trace|request|nonce|token|uuid|version|etag|cursor|next|last)(?:$|[_\-.])", re.I)
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{2,}|[\u4e00-\u9fff]{2,}")


def _read_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8", errors="replace") or "null")
    except Exception:
        return default
    return default


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _resolve_ref(schema: Any, components: dict[str, Any]) -> dict[str, Any]:
    current = schema if isinstance(schema, dict) else {}
    seen: set[str] = set()
    while isinstance(current, dict) and current.get("$ref"):
        ref = str(current.get("$ref"))
        if ref in seen:
            break
        seen.add(ref)
        if not ref.startswith("#/components/schemas/"):
            break
        name = ref.rsplit("/", 1)[-1]
        target = ((components.get("schemas") or {}).get(name) if isinstance(components, dict) else None)
        if not isinstance(target, dict):
            break
        merged = dict(target)
        for key, value in current.items():
            if key != "$ref":
                merged[key] = value
        current = merged
    return current if isinstance(current, dict) else {}


def _schema_type(schema: dict[str, Any], components: dict[str, Any]) -> str:
    schema = _resolve_ref(schema, components)
    value = str(schema.get("type") or "")
    if value:
        return value
    if "properties" in schema:
        return "object"
    if "items" in schema:
        return "array"
    return "unknown"


def _extract_schema_fields(schema: Any, components: dict[str, Any], prefix: str = "", depth: int = 0) -> list[dict[str, Any]]:
    if depth > 4:
        return []
    node = _resolve_ref(schema, components)
    if not node:
        return []
    required = set(str(x) for x in node.get("required") or [])
    fields: list[dict[str, Any]] = []
    if _schema_type(node, components) == "object":
        for name, child in (node.get("properties") or {}).items():
            if not isinstance(child, dict):
                continue
            path = f"{prefix}.{name}" if prefix else str(name)
            resolved = _resolve_ref(child, components)
            row = {
                "name": str(name),
                "field_path": path,
                "type": _schema_type(resolved, components),
                "required": str(name) in required,
                "enum": list(resolved.get("enum") or [])[:20],
                "minimum": resolved.get("minimum"),
                "maximum": resolved.get("maximum"),
                "min_length": resolved.get("minLength"),
                "max_length": resolved.get("maxLength"),
                "format": resolved.get("format"),
                "read_only": bool(resolved.get("readOnly")),
                "nullable": bool(resolved.get("nullable")),
            }
            fields.append(row)
            fields.extend(_extract_schema_fields(resolved, components, path, depth + 1))
    elif _schema_type(node, components) == "array":
        fields.extend(_extract_schema_fields(node.get("items") or {}, components, f"{prefix}[]" if prefix else "[]", depth + 1))
    return fields


def _response_schema(operation: dict[str, Any], components: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    responses = operation.get("responses") or {}
    if not isinstance(responses, dict):
        return "", {}
    preferred = []
    for key in responses:
        text = str(key)
        if text.startswith("2"):
            preferred.append(text)
    for code in [*sorted(preferred), "default", *sorted(map(str, responses.keys()))]:
        spec = responses.get(code)
        if not isinstance(spec, dict):
            continue
        content = spec.get("content") or {}
        if not isinstance(content, dict):
            continue
        for media in ("application/json", "application/problem+json", *content.keys()):
            body = content.get(media)
            if isinstance(body, dict) and isinstance(body.get("schema"), dict):
                return str(code), _resolve_ref(body["schema"], components)
    return "", {}


def _request_schema(operation: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    body = operation.get("requestBody") or {}
    if not isinstance(body, dict):
        return {}
    content = body.get("content") or {}
    if not isinstance(content, dict):
        return {}
    for media in ("application/json", "application/x-www-form-urlencoded", *content.keys()):
        value = content.get(media)
        if isinstance(value, dict) and isinstance(value.get("schema"), dict):
            return _resolve_ref(value["schema"], components)
    return {}


def _operation_text(path: str, method: str, operation: dict[str, Any]) -> str:
    parts = [path, method, operation.get("operationId"), operation.get("summary"), operation.get("description"), operation.get("tags")]
    return _normalize_text(parts).lower()


def _path_tokens(path: str) -> set[str]:
    raw = re.sub(r"\{[^{}]+\}", " ", str(path).lower())
    tokens = {x.lower() for x in WORD_RE.findall(raw)}
    for piece in re.split(r"[/_.\-]+", raw):
        piece = piece.strip()
        if len(piece) >= 3:
            tokens.add(piece)
    return tokens


def _operations(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    components = openapi.get("components") or {}
    items: list[dict[str, Any]] = []
    for path, methods in (openapi.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        path_parameters = methods.get("parameters") if isinstance(methods.get("parameters"), list) else []
        for method, raw_operation in methods.items():
            method_u = str(method).upper()
            if method_u not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                continue
            operation = raw_operation if isinstance(raw_operation, dict) else {}
            params = []
            for param in [*path_parameters, *(operation.get("parameters") or [])]:
                if isinstance(param, dict):
                    params.append(param)
            req_schema = _request_schema(operation, components)
            response_code, resp_schema = _response_schema(operation, components)
            items.append({
                "method": method_u,
                "path": str(path),
                "operation_id": operation.get("operationId"),
                "summary": operation.get("summary") or "",
                "description": operation.get("description") or "",
                "tags": operation.get("tags") or [],
                "raw_operation": operation,
                "parameters": params,
                "request_schema": req_schema,
                "response_schema": resp_schema,
                "response_code": response_code,
                "request_fields": _extract_schema_fields(req_schema, components),
                "response_fields": _extract_schema_fields(resp_schema, components),
                "text": _operation_text(str(path), method_u, operation),
                "path_tokens": sorted(_path_tokens(str(path))),
            })
    return items


def _classify_requirement(text: str) -> str:
    lower = text.lower()
    if any(x in lower for x in ["幂等", "重复", "一次", "唯一", "duplicate", "idempot", "unique", "once"]):
        return "idempotency"
    if any(x in lower for x in ["权限", "归属", "仅", "只有", "不能访问", "不得访问", "permission", "authorize", "owner", "role", "tenant"]):
        return "authorization"
    if any(x in lower for x in ["状态", "流程", "审批", "流转", "回退", "state", "transition", "workflow"]):
        return "state_transition"
    if any(x in lower for x in ["上限", "下限", "最大", "最小", "范围", "超过", "不少于", "不超过", "limit", "maximum", "minimum", "range"]):
        return "boundary"
    if any(x in lower for x in ["一致", "相等", "匹配", "同步", "守恒", "consistent", "match", "same"]):
        return "consistency"
    if any(x in lower for x in ["必须", "不能", "不得", "禁止", "only", "must", "should not", "forbid"]):
        return "negative_constraint"
    return "general"


def _extract_requirement_rules(text: str) -> list[dict[str, Any]]:
    if not text:
        return []
    clauses = re.split(r"[。；;!！?？\n]+", text)
    rows: list[dict[str, Any]] = []
    for clause in clauses:
        clean = re.sub(r"\s+", " ", clause).strip(" -：:")
        if len(clean) < 5 or len(clean) > 320:
            continue
        rule_type = _classify_requirement(clean)
        if rule_type == "general" and not re.search(r"必须|不能|不得|禁止|only|must|should|limit|范围|状态|权限", clean, re.I):
            continue
        tokens = sorted({x.lower() for x in WORD_RE.findall(clean) if len(x) >= 2})[:20]
        rows.append({
            "rule_id": f"REQ_{len(rows)+1:03d}",
            "rule_type": rule_type,
            "statement": clean,
            "keywords": tokens,
            "test_oracle": _oracle_for_requirement(rule_type, clean),
        })
    # Deduplicate close copies from repeated documents.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["rule_type"], re.sub(r"\W+", "", row["statement"].lower()))
        if key not in seen:
            seen.add(key)
            unique.append(row)
    return unique[:160]


def _oracle_for_requirement(rule_type: str, statement: str) -> str:
    mapping = {
        "idempotency": "对同一请求标识、同一资源或等价参数执行重放，业务副作用应保持一次。",
        "authorization": "更换低权限主体、资源标识或租户范围后，系统应拒绝未授权访问和操作。",
        "state_transition": "构造顺序错误、重复或回退操作，后端应拒绝不允许的状态转换。",
        "boundary": "在阈值前、阈值、阈值后和空值/极值处，后端应保持规则一致。",
        "consistency": "前后查询、关联接口和汇总字段应满足声明的一致性关系。",
        "negative_constraint": "构造违反该约束的输入或调用顺序，后端应显式拒绝且无副作用。",
    }
    return mapping.get(rule_type, f"将需求规则转为可执行断言：{statement}")


def _operation_match_score(rule: dict[str, Any], operation: dict[str, Any]) -> float:
    text = operation.get("text") or ""
    tokens = set(operation.get("path_tokens") or []) | set(WORD_RE.findall(text))
    hits = [x for x in rule.get("keywords") or [] if x.lower() in {str(t).lower() for t in tokens} or x.lower() in text]
    score = len(hits)
    kind = str(rule.get("rule_type") or "")
    method = str(operation.get("method") or "GET")
    if kind in {"idempotency", "state_transition"} and method in MUTATION_METHODS:
        score += 1.2
    if kind == "authorization" and ("{" in str(operation.get("path") or "") or method in {"GET", "DELETE", "PUT", "PATCH"}):
        score += 0.7
    if kind == "boundary" and operation.get("request_fields"):
        score += 0.5
    return score


def _path_parameters(path: str) -> list[str]:
    return re.findall(r"\{([^{}]+)\}", str(path))


def _parameter_schema(param: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    schema = param.get("schema") if isinstance(param, dict) else {}
    return _resolve_ref(schema, components) if isinstance(schema, dict) else {}


def _operation_structure_findings(openapi: dict[str, Any], ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    components = openapi.get("components") or {}
    findings: list[dict[str, Any]] = []
    operation_ids: dict[str, list[dict[str, Any]]] = {}
    for op in ops:
        opid = str(op.get("operation_id") or "").strip()
        if opid:
            operation_ids.setdefault(opid, []).append(op)
        declared_path_params = {str(p.get("name")) for p in op.get("parameters") or [] if str(p.get("in")) == "path"}
        for name in _path_parameters(str(op.get("path") or "")):
            if name not in declared_path_params:
                findings.append({
                    "finding_id": f"STR_{len(findings)+1:03d}",
                    "risk_type": "spec_structure",
                    "severity": "P2",
                    "path": op.get("path"),
                    "method": op.get("method"),
                    "title": "路径参数未声明",
                    "detail": f"路径参数 {{{name}}} 未在 parameters 中声明，自动生成测试数据与路由校验可能偏离。",
                    "evidence": {"path_parameter": name},
                })
        for param in op.get("parameters") or []:
            schema = _parameter_schema(param, components)
            low, high = schema.get("minimum"), schema.get("maximum")
            if low is not None and high is not None:
                try:
                    if float(low) > float(high):
                        findings.append({
                            "finding_id": f"STR_{len(findings)+1:03d}", "risk_type": "spec_structure", "severity": "P1",
                            "path": op.get("path"), "method": op.get("method"), "title": "参数范围定义矛盾",
                            "detail": f"参数 {param.get('name')} 的 minimum 大于 maximum。", "evidence": {"minimum": low, "maximum": high},
                        })
                except Exception:
                    pass
        for field in op.get("request_fields") or []:
            if field.get("required") and field.get("read_only"):
                findings.append({
                    "finding_id": f"STR_{len(findings)+1:03d}", "risk_type": "spec_structure", "severity": "P1",
                    "path": op.get("path"), "method": op.get("method"), "title": "请求字段同时 required 与 readOnly",
                    "detail": f"字段 {field.get('field_path')} 同时要求提交且标记为只读。", "evidence": {"field": field.get("field_path")},
                })
    for opid, repeated in operation_ids.items():
        if len(repeated) > 1:
            findings.append({
                "finding_id": f"STR_{len(findings)+1:03d}", "risk_type": "spec_structure", "severity": "P2",
                "path": ", ".join(f"{x.get('method')} {x.get('path')}" for x in repeated[:5]), "method": "MULTI",
                "title": "operationId 重复", "detail": f"operationId {opid} 在 {len(repeated)} 个接口中重复，客户端/回归用例可能路由错误。", "evidence": {"operation_id": opid},
            })
    return findings[:200]


def _probe_meta(risk_type: str) -> dict[str, Any]:
    meta = RISK_META.get(risk_type, RISK_META["spec_invariant"])
    return {"severity": meta["severity"], "title": meta["title"], "destructive": bool(meta["destructive"])}


def _probe(
    number: int,
    risk_type: str,
    operation: dict[str, Any],
    *,
    title_suffix: str = "",
    expected: str = "后端应拒绝异常输入或保持契约一致",
    bug_signal: str = "异常请求成功、返回结构偏离契约或产生不应有副作用",
    execution_policy: str = "safe_read_only",
    oracle: dict[str, Any] | None = None,
    mutation: dict[str, Any] | None = None,
    requirement_rule: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = _probe_meta(risk_type)
    method = str(operation.get("method") or "GET")
    destructive = bool(meta["destructive"]) or method in MUTATION_METHODS
    policy = execution_policy
    if destructive and policy == "safe_read_only":
        policy = "candidate_only"
    return {
        "probe_id": f"UDM_{number:04d}",
        "source": "universal_spec_behavior",
        "universal_risk_type": risk_type,
        "risk_type": RISK_ALIASES.get(risk_type, "business_rule"),
        "title": f"{meta['title']}{('：' + title_suffix) if title_suffix else ''}",
        "severity": meta["severity"],
        "actor": "normal_user",
        "path": operation.get("path"),
        "method": method,
        "operation_id": operation.get("operation_id"),
        "expected": expected,
        "bug_signal": bug_signal,
        "destructive": destructive,
        "execution_policy": policy,
        "test_oracle": oracle or {},
        "mutation_blueprint": mutation or {},
        "requirement_rule_id": (requirement_rule or {}).get("rule_id"),
        "requirement_statement": (requirement_rule or {}).get("statement"),
        "discovery_mode": "universal",
    }


def _value_mutations(field: dict[str, Any]) -> list[dict[str, Any]]:
    name = str(field.get("field_path") or field.get("name") or "field")
    kind = str(field.get("type") or "unknown")
    rows: list[dict[str, Any]] = []
    if field.get("required"):
        rows.append({"kind": "remove_required", "field": name, "description": "移除必填字段"})
    enum = field.get("enum") or []
    if enum:
        rows.append({"kind": "invalid_enum", "field": name, "value": "__QUALIBUG_INVALID_ENUM__", "description": "注入未声明枚举值"})
    if kind in {"integer", "number"}:
        if field.get("minimum") is not None:
            rows.append({"kind": "below_minimum", "field": name, "value": _numeric_below(field.get("minimum")), "description": "小于最小值"})
        if field.get("maximum") is not None:
            rows.append({"kind": "above_maximum", "field": name, "value": _numeric_above(field.get("maximum")), "description": "大于最大值"})
        rows.append({"kind": "numeric_type_confusion", "field": name, "value": "not-a-number", "description": "数值字段类型错配"})
    if kind == "string":
        if field.get("min_length") is not None:
            rows.append({"kind": "shorter_than_min_length", "field": name, "value": "", "description": "短于最小长度"})
        if field.get("max_length") is not None:
            rows.append({"kind": "longer_than_max_length", "field": name, "value": "X" * min(1024, int(field.get("max_length") or 0) + 1), "description": "超出最大长度"})
        rows.append({"kind": "string_type_confusion", "field": name, "value": {"unexpected": True}, "description": "字符串字段类型错配"})
    if kind == "boolean":
        rows.append({"kind": "boolean_type_confusion", "field": name, "value": "true-ish", "description": "布尔字段类型错配"})
    return rows[:4]


def _numeric_below(value: Any) -> float:
    try:
        return float(value) - 1
    except Exception:
        return -1


def _numeric_above(value: Any) -> float:
    try:
        return float(value) + 1
    except Exception:
        return 1


def generate_universal_defect_probes(
    openapi: dict[str, Any],
    cfg: dict[str, Any],
    project_id: str,
    root: Path | None = None,
    max_count: int | None = None,
) -> list[dict[str, Any]]:
    """Generate domain-independent probes from interface structure and requirements.

    The function is intentionally deterministic: the same PRD/OpenAPI generates
    the same probe ids/order, making outcomes suitable for regression learning.
    """
    root = root or ROOT
    project = _safe_project_id(project_id)
    limit = int(max_count or max(80, int(cfg.get("max_probe_count") or 100) * 2))
    components = openapi.get("components") or {}
    ops = _operations(openapi)
    profile = load_universal_defect_mining(project, root)
    rules = list((profile or {}).get("requirement_rules") or [])
    if not rules:
        paths = config_paths(project, root)
        text = "\n".join(_read_text(paths["input_dir"] / name) for name in ["prd.md", "requirements.md", "business_rules.md"])
        rules = _extract_requirement_rules(text)

    probes: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    def add(risk: str, op: dict[str, Any], **kwargs: Any) -> None:
        if len(probes) >= limit:
            return
        sample = _probe(len(probes) + 1, risk, op, **kwargs)
        mutation_kind = str((sample.get("mutation_blueprint") or {}).get("kind") or "")
        key = (str(sample.get("universal_risk_type")), str(sample.get("method")), str(sample.get("path")), mutation_kind)
        if key in seen:
            return
        seen.add(key)
        probes.append(sample)

    for op in ops:
        method = str(op.get("method") or "GET")
        request_fields = list(op.get("request_fields") or [])
        params = [p for p in op.get("parameters") or [] if isinstance(p, dict)]
        all_fields = list(request_fields)
        for param in params:
            schema = _parameter_schema(param, components)
            all_fields.append({
                "name": str(param.get("name") or "parameter"),
                "field_path": f"{param.get('in') or 'query'}.{param.get('name') or 'parameter'}",
                "type": _schema_type(schema, components),
                "required": bool(param.get("required")),
                "enum": list(schema.get("enum") or [])[:20],
                "minimum": schema.get("minimum"), "maximum": schema.get("maximum"),
                "min_length": schema.get("minLength"), "max_length": schema.get("maxLength"),
                "format": schema.get("format"), "read_only": False, "nullable": bool(schema.get("nullable")),
            })

        if op.get("response_schema"):
            add(
                "response_schema", op,
                title_suffix="响应 JSON 与 OpenAPI schema 校验",
                expected="2xx 响应的字段、类型、必填项和枚举满足 OpenAPI 契约",
                bug_signal="响应成功但缺失必填字段、类型不符或返回未声明状态码",
                oracle={"kind": "response_schema", "response_code": op.get("response_code"), "schema": op.get("response_schema")},
            )
        else:
            add(
                "api_contract", op,
                title_suffix="缺少可验证成功响应定义",
                expected="每个操作应定义可验证的成功响应契约",
                bug_signal="接口文档无法生成稳定断言，真实返回无法自动校验",
                execution_policy="candidate_only",
                oracle={"kind": "document_contract_gap"},
            )

        for field in all_fields:
            for mutation in _value_mutations(field):
                kind = str(mutation.get("kind") or "")
                risk = "input_validation"
                if kind == "invalid_enum":
                    risk = "enum_validation"
                elif kind in {"below_minimum", "above_maximum", "shorter_than_min_length", "longer_than_max_length"}:
                    risk = "boundary_validation"
                add(
                    risk, op,
                    title_suffix=str(mutation.get("description") or field.get("field_path")),
                    expected="后端返回明确 4xx 校验错误，且不产生业务副作用",
                    bug_signal="非法值返回成功、被静默截断/转换，或错误结构不稳定",
                    execution_policy="safe_read_only" if method == "GET" else "candidate_only",
                    oracle={"kind": "negative_input", "field": field.get("field_path"), "expected_status_family": "4xx"},
                    mutation=mutation,
                )

        if _path_parameters(str(op.get("path") or "")) and method in {"GET", "PUT", "PATCH", "DELETE"}:
            add(
                "resource_authorization", op,
                title_suffix="替换资源标识后的归属校验",
                expected="切换资源标识或主体后，未授权资源返回 401/403/404，不能泄露或修改数据",
                bug_signal="低权限主体可读取、更新或删除其他资源",
                execution_policy="safe_read_only" if method == "GET" else "candidate_only",
                oracle={"kind": "resource_authorization", "path_parameters": _path_parameters(str(op.get("path") or ""))},
                mutation={"kind": "alternate_resource_identifier", "description": "替换路径资源标识为另一个测试资源"},
            )

        parameter_names = {str(p.get("name") or "").lower() for p in params}
        if {"page", "limit", "offset", "cursor", "size"} & parameter_names:
            add(
                "pagination_consistency", op,
                title_suffix="边界分页与总量一致性",
                expected="分页边界、空页、重复 cursor 和总量/next 标识应一致",
                bug_signal="重复/漏数据、负分页成功、total 与数据集不匹配",
                execution_policy="safe_read_only" if method == "GET" else "candidate_only",
                oracle={"kind": "pagination", "parameters": sorted({"page", "limit", "offset", "cursor", "size"} & parameter_names)},
                mutation={"kind": "pagination_boundary", "values": [0, 1, -1, 999999]},
            )

        if method == "GET":
            add(
                "read_consistency", op,
                title_suffix="等价重复读取的一致性",
                expected="同一请求在短时间内除时间戳/追踪字段外结构与核心值稳定",
                bug_signal="无外部写入时同一读取结果无解释变化或返回结构漂移",
                oracle={"kind": "repeat_read", "ignore_dynamic_fields": True, "repeat": 2},
            )

        text = str(op.get("text") or "")
        if method in MUTATION_METHODS and not re.search(r"login|auth|token|session|logout|signin|signup|注册|登录", text, re.I):
            add(
                "idempotency_generic", op,
                title_suffix="等价请求重放",
                expected="相同幂等键/等价输入不能重复创建、重复推进或重复扣减",
                bug_signal="第二次提交产生额外副作用或返回不一致资源",
                execution_policy="candidate_only",
                oracle={"kind": "replay_equivalence", "repeat": 2},
                mutation={"kind": "duplicate_request", "description": "使用同一请求标识或等价 payload 重放"},
            )
            add(
                "concurrency_generic", op,
                title_suffix="并发等价请求",
                expected="并发执行同一业务动作时，系统保持唯一性、容量和状态约束",
                bug_signal="重复数据、负库存、超容量、状态跳跃或异常 5xx",
                execution_policy="candidate_only",
                oracle={"kind": "concurrent_equivalence", "parallelism": 2},
                mutation={"kind": "parallel_duplicate_request", "parallelism": 2},
            )
        if method in MUTATION_METHODS and re.search(r"state|status|approve|reject|cancel|submit|activate|deactivate|publish|close|open|审批|取消|提交|启用|停用|发布|关闭|状态", text, re.I):
            add(
                "state_transition_generic", op,
                title_suffix="非法顺序与回退",
                expected="未满足前置状态、重复动作或回退操作被明确拒绝",
                bug_signal="状态跳跃、重复推进、回退成功或状态与返回不一致",
                execution_policy="candidate_only",
                oracle={"kind": "state_transition", "sequence": "invalid_or_repeated"},
                mutation={"kind": "invalid_action_order", "description": "先执行后置动作/重复动作/回退动作"},
            )

    for rule in rules:
        matches = sorted(ops, key=lambda op: (-_operation_match_score(rule, op), str(op.get("path") or "")))
        matched = [op for op in matches if _operation_match_score(rule, op) >= 1.0][:4]
        if not matched:
            # A PRD rule without a matching API becomes a traceability gap rather than a fake bug.
            generic_op = {"method": "GET", "path": "/", "operation_id": None}
            add(
                "spec_coverage_gap", generic_op,
                title_suffix=f"需求规则 {rule.get('rule_id')} 未映射到接口",
                expected="每项关键需求都有可追溯接口、断言和可观测数据",
                bug_signal="关键规则无法建立测试闭环，存在遗漏实现或不可验证风险",
                execution_policy="candidate_only",
                oracle={"kind": "requirement_traceability", "rule": rule},
                requirement_rule=rule,
            )
            continue
        for op in matched:
            risk = {
                "idempotency": "idempotency_generic",
                "authorization": "resource_authorization",
                "state_transition": "state_transition_generic",
                "boundary": "boundary_validation",
                "consistency": "spec_invariant",
                "negative_constraint": "spec_invariant",
            }.get(str(rule.get("rule_type") or ""), "spec_invariant")
            add(
                risk, op,
                title_suffix=f"需求规则 {rule.get('rule_id')}",
                expected=str(rule.get("test_oracle") or "需求约束应由后端强制执行"),
                bug_signal="违反需求约束的调用仍成功或产生不可逆副作用",
                execution_policy="safe_read_only" if str(op.get("method")) == "GET" else "candidate_only",
                oracle={"kind": "requirement_invariant", "rule": rule},
                requirement_rule=rule,
            )

    return probes[:limit]


def _safe_summary(ops: list[dict[str, Any]], rules: list[dict[str, Any]], findings: list[dict[str, Any]], probes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "operation_count": len(ops),
        "get_operation_count": sum(1 for op in ops if op.get("method") == "GET"),
        "mutation_operation_count": sum(1 for op in ops if op.get("method") in MUTATION_METHODS),
        "parameterized_operation_count": sum(1 for op in ops if "{" in str(op.get("path") or "")),
        "request_schema_operation_count": sum(1 for op in ops if op.get("request_schema")),
        "response_schema_operation_count": sum(1 for op in ops if op.get("response_schema")),
        "requirement_rule_count": len(rules),
        "structure_finding_count": len(findings),
        "probe_count": len(probes),
        "safe_read_probe_count": sum(1 for p in probes if p.get("execution_policy") == "safe_read_only"),
        "candidate_only_probe_count": sum(1 for p in probes if p.get("execution_policy") == "candidate_only"),
        "risk_distribution": _count([str(p.get("universal_risk_type") or "unknown") for p in probes]),
    }


def _count(values: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda row: (-row[1], row[0])))


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False).lower()
    leaks = sorted(marker for marker in PRIVATE_MARKERS if marker.lower() in text)
    return {"passed": not leaks, "leak_terms": leaks}


def build_universal_defect_mining_profile(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    paths = config_paths(project, root)
    cfg = load_real_project_config(project, root)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    openapi = openapi if isinstance(openapi, dict) else {}
    ops = _operations(openapi)
    text = "\n".join(_read_text(paths["input_dir"] / name) for name in ["prd.md", "requirements.md", "business_rules.md"])
    rules = _extract_requirement_rules(text)
    findings = _operation_structure_findings(openapi, ops)
    preview_limit = max(20, min(int(options.get("preview_probe_count") or 160), 600))
    probes = generate_universal_defect_probes(openapi, cfg, project, root, max_count=preview_limit)
    compact_ops = [{k: op.get(k) for k in ("method", "path", "operation_id", "summary", "response_code", "path_tokens")} for op in ops]
    result = {
        "phase": "phase41_universal_spec_behavior_mining",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": _safe_summary(ops, rules, findings, probes),
        "requirement_rules": rules,
        "structure_findings": findings,
        "operation_inventory": compact_ops,
        "preview_probes": probes,
        "governance": {
            "domain_agnostic": True,
            "uses_only_project_prd_requirements_openapi": True,
            "safe_read_execution_only_by_default": True,
            "write_and_concurrency_probes_are_candidate_only_by_default": True,
            "uses_no_benchmark_answer_files": True,
        },
    }
    result["private_leak_check"] = _private_leak_check(result)
    out_dir = root / "platform_outputs" / project / "universal_defect_mining"
    ws_dir = root / "platform_workspace" / project / "defect_discovery"
    _write_json(out_dir / "universal_defect_mining.json", result)
    _write_json(out_dir / "universal_defect_mining_summary.json", {"summary": result["summary"], "private_leak_check": result["private_leak_check"]})
    _write_json(ws_dir / "universal_defect_mining.json", result)
    (out_dir / "universal_defect_mining_report.html").write_text(render_universal_defect_mining_report(result), encoding="utf-8")
    return result


def load_universal_defect_mining(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    path = root / "platform_workspace" / project / "defect_discovery" / "universal_defect_mining.json"
    if not path.exists():
        return None
    data = _read_json(path, {})
    return data if isinstance(data, dict) else None


def _render_path(path: str) -> str:
    def fill(match: re.Match[str]) -> str:
        name = match.group(1).lower()
        if any(x in name for x in ("uuid", "guid")):
            return "00000000-0000-4000-8000-000000000001"
        if any(x in name for x in ("id", "number", "code")):
            return "1"
        return "qualibug-sample"

    return re.sub(r"\{([^{}]+)\}", fill, path)


def _http_get(url: str, token: str | None, timeout: int) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    try:
        req = urllib.request.Request(url, method="GET", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            text = response.read(300_000).decode("utf-8", errors="replace")
            return {"status_code": response.status, "headers": dict(response.headers.items()), "body": text, "error": None}
    except urllib.error.HTTPError as exc:
        try:
            text = exc.read(300_000).decode("utf-8", errors="replace")
        except Exception:
            text = ""
        return {"status_code": exc.code, "headers": dict(exc.headers.items()) if exc.headers else {}, "body": text, "error": str(exc)}
    except Exception as exc:
        return {"status_code": None, "headers": {}, "body": "", "error": str(exc)}


def _type_matches(value: Any, expected: str) -> bool:
    if expected in {"", "unknown"}:
        return True
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    return True


def validate_response_schema(value: Any, schema: dict[str, Any], components: dict[str, Any], path: str = "$", depth: int = 0) -> list[str]:
    if depth > 6:
        return []
    schema = _resolve_ref(schema, components)
    if not schema:
        return []
    if value is None and schema.get("nullable"):
        return []
    expected = _schema_type(schema, components)
    errors: list[str] = []
    if not _type_matches(value, expected):
        return [f"{path}: expected {expected}, got {type(value).__name__}"]
    enum = schema.get("enum") or []
    if enum and value not in enum:
        errors.append(f"{path}: value is not in enum")
    if expected == "object" and isinstance(value, dict):
        for name in schema.get("required") or []:
            if name not in value:
                errors.append(f"{path}.{name}: required field missing")
        for name, child in (schema.get("properties") or {}).items():
            if name in value and isinstance(child, dict):
                errors.extend(validate_response_schema(value[name], child, components, f"{path}.{name}", depth + 1))
    if expected == "array" and isinstance(value, list) and isinstance(schema.get("items"), dict):
        for idx, item in enumerate(value[:20]):
            errors.extend(validate_response_schema(item, schema["items"], components, f"{path}[{idx}]", depth + 1))
    return errors[:80]


def _strip_dynamic(value: Any, key: str = "") -> Any:
    if DYNAMIC_FIELD_RE.search(key):
        return "<dynamic>"
    if isinstance(value, dict):
        return {k: _strip_dynamic(v, str(k)) for k, v in value.items() if not DYNAMIC_FIELD_RE.search(str(k))}
    if isinstance(value, list):
        return [_strip_dynamic(v, key) for v in value[:100]]
    return value


def run_universal_defect_mining(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the profile and optionally execute only GET/HEAD contract checks.

    Modes: plan_only (default) and safe_live.  No write request, replay or
    concurrency action is executed here; those remain evidence-backed candidates.
    """
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    execution_mode = str(options.get("execution_mode") or "plan_only").lower()
    if execution_mode not in {"plan_only", "safe_live"}:
        execution_mode = "plan_only"
    profile = build_universal_defect_mining_profile(project, root, options)
    cfg = load_real_project_config(project, root)
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    timeout = max(1, int(cfg.get("request_timeout_seconds") or 10))
    max_safe = max(1, min(int(options.get("max_safe_probe_count") or 30), 120))
    paths = config_paths(project, root)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    openapi = openapi if isinstance(openapi, dict) else {}
    components = openapi.get("components") or {}
    ops_map = {(op["method"], op["path"]): op for op in _operations(openapi)}
    accounts = _load_json(paths["input_dir"] / "test_accounts.json", {})
    account = (accounts.get("normal_user") or accounts.get("normal") or accounts.get("user") or {}) if isinstance(accounts, dict) else {}
    token = account.get("token") if isinstance(account, dict) else None

    safe_probes = [p for p in profile.get("preview_probes") or [] if p.get("execution_policy") == "safe_read_only" and p.get("method") == "GET"][:max_safe]
    executions: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    if execution_mode == "safe_live" and base_url:
        for probe in safe_probes:
            path = _render_path(str(probe.get("path") or "/"))
            url = _join_url(base_url, path)
            response = _http_get(url, token, timeout)
            record = {"probe_id": probe.get("probe_id"), "method": "GET", "path": path, "status_code": response.get("status_code"), "error": response.get("error"), "checks": []}
            status = response.get("status_code")
            op = ops_map.get(("GET", str(probe.get("path") or ""))) or {}
            universal_risk = str(probe.get("universal_risk_type") or "")
            if status is not None and 200 <= int(status) < 300:
                raw = response.get("body") or ""
                try:
                    body = json.loads(raw)
                except Exception:
                    body = None
                if universal_risk == "response_schema" and op.get("response_schema") and body is not None:
                    errors = validate_response_schema(body, op["response_schema"], components)
                    record["checks"].append({"kind": "response_schema", "errors": errors})
                    if errors:
                        findings.append(_live_finding(probe, response, "响应成功但不满足 OpenAPI schema：" + "; ".join(errors[:4]), 0.88))
                elif universal_risk == "read_consistency":
                    second = _http_get(url, token, timeout)
                    try:
                        first_body = _strip_dynamic(json.loads(raw))
                        second_body = _strip_dynamic(json.loads(second.get("body") or ""))
                        differs = first_body != second_body
                    except Exception:
                        differs = False
                    record["checks"].append({"kind": "repeat_read", "second_status": second.get("status_code"), "core_payload_differs": differs})
                    if differs and second.get("status_code") == status:
                        findings.append(_live_finding(probe, response, "短时间等价读取的核心载荷发生变化，需要排查缓存、排序或并发可见性。", 0.58))
                elif universal_risk == "api_contract":
                    record["checks"].append({"kind": "document_contract_gap", "status": status})
            executions.append(record)
    else:
        for probe in safe_probes:
            executions.append({"probe_id": probe.get("probe_id"), "method": "GET", "path": probe.get("path"), "status_code": None, "error": "plan_only_or_missing_base_url", "checks": []})

    result = {
        "phase": "phase41_universal_spec_behavior_mining",
        "project_id": project,
        "project_name": profile.get("project_name") or project,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "summary": {
            **profile.get("summary", {}),
            "execution_mode": execution_mode,
            "safe_execution_probe_count": len(safe_probes),
            "safe_execution_count": len([x for x in executions if x.get("status_code") is not None]),
            "live_finding_count": len(findings),
            "candidate_write_or_concurrency_probe_count": sum(1 for p in profile.get("preview_probes") or [] if p.get("execution_policy") == "candidate_only"),
        },
        "profile": profile,
        "safe_executions": executions,
        "live_findings": findings,
        "candidate_probes": [p for p in profile.get("preview_probes") or [] if p.get("execution_policy") == "candidate_only"],
        "governance": {
            "execution_mode": execution_mode,
            "live_requests_limited_to_read_only": True,
            "write_replay_disabled": True,
            "concurrency_execution_disabled": True,
            "uses_no_benchmark_answer_files": True,
        },
    }
    result["private_leak_check"] = _private_leak_check(result)
    out_dir = root / "platform_outputs" / project / "universal_defect_mining"
    ws_dir = root / "platform_workspace" / project / "defect_discovery"
    _write_json(out_dir / "universal_defect_mining_run.json", result)
    _write_json(ws_dir / "universal_defect_mining_run.json", result)
    (out_dir / "universal_defect_mining_run_report.html").write_text(render_universal_defect_mining_run_report(result), encoding="utf-8")
    return result


def _live_finding(probe: dict[str, Any], response: dict[str, Any], actual: str, confidence: float) -> dict[str, Any]:
    return {
        "issue_id": f"UDM_ISSUE_{probe.get('probe_id')}",
        "probe_id": probe.get("probe_id"),
        "title": probe.get("title"),
        "risk_type": probe.get("risk_type"),
        "universal_risk_type": probe.get("universal_risk_type"),
        "severity": probe.get("severity"),
        "confidence": confidence,
        "status": "needs_human_review",
        "expected": probe.get("expected"),
        "actual": actual,
        "evidence": {"request": {"method": "GET", "path": probe.get("path")}, "response": {"status_code": response.get("status_code"), "body_excerpt": (response.get("body") or "")[:600], "error": response.get("error")}},
    }


def render_universal_defect_mining_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(key)}</span><b>{_html_escape(value)}</b></div>" for key, value in summary.items() if key not in {"risk_distribution"})
    rules = "".join(
        f"<tr><td>{_html_escape(row.get('rule_id'))}</td><td>{_html_escape(row.get('rule_type'))}</td><td>{_html_escape(row.get('statement'))}</td><td>{_html_escape(row.get('test_oracle'))}</td></tr>"
        for row in (data.get("requirement_rules") or [])[:80]
    )
    findings = "".join(
        f"<tr><td>{_html_escape(row.get('severity'))}</td><td>{_html_escape(row.get('method'))} {_html_escape(row.get('path'))}</td><td>{_html_escape(row.get('title'))}</td><td>{_html_escape(row.get('detail'))}</td></tr>"
        for row in (data.get("structure_findings") or [])[:80]
    )
    probes = "".join(
        f"<tr><td>{_html_escape(row.get('probe_id'))}</td><td>{_html_escape(row.get('severity'))}</td><td>{_html_escape(row.get('universal_risk_type'))}</td><td>{_html_escape(row.get('method'))} {_html_escape(row.get('path'))}</td><td>{_html_escape(row.get('execution_policy'))}</td><td>{_html_escape(row.get('expected'))}</td></tr>"
        for row in (data.get("preview_probes") or [])[:120]
    )
    leak = data.get("private_leak_check") or {}
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>通用规格与行为缺陷挖掘</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top;word-break:break-word}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#ecfdf5;color:#065f46}}</style></head><body>
<section class='hero'><span class='badge'>Phase41 · Domain-Agnostic</span><h1>通用规格与行为缺陷挖掘</h1><p>不依赖行业词库：从需求约束、OpenAPI schema、参数边界、资源标识、读一致性和重放行为中生成可验证 Bug 探针。</p><p>私有数据泄露检查：<b>{_html_escape('passed' if leak.get('passed') else 'failed')}</b></p></section>
<section class='panel'><h2>覆盖概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>需求规则 → 测试 Oracle</h2><table><thead><tr><th>ID</th><th>类型</th><th>需求</th><th>Oracle</th></tr></thead><tbody>{rules or '<tr><td colspan="4">未提取到显式规则</td></tr>'}</tbody></table></section>
<section class='panel'><h2>OpenAPI 结构问题</h2><table><thead><tr><th>等级</th><th>接口</th><th>问题</th><th>说明</th></tr></thead><tbody>{findings or '<tr><td colspan="4">未发现结构性矛盾</td></tr>'}</tbody></table></section>
<section class='panel'><h2>通用高价值探针</h2><table><thead><tr><th>ID</th><th>等级</th><th>类型</th><th>接口</th><th>执行策略</th><th>断言</th></tr></thead><tbody>{probes or '<tr><td colspan="6">暂无探针</td></tr>'}</tbody></table></section></body></html>"""


def render_universal_defect_mining_run_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(key)}</span><b>{_html_escape(value)}</b></div>" for key, value in summary.items() if key not in {"risk_distribution"})
    findings = "".join(
        f"<tr><td>{_html_escape(row.get('severity'))}</td><td>{_html_escape(row.get('title'))}</td><td>{_html_escape(row.get('confidence'))}</td><td>{_html_escape(row.get('actual'))}</td></tr>"
        for row in (data.get("live_findings") or [])[:80]
    )
    executions = "".join(
        f"<tr><td>{_html_escape(row.get('probe_id'))}</td><td>{_html_escape(row.get('method'))} {_html_escape(row.get('path'))}</td><td>{_html_escape(row.get('status_code'))}</td><td>{_html_escape(row.get('error'))}</td><td>{_html_escape(row.get('checks'))}</td></tr>"
        for row in (data.get("safe_executions") or [])[:100]
    )
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>通用缺陷挖掘执行报告</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top;word-break:break-word}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#ecfdf5;color:#065f46}}</style></head><body>
<section class='hero'><span class='badge'>Phase41 Safe Execution</span><h1>通用缺陷挖掘执行</h1><p>仅在 safe_live 模式下执行 GET/HEAD 检查；写接口、重放与并发仅生成候选测试，不会自动执行。</p></section>
<section class='panel'><h2>执行概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>实时候选缺陷</h2><table><thead><tr><th>等级</th><th>标题</th><th>置信度</th><th>实际</th></tr></thead><tbody>{findings or '<tr><td colspan="4">暂无实时问题</td></tr>'}</tbody></table></section>
<section class='panel'><h2>只读执行记录</h2><table><thead><tr><th>探针</th><th>请求</th><th>Status</th><th>错误</th><th>检查</th></tr></thead><tbody>{executions or '<tr><td colspan="5">plan_only 或暂无可执行探针</td></tr>'}</tbody></table></section></body></html>"""


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    execution_mode = os.environ.get("UNIVERSAL_DEFECT_EXECUTION_MODE") or "plan_only"
    result = run_universal_defect_mining(project, options={"execution_mode": execution_mode})
    print(json.dumps({"ok": True, "project_id": project, "summary": result.get("summary"), "private_leak_check": result.get("private_leak_check")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(main(sys.argv[1:]))
