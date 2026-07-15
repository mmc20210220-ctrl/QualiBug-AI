from __future__ import annotations

"""High-signal PRD/OpenAPI defect mining for QualiBug.

This layer is deliberately conservative: it does not execute mutating requests.
It mines product defects from requirement language and API contracts, then emits
explainable findings that can drive safe probes, human review, or later live
validation.
"""

import json
import re
from pathlib import Path
from typing import Any

from .real_project_onboarding import (
    ROOT,
    _load_json,
    _read_text,
    _safe_project_id,
    config_paths,
    load_real_project_config,
)

MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SENSITIVE_FIELD_RE = re.compile(
    r"(api[_-]?key|secret|password|passwd|pwd|token|access[_-]?token|refresh[_-]?token|private[_-]?key|credential|session)",
    re.I,
)
AUTH_WORD_RE = re.compile(r"auth|login|token|role|permission|owner|admin|tenant|用户|权限|角色|租户|归属|只能|不得访问|认证|授权", re.I)
IDEMPOTENCY_WORD_RE = re.compile(r"idempot|retry|duplicate|once|payment|pay|order|create|submit|import|幂等|重试|重复|支付|下单|创建|提交|导入", re.I)
# An action name alone does not prove background execution: many scan/import/
# export routes complete synchronously.  Require an explicit job/queue/async
# signal before raising an observability-gap candidate.
ASYNC_WORD_RE = re.compile(r"async|asynchronous|background|queue|queued|job|task|batch|异步|后台|队列|排队|任务|批处理", re.I)
PROGRESS_WORD_RE = re.compile(r"progress|status|history|result|snapshot|进度|状态|历史|结果|快照", re.I)


def run_deep_bug_mining(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    *,
    prd_text: str = "",
    api_spec_text: str = "",
) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    prd, api_text = _load_project_docs(project, root, prd_text, api_spec_text)
    openapi = _parse_openapi(api_text)
    operations = _operations(openapi)
    findings: list[dict[str, Any]] = []

    if not prd:
        findings.append(_finding(
            "P2",
            "PRD is missing, semantic bug mining is underpowered",
            "missing_prd",
            "No PRD or requirement document was available, so business-rule defects cannot be traced.",
            "Import a PRD/MRD so the scanner can compare API behavior against business requirements.",
            "Only OpenAPI/config-derived checks can run.",
            ["Open the Knowledge page", "Import a PRD/MRD", "Run scan again"],
            confidence=0.82,
        ))
    if not openapi:
        findings.append(_finding(
            "P1",
            "OpenAPI is missing or invalid, API-level bug mining cannot run",
            "missing_openapi",
            "No parseable OpenAPI contract was available for endpoint, schema, permission, and response checks.",
            "Import a valid OpenAPI JSON/YAML file.",
            "Scanner cannot produce endpoint-level probes or explain API contract gaps.",
            ["Open the Knowledge page", "Import OpenAPI", "Run scan again"],
            confidence=0.88,
        ))
        return _result(project, cfg, _rank_and_annotate(_dedupe(findings)), operations)

    findings.extend(_spec_structure_findings(openapi, operations))
    findings.extend(_permission_findings(openapi, operations, prd))
    findings.extend(_sensitive_field_findings(openapi, operations))
    findings.extend(_error_contract_findings(operations, prd))
    findings.extend(_idempotency_findings(operations, prd))
    findings.extend(_async_progress_findings(operations, prd))
    findings.extend(_requirement_traceability_findings(operations, prd))

    return _result(project, cfg, _rank_and_annotate(_dedupe(findings)), operations)


def _load_project_docs(project: str, root: Path, prd_text: str, api_spec_text: str) -> tuple[str, str]:
    paths = config_paths(project, root)
    prd = prd_text or _read_text(paths["input_dir"] / "prd.md")
    api_text = api_spec_text
    if not api_text:
        api_json = _load_json(paths["input_dir"] / "openapi.json", {})
        if isinstance(api_json, dict) and api_json:
            api_text = json.dumps(api_json, ensure_ascii=False)
        else:
            api_text = _read_text(paths["input_dir"] / "openapi_raw.txt")

    if prd and api_text:
        return prd, api_text

    try:
        from .enterprise_knowledge_center import _load_registry, _paths as _kc_paths

        registry = _load_registry(project, root)
        kc_paths = _kc_paths(project, root)
        for src in registry.get("sources", []):
            if src.get("status") != "active":
                continue
            source_type = str(src.get("source_type") or "").lower()
            original = str(src.get("original_name") or "")
            source_id = str(src.get("source_id") or "")
            version = src.get("version", 1)
            path = kc_paths["source_dir"] / f"{source_id}_v{version}_{original}"
            if not path.exists():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            lower_name = original.lower()
            looks_prd = lower_name.endswith((".md", ".txt")) or source_type in {"prd", "mrd"}
            looks_openapi = (
                lower_name.endswith((".json", ".yaml", ".yml"))
                and ("openapi" in text[:500].lower() or "swagger" in text[:500].lower() or '"paths"' in text[:1200])
            ) or (source_type == "openapi" and not lower_name.endswith((".md", ".txt")))
            if not prd and looks_prd:
                prd = text
            if not api_text and looks_openapi:
                api_text = text
    except Exception:
        pass
    return prd, api_text


def _parse_openapi(text: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        pass
    try:
        import yaml  # type: ignore

        value = yaml.safe_load(text)
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _operations(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    components = openapi.get("components") or {}
    for path, methods in (openapi.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        path_params = methods.get("parameters") if isinstance(methods.get("parameters"), list) else []
        for method, raw in methods.items():
            method_u = str(method).upper()
            if method_u not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            op = raw if isinstance(raw, dict) else {}
            params = [p for p in [*path_params, *(op.get("parameters") or [])] if isinstance(p, dict)]
            rows.append({
                "method": method_u,
                "path": str(path),
                "operation": op,
                "operation_id": str(op.get("operationId") or ""),
                "summary": str(op.get("summary") or ""),
                "description": str(op.get("description") or ""),
                "parameters": params,
                "responses": op.get("responses") if isinstance(op.get("responses"), dict) else {},
                "request_fields": _schema_fields(_request_schema(op, components), components),
                "response_fields": _schema_fields(_response_schema(op, components), components),
                "security": op.get("security", None),
                "text": _op_text(path, method_u, op),
            })
    return rows


def _op_text(path: Any, method: str, op: dict[str, Any]) -> str:
    return " ".join([
        str(method),
        str(path),
        str(op.get("operationId") or ""),
        str(op.get("summary") or ""),
        str(op.get("description") or ""),
        json.dumps(op.get("tags") or [], ensure_ascii=False),
    ]).lower()


def _resolve_ref(schema: Any, components: dict[str, Any]) -> dict[str, Any]:
    node = schema if isinstance(schema, dict) else {}
    seen: set[str] = set()
    while isinstance(node, dict) and node.get("$ref"):
        ref = str(node.get("$ref"))
        if ref in seen or not ref.startswith("#/components/schemas/"):
            break
        seen.add(ref)
        target = (components.get("schemas") or {}).get(ref.rsplit("/", 1)[-1])
        if not isinstance(target, dict):
            break
        node = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
    return node if isinstance(node, dict) else {}


def _request_schema(op: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    body = op.get("requestBody") if isinstance(op.get("requestBody"), dict) else {}
    return _content_schema(body, components)


def _response_schema(op: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    responses = op.get("responses") if isinstance(op.get("responses"), dict) else {}
    for code in [*sorted(k for k in responses if str(k).startswith("2")), "default"]:
        spec = responses.get(code)
        if isinstance(spec, dict):
            schema = _content_schema(spec, components)
            if schema:
                return schema
    return {}


def _content_schema(container: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    content = container.get("content") if isinstance(container.get("content"), dict) else {}
    for media in ("application/json", "application/problem+json", *content.keys()):
        item = content.get(media)
        if isinstance(item, dict) and isinstance(item.get("schema"), dict):
            return _resolve_ref(item["schema"], components)
    return {}


def _schema_fields(schema: dict[str, Any], components: dict[str, Any], prefix: str = "", depth: int = 0) -> list[dict[str, Any]]:
    if depth > 5:
        return []
    node = _resolve_ref(schema, components)
    if not node:
        return []
    if node.get("type") == "array" or "items" in node:
        return _schema_fields(node.get("items") or {}, components, f"{prefix}[]" if prefix else "[]", depth + 1)
    fields: list[dict[str, Any]] = []
    required = set(str(x) for x in node.get("required") or [])
    for name, child in (node.get("properties") or {}).items():
        if not isinstance(child, dict):
            continue
        child_node = _resolve_ref(child, components)
        path = f"{prefix}.{name}" if prefix else str(name)
        fields.append({
            "name": str(name),
            "path": path,
            "type": str(child_node.get("type") or ("object" if child_node.get("properties") else "")),
            "required": str(name) in required,
            "read_only": bool(child_node.get("readOnly")),
            "write_only": bool(child_node.get("writeOnly")),
            "format": str(child_node.get("format") or ""),
        })
        fields.extend(_schema_fields(child_node, components, path, depth + 1))
    return fields


def _has_effective_security(openapi: dict[str, Any], op: dict[str, Any]) -> bool:
    raw = op.get("security")
    if raw is not None:
        return bool(raw)
    return bool(openapi.get("security"))


def _security_schemes(openapi: dict[str, Any]) -> dict[str, Any]:
    components = openapi.get("components") if isinstance(openapi.get("components"), dict) else {}
    schemes = components.get("securitySchemes") if isinstance(components.get("securitySchemes"), dict) else {}
    return schemes


def _spec_structure_findings(openapi: dict[str, Any], ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    operation_ids: dict[str, list[str]] = {}
    for op in ops:
        opid = str(op.get("operation_id") or "").strip()
        if not opid:
            findings.append(_op_finding(
                "P3", "Operation is missing operationId", "spec_structure",
                "The operation has no stable operationId, weakening generated tests, clients, and traceability.",
                "Every operation should have a unique operationId.", op, confidence=0.68,
            ))
        else:
            operation_ids.setdefault(opid, []).append(f"{op['method']} {op['path']}")
        declared = {str(p.get("name")) for p in op.get("parameters") or [] if str(p.get("in")) == "path"}
        for name in re.findall(r"\{([^{}]+)\}", str(op.get("path") or "")):
            if name not in declared:
                findings.append(_op_finding(
                    "P2", f"Path parameter {{{name}}} is not declared", "spec_structure",
                    "The URL template contains a path parameter that is missing from OpenAPI parameters.",
                    "Path parameters must be declared with schema and required=true.", op, confidence=0.84,
                ))
    for opid, repeated in operation_ids.items():
        if len(repeated) > 1:
            findings.append(_finding(
                "P2", f"Duplicate operationId: {opid}", "spec_structure",
                f"The same operationId is used by {len(repeated)} operations: {', '.join(repeated[:5])}.",
                "operationId values should be unique.",
                "Generated clients and tests may call the wrong operation.",
                ["Open the OpenAPI document", f"Search operationId {opid}", "Compare all duplicated operations"],
                confidence=0.82,
            ))
    return findings


def _permission_findings(openapi: dict[str, Any], ops: list[dict[str, Any]], prd: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    auth_expected = bool(AUTH_WORD_RE.search(prd)) or bool(_security_schemes(openapi))
    if AUTH_WORD_RE.search(prd) and not _security_schemes(openapi):
        findings.append(_finding(
            "P1", "PRD requires permission boundaries but OpenAPI defines no securitySchemes",
            "permission_boundary",
            "Requirement text mentions authentication, roles, tenants, ownership, or permissions, but the API contract has no security scheme.",
            "OpenAPI should define securitySchemes and apply security globally or per operation.",
            "Permission-sensitive behavior is not enforceable or testable from the contract.",
            ["Import PRD and OpenAPI", "Search PRD for permission/tenant/role requirements", "Inspect components.securitySchemes"],
            confidence=0.9,
        ))
    if not auth_expected:
        return findings
    for op in ops:
        secured = _has_effective_security(openapi, op)
        text = str(op.get("text") or "")
        path = str(op.get("path") or "")
        is_sensitive = op["method"] in MUTATION_METHODS or "{" in path or AUTH_WORD_RE.search(text)
        if is_sensitive and not secured:
            severity = "P1" if op["method"] in MUTATION_METHODS else "P2"
            findings.append(_op_finding(
                severity,
                f"{op['method']} {op['path']} has no documented auth boundary",
                "permission_boundary",
                "The operation is mutation/resource-specific/permission-related, but OpenAPI does not require any security.",
                "Require trusted identity, role, and tenant/owner checks for this operation.",
                op,
                actual="OpenAPI operation has no security requirement.",
                confidence=0.88,
            ))
    return findings


def _sensitive_field_findings(openapi: dict[str, Any], ops: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for op in ops:
        secured = _has_effective_security(openapi, op)
        for field in op.get("response_fields") or []:
            name = str(field.get("path") or field.get("name") or "")
            if not SENSITIVE_FIELD_RE.search(name):
                continue
            if field.get("write_only"):
                continue
            severity = "P1" if not secured else "P2"
            findings.append(_op_finding(
                severity,
                f"Sensitive response field may leak: {name}",
                "sensitive_data_exposure",
                "The success response schema includes a secret/token/password-like field that is not writeOnly.",
                "Secrets should not be returned in normal responses; return masked metadata or status only.",
                op,
                actual=f"Response schema exposes field `{name}`.",
                confidence=0.9,
            ))
    return findings


def _error_contract_findings(ops: list[dict[str, Any]], prd: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    auth_expected = bool(AUTH_WORD_RE.search(prd))
    for op in ops:
        responses = {str(k) for k in (op.get("responses") or {}).keys()}
        if op["method"] in MUTATION_METHODS:
            if auth_expected and not ({"401", "403"} & responses):
                findings.append(_op_finding(
                    "P1",
                    f"{op['method']} {op['path']} lacks 401/403 error contract",
                    "error_contract",
                    "A permission-sensitive mutating operation does not document authentication/authorization failures.",
                    "Document and implement 401/403 behavior for missing identity and insufficient role.",
                    op,
                    actual=f"Declared responses: {sorted(responses)[:8]}",
                    confidence=0.8,
                ))
            if not ({"400", "409", "422"} & responses):
                findings.append(_op_finding(
                    "P2",
                    f"{op['method']} {op['path']} lacks validation/conflict error contract",
                    "error_contract",
                    "A mutating operation has no documented validation or conflict response.",
                    "Document 400/409/422 responses for invalid input, duplicate submission, and state conflict.",
                    op,
                    actual=f"Declared responses: {sorted(responses)[:8]}",
                    confidence=0.74,
                ))
    return findings


def _idempotency_findings(ops: list[dict[str, Any]], prd: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for op in ops:
        operation_text = str(op.get("text") or "")
        requirement_text = prd[:2000]
        if op["method"] not in {"POST", "PUT", "PATCH"}:
            continue
        # A global PRD mention of retries must not turn every mutation (for
        # example, a last-write-wins settings save) into a P1 duplicate-side-
        # effect candidate.  Require an operation-local signal first; the PRD
        # then strengthens the evidence instead of supplying it by itself.
        if not IDEMPOTENCY_WORD_RE.search(operation_text):
            continue
        headers = {str(p.get("name") or "").lower() for p in op.get("parameters") or [] if str(p.get("in")) == "header"}
        if not any(name in headers for name in {"idempotency-key", "x-idempotency-key", "request-id", "x-request-id"}):
            findings.append(_op_finding(
                "P1",
                f"{op['method']} {op['path']} has replay/idempotency risk",
                "idempotency_gap",
                "The operation looks like create/pay/submit/import work, but no idempotency/request key is documented.",
                "Require an idempotency key or deterministic duplicate suppression for retries and double clicks.",
                op,
                actual="No Idempotency-Key/X-Request-Id header parameter is documented.",
                confidence=0.83 if IDEMPOTENCY_WORD_RE.search(requirement_text) else 0.78,
            ))
    return findings


def _async_progress_findings(ops: list[dict[str, Any]], prd: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for op in ops:
        text = str(op.get("text") or "")
        if op["method"] not in MUTATION_METHODS or not ASYNC_WORD_RE.search(text):
            continue
        if _has_related_progress_operation(op, ops):
            continue
        if "progress" in prd.lower() or "进度" in prd or "失败原因" in prd:
            expected = "Long-running work should expose progress, result, and failure reason APIs."
        else:
            expected = "Long-running scan/import/export jobs should expose status/result/failure APIs."
        findings.append(_op_finding(
            "P2",
            f"{op['method']} {op['path']} may start async work without observable progress",
            "async_observability_gap",
            "The operation appears to trigger scan/import/export/report work, but the contract does not expose progress/result/history endpoints.",
            expected,
            op,
            actual="No status/progress/result/history operation is visible in OpenAPI.",
            confidence=0.76,
        ))
    return findings


def _has_related_progress_operation(source: dict[str, Any], ops: list[dict[str, Any]]) -> bool:
    source_tokens = _path_words(str(source.get("path") or "")) | _path_words(str(source.get("text") or ""))
    source_tokens |= {token for token in {"scan", "import", "export", "report", "job", "task", "run"} if token in str(source.get("text") or "")}
    for candidate in ops:
        if candidate is source:
            continue
        candidate_text = f"{candidate.get('path','')} {candidate.get('text','')}"
        if not PROGRESS_WORD_RE.search(candidate_text):
            continue
        candidate_tokens = _path_words(str(candidate.get("path") or "")) | _path_words(str(candidate.get("text") or ""))
        if source_tokens & candidate_tokens:
            return True
    return False


def _path_words(value: str) -> set[str]:
    return {part for part in re.split(r"[^a-zA-Z0-9\u4e00-\u9fff]+", value.lower()) if len(part) >= 3}


def _requirement_traceability_findings(ops: list[dict[str, Any]], prd: str) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if not prd.strip():
        return findings
    op_text = " ".join(str(op.get("text") or "") for op in ops)
    requirement_topics = [
        ("audit_log_gap", r"audit|log|trace|审计|日志|留痕", "audit/logging"),
        ("export_gap", r"export|snapshot|report|导出|快照|报告", "report export/snapshot"),
        ("permission_role_gap", r"role|admin|owner|qa|permission|角色|管理员|项目 owner|权限", "role/permission management"),
        ("failure_reason_gap", r"failure reason|error reason|失败原因|错误原因", "failure reason visibility"),
    ]
    for risk, pattern, label in requirement_topics:
        if re.search(pattern, prd, re.I) and not re.search(pattern, op_text, re.I):
            findings.append(_finding(
                "P2",
                f"PRD topic has no visible API coverage: {label}",
                risk,
                f"The PRD mentions {label}, but no matching endpoint or operation description is visible in OpenAPI.",
                f"Add traceable API coverage or explicitly document why {label} is handled outside the API.",
                "Requirement cannot be validated by automated probes from the current contract.",
                ["Import PRD and OpenAPI", f"Search PRD for {label}", "Search OpenAPI paths, summaries, and operationIds for matching coverage"],
                confidence=0.72,
            ))
    return findings


def _finding(
    severity: str,
    title: str,
    risk_type: str,
    description: str,
    expected: str,
    actual: str,
    reproduction_steps: list[str],
    *,
    confidence: float,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "title": title,
        "category": risk_type,
        "risk_type": risk_type,
        "description": description,
        "expected_behavior": expected,
        "actual_behavior": actual,
        "reproduction_steps": reproduction_steps,
        "confidence_score": confidence,
        "source": "deep_bug_mining",
        "llm_participated": False,
        "status": "needs_validation",
    }


def _op_finding(
    severity: str,
    title: str,
    risk_type: str,
    description: str,
    expected: str,
    op: dict[str, Any],
    *,
    actual: str | None = None,
    confidence: float,
) -> dict[str, Any]:
    method = str(op.get("method") or "")
    path = str(op.get("path") or "")
    item = _finding(
        severity,
        title,
        risk_type,
        description,
        expected,
        actual or f"OpenAPI operation {method} {path} violates this mining rule.",
        ["Open the OpenAPI document", f"Inspect {method} {path}", "Compare the operation against the PRD and expected behavior"],
        confidence=confidence,
    )
    item["method"] = method
    item["path"] = path
    item["operation_id"] = op.get("operation_id")
    item["business_rule_source"] = f"OpenAPI {method} {path}"
    item["evidence"] = {
        "method": method,
        "path": path,
        "operation_id": op.get("operation_id"),
        "summary": op.get("summary"),
        "responses": sorted(str(k) for k in (op.get("responses") or {}).keys())[:10],
    }
    return item


def _dedupe(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    out: list[dict[str, Any]] = []
    severity_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    for item in sorted(findings, key=lambda x: (severity_rank.get(str(x.get("severity")), 9), -float(x.get("confidence_score") or 0), str(x.get("title")))):
        key = (str(item.get("risk_type")), str(item.get("method") or ""), str(item.get("path") or item.get("title")))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out[:80]


def _rank_and_annotate(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    annotated = [_annotate_finding(dict(item)) for item in findings]
    return sorted(
        annotated,
        key=lambda item: (
            -float(item.get("rank_score") or 0),
            _severity_rank(str(item.get("severity") or "")),
            str(item.get("title") or ""),
        ),
    )


def _annotate_finding(item: dict[str, Any]) -> dict[str, Any]:
    risk = str(item.get("risk_type") or item.get("category") or "")
    method = str(item.get("method") or "")
    path = str(item.get("path") or "")
    confidence = float(item.get("confidence_score") or 0)
    severity = str(item.get("severity") or "P3")
    evidence_strength = _evidence_strength(risk)
    verification_level = _verification_level(risk)
    execution_policy = _execution_policy(risk, method)
    false_positive_risk = _false_positive_risk(risk, confidence, verification_level)
    item["evidence_strength"] = evidence_strength
    item["verification_level"] = verification_level
    item["execution_policy"] = execution_policy
    item["false_positive_risk"] = false_positive_risk
    item["status"] = "confirmed_static" if verification_level == "static_verified" else "needs_live_validation"
    item["rank_score"] = round(_rank_score(severity, confidence, evidence_strength, false_positive_risk), 3)
    item["validation_plan"] = _validation_plan(risk, method, path, execution_policy)
    item["can_auto_validate"] = execution_policy in {"no_runtime_required", "safe_read_only"}
    return item


def _evidence_strength(risk: str) -> str:
    if risk in {"spec_structure", "sensitive_data_exposure", "missing_openapi", "missing_prd"}:
        return "strong_static"
    if risk in {"permission_boundary", "error_contract", "idempotency_gap", "async_observability_gap"}:
        return "contract_prd_inferred"
    return "traceability_inferred"


def _verification_level(risk: str) -> str:
    if risk in {"spec_structure", "sensitive_data_exposure", "missing_openapi", "missing_prd"}:
        return "static_verified"
    return "candidate_requires_runtime"


def _execution_policy(risk: str, method: str) -> str:
    if risk in {"spec_structure", "sensitive_data_exposure", "missing_openapi", "missing_prd"}:
        return "no_runtime_required"
    if method == "GET":
        return "safe_read_only"
    if method in MUTATION_METHODS:
        return "sandbox_required"
    return "candidate_only"


def _false_positive_risk(risk: str, confidence: float, verification_level: str) -> str:
    if verification_level == "static_verified" and confidence >= 0.84:
        return "low"
    if risk in {"permission_boundary", "idempotency_gap"} and confidence >= 0.82:
        return "medium"
    if confidence < 0.75:
        return "medium_high"
    return "medium"


def _rank_score(severity: str, confidence: float, evidence_strength: str, false_positive_risk: str) -> float:
    severity_weight = {"P0": 1.0, "P1": 0.86, "P2": 0.62, "P3": 0.38}.get(severity, 0.3)
    evidence_weight = {"strong_static": 0.12, "contract_prd_inferred": 0.07, "traceability_inferred": 0.03}.get(evidence_strength, 0)
    fp_penalty = {"low": 0, "medium": 0.04, "medium_high": 0.1, "high": 0.18}.get(false_positive_risk, 0.05)
    return max(0.0, min(1.0, severity_weight * 0.65 + confidence * 0.28 + evidence_weight - fp_penalty))


def _validation_plan(risk: str, method: str, path: str, execution_policy: str) -> dict[str, Any]:
    if execution_policy == "no_runtime_required":
        return {
            "mode": "static_review",
            "steps": [
                "Open the imported PRD/OpenAPI evidence.",
                "Inspect the cited schema, operation, or missing artifact.",
                "Confirm the contract itself proves the defect or capability gap.",
            ],
        }
    if risk == "permission_boundary":
        steps = [
            f"Call {method} {path or '<operation>'} with no trusted identity and expect 401.",
            "Call it as a normal QA/user against another tenant or owner resource and expect 403/404.",
            "Call it as project owner/admin and confirm only authorized behavior succeeds.",
        ]
    elif risk == "idempotency_gap":
        steps = [
            f"Submit the same {method} {path or '<operation>'} request twice in a sandbox.",
            "Repeat with the same request identifier/idempotency key when supported.",
            "Verify only one business side effect is created and the second result is deterministic.",
        ]
    elif risk == "async_observability_gap":
        steps = [
            f"Start work through {method} {path or '<operation>'}.",
            "Query progress/status/result/failure-reason endpoints until completion or failure.",
            "Confirm users can distinguish running, succeeded, failed, and empty-result states.",
        ]
    elif risk == "error_contract":
        steps = [
            f"Send invalid, unauthorized, duplicate, and conflict inputs to {method} {path or '<operation>'}.",
            "Verify documented 4xx responses and stable error bodies.",
            "Confirm no business side effects occur on rejected requests.",
        ]
    else:
        steps = [
            "Map the PRD statement to an API or workflow.",
            "Run the smallest safe probe that can falsify the rule.",
            "Capture request, response, and state evidence.",
        ]
    return {"mode": execution_policy, "steps": steps}


def _severity_rank(severity: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(severity, 9)


def _result(project: str, cfg: dict[str, Any], findings: list[dict[str, Any]], ops: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "phase": "deep_bug_mining_v1",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "summary": {
            "operation_count": len(ops),
            "finding_count": len(findings),
            "p0p1_count": sum(1 for f in findings if str(f.get("severity")) in {"P0", "P1"}),
            "static_verified_count": sum(1 for f in findings if f.get("verification_level") == "static_verified"),
            "live_validation_required_count": sum(1 for f in findings if f.get("verification_level") == "candidate_requires_runtime"),
            "auto_validatable_count": sum(1 for f in findings if f.get("can_auto_validate")),
            "avg_rank_score": round(sum(float(f.get("rank_score") or 0) for f in findings) / max(1, len(findings)), 3),
            "risk_distribution": _count(str(f.get("risk_type") or "unknown") for f in findings),
            "verification_distribution": _count(str(f.get("verification_level") or "unknown") for f in findings),
        },
        "findings": findings,
        "governance": {
            "executes_mutating_requests": False,
            "uses_project_prd_openapi_only": True,
            "requires_human_or_live_validation_for_candidates": True,
        },
    }


def _count(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda row: (-row[1], row[0])))
