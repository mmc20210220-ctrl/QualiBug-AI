from __future__ import annotations

"""Phase48: enterprise consistency, async-result and isolation counterexample engine.

The previous phases validate individual API contracts, business invariants,
reports, cross-system Oracles and lifecycle state machines.  This module
focuses on a different class of production defects: every endpoint can return
200 while the *observed business state* is wrong because data propagation,
read models, task results, tenant boundaries or caches are inconsistent.

The engine turns PRD/OpenAPI/configuration into five read-only Oracle families:

* tenant isolation: a tenant context must only see its own records and must not
  read a sampled record from another tenant;
* role access and field authorization: independently credentialed role contexts
  must receive the declared allow/deny/empty result, and an allowed view must not
  expose declared restricted business fields;
* async completion: a terminal job must expose its result, and a failed job
  must expose diagnostic evidence instead of a silent terminal state;
* read-model propagation: source and derived/read-model records must agree on
  identity, selected business fields and update freshness;
* read stability: two safe GET observations of a declared stable view must not
  drift on stable business fields without a legitimate dynamic-field exception.

All live execution is GET-only.  Token/header values are used in memory only,
redacted before persistence, and write/race tests are emitted solely as
sandbox-required candidate plans.  Findings receive stable fingerprints so a
repeated counterexample gains confidence without treating a one-off anomaly as
confirmed knowledge.
"""

import argparse
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .business_invariant_mining import _infer_identity, _is_collection_read, _item_fields
from .business_outcome_validation import (
    _build_url,
    _http_get,
    _normal_token,
    _private_leak_check,
    _redact,
    _update_registry,
)
from .llm_reasoning import reason as _llm_reason
from .business_reconciliation import _extract_records, _parse_json
from .multisource_reasoning import _learning_bonus, ingest_confirmed_bug_feedback
from .real_project_onboarding import (
    ROOT,
    _html_escape,
    _load_json,
    _read_text,
    _safe_project_id,
    _write_json,
    config_paths,
    execution_safety_verdict,
    load_real_project_config,
)
from .universal_defect_mining import _operations


TENANT_FIELD_RE = re.compile(r"(?:^|[_\-.])(tenant|tenantid|org|orgid|organization|organizationid|workspace|workspaceid|company|companyid)(?:$|[_\-.])|租户|组织|机构|公司", re.I)
STATUS_FIELD_RE = re.compile(r"(?:^|[_\-.])(status|state|phase)(?:$|[_\-.])|状态|阶段", re.I)
JOB_WORD_RE = re.compile(r"job|task|export|import|batch|sync|async|queue|worker|任务|作业|导入|导出|同步|异步|批处理", re.I)
RESULT_FIELD_RE = re.compile(r"result|output|download|file|url|report|artifact|payload|结果|输出|下载|文件|报表", re.I)
ERROR_FIELD_RE = re.compile(r"error|reason|message|detail|code|异常|错误|原因|失败", re.I)
MODEL_WORD_RE = re.compile(r"index|search|projection|view|cache|read[-_]?model|summary|dashboard|lookup|查询|索引|视图|缓存|看板", re.I)
UPDATED_FIELD_RE = re.compile(r"(?:^|[_\-.])(updated|modified|changed|sync|refreshed|version)(?:_?at|_?time|_?date)?(?:$|[_\-.])|更新时间|修改时间|同步时间|版本", re.I)
DYNAMIC_FIELD_RE = re.compile(r"(?:^|[_\-.])(created|updated|time|timestamp|trace|request|nonce|cursor|next|etag|version|token)(?:$|[_\-.])|时间|时间戳|请求|游标|版本", re.I)
ID_FIELD_RE = re.compile(r"(?:^|[_\-.])(id|uuid|guid|code|number|no|serial)(?:$|[_\-.])|编号|单号|编码", re.I)
SUCCESS_TOKENS = {"success", "succeeded", "completed", "complete", "done", "finished", "ready", "成功", "完成", "已完成", "就绪"}
FAILED_TOKENS = {"failed", "failure", "error", "cancelled", "canceled", "rejected", "失败", "错误", "已取消", "拒绝"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8", errors="replace")).hexdigest()


def _short(value: Any, size: int = 12) -> str:
    return _hash(value)[:size]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _canon(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value).strip()


def _section(cfg: dict[str, Any]) -> dict[str, Any]:
    value = cfg.get("consistency_isolation_reasoning") or cfg.get("consistency_reasoning") or cfg.get("enterprise_consistency") or {}
    return value if isinstance(value, dict) else {}


def _output_paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "defect_discovery"
    return {
        "out": root / "platform_outputs" / project / "consistency_isolation_reasoning",
        "workspace": workspace,
        "registry": workspace / "consistency_isolation_evidence_registry.json",
    }


def _resource_key(path: str) -> str:
    parts = [part for part in str(path or "").split("/") if part and not part.startswith("{")]
    raw = parts[-1] if parts else "resource"
    value = _norm(raw)
    if value.endswith("ies") and len(value) > 4:
        return value[:-3] + "y"
    return value.rstrip("s") or "resource"


def _field_name(fields: dict[str, Any], desired: str | None) -> str | None:
    target = _norm(desired)
    if not target:
        return None
    for name in fields:
        if _norm(name) == target:
            return str(name)
    for name in fields:
        candidate = _norm(name)
        if target in candidate or candidate in target:
            return str(name)
    return None


def _field_value(row: dict[str, Any], field: str | None, mappings: dict[str, Any] | None = None) -> Any:
    if not isinstance(row, dict) or not field:
        return None
    mappings = mappings or {}
    wanted = {_norm(field)}
    mapped = mappings.get(str(field))
    if mapped:
        wanted.add(_norm(mapped))
    for left, right in mappings.items():
        if _norm(right) == _norm(field):
            wanted.add(_norm(left))
    for key, value in row.items():
        if _norm(key) in wanted:
            return value
    return None


def _safe_context_name(context: dict[str, Any], index: int) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-.]+", "_", str(context.get("name") or context.get("tenant_value") or f"context_{index}")).strip("_") or f"context_{index}"


def _configured_rows(section: dict[str, Any], names: tuple[str, ...]) -> list[dict[str, Any]]:
    raw: Any = []
    for name in names:
        if section.get(name) is not None:
            raw = section.get(name)
            break
    if isinstance(raw, dict):
        raw = [raw]
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _find_operation(operations: list[dict[str, Any]], path: str, method: str = "GET") -> dict[str, Any] | None:
    target = str(path or "").rstrip("/") or "/"
    wanted = str(method or "GET").upper()
    for operation in operations:
        if str(operation.get("method") or "").upper() != wanted:
            continue
        if (str(operation.get("path") or "").rstrip("/") or "/") == target:
            return operation
    return None


def _collection_catalog(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    components = openapi.get("components") or {}
    output: list[dict[str, Any]] = []
    for operation in _operations(openapi):
        if not _is_collection_read(operation, components):
            continue
        path = str(operation.get("path") or "")
        fields = _item_fields(operation, components)
        resource = _resource_key(path)
        output.append({
            "path": path,
            "method": "GET",
            "resource": resource,
            "operation": operation,
            "fields": fields,
            "identity_field": _infer_identity(resource, fields, {}),
            "summary": str(operation.get("summary") or ""),
        })
    return output


def _tenant_field(fields: dict[str, Any], configured: dict[str, Any]) -> str | None:
    for key in ("tenant_field", "organization_field", "org_field", "scope_field"):
        field = _field_name(fields, str(configured.get(key) or ""))
        if field:
            return field
    for field in fields:
        if TENANT_FIELD_RE.search(str(field)):
            return str(field)
    return None


def _status_field(fields: dict[str, Any], configured: dict[str, Any]) -> str | None:
    for key in ("status_field", "state_field", "job_status_field"):
        field = _field_name(fields, str(configured.get(key) or ""))
        if field:
            return field
    for field in fields:
        if STATUS_FIELD_RE.search(str(field)):
            return str(field)
    return None


def _result_fields(fields: dict[str, Any], configured: dict[str, Any]) -> list[str]:
    explicit = configured.get("required_result_fields") or configured.get("result_fields") or []
    if isinstance(explicit, str):
        explicit = [explicit]
    values = [_field_name(fields, str(item)) or str(item) for item in explicit if str(item).strip()]
    if not values:
        values = [str(name) for name in fields if RESULT_FIELD_RE.search(str(name))]
    return list(dict.fromkeys(values))[:10]


def _error_fields(fields: dict[str, Any], configured: dict[str, Any]) -> list[str]:
    explicit = configured.get("failure_evidence_fields") or configured.get("error_fields") or []
    if isinstance(explicit, str):
        explicit = [explicit]
    values = [_field_name(fields, str(item)) or str(item) for item in explicit if str(item).strip()]
    if not values:
        values = [str(name) for name in fields if ERROR_FIELD_RE.search(str(name))]
    return list(dict.fromkeys(values))[:10]


def _states(raw: Any, defaults: set[str]) -> list[str]:
    if raw is None:
        return sorted(defaults)
    if isinstance(raw, str):
        raw = [item.strip() for item in re.split(r"[,，|/]+", raw) if item.strip()]
    return [str(item).strip() for item in raw if str(item).strip()] if isinstance(raw, list) else sorted(defaults)


def _same_token(left: Any, right: Any) -> bool:
    return _norm(left) == _norm(right)


def _access_expectation(context: dict[str, Any]) -> str:
    """Normalize only explicitly configured GET-read access expectations."""
    value = _norm(context.get("expected_access") or context.get("expect") or context.get("mode") or "allow")
    if value in {"deny", "denied", "forbidden", "reject", "blocked"}:
        return "deny"
    if value in {"empty", "emptycollection", "filteredempty", "noresults"}:
        return "empty"
    return "allow"


def _status_codes(raw: Any, defaults: set[int]) -> list[int]:
    values = raw if isinstance(raw, list) else [raw] if raw is not None else []
    output: list[int] = []
    for value in values:
        try:
            code = int(value)
        except Exception:
            continue
        if 100 <= code <= 599 and code not in output:
            output.append(code)
    return output or sorted(defaults)


def _context_has_explicit_auth(context: dict[str, Any]) -> bool:
    """Role checks are only meaningful with an explicit, isolated test context."""
    return bool(context.get("token") or context.get("token_env") or context.get("headers"))


def _field_names(raw: Any, limit: int = 30) -> list[str]:
    values = raw if isinstance(raw, list) else [raw] if raw is not None else []
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = str(value or "").strip()
        token = _norm(name)
        if name and token and token not in seen:
            seen.add(token)
            output.append(name[:160])
        if len(output) >= limit:
            break
    return output


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict, tuple, set)):
        return bool(value)
    return True


def _parse_datetime(value: Any) -> datetime | None:
    text = _canon(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(text[:19], pattern)
                break
            except Exception:
                parsed = None
        if parsed is None:
            return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _custom_headers(context: dict[str, Any]) -> tuple[str | None, dict[str, str]]:
    token: str | None = None
    if context.get("token"):
        token = str(context.get("token"))
    if context.get("token_env"):
        token = os.environ.get(str(context.get("token_env"))) or token
    headers: dict[str, str] = {}
    raw = context.get("headers") or {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            text = str(value)
            if text.startswith("env:"):
                text = os.environ.get(text[4:].strip(), "")
            if text:
                headers[str(key)] = text
    return token, headers


def _redact_context(context: dict[str, Any]) -> dict[str, Any]:
    safe = {key: value for key, value in context.items() if key not in {"token", "headers"}}
    if context.get("token") or context.get("token_env"):
        safe["auth_configured"] = True
    if context.get("headers"):
        safe["headers_configured"] = sorted(str(key) for key in (context.get("headers") or {}).keys())
    return _redact(safe)


def _http_get_context(url: str, token: str | None, headers: dict[str, str], timeout: int, max_bytes: int) -> dict[str, Any]:
    if not headers:
        return _http_get(url, token, timeout, max_bytes)
    request_headers = {"Accept": "application/json, */*", **headers}
    if token and not any(key.lower() == "authorization" for key in request_headers):
        request_headers["Authorization"] = f"Bearer {token}"
    try:
        request = urllib.request.Request(url, method="GET", headers=request_headers)
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
            truncated = len(body) > max_bytes
            if truncated:
                body = body[:max_bytes]
            return {
                "ok": 200 <= int(response.status) < 400,
                "status_code": int(response.status),
                "body": body,
                "headers": {str(key).lower(): str(value) for key, value in response.headers.items()},
                "url": response.geturl(),
                "truncated": truncated,
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read(min(max_bytes, 300_000))
        except Exception:
            pass
        return {"ok": False, "status_code": int(exc.code), "body": body, "headers": {}, "url": url, "truncated": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "status_code": None, "body": b"", "headers": {}, "url": url, "truncated": False, "error": str(exc)}


def _decode_records(response: dict[str, Any]) -> tuple[list[dict[str, Any]], int | None, Any]:
    payload = _parse_json(response)
    records, total = _extract_records(payload)
    return records, total, payload


def _pagination_spec(contract: dict[str, Any]) -> dict[str, Any]:
    configured = dict(contract.get("pagination") or {})
    params = contract.get("parameters") or []
    names = [str(item.get("name") or "") for item in params if isinstance(item, dict) and str(item.get("in") or "query").lower() == "query"]
    normalized = {_norm(name): name for name in names}
    page = configured.get("page_param") or next((normalized[key] for key in ("page", "pageno", "pagenum", "pageindex", "页码") if key in normalized), None)
    size = configured.get("size_param") or next((normalized[key] for key in ("size", "pagesize", "limit", "perpage", "per_page", "rows", "条数") if key in normalized), None)
    offset = configured.get("offset_param") or next((normalized[key] for key in ("offset", "start", "skip") if key in normalized), None)
    return {"page_param": page, "size_param": size, "offset_param": offset, "page_start": int(configured.get("page_start") or 1), "page_size": max(1, min(int(configured.get("page_size") or 100), 1000))}


def _fetch_collection(base_url: str, spec: dict[str, Any], token: str | None, headers: dict[str, str], timeout: int, max_bytes: int, max_pages: int) -> dict[str, Any]:
    path = str(spec.get("path") or "")
    query = dict(spec.get("sample_query") or spec.get("query") or {})
    page = _pagination_spec(spec)
    records: list[dict[str, Any]] = []
    responses: list[dict[str, Any]] = []
    total: int | None = None
    seen: set[str] = set()
    page_mode = bool(page.get("page_param") or page.get("offset_param"))
    for index in range(max_pages if page_mode else 1):
        current = dict(query)
        if page.get("page_param"):
            current[str(page["page_param"])] = int(page["page_start"]) + index
            if page.get("size_param"):
                current[str(page["size_param"])] = int(page["page_size"])
        elif page.get("offset_param"):
            current[str(page["offset_param"])] = index * int(page["page_size"])
            if page.get("size_param"):
                current[str(page["size_param"])] = int(page["page_size"])
        url = _build_url(base_url, path, current)
        response = _http_get_context(url, token, headers, timeout, max_bytes)
        responses.append({"url": url, "status_code": response.get("status_code"), "error": response.get("error"), "truncated": bool(response.get("truncated"))})
        if not response.get("ok"):
            break
        rows, discovered_total, _ = _decode_records(response)
        if total is None and discovered_total is not None:
            total = discovered_total
        signature = _hash(rows[:10])
        if page_mode and rows and signature in seen:
            break
        seen.add(signature)
        records.extend(rows)
        if not page_mode or not rows:
            break
        if total is not None and len(records) >= total:
            break
        if total is None and len(rows) < int(page["page_size"]):
            break
    complete = bool((not page_mode and (total is None or len(records) >= total)) or (page_mode and total is not None and len(records) >= total))
    return {"records": records, "total": total, "complete": complete, "responses": responses, "page_mode": page_mode, "request_path": path}


def _contracts_from_config_and_openapi(openapi: dict[str, Any], cfg: dict[str, Any], prd: str) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    section = _section(cfg)
    operations = _operations(openapi)
    catalog = _collection_catalog(openapi)
    contracts: dict[str, list[dict[str, Any]]] = {"tenant": [], "project_scope": [], "access": [], "async": [], "read_model": [], "stability": []}
    candidates: list[dict[str, Any]] = []

    # Tenant boundaries: credentials/headers are enterprise-specific, but fields and list endpoints are inferable.
    configured_tenant = _configured_rows(section, ("tenant_contracts", "isolation_contracts", "tenant_isolation_contracts"))
    seen_tenant_paths: set[str] = set()
    for row in configured_tenant:
        path = str(row.get("path") or row.get("collection_path") or "")
        operation = _find_operation(operations, path, str(row.get("method") or "GET"))
        fields = _item_fields(operation, openapi.get("components") or {}) if operation else {}
        resource = str(row.get("resource") or _resource_key(path))
        identity = _field_name(fields, str(row.get("identity_field") or "")) or str(row.get("identity_field") or "") or _infer_identity(resource, fields, row)
        tenant_field = _field_name(fields, str(row.get("tenant_field") or "")) or str(row.get("tenant_field") or "") or _tenant_field(fields, row)
        contract = {
            "contract_id": f"CIR_TENANT_{len(contracts['tenant'])+1:04d}", "family": "tenant", "resource": resource,
            "path": path, "method": "GET", "parameters": (operation or {}).get("parameters") or [], "sample_query": dict(row.get("sample_query") or row.get("query") or {}),
            "pagination": dict(row.get("pagination") or {}), "identity_field": identity, "tenant_field": tenant_field,
            "contexts": [item for item in (row.get("contexts") or row.get("tenant_contexts") or []) if isinstance(item, dict)],
            "detail_path_template": str(row.get("detail_path_template") or row.get("detail_path") or ""),
            "verify_cross_access": bool(row.get("verify_cross_access", True)), "cross_access_sample_limit": max(1, min(int(row.get("cross_access_sample_limit") or 3), 10)),
            "shared_identity_allowlist": [str(item) for item in (row.get("shared_identity_allowlist") or [])],
            "field_mappings": dict(row.get("field_mappings") or {}), "execution_policy": "safe_read_only", "discovery": "configured",
            "source_evidence": ["enterprise_config", "openapi"] if operation else ["enterprise_config"],
        }
        contracts["tenant"].append(contract)
        seen_tenant_paths.add(path)
        if not identity or not tenant_field:
            candidates.append({"candidate_id": f"{contract['contract_id']}_MAPPING", "family": "tenant", "risk_type": "consistency_contract_gap", "severity": "P2", "title": f"租户隔离契约字段未映射：{path}", "detail": "配置 identity_field 与 tenant_field，才能验证跨租户记录泄漏和身份重叠。"})
        elif len(contract["contexts"]) < 2:
            candidates.append({"candidate_id": f"{contract['contract_id']}_CONTEXT", "family": "tenant", "risk_type": "consistency_contract_gap", "severity": "P2", "title": f"租户隔离缺少双租户只读上下文：{path}", "detail": "在 contexts 配置至少两个租户的 token_env/headers 与 tenant_value；凭证不会写入报告。"})
    for item in catalog:
        if item["path"] in seen_tenant_paths:
            continue
        tenant_field = _tenant_field(item["fields"], {})
        if not tenant_field:
            continue
        candidates.append({"candidate_id": f"CIR_TENANT_AUTO_{len(candidates)+1:04d}", "family": "tenant", "risk_type": "consistency_contract_gap", "severity": "P2", "title": f"发现可能的租户字段但未配置隔离上下文：{item['path']}", "detail": f"检测到字段 {tenant_field}。配置 tenant_contracts 后可进行跨租户 GET 只读验证。"})

    # Project/workspace boundaries are similar to tenant isolation, but many
    # enterprise control planes scope data through a query parameter rather
    # than a tenant field inside each returned row.  Do not infer a project
    # selector from a route name: a false assumption here could turn a public
    # aggregate endpoint into a false P0.  The enterprise must explicitly
    # declare the project-scoped GET route, an allowed project and a foreign
    # project for each independently authenticated context.
    configured_project_scope = _configured_rows(section, ("project_scope_contracts", "project_isolation_contracts", "workspace_scope_contracts"))
    for row in configured_project_scope:
        path = str(row.get("path") or row.get("endpoint_path") or "").strip()
        operation = _find_operation(operations, path, str(row.get("method") or "GET"))
        contexts = [item for item in (row.get("contexts") or row.get("project_contexts") or row.get("scope_contexts") or []) if isinstance(item, dict)]
        if not operation:
            candidates.append({
                "candidate_id": f"CIR_PROJECT_SCOPE_GAP_{len(candidates)+1:04d}", "family": "project_scope", "risk_type": "project_scope_contract_gap", "severity": "P2",
                "title": f"项目作用域契约未映射到 OpenAPI 的 GET 接口：{path}",
                "detail": "仅对 OpenAPI 已声明的 GET 路径配置 project_scope_contracts，并明确允许项目、外部项目和隔离认证上下文。",
            })
            continue
        project_param = str(row.get("project_param") or row.get("scope_param") or "project").strip()
        query_params = {
            str(item.get("name") or "")
            for item in (operation.get("parameters") or [])
            if isinstance(item, dict) and str(item.get("in") or "query").lower() == "query"
        }
        if not project_param or project_param not in query_params:
            candidates.append({
                "candidate_id": f"CIR_PROJECT_SCOPE_PARAM_{len(candidates)+1:04d}", "family": "project_scope", "risk_type": "project_scope_contract_gap", "severity": "P2",
                "title": f"项目作用域参数未映射到 OpenAPI 查询参数：{path}",
                "detail": f"project_scope_contracts 声明的参数 {project_param or '(empty)'} 必须是该 GET 操作的 OpenAPI query 参数。",
            })
            continue
        contract = {
            "contract_id": f"CIR_PROJECT_SCOPE_{len(contracts['project_scope'])+1:04d}", "family": "project_scope",
            "resource": str(row.get("resource") or _resource_key(path)), "path": path, "method": "GET",
            "parameters": (operation or {}).get("parameters") or [], "sample_query": dict(row.get("sample_query") or row.get("query") or {}),
            "project_param": project_param, "contexts": contexts[:20],
            "allowed_statuses": _status_codes(row.get("allowed_statuses") or row.get("success_statuses"), {200}),
            "denied_statuses": _status_codes(row.get("denied_statuses") or row.get("forbidden_statuses"), {401, 403, 404}),
            "execution_policy": "safe_read_only", "discovery": "configured", "source_evidence": ["enterprise_config", "openapi"],
        }
        contracts["project_scope"].append(contract)
        if not contexts:
            candidates.append({
                "candidate_id": f"{contract['contract_id']}_CONTEXT", "family": "project_scope", "risk_type": "project_scope_contract_gap", "severity": "P2",
                "title": f"项目作用域缺少显式隔离认证上下文：{path}",
                "detail": "每个上下文必须独立提供 token_env、token 或 headers，以及 allowed_project/project_id 与 foreign_project。",
            })
        elif any(not _context_has_explicit_auth(context) for context in contexts):
            candidates.append({
                "candidate_id": f"{contract['contract_id']}_AUTH", "family": "project_scope", "risk_type": "project_scope_contract_gap", "severity": "P2",
                "title": f"项目作用域存在未隔离认证上下文：{path}",
                "detail": "项目作用域验证必须使用显式 token_env、token 或 headers，不能复用默认凭证。",
            })
        elif any(
            not str(context.get("allowed_project") or context.get("project_id") or context.get("own_project") or "").strip()
            or not str(context.get("foreign_project") or context.get("other_project") or "").strip()
            for context in contexts
        ):
            candidates.append({
                "candidate_id": f"{contract['contract_id']}_SCOPE", "family": "project_scope", "risk_type": "project_scope_contract_gap", "severity": "P2",
                "title": f"项目作用域缺少允许/外部项目映射：{path}",
                "detail": "为每个上下文配置 allowed_project/project_id 和 foreign_project；它们必须是不同的测试项目标识。",
            })

    # Role/permission boundaries deliberately require explicit test accounts and explicit
    # expected outcomes.  We never infer a route as sensitive, and we never use a
    # production credential: this is a bounded GET-only regression oracle for
    # authorization bypass and field-level redaction leaks.
    configured_access = _configured_rows(section, ("access_contracts", "permission_contracts", "role_access_contracts", "rbac_contracts"))
    for row in configured_access:
        path = str(row.get("path") or row.get("endpoint_path") or "").strip()
        operation = _find_operation(operations, path, str(row.get("method") or "GET"))
        contexts = [item for item in (row.get("contexts") or row.get("role_contexts") or row.get("access_contexts") or []) if isinstance(item, dict)]
        resource = str(row.get("resource") or _resource_key(path))
        if not operation:
            candidates.append({
                "candidate_id": f"CIR_ACCESS_GAP_{len(candidates)+1:04d}", "family": "access", "risk_type": "role_access_contract_gap", "severity": "P2",
                "title": f"权限边界契约未映射到 OpenAPI 的 GET 接口：{path}",
                "detail": "仅对 OpenAPI 已声明的 GET 路径配置 access_contracts，明确每个隔离测试角色的 allow/deny/empty 预期。",
            })
            continue
        contract = {
            "contract_id": f"CIR_ACCESS_{len(contracts['access'])+1:04d}", "family": "access", "resource": resource,
            "path": path, "method": "GET", "parameters": (operation or {}).get("parameters") or [],
            "sample_query": dict(row.get("sample_query") or row.get("query") or {}), "contexts": contexts[:20],
            "allowed_statuses": _status_codes(row.get("allowed_statuses") or row.get("success_statuses"), {200}),
            "denied_statuses": _status_codes(row.get("denied_statuses") or row.get("forbidden_statuses"), {401, 403, 404}),
            "forbidden_fields": _field_names(row.get("forbidden_fields") or row.get("redacted_fields") or row.get("sensitive_fields")),
            "execution_policy": "safe_read_only", "discovery": "configured", "source_evidence": ["enterprise_config", "openapi"],
        }
        contracts["access"].append(contract)
        if not contexts:
            candidates.append({
                "candidate_id": f"{contract['contract_id']}_CONTEXT", "family": "access", "risk_type": "role_access_contract_gap", "severity": "P2",
                "title": f"权限边界缺少显式隔离角色上下文：{path}",
                "detail": "为每个角色配置 token_env 或 headers；凭证仅在内存中使用，永不写入 profile 或证据。",
            })
        elif any(not _context_has_explicit_auth(context) for context in contexts):
            candidates.append({
                "candidate_id": f"{contract['contract_id']}_AUTH", "family": "access", "risk_type": "role_access_contract_gap", "severity": "P2",
                "title": f"权限边界存在未隔离认证上下文：{path}",
                "detail": "每个角色上下文必须独立提供 token_env、token 或 headers，不能复用默认凭证来声称角色隔离。",
            })

    # Async/list-of-jobs contracts: infer from job/task-like collection endpoints and explicit mappings.
    configured_async = _configured_rows(section, ("async_contracts", "job_contracts", "task_contracts"))
    configured_async_paths = {str(row.get("path") or row.get("jobs_path") or "") for row in configured_async}
    candidate_async_rows = list(configured_async)
    for item in catalog:
        text = " ".join([item["path"], item["summary"], item["resource"]])
        if item["path"] not in configured_async_paths and JOB_WORD_RE.search(text) and _status_field(item["fields"], {}):
            candidate_async_rows.append({"path": item["path"], "resource": item["resource"], "_inferred": True})
    for row in candidate_async_rows:
        path = str(row.get("path") or row.get("jobs_path") or row.get("collection_path") or "")
        operation = _find_operation(operations, path, "GET")
        fields = _item_fields(operation, openapi.get("components") or {}) if operation else {}
        resource = str(row.get("resource") or _resource_key(path))
        identity = _field_name(fields, str(row.get("identity_field") or row.get("job_id_field") or "")) or str(row.get("identity_field") or row.get("job_id_field") or "") or _infer_identity(resource, fields, row)
        status = _status_field(fields, row)
        results = _result_fields(fields, row)
        errors = _error_fields(fields, row)
        if not (status and (results or errors)):
            if row.get("_inferred"):
                continue
            candidates.append({"candidate_id": f"CIR_ASYNC_GAP_{len(candidates)+1:04d}", "family": "async", "risk_type": "consistency_contract_gap", "severity": "P2", "title": f"异步任务契约不完整：{path}", "detail": "至少映射 status_field，以及 required_result_fields 或 failure_evidence_fields。"})
            continue
        contracts["async"].append({
            "contract_id": f"CIR_ASYNC_{len(contracts['async'])+1:04d}", "family": "async", "resource": resource, "path": path, "method": "GET",
            "parameters": (operation or {}).get("parameters") or [], "sample_query": dict(row.get("sample_query") or row.get("query") or {}), "pagination": dict(row.get("pagination") or {}),
            "identity_field": identity, "status_field": status, "success_states": _states(row.get("success_states"), SUCCESS_TOKENS), "failure_states": _states(row.get("failure_states"), FAILED_TOKENS),
            "required_result_fields": results, "failure_evidence_fields": errors, "field_mappings": dict(row.get("field_mappings") or {}), "execution_policy": "safe_read_only", "discovery": "openapi_inferred" if row.get("_inferred") else "configured",
            "source_evidence": ["openapi", *( ["enterprise_config"] if not row.get("_inferred") else [])],
        })

    # Read-model contracts: manual mappings are strongest; a conservative inference only pairs names that share a resource stem.
    configured_models = _configured_rows(section, ("read_model_contracts", "propagation_contracts", "cache_contracts"))
    for row in configured_models:
        source_path = str(row.get("source_path") or row.get("source") or "")
        target_path = str(row.get("target_path") or row.get("read_model_path") or row.get("projection_path") or "")
        source_op = _find_operation(operations, source_path, "GET")
        target_op = _find_operation(operations, target_path, "GET")
        source_fields = _item_fields(source_op, openapi.get("components") or {}) if source_op else {}
        target_fields = _item_fields(target_op, openapi.get("components") or {}) if target_op else {}
        resource = str(row.get("resource") or _resource_key(source_path or target_path))
        identity = _field_name(source_fields, str(row.get("identity_field") or "")) or _field_name(target_fields, str(row.get("identity_field") or "")) or str(row.get("identity_field") or "") or _infer_identity(resource, source_fields or target_fields, row)
        compare = row.get("compare_fields") or row.get("fields") or []
        if isinstance(compare, str):
            compare = [item.strip() for item in re.split(r"[,，|/]+", compare) if item.strip()]
        if not compare:
            compare = [name for name in source_fields if _field_name(target_fields, name) and not DYNAMIC_FIELD_RE.search(str(name)) and not ID_FIELD_RE.search(str(name))][:8]
        source_updated = _field_name(source_fields, str(row.get("source_updated_at_field") or "")) or str(row.get("source_updated_at_field") or "") or next((str(name) for name in source_fields if UPDATED_FIELD_RE.search(str(name))), None)
        target_updated = _field_name(target_fields, str(row.get("target_updated_at_field") or "")) or str(row.get("target_updated_at_field") or "") or next((str(name) for name in target_fields if UPDATED_FIELD_RE.search(str(name))), None)
        contract = {
            "contract_id": f"CIR_MODEL_{len(contracts['read_model'])+1:04d}", "family": "read_model", "resource": resource,
            "source": {"path": source_path, "parameters": (source_op or {}).get("parameters") or [], "sample_query": dict(row.get("source_query") or row.get("sample_query") or {}), "pagination": dict(row.get("source_pagination") or row.get("pagination") or {})},
            "target": {"path": target_path, "parameters": (target_op or {}).get("parameters") or [], "sample_query": dict(row.get("target_query") or row.get("sample_query") or {}), "pagination": dict(row.get("target_pagination") or row.get("pagination") or {})},
            "identity_field": identity, "compare_fields": [str(item) for item in compare][:15], "source_updated_at_field": source_updated, "target_updated_at_field": target_updated,
            "staleness_tolerance_seconds": max(0, min(int(row.get("staleness_tolerance_seconds") or 60), 86_400)), "field_mappings": dict(row.get("field_mappings") or {}),
            "execution_policy": "safe_read_only", "discovery": "configured", "source_evidence": ["enterprise_config", "openapi"],
        }
        contracts["read_model"].append(contract)
        if not (source_path and target_path and identity and compare):
            candidates.append({"candidate_id": f"{contract['contract_id']}_MAPPING", "family": "read_model", "risk_type": "consistency_contract_gap", "severity": "P2", "title": f"读模型传播契约不完整：{source_path} → {target_path}", "detail": "配置 source_path、target_path、identity_field 与 compare_fields，才能验证缓存/索引/投影滞后或字段漂移。"})

    # Explicit stability contracts prevent false positives from naturally changing dashboards.
    configured_stability = _configured_rows(section, ("stability_contracts", "cache_stability_contracts", "read_stability_contracts"))
    for row in configured_stability:
        path = str(row.get("path") or row.get("collection_path") or "")
        operation = _find_operation(operations, path, "GET")
        fields = _item_fields(operation, openapi.get("components") or {}) if operation else {}
        resource = str(row.get("resource") or _resource_key(path))
        identity = _field_name(fields, str(row.get("identity_field") or "")) or str(row.get("identity_field") or "") or _infer_identity(resource, fields, row)
        stable = row.get("stable_fields") or row.get("compare_fields") or []
        if isinstance(stable, str):
            stable = [item.strip() for item in re.split(r"[,，|/]+", stable) if item.strip()]
        if not stable:
            stable = [str(name) for name in fields if not DYNAMIC_FIELD_RE.search(str(name)) and not ID_FIELD_RE.search(str(name))][:8]
        contracts["stability"].append({
            "contract_id": f"CIR_STABILITY_{len(contracts['stability'])+1:04d}", "family": "stability", "resource": resource, "path": path, "method": "GET", "parameters": (operation or {}).get("parameters") or [],
            "sample_query": dict(row.get("sample_query") or row.get("query") or {}), "pagination": {}, "identity_field": identity, "stable_fields": [str(item) for item in stable][:15],
            "repeat_count": max(2, min(int(row.get("repeat_count") or 2), 4)), "delay_ms": max(0, min(int(row.get("delay_ms") or 0), 5000)), "field_mappings": dict(row.get("field_mappings") or {}),
            "execution_policy": "safe_read_only", "discovery": "configured", "source_evidence": ["enterprise_config", "openapi"],
        })
    if re.search(r"缓存|cache|异步|同步延迟|最终一致性|tenant|租户|隔离|项目隔离|项目权限|workspace|权限|角色|RBAC|access control|authorization", prd or "", re.I) and not any(contracts.values()):
        candidates.append({"candidate_id": "CIR_PRD_UNMAPPED", "family": "contract_gap", "risk_type": "consistency_contract_gap", "severity": "P2", "title": "PRD 提到了缓存/异步/隔离或角色权限，但未映射可执行只读契约", "detail": "补充 consistency_isolation_reasoning 中的 tenant_contracts、project_scope_contracts、access_contracts、async_contracts、read_model_contracts 或 stability_contracts。"})
    return contracts, candidates[:160]


def _summary(contracts: dict[str, list[dict[str, Any]]], candidates: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "tenant_isolation_contract_count": len(contracts.get("tenant") or []),
        "project_scope_contract_count": len(contracts.get("project_scope") or []),
        "role_access_contract_count": len(contracts.get("access") or []),
        "async_result_contract_count": len(contracts.get("async") or []),
        "read_model_contract_count": len(contracts.get("read_model") or []),
        "read_stability_contract_count": len(contracts.get("stability") or []),
        "consistency_contract_count": sum(len(items) for items in contracts.values()),
        "contract_gap_count": len(candidates),
    }


def _profile_for_persistence(profile: dict[str, Any]) -> dict[str, Any]:
    """Remove credential/header values from profiles written to disk or returned to callers."""
    data = json.loads(_json(profile))
    for family in ("tenant", "project_scope", "access"):
        for contract in ((data.get("contracts") or {}).get(family) or []):
            if isinstance(contract, dict):
                contract["contexts"] = [_redact_context(item) for item in (contract.get("contexts") or []) if isinstance(item, dict)]
    return data


def build_consistency_isolation_profile(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    paths = config_paths(project, root)
    cfg = load_real_project_config(project, root)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    openapi = openapi if isinstance(openapi, dict) else {}
    prd = _read_text(paths["input_dir"] / "prd.md")
    contracts, candidates = _contracts_from_config_and_openapi(openapi, cfg, prd)
    learning = ingest_confirmed_bug_feedback(project, root)
    memory = learning.get("memory") or {}
    for items in contracts.values():
        for contract in items:
            bonus, matches = _learning_bonus(contract, memory)
            contract["learning_bonus"] = bonus
            contract["learning_matches"] = matches
    result = {
        "phase": "phase48_consistency_isolation_reasoning", "project_id": project, "project_name": cfg.get("project_name") or project, "generated_at_utc": _now(),
        "source_inventory": {"prd_available": bool(prd.strip()), "api_operation_count": len(_operations(openapi)), "collection_count": len(_collection_catalog(openapi))},
        "contracts": contracts, "candidates": candidates,
        "summary": {**_summary(contracts, candidates), "confirmed_bug_memory_count": int((learning.get("summary") or {}).get("confirmed_bug_memory_count") or 0), "learned_pattern_count": int((learning.get("summary") or {}).get("learned_pattern_count") or 0)},
        "confirmed_bug_learning": {"summary": learning.get("summary") or {}, "patterns": (memory.get("patterns") or [])[:50]},
        "governance": {"default_execution": "plan_only", "safe_live_only_uses_get": True, "cross_tenant_checks_require_explicit_test_contexts": True, "role_access_checks_require_explicit_isolated_contexts": True, "credentials_redacted_before_persistence": True, "mutation_race_tests_are_sandbox_required": True, "findings_need_human_review": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    persisted = _profile_for_persistence(result)
    persisted["private_leak_check"] = _private_leak_check(persisted)
    output = _output_paths(project, root)
    _write_json(output["out"] / "consistency_isolation_profile.json", persisted)
    _write_json(output["workspace"] / "consistency_isolation_profile.json", persisted)
    output["out"].mkdir(parents=True, exist_ok=True)
    (output["out"] / "consistency_isolation_profile_report.html").write_text(render_consistency_isolation_profile_report(persisted), encoding="utf-8")
    return persisted


def load_consistency_isolation_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _load_json(_output_paths(project, root)["workspace"] / "consistency_isolation_profile.json", {})
    return data if isinstance(data, dict) and data else None


def _probe(contract: dict[str, Any], number: int, kind: str, title: str, risk_type: str, **extra: Any) -> dict[str, Any]:
    path = str(contract.get("path") or ((contract.get("source") or {}).get("path")) or "")
    return {
        "probe_id": f"CIR_PROBE_{number:04d}", "source": "consistency_isolation_reasoning", "consistency_type": kind,
        "contract_id": contract.get("contract_id"), "title": title, "risk_type": risk_type, "severity": extra.pop("severity", "P1"),
        "expected": extra.pop("expected", "企业业务数据的一致性、隔离和最终结果约束必须成立。"), "method": "GET", "path": path, "actor": "normal_user", "destructive": False,
        "execution_policy": extra.pop("execution_policy", "safe_read_only"), "learning_bonus": contract.get("learning_bonus") or 0.0, "learning_matches": contract.get("learning_matches") or [], **extra,
    }


def generate_consistency_isolation_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    profile = load_consistency_isolation_profile(project_id, root) or build_consistency_isolation_profile(project_id, root)
    limit = max(1, int(max_count or cfg.get("max_probe_count") or 180))
    probes: list[dict[str, Any]] = []
    for contract in profile.get("contracts", {}).get("tenant") or []:
        probes.append(_probe(contract, len(probes)+1, "tenant_visibility", f"租户隔离：{contract.get('resource')} 不得出现其他租户记录", "tenant_isolation", expected="每个租户上下文只能看到匹配 tenant_field 的业务记录。", severity="P0"))
        if contract.get("detail_path_template"):
            probes.append(_probe(contract, len(probes)+1, "tenant_cross_access", f"租户隔离：{contract.get('resource')} 跨租户详情访问必须被拒绝", "tenant_isolation", expected="其他租户对已知资源 ID 的 GET 访问必须返回 403/404 或无资源。", severity="P0"))
    for contract in profile.get("contracts", {}).get("project_scope") or []:
        probes.append(_probe(contract, len(probes)+1, "project_scope_cross_access", f"项目隔离：{contract.get('resource')} 不得读取外部项目", "project_scope_isolation", expected="已认证调用者请求显式外部项目时必须收到 401/403/404，而不是成功业务响应。", severity="P0"))
    for contract in profile.get("contracts", {}).get("access") or []:
        for index, context in enumerate(contract.get("contexts") or [], start=1):
            if not isinstance(context, dict):
                continue
            name = _safe_context_name(context, index)
            expected_access = _access_expectation(context)
            if expected_access == "deny":
                probes.append(_probe(contract, len(probes)+1, "role_access_denied", f"权限边界：{contract.get('resource')} 必须拒绝角色 {name}", "role_access_control", expected="显式不具备读取权限的隔离测试角色必须收到声明的拒绝状态。", severity="P0", access_context=name))
            elif expected_access == "empty":
                probes.append(_probe(contract, len(probes)+1, "role_access_empty", f"权限边界：{contract.get('resource')} 角色 {name} 不得看到受限数据", "role_access_control", expected="按空集合拒绝的角色只能得到空业务结果，不能返回受限实体。", severity="P0", access_context=name))
            else:
                probes.append(_probe(contract, len(probes)+1, "role_access_allowed", f"权限边界：{contract.get('resource')} 授权角色 {name} 应可读取", "role_access_control", expected="已授权的隔离测试角色应收到声明的成功状态。", severity="P2", access_context=name))
                forbidden = _field_names(context.get("forbidden_fields") or contract.get("forbidden_fields"))
                if forbidden:
                    probes.append(_probe(contract, len(probes)+1, "field_authorization_leak", f"字段权限：{contract.get('resource')} 不得向角色 {name} 暴露受限字段", "field_level_access_control", expected="角色可访问视图中不得出现该角色被明确禁止的业务字段。", severity="P0", access_context=name, forbidden_fields=forbidden))
    for contract in profile.get("contracts", {}).get("async") or []:
        probes.append(_probe(contract, len(probes)+1, "async_terminal_result", f"异步结果：{contract.get('resource')} 成功任务必须产出可读取结果", "async_result_consistency", expected="任务进入成功终态时，结果/文件/输出字段必须完整。"))
        if contract.get("failure_evidence_fields"):
            probes.append(_probe(contract, len(probes)+1, "async_failure_evidence", f"异常路径：{contract.get('resource')} 失败任务必须保留错误证据", "async_result_consistency", expected="任务进入失败终态时必须返回错误码、原因或可追溯诊断。", severity="P2"))
        probes.append(_probe(contract, len(probes)+1, "async_duplicate_completion", f"沙箱并发：{contract.get('resource')} 重复完成/回调不得产生双结果", "async_idempotency", expected="重复回调或并发完成必须幂等，且只能生成一个业务结果。", method="POST", destructive=True, execution_policy="sandbox_required", mutation_scenario="duplicate_async_completion", severity="P0"))
    for contract in profile.get("contracts", {}).get("read_model") or []:
        probes.append(_probe(contract, len(probes)+1, "read_model_field", f"读模型一致性：{contract.get('resource')} 源数据与索引/缓存字段必须一致", "read_model_consistency", expected="相同业务实体在事实源与读模型的关键字段必须一致。"))
        if contract.get("source_updated_at_field") and contract.get("target_updated_at_field"):
            probes.append(_probe(contract, len(probes)+1, "read_model_staleness", f"最终一致性：{contract.get('resource')} 读模型不能超过允许滞后", "read_model_staleness", expected="读模型更新时间不能显著早于事实源更新时间。", severity="P1"))
    for contract in profile.get("contracts", {}).get("stability") or []:
        probes.append(_probe(contract, len(probes)+1, "read_stability", f"缓存/视图稳定性：{contract.get('resource')} 连续读取不应漂移", "read_stability", expected="同一只读查询在短间隔内的声明稳定字段不应出现无业务变更的漂移。", severity="P2"))
    for gap in profile.get("candidates") or []:
        probes.append({"probe_id": f"CIR_GAP_{len(probes)+1:04d}", "source": "consistency_isolation_reasoning", "consistency_type": "contract_gap", "contract_id": gap.get("candidate_id"), "title": gap.get("title"), "risk_type": gap.get("risk_type") or "consistency_contract_gap", "severity": gap.get("severity") or "P2", "expected": gap.get("detail"), "method": "GET", "path": "", "actor": "normal_user", "destructive": False, "execution_policy": "candidate_only"})
    return probes[:limit]


def _finding(contract: dict[str, Any], kind: str, title: str, expected: str, actual: str, evidence: dict[str, Any], severity: str = "P1", confidence: float = 0.86, key: Any | None = None) -> dict[str, Any]:
    fingerprint = _hash({"contract": contract.get("contract_id"), "kind": kind, "key": key})
    risk_map = {"tenant_visibility": "tenant_isolation", "tenant_cross_access": "tenant_isolation", "project_scope_cross_access": "project_scope_isolation", "project_scope_allowed_denied": "project_scope_access", "role_access_denied": "role_access_control", "role_access_allowed": "role_access_control", "role_access_empty": "role_access_control", "field_authorization_leak": "field_level_access_control", "async_terminal_result": "async_result_consistency", "async_failure_evidence": "async_result_consistency", "read_model_field": "read_model_consistency", "read_model_staleness": "read_model_staleness", "read_stability": "read_stability"}
    return {
        "issue_id": f"CIR_{fingerprint[:12].upper()}", "fingerprint": fingerprint, "source": "consistency_isolation_reasoning", "risk_type": risk_map.get(kind, "consistency_integrity"), "consistency_type": kind,
        "contract_id": contract.get("contract_id"), "title": title, "severity": severity, "status": "needs_human_review", "confidence": confidence, "expected": expected, "actual": actual, "evidence": _redact(evidence),
        "learning_matches": contract.get("learning_matches") or [],
    }


def _context_records(contract: dict[str, Any], base_url: str, context: dict[str, Any], default_token: str | None, timeout: int, max_bytes: int, max_pages: int) -> dict[str, Any]:
    token, headers = _custom_headers(context)
    token = token or default_token
    result = _fetch_collection(base_url, contract, token, headers, timeout, max_bytes, max_pages)
    return {**result, "context": _redact_context(context), "_token": token, "_headers": headers}


def audit_tenant_isolation(contract: dict[str, Any], base_url: str, default_token: str | None, timeout: int, max_bytes: int, max_pages: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    contexts = [item for item in (contract.get("contexts") or []) if isinstance(item, dict)]
    if len(contexts) < 2:
        return findings, executions, []
    snapshots: list[dict[str, Any]] = []
    tenant_field = str(contract.get("tenant_field") or "")
    identity_field = str(contract.get("identity_field") or "")
    mappings = dict(contract.get("field_mappings") or {})
    for index, context in enumerate(contexts, start=1):
        source = _context_records(contract, base_url, context, default_token, timeout, max_bytes, max_pages)
        safe_context = source.get("context") or {}
        executions.append({"contract_id": contract.get("contract_id"), "family": "tenant", "status": "executed" if source.get("responses") else "error", "context": safe_context, "request_count": len(source.get("responses") or []), "row_count": len(source.get("records") or []), "total": source.get("total"), "complete": source.get("complete")})
        expected_tenant = context.get("tenant_value")
        leakage: list[dict[str, Any]] = []
        for row_index, row in enumerate(source.get("records") or [], start=1):
            actual_tenant = _field_value(row, tenant_field, mappings)
            if expected_tenant is not None and _has_value(actual_tenant) and not _same_token(actual_tenant, expected_tenant):
                leakage.append({"row_index": row_index, "identity_hash": _short(_canon(_field_value(row, identity_field, mappings))), "actual_tenant_hash": _short(_canon(actual_tenant)), "expected_tenant_hash": _short(_canon(expected_tenant))})
        if leakage:
            findings.append(_finding(contract, "tenant_visibility", f"跨租户数据泄漏：{contract.get('resource')} 列表返回了其他租户记录", "租户上下文内所有记录必须匹配当前租户字段。", f"上下文 {_safe_context_name(context, index)} 返回 {len(leakage)} 条 tenant_field 不匹配记录。", {"request": {"method": "GET", "path": contract.get("path"), "query": contract.get("sample_query") or {}, "context": safe_context}, "violations": leakage[:20], "coverage": {"row_count": len(source.get("records") or []), "total": source.get("total"), "complete": source.get("complete")}}, severity="P0", confidence=0.98, key={"context": _safe_context_name(context, index), "kind": "tenant_value"}))
        snapshots.append(source)
    # Identity overlap across distinct tenant contexts.  Only compare identities that appeared in both sides.
    allow = set(str(item) for item in (contract.get("shared_identity_allowlist") or []))
    for left_index in range(len(snapshots)):
        for right_index in range(left_index + 1, len(snapshots)):
            left, right = snapshots[left_index], snapshots[right_index]
            left_context, right_context = contexts[left_index], contexts[right_index]
            left_ids = {_canon(_field_value(row, identity_field, mappings)) for row in left.get("records") or [] if _has_value(_field_value(row, identity_field, mappings))}
            right_ids = {_canon(_field_value(row, identity_field, mappings)) for row in right.get("records") or [] if _has_value(_field_value(row, identity_field, mappings))}
            overlap = sorted(item for item in left_ids & right_ids if item not in allow)
            if overlap:
                findings.append(_finding(contract, "tenant_visibility", f"跨租户身份重叠：{contract.get('resource')} 两个租户看到相同业务实体", "不同租户的私有业务实体 ID 不应在只读列表中重叠。", f"两个租户上下文出现 {len(overlap)} 个相同业务身份。", {"left_context": _redact_context(left_context), "right_context": _redact_context(right_context), "overlap_identity_hashes": [_short(item) for item in overlap[:20]], "left_coverage": {"row_count": len(left.get("records") or []), "total": left.get("total")}, "right_coverage": {"row_count": len(right.get("records") or []), "total": right.get("total")}}, severity="P0", confidence=0.97, key={"contexts": sorted([_safe_context_name(left_context, left_index+1), _safe_context_name(right_context, right_index+1)]), "kind": "identity_overlap"}))
    # Cross-tenant detail access uses a known ID from source context under another tenant context. GET only.
    template = str(contract.get("detail_path_template") or "")
    if template and bool(contract.get("verify_cross_access", True)):
        limit = int(contract.get("cross_access_sample_limit") or 3)
        for source_index, source in enumerate(snapshots):
            values = [_canon(_field_value(row, identity_field, mappings)) for row in source.get("records") or [] if _has_value(_field_value(row, identity_field, mappings))][:limit]
            for target_index, target_context in enumerate(contexts):
                if source_index == target_index:
                    continue
                token, headers = _custom_headers(target_context)
                token = token or default_token
                for identity in values:
                    path = template.replace("{" + identity_field + "}", identity).replace("{id}", identity).replace("{resource_id}", identity)
                    url = _build_url(base_url, path)
                    response = _http_get_context(url, token, headers, timeout, max_bytes)
                    executions.append({"contract_id": contract.get("contract_id"), "family": "tenant", "status": "executed", "type": "cross_access", "source_context": _redact_context(contexts[source_index]), "target_context": _redact_context(target_context), "status_code": response.get("status_code"), "path": template})
                    if response.get("ok") and 200 <= int(response.get("status_code") or 0) < 300:
                        findings.append(_finding(contract, "tenant_cross_access", f"跨租户详情越权：{contract.get('resource')} 已知 ID 可被其他租户读取", "其他租户访问私有资源详情必须返回 403/404 或无资源。", f"租户 {_safe_context_name(target_context, target_index+1)} 对其他租户资源的 GET 返回 {response.get('status_code')}。", {"request": {"method": "GET", "path_template": template, "source_context": _redact_context(contexts[source_index]), "target_context": _redact_context(target_context)}, "identity_hash": _short(identity), "status_code": response.get("status_code"), "response_shape": _short((response.get("body") or b"")[:300])}, severity="P0", confidence=0.99, key={"source": _safe_context_name(contexts[source_index], source_index+1), "target": _safe_context_name(target_context, target_index+1), "id": _short(identity)}))
    return findings, executions, snapshots


def _project_scope_value(context: dict[str, Any], *names: str) -> str:
    for name in names:
        value = str(context.get(name) or "").strip()
        if value:
            return value
    return ""


def audit_project_scope(contract: dict[str, Any], base_url: str, default_token: str | None, timeout: int, max_bytes: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Verify an explicit project/workspace boundary with GET-only requests.

    A contract is intentionally explicit because a ``project`` query parameter
    does not itself prove that the endpoint contains private project data.  For
    each supplied principal, we first verify access to its declared project,
    then request a declared foreign project using the same identity.  A success
    response to the foreign project is a deterministic authorization bypass.
    """
    findings: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    path = str(contract.get("path") or "")
    project_param = str(contract.get("project_param") or "project")
    base_query = dict(contract.get("sample_query") or {})
    allowed_statuses = _status_codes(contract.get("allowed_statuses"), {200})
    denied_statuses = _status_codes(contract.get("denied_statuses"), {401, 403, 404})
    for index, context in enumerate(contract.get("contexts") or [], start=1):
        if not isinstance(context, dict):
            continue
        name = _safe_context_name(context, index)
        token, headers = _custom_headers(context)
        allowed_project = _project_scope_value(context, "allowed_project", "project_id", "own_project")
        foreign_project = _project_scope_value(context, "foreign_project", "other_project")
        context_safe = _redact_context(context)
        if not _context_has_explicit_auth(context) or not allowed_project or not foreign_project or _same_token(allowed_project, foreign_project):
            executions.append({"contract_id": contract.get("contract_id"), "family": "project_scope", "status": "skipped_invalid_context", "scope_context": name})
            observations.append({"kind": "project_scope", "scope_context": name, "result": "skipped_invalid_context"})
            continue

        own_query = {**base_query, project_param: allowed_project}
        own_response = _http_get_context(_build_url(base_url, path, own_query), token, headers, timeout, max_bytes)
        own_status = own_response.get("status_code")
        foreign_query = {**base_query, project_param: foreign_project}
        foreign_response = _http_get_context(_build_url(base_url, path, foreign_query), token, headers, timeout, max_bytes)
        foreign_status = foreign_response.get("status_code")
        execution = {
            "contract_id": contract.get("contract_id"), "family": "project_scope", "status": "executed", "scope_context": name,
            "own_status_code": own_status, "foreign_status_code": foreign_status,
            "request_count": 2, "request": {"method": "GET", "path": path, "project_param": project_param},
        }
        executions.append(execution)
        own_allowed = own_status in allowed_statuses
        foreign_bypass = isinstance(foreign_status, int) and (200 <= foreign_status < 400 or foreign_status in allowed_statuses) and foreign_status not in denied_statuses
        observations.append({
            "kind": "project_scope", "scope_context": name, "own_status_code": own_status, "foreign_status_code": foreign_status,
            "own_allowed": own_allowed, "foreign_bypass": foreign_bypass, "allowed_statuses": allowed_statuses, "denied_statuses": denied_statuses,
        })
        scope_evidence = {
            "request": {"method": "GET", "path": path, "project_param": project_param},
            "scope_context": context_safe, "allowed_project_hash": _short(allowed_project), "foreign_project_hash": _short(foreign_project),
            "own_status_code": own_status, "foreign_status_code": foreign_status,
            "foreign_response_shape": _short((foreign_response.get("body") or b"")[:300]),
        }
        if own_status in {401, 403, 404}:
            findings.append(_finding(
                contract, "project_scope_allowed_denied", f"项目作用域异常：{contract.get('resource')} 拒绝了授权项目 {name}",
                "声明拥有项目访问权的隔离测试身份应获得配置的成功状态。",
                f"身份 {name} 对自己的项目 GET {path} 收到 HTTP {own_status}。", scope_evidence,
                severity="P2", confidence=0.93, key={"context": name, "kind": "own_denied", "status": own_status},
            ))
        if foreign_bypass:
            findings.append(_finding(
                contract, "project_scope_cross_access", f"跨项目数据读取：{contract.get('resource')} 向 {name} 返回外部项目", 
                "已认证调用者读取显式外部项目时必须被拒绝，不能返回成功业务响应。",
                f"身份 {name} 对外部项目的 GET {path} 收到 HTTP {foreign_status}。", scope_evidence,
                severity="P0", confidence=0.99, key={"context": name, "kind": "foreign_success", "status": foreign_status},
            ))
    return findings, executions, observations


def _forbidden_field_paths(payload: Any, fields: list[str], max_nodes: int = 1200) -> list[str]:
    """Return field paths only; never copy sensitive values into evidence."""
    wanted = {_norm(field) for field in fields if _norm(field)}
    if not wanted:
        return []
    found: list[str] = []
    seen: set[str] = set()
    stack: list[tuple[Any, str]] = [(payload, "")]
    visited = 0
    while stack and visited < max_nodes:
        value, prefix = stack.pop()
        visited += 1
        if isinstance(value, dict):
            for key, child in value.items():
                name = str(key)
                path = f"{prefix}.{name}" if prefix else name
                if _norm(name) in wanted and _has_value(child):
                    if path not in seen:
                        seen.add(path)
                        found.append(path[:220])
                if isinstance(child, (dict, list)):
                    stack.append((child, path))
        elif isinstance(value, list):
            for child in value[:100]:
                if isinstance(child, (dict, list)):
                    stack.append((child, f"{prefix}[]" if prefix else "[]"))
    return found[:30]


def audit_role_access(contract: dict[str, Any], base_url: str, default_token: str | None, timeout: int, max_bytes: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Evaluate only configured roles against a configured GET endpoint.

    This deliberately does not attempt route discovery or privilege escalation.
    A finding is emitted only for an explicit contract violation: a denied role
    receives a successful response, an allowed role receives an authorization
    rejection, an "empty" role receives entities, or a permitted view leaks a
    field the enterprise explicitly marked as forbidden for that role.
    """
    findings: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    path = str(contract.get("path") or "")
    query = dict(contract.get("sample_query") or {})
    for index, context in enumerate(contract.get("contexts") or [], start=1):
        if not isinstance(context, dict):
            continue
        name = _safe_context_name(context, index)
        expected_access = _access_expectation(context)
        if not _context_has_explicit_auth(context):
            executions.append({"contract_id": contract.get("contract_id"), "family": "access", "status": "skipped_missing_explicit_context_auth", "access_context": name, "expected_access": expected_access})
            observations.append({"kind": "role_access", "access_context": name, "result": "skipped_missing_explicit_context_auth"})
            continue
        token, headers = _custom_headers(context)
        # Role-bound contexts must not silently inherit a project-wide token:
        # doing so can accidentally test the administrator instead of the role
        # named in the access contract.  Inheritance is opt-in for the rare
        # deployments that use a shared bearer token plus role-switch headers.
        if not token and bool(context.get("inherit_default_token")):
            token = default_token
        if not token and not headers:
            executions.append({"contract_id": contract.get("contract_id"), "family": "access", "status": "skipped_unavailable_context_credentials", "access_context": name, "expected_access": expected_access})
            observations.append({"kind": "role_access", "access_context": name, "result": "skipped_unavailable_context_credentials"})
            continue
        response = _http_get_context(_build_url(base_url, path, query), token, headers, timeout, max_bytes)
        status = response.get("status_code")
        execution = {"contract_id": contract.get("contract_id"), "family": "access", "status": "executed", "access_context": name, "expected_access": expected_access, "status_code": status, "request": {"method": "GET", "path": path, "query": query}}
        executions.append(execution)
        context_safe = _redact_context(context)
        if expected_access == "deny":
            denied = _status_codes(context.get("denied_statuses") or contract.get("denied_statuses"), {401, 403, 404})
            allowed = _status_codes(context.get("allowed_statuses") or contract.get("allowed_statuses"), {200})
            bypass = isinstance(status, int) and (200 <= status < 400 or status in allowed) and status not in denied
            observations.append({"kind": "role_access", "access_context": name, "expected_access": "deny", "status_code": status, "allowed_statuses": allowed, "denied_statuses": denied, "bypass": bypass})
            if bypass:
                findings.append(_finding(contract, "role_access_denied", f"角色越权读取：{contract.get('resource')} 未拒绝 {name}", "配置为无读取权限的隔离测试角色必须收到明确拒绝，不能得到成功业务响应。", f"角色 {name} 对 GET {path} 收到 HTTP {status}，未被拒绝。", {"request": {"method": "GET", "path": path, "query": query}, "access_context": context_safe, "expected_denied_statuses": denied, "observed_status": status}, severity="P0", confidence=0.99, key={"context": name, "status": status}))
            continue

        allowed = _status_codes(context.get("allowed_statuses") or contract.get("allowed_statuses"), {200})
        authorization_rejection = isinstance(status, int) and status in {401, 403, 404}
        allowed_ok = status in allowed
        observations.append({"kind": "role_access", "access_context": name, "expected_access": expected_access, "status_code": status, "allowed_statuses": allowed, "allowed": allowed_ok})
        if authorization_rejection:
            findings.append(_finding(contract, "role_access_allowed", f"授权角色无法读取：{contract.get('resource')} 拒绝了 {name}", "配置为具备读取权限的隔离测试角色必须收到声明的成功状态。", f"角色 {name} 对 GET {path} 收到 HTTP {status}。", {"request": {"method": "GET", "path": path, "query": query}, "access_context": context_safe, "expected_allowed_statuses": allowed, "observed_status": status}, severity="P2", confidence=0.93, key={"context": name, "status": status}))
            continue
        if not allowed_ok:
            # Network/service failures are evidence for execution health but are
            # not claimed as a permission defect.
            execution["status"] = "error" if not response.get("ok") else "unexpected_status"
            continue

        payload = _parse_json(response)
        if expected_access == "empty":
            records, total = _extract_records(payload)
            non_empty = bool(records) or (total is not None and int(total) > 0)
            observations[-1].update({"record_count": len(records), "total": total, "non_empty": non_empty})
            if non_empty:
                findings.append(_finding(contract, "role_access_empty", f"角色数据越权：{contract.get('resource')} 向 {name} 返回受限实体", "配置为只能得到空集合的隔离测试角色不得获得受限业务实体。", f"角色 {name} 得到 {len(records)} 条记录，total={total}。", {"request": {"method": "GET", "path": path, "query": query}, "access_context": context_safe, "record_count": len(records), "total": total}, severity="P0", confidence=0.98, key={"context": name, "rows": len(records), "total": total}))
            continue

        forbidden = _field_names(context.get("forbidden_fields") or contract.get("forbidden_fields"))
        leaked_paths = _forbidden_field_paths(payload, forbidden)
        observations[-1].update({"forbidden_field_count": len(forbidden), "forbidden_field_path_count": len(leaked_paths)})
        if leaked_paths:
            findings.append(_finding(contract, "field_authorization_leak", f"字段级越权暴露：{contract.get('resource')} 向 {name} 返回受限字段", "角色可访问的业务视图中不得包含该角色被显式禁止读取的字段。", f"发现 {len(leaked_paths)} 处受限字段路径仍带有非空值。", {"request": {"method": "GET", "path": path, "query": query}, "access_context": context_safe, "forbidden_field_paths": leaked_paths, "forbidden_field_count": len(forbidden)}, severity="P0", confidence=0.99, key={"context": name, "paths": leaked_paths}))
    return findings, executions, observations


def audit_async_results(contract: dict[str, Any], source: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [row for row in source.get("records") or [] if isinstance(row, dict)]
    mappings = dict(contract.get("field_mappings") or {})
    identity_field = str(contract.get("identity_field") or "")
    status_field = str(contract.get("status_field") or "")
    success = list(contract.get("success_states") or [])
    failures = list(contract.get("failure_states") or [])
    required = list(contract.get("required_result_fields") or [])
    errors = list(contract.get("failure_evidence_fields") or [])
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    missing_result: list[dict[str, Any]] = []
    missing_error: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        status = _field_value(row, status_field, mappings)
        identity = _field_value(row, identity_field, mappings)
        if any(_same_token(status, item) for item in success):
            absent = [field for field in required if not _has_value(_field_value(row, field, mappings))]
            if absent:
                missing_result.append({"row_index": index, "identity_hash": _short(_canon(identity)), "status_hash": _short(_canon(status)), "missing_fields": absent})
        if any(_same_token(status, item) for item in failures):
            absent = [field for field in errors if not _has_value(_field_value(row, field, mappings))]
            if errors and absent:
                missing_error.append({"row_index": index, "identity_hash": _short(_canon(identity)), "status_hash": _short(_canon(status)), "missing_fields": absent})
    coverage = {"row_count": len(rows), "total": source.get("total"), "complete": source.get("complete")}
    observations.append({"kind": "async_terminal_result", "missing_result_count": len(missing_result), "missing_error_count": len(missing_error), "coverage": coverage})
    if missing_result:
        findings.append(_finding(contract, "async_terminal_result", f"异步任务假成功：{contract.get('resource')} 已成功但没有结果", "成功终态的任务必须提供结果、下载地址、输出或可追溯产物。", f"发现 {len(missing_result)} 条成功任务缺少结果字段。", {"request": {"method": "GET", "path": contract.get("path"), "query": contract.get("sample_query") or {}}, "violations": missing_result[:20], "coverage": coverage}, confidence=0.95, key="success_without_result"))
    if missing_error:
        findings.append(_finding(contract, "async_failure_evidence", f"异步任务静默失败：{contract.get('resource')} 失败任务缺少诊断信息", "失败终态的任务必须提供错误码、错误原因或可追溯诊断。", f"发现 {len(missing_error)} 条失败任务缺少错误证据。", {"request": {"method": "GET", "path": contract.get("path"), "query": contract.get("sample_query") or {}}, "violations": missing_error[:20], "coverage": coverage}, severity="P2", confidence=0.86, key="failure_without_evidence"))
    return findings, observations


def audit_read_model(contract: dict[str, Any], source: dict[str, Any], target: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    mappings = dict(contract.get("field_mappings") or {})
    identity_field = str(contract.get("identity_field") or "")
    fields = [str(item) for item in (contract.get("compare_fields") or [])]
    source_rows = [row for row in source.get("records") or [] if isinstance(row, dict)]
    target_rows = [row for row in target.get("records") or [] if isinstance(row, dict)]
    source_index = {_canon(_field_value(row, identity_field, mappings)): row for row in source_rows if _has_value(_field_value(row, identity_field, mappings))}
    target_index = {_canon(_field_value(row, identity_field, mappings)): row for row in target_rows if _has_value(_field_value(row, identity_field, mappings))}
    common = sorted(set(source_index) & set(target_index))
    mismatches: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    source_updated = str(contract.get("source_updated_at_field") or "")
    target_updated = str(contract.get("target_updated_at_field") or "")
    tolerance = int(contract.get("staleness_tolerance_seconds") or 0)
    for identity in common:
        left, right = source_index[identity], target_index[identity]
        differences = [field for field in fields if _canon(_field_value(left, field, mappings)) != _canon(_field_value(right, field, mappings))]
        if differences:
            mismatches.append({"identity_hash": _short(identity), "fields": differences[:10]})
        if source_updated and target_updated:
            source_time = _parse_datetime(_field_value(left, source_updated, mappings))
            target_time = _parse_datetime(_field_value(right, target_updated, mappings))
            if source_time and target_time and (source_time - target_time).total_seconds() > tolerance:
                stale.append({"identity_hash": _short(identity), "lag_seconds": round((source_time-target_time).total_seconds(), 3)})
    coverage = {"source_rows": len(source_rows), "target_rows": len(target_rows), "matched_identity_count": len(common), "source_complete": source.get("complete"), "target_complete": target.get("complete")}
    findings: list[dict[str, Any]] = []
    observations = [{"kind": "read_model", "mismatch_count": len(mismatches), "stale_count": len(stale), "coverage": coverage}]
    if mismatches:
        findings.append(_finding(contract, "read_model_field", f"读模型数据漂移：{contract.get('resource')} 事实源与缓存/索引字段不一致", "同一业务实体在事实源和读模型中的关键字段必须一致。", f"匹配到 {len(mismatches)} 个实体存在关键字段差异。", {"source_request": {"method": "GET", "path": (contract.get("source") or {}).get("path")}, "target_request": {"method": "GET", "path": (contract.get("target") or {}).get("path")}, "violations": mismatches[:20], "coverage": coverage}, confidence=0.93, key="field_mismatch"))
    if stale:
        findings.append(_finding(contract, "read_model_staleness", f"读模型滞后：{contract.get('resource')} 缓存/索引早于事实源", "读模型更新时间不能超过声明的最终一致性容忍窗口。", f"匹配到 {len(stale)} 个实体超过 {tolerance} 秒允许滞后。", {"source_request": {"method": "GET", "path": (contract.get("source") or {}).get("path")}, "target_request": {"method": "GET", "path": (contract.get("target") or {}).get("path")}, "violations": stale[:20], "coverage": coverage}, confidence=0.90, key="staleness"))
    return findings, observations


def audit_read_stability(contract: dict[str, Any], observations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if len(observations) < 2:
        return [], []
    mappings = dict(contract.get("field_mappings") or {})
    identity_field = str(contract.get("identity_field") or "")
    fields = [str(item) for item in (contract.get("stable_fields") or [])]
    first, last = observations[0], observations[-1]
    first_index = {_canon(_field_value(row, identity_field, mappings)): row for row in first.get("records") or [] if _has_value(_field_value(row, identity_field, mappings))}
    last_index = {_canon(_field_value(row, identity_field, mappings)): row for row in last.get("records") or [] if _has_value(_field_value(row, identity_field, mappings))}
    common = sorted(set(first_index) & set(last_index))
    drift: list[dict[str, Any]] = []
    for identity in common:
        changed = [field for field in fields if _canon(_field_value(first_index[identity], field, mappings)) != _canon(_field_value(last_index[identity], field, mappings))]
        if changed:
            drift.append({"identity_hash": _short(identity), "changed_fields": changed[:10]})
    evidence = {"first_request": first.get("request"), "last_request": last.get("request"), "repeat_count": len(observations), "matched_identity_count": len(common), "drift": drift[:20]}
    if not drift:
        return [], [{"kind": "read_stability", "drift_count": 0, "matched_identity_count": len(common)}]
    finding = _finding(contract, "read_stability", f"缓存/读视图漂移：{contract.get('resource')} 连续读取返回不一致业务字段", "短时间内相同只读查询的声明稳定字段不应无业务变更而漂移。", f"发现 {len(drift)} 个业务实体在连续读取中发生字段漂移。", evidence, severity="P2", confidence=0.84, key="short_interval_drift")
    return [finding], [{"kind": "read_stability", "drift_count": len(drift), "matched_identity_count": len(common)}]


def run_consistency_isolation_reasoning(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    profile = build_consistency_isolation_profile(project, root, options)
    paths = config_paths(project, root)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    openapi = openapi if isinstance(openapi, dict) else {}
    runtime_contracts, _ = _contracts_from_config_and_openapi(openapi, cfg, _read_text(paths["input_dir"] / "prd.md"))
    learning = ingest_confirmed_bug_feedback(project, root)
    runtime_memory = learning.get("memory") or {}
    for items in runtime_contracts.values():
        for contract in items:
            bonus, matches = _learning_bonus(contract, runtime_memory)
            contract["learning_bonus"] = bonus
            contract["learning_matches"] = matches
    mode = str(options.get("execution_mode") or cfg.get("consistency_isolation_execution_mode") or "plan_only").lower()
    if mode not in {"plan_only", "safe_live"}:
        mode = "plan_only"
    section = _section(cfg)
    max_pages = max(1, min(int(options.get("max_source_pages") or section.get("max_source_pages") or 5), 30))
    max_bytes = max(10_000, min(int(options.get("max_response_bytes") or section.get("max_response_bytes") or 1_500_000), 12_000_000))
    timeout = max(1, min(int(cfg.get("request_timeout_seconds") or 10), 60))
    base_url = str(cfg.get("base_url") or "").strip()
    accounts = _load_json(paths["input_dir"] / "test_accounts.json", {})
    safety = execution_safety_verdict(project, cfg, accounts)
    live_execution_allowed = mode == "safe_live" and bool(safety.get("safe_to_proceed"))
    default_token = _normal_token(cfg, project, root, timeout) if live_execution_allowed and base_url else None
    findings: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    readiness_findings: list[dict[str, Any]] = []
    if mode == "safe_live" and not live_execution_allowed:
        readiness_findings.append({"kind": "safety_boundary", "status": "blocked", "message": "在线一致性/权限执行已被安全边界阻断；这不是产品缺陷。", "violations": safety.get("violations") or []})

    if mode == "safe_live" and base_url and live_execution_allowed:
        for contract in runtime_contracts.get("tenant") or []:
            current, rows, _ = audit_tenant_isolation(contract, base_url, default_token, timeout, max_bytes, max_pages)
            findings.extend(current); executions.extend(rows)
        for contract in runtime_contracts.get("project_scope") or []:
            current, rows, facts = audit_project_scope(contract, base_url, default_token, timeout, max_bytes)
            findings.extend(current); executions.extend(rows); observations.extend(facts)
        for contract in runtime_contracts.get("access") or []:
            current, rows, facts = audit_role_access(contract, base_url, default_token, timeout, max_bytes)
            findings.extend(current); executions.extend(rows); observations.extend(facts)
        for contract in runtime_contracts.get("async") or []:
            source = _fetch_collection(base_url, contract, default_token, {}, timeout, max_bytes, max_pages)
            executions.append({"contract_id": contract.get("contract_id"), "family": "async", "status": "executed" if source.get("responses") else "error", "request_count": len(source.get("responses") or []), "row_count": len(source.get("records") or []), "total": source.get("total")})
            current, facts = audit_async_results(contract, source)
            findings.extend(current); observations.extend(facts)
            executions.append({"contract_id": contract.get("contract_id"), "family": "async", "status": "candidate_only", "type": "duplicate_async_completion", "method": "POST", "execution_policy": "sandbox_required", "reason": "write_or_race_execution_disabled"})
        for contract in runtime_contracts.get("read_model") or []:
            source_spec = {**(contract.get("source") or {}), "path": (contract.get("source") or {}).get("path")}
            target_spec = {**(contract.get("target") or {}), "path": (contract.get("target") or {}).get("path")}
            source = _fetch_collection(base_url, source_spec, default_token, {}, timeout, max_bytes, max_pages)
            target = _fetch_collection(base_url, target_spec, default_token, {}, timeout, max_bytes, max_pages)
            executions.append({"contract_id": contract.get("contract_id"), "family": "read_model", "status": "executed", "source_request_count": len(source.get("responses") or []), "target_request_count": len(target.get("responses") or []), "source_rows": len(source.get("records") or []), "target_rows": len(target.get("records") or [])})
            current, facts = audit_read_model(contract, source, target)
            findings.extend(current); observations.extend(facts)
        for contract in runtime_contracts.get("stability") or []:
            records: list[dict[str, Any]] = []
            for repeat in range(int(contract.get("repeat_count") or 2)):
                source = _fetch_collection(base_url, contract, default_token, {}, timeout, max_bytes, 1)
                records.append({**source, "request": {"method": "GET", "path": contract.get("path"), "query": contract.get("sample_query") or {}, "repeat": repeat + 1}})
                if repeat + 1 < int(contract.get("repeat_count") or 2) and int(contract.get("delay_ms") or 0):
                    time.sleep(int(contract.get("delay_ms") or 0) / 1000.0)
            executions.append({"contract_id": contract.get("contract_id"), "family": "stability", "status": "executed", "repeat_count": len(records), "request_count": sum(len(item.get("responses") or []) for item in records)})
            current, facts = audit_read_stability(contract, records)
            findings.extend(current); observations.extend(facts)
    else:
        for family, contracts in runtime_contracts.items():
            for contract in contracts or []:
                if mode == "safe_live" and base_url and not live_execution_allowed:
                    executions.append({"contract_id": contract.get("contract_id"), "family": family, "status": "blocked_by_safety_boundary", "reason": "unsafe_or_undeclared_target"})
                else:
                    executions.append({"contract_id": contract.get("contract_id"), "family": family, "status": "planned", "reason": "plan_only_or_missing_base_url"})

    # LLM reasoning can propose a follow-up, but never becomes a customer-visible
    # defect without deterministic evidence from this run.
    semantic_hypotheses: list[dict[str, Any]] = []
    if live_execution_allowed and findings:
        try:
            llm_result = _llm_reason("consistency", {
                "prd_text": "", "api_schema": "", "observed_data": json.dumps(observations[-5:], ensure_ascii=False, default=str)[:4000],
                "heuristic_findings": json.dumps(findings[:15], ensure_ascii=False, default=str)[:4000],
                "tenant_context": json.dumps({"tenant_count": len(set(e.get("tenant_id","") for e in executions if e.get("tenant_id")))}, ensure_ascii=False),
                "model_comparison": "{}",
            })
            if llm_result and llm_result.get("findings"):
                for lf in llm_result["findings"]:
                    if isinstance(lf, dict):
                        semantic_hypotheses.append(_redact({"hypothesis_id": f"CIR_HYP_{_short({'project': project, 'rule': lf.get('rule'), 'title': lf.get('title')})}", "source": "llm_reasoning", "status": "unverified_hypothesis", "consistency_type": "llm_semantic_"+str(lf.get("rule","unknown"))[:120], "title": str(lf.get("title", "建议补充权限或一致性观察"))[:300], "suggested_next_observation": str(lf.get("expected") or lf.get("observed") or "")[:500], "confidence": min(0.6, max(0.0, float(lf.get("confidence", 0.5))))}))
        except Exception:
            pass

    output = _output_paths(project, root)
    registry, findings = _update_registry(output["registry"], findings)
    result = {
        "phase": "phase48_consistency_isolation_reasoning", "project_id": project, "project_name": cfg.get("project_name") or project, "generated_at_utc": _now(),
        "summary": {**(profile.get("summary") or {}), "execution_mode": mode, "executed_contract_count": len({item.get("contract_id") for item in executions if item.get("status") == "executed" and item.get("contract_id")}), "consistency_isolation_finding_count": len(findings), "persistent_consistency_isolation_count": sum(1 for item in findings if (item.get("evidence_stability") or {}).get("persistent")), "sandbox_candidate_count": sum(1 for item in executions if item.get("execution_policy") == "sandbox_required"), "memory_fingerprint_count": len((registry or {}).get("entries") or {})},
        "profile": profile, "safety_boundary": safety, "readiness_findings": readiness_findings, "executions": executions, "observations": observations, "findings": findings, "semantic_hypotheses": semantic_hypotheses,
        "memory_summary": {"fingerprint_count": len((registry or {}).get("entries") or {}), "updated_at_utc": _now(), "learning_policy": "跨运行重复的最终一致性、隔离或异步结果反例提升置信度；所有确定性发现仍需人工确认。"},
        "governance": {"execution_mode": mode, "live_requests_limited_to_get": True, "shared_safety_boundary_required_for_live_execution": True, "cross_tenant_contexts_explicit": True, "project_scope_contexts_explicit": True, "role_access_contexts_explicit": True, "tokens_and_headers_not_persisted": True, "llm_output_is_unverified_hypothesis_only": True, "write_and_race_tests_sandbox_required": True, "evidence_redacted_before_persistence": True, "uses_no_benchmark_answer_files": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    _write_json(output["out"] / "consistency_isolation_run.json", result)
    _write_json(output["workspace"] / "consistency_isolation_run.json", result)
    output["out"].mkdir(parents=True, exist_ok=True)
    (output["out"] / "consistency_isolation_run_report.html").write_text(render_consistency_isolation_run_report(result), encoding="utf-8")
    return result


def _cards(summary: dict[str, Any]) -> str:
    return "".join(f"<div class='card'><span>{_html_escape(key)}</span><b>{_html_escape(value)}</b></div>" for key, value in summary.items())


def _render_html(title: str, badge: str, subtitle: str, cards: str, body: str) -> str:
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>{_html_escape(title)}</title><style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top;word-break:break-word}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#ecfeff;color:#155e75}}</style></head><body><section class='hero'><span class='badge'>{_html_escape(badge)}</span><h1>{_html_escape(title)}</h1><p>{_html_escape(subtitle)}</p></section><section class='panel'><div class='grid'>{cards}</div></section>{body}</body></html>"""


def _table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    head = "".join(f"<th>{_html_escape(item)}</th>" for item in headers)
    body = "".join("<tr>" + "".join(f"<td>{_html_escape(item)}</td>" for item in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_consistency_isolation_profile_report(data: dict[str, Any]) -> str:
    rows: list[tuple[Any, ...]] = []
    for family, contracts in (data.get("contracts") or {}).items():
        for contract in contracts or []:
            rows.append((family, contract.get("contract_id"), contract.get("resource"), contract.get("path") or ((contract.get("source") or {}).get("path")), contract.get("execution_policy"), contract.get("discovery")))
    body = "<section class='panel'><h2>可执行一致性与隔离契约</h2>" + _table(["族", "ID", "资源", "入口", "执行策略", "来源"], rows[:300]) + "</section>"
    return _render_html("Phase48 一致性与隔离反例引擎", "PROFILE", "从接口、PRD、企业配置和确认缺陷记忆中构建租户、角色权限、异步、读模型和缓存稳定性 Oracle。", _cards(data.get("summary") or {}), body)


def render_consistency_isolation_run_report(data: dict[str, Any]) -> str:
    rows = [(item.get("severity"), item.get("consistency_type"), item.get("title"), item.get("expected"), item.get("actual"), item.get("confidence"), (item.get("evidence_stability") or {}).get("observations", 1)) for item in (data.get("findings") or [])]
    body = "<section class='panel'><h2>发现的最终一致性/隔离反例</h2>" + _table(["等级", "类型", "问题", "期望", "实际", "置信度", "观测次数"], rows[:300]) + "</section>"
    return _render_html("Phase48 一致性与隔离执行报告", "RUN", "safe_live 仅执行 GET；跨租户与角色凭证仅内存使用，写入/竞态验证仅生成隔离沙箱候选。", _cards(data.get("summary") or {}), body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase48 consistency and isolation reasoning")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--mode", choices=["profile", "run"], default="profile")
    parser.add_argument("--execution-mode", choices=["plan_only", "safe_live"], default=None)
    args = parser.parse_args(argv)
    if args.mode == "run":
        result = run_consistency_isolation_reasoning(args.project, options={"execution_mode": args.execution_mode} if args.execution_mode else {})
    else:
        result = build_consistency_isolation_profile(args.project)
    print(json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
