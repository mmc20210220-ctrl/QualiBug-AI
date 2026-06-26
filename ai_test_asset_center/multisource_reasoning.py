from __future__ import annotations

"""Phase46: multi-source business reasoning and confirmed-bug learning loop.

This module turns product documents, API contracts, optional UI observations and
safe runtime observations into *testable* business hypotheses.  It deliberately
keeps the distinction between:

* executable read-only checks (safe_live, GET only),
* mutation/concurrency scenarios that require a controlled sandbox,
* unverified PRD/UI statements that remain human-review candidates.

The engine covers five sources of high-value enterprise defects:

1. business invariants already inferred by Phase45;
2. cross-system oracles (for example order-center vs ERP/CRM/WMS);
3. abnormal paths where invalid input is silently accepted or ignored;
4. concurrent write paths derived from contracts/PRD, emitted as sandbox plans;
5. historical-data compatibility between recorded legacy snapshots and current APIs.

Confirmed human feedback is ingested as redacted, de-duplicated enterprise
memory.  It may raise priority for similar future hypotheses, but it never
promotes an unconfirmed finding to a production defect automatically.
"""

import argparse
import hashlib
import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .business_outcome_validation import (
    _build_url,
    _http_get,
    _normal_token,
    _private_leak_check,
    _redact,
    _update_registry,
)
from .business_reconciliation import _extract_records, _fetch_source_pages, _parse_json
from .business_invariant_mining import _field_by_name, _infer_identity, _item_fields, _is_collection_read
from .llm_reasoning import compile_unverified_semantic_hypotheses, reason as _llm_reason
from .real_project_onboarding import (
    ROOT,
    _html_escape,
    _load_json,
    _read_text,
    _safe_project_id,
    _write_json,
    config_paths,
    load_real_project_config,
)
from .universal_defect_mining import _operations


# Only non-sensitive, stable cues are used when deriving learned patterns.
_DYNAMIC_FIELD_RE = re.compile(r"(?:^|[_\-.])(updated|created|time|timestamp|trace|request|nonce|version|etag|cursor|token|secret|password)(?:$|[_\-.])", re.I)
_PRIVATE_FIELD_RE = re.compile(r"(?:password|secret|token|authorization|cookie|mobile|phone|email|idcard|ssn|address)", re.I)
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_STATUS_WORDS = ("status", "state", "状态", "状态码", "阶段", "phase")
_CONCURRENT_WORDS = ("幂等", "重复", "并发", "同时", "抢", "扣减", "库存", "支付", "退款", "提交", "idempot", "concurr", "duplicate", "reserve", "checkout")
_CROSS_SYSTEM_WORDS = ("erp", "crm", "wms", "mes", "财务", "库存", "仓储", "订单中心", "同步", "对账", "跨系统", "third party", "external")
_HISTORY_WORDS = ("历史", "存量", "旧数据", "迁移", "兼容", "归档", "legacy", "migration", "backward compatible", "historical")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8", errors="replace")).hexdigest()


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _short(value: Any, length: int = 12) -> str:
    return _hash(value)[:length]


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("items", "contracts", "oracles", "rows", "data"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
    return []


def _output_paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "defect_discovery"
    return {
        "out": root / "platform_outputs" / project / "multi_source_reasoning",
        "workspace": workspace,
        "registry": workspace / "multi_source_reasoning_evidence_registry.json",
        "memory": workspace / "confirmed_bug_memory.json",
        "training": workspace / "confirmed_bug_learning_samples.jsonl",
    }


def _section(cfg: dict[str, Any]) -> dict[str, Any]:
    value = cfg.get("multi_source_reasoning") or cfg.get("business_reasoning") or cfg.get("reasoning_engine") or {}
    return value if isinstance(value, dict) else {}


def _resource_key(path: str) -> str:
    parts = [item for item in str(path or "").split("/") if item and not item.startswith("{")]
    return _norm(parts[-1] if parts else "resource").rstrip("s") or "resource"


def _system_hint(operation: dict[str, Any]) -> str:
    raw = operation.get("raw_operation") if isinstance(operation.get("raw_operation"), dict) else {}
    for key in ("x-system", "x-source-system", "x-domain", "x-service", "x-bounded-context"):
        if raw.get(key):
            return _norm(raw.get(key)) or "unknown"
    tags = [str(item) for item in (operation.get("tags") or []) if str(item).strip()]
    if tags:
        return _norm(tags[0]) or "unknown"
    path_parts = [part for part in str(operation.get("path") or "").split("/") if part and not part.startswith("{")]
    if len(path_parts) >= 2:
        return _norm(path_parts[-2]) or "default"
    return "default"


def _collection_catalog(openapi: dict[str, Any], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    components = openapi.get("components") or {}
    configured = _section(cfg).get("collections") or []
    configured = _as_list(configured)
    out: list[dict[str, Any]] = []
    for operation in _operations(openapi):
        if not _is_collection_read(operation, components):
            continue
        path = str(operation.get("path") or "")
        override = next((row for row in configured if str(row.get("path") or "").rstrip("/") == path.rstrip("/")), {})
        fields = _item_fields(operation, components)
        resource = str(override.get("resource") or _resource_key(path))
        identity = _infer_identity(resource, fields, override)
        out.append({
            "path": path,
            "method": "GET",
            "resource": resource,
            "system": str(override.get("system") or _system_hint(operation)),
            "identity_field": identity,
            "field_names": list(fields),
            "parameters": operation.get("parameters") or [],
            "sample_query": dict(override.get("sample_query") or override.get("query") or {}),
            "pagination": dict(override.get("pagination") or {}),
            "response_schema": operation.get("response_schema") or {},
            "tags": list(operation.get("tags") or []),
            "summary": str(operation.get("summary") or ""),
            "raw_operation": operation.get("raw_operation") or {},
            "discovery": "configured" if override else "openapi_inferred",
        })
    return out


def _find_collection(catalog: list[dict[str, Any]], path: str, system: str | None = None) -> dict[str, Any] | None:
    wanted = str(path or "").rstrip("/") or "/"
    system_norm = _norm(system)
    for row in catalog:
        if (str(row.get("path") or "").rstrip("/") or "/") != wanted:
            continue
        if system_norm and _norm(row.get("system")) != system_norm:
            continue
        return row
    return None


def _meaningful_common_fields(left: dict[str, Any], right: dict[str, Any], left_id: str | None, right_id: str | None) -> list[dict[str, str]]:
    fields: list[dict[str, str]] = []
    right_by_norm = {_norm(name): str(name) for name in (right.get("field_names") or [])}
    preferred = ("status", "state", "amount", "total", "quantity", "count", "customer_id", "owner_id", "tenant_id", "currency")
    for name in left.get("field_names") or []:
        key = _norm(name)
        if not key or key == _norm(left_id) or key == _norm(right_id):
            continue
        if _PRIVATE_FIELD_RE.search(str(name)) or _DYNAMIC_FIELD_RE.search(str(name)):
            continue
        other = right_by_norm.get(key)
        if not other:
            continue
        if any(token in key for token in preferred):
            fields.append({"left_field": str(name), "right_field": other})
    return fields[:8]


def _configured_cross_oracles(section: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("cross_system_oracles", "cross_system_contracts", "oracles"):
        value = section.get(key)
        rows = _as_list(value)
        if rows:
            return rows
    return []


def _cross_system_contracts(catalog: list[dict[str, Any]], section: dict[str, Any], prd: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contracts: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    explicit = _configured_cross_oracles(section)
    known: set[tuple[str, str]] = set()
    for row in explicit:
        left_path = str(row.get("left_path") or row.get("source_path") or row.get("path_a") or "")
        right_path = str(row.get("right_path") or row.get("target_path") or row.get("path_b") or "")
        left = _find_collection(catalog, left_path, str(row.get("left_system") or "") or None)
        right = _find_collection(catalog, right_path, str(row.get("right_system") or "") or None)
        if not left or not right:
            gaps.append({
                "candidate_id": f"MSR_CROSS_GAP_{len(gaps)+1:03d}",
                "title": "跨系统 Oracle 未映射到两个可读取集合接口",
                "severity": "P2",
                "risk_type": "cross_system_oracle",
                "detail": "补充 left_path/right_path，或将对应接口标记为 GET collection。",
                "source": "enterprise_config",
            })
            continue
        left_id = str(row.get("left_identity_field") or left.get("identity_field") or "") or None
        right_id = str(row.get("right_identity_field") or right.get("identity_field") or "") or None
        mappings = _as_list(row.get("field_mappings"))
        if not mappings:
            mappings = _meaningful_common_fields(left, right, left_id, right_id)
        normalized_mappings: list[dict[str, str]] = []
        for mapping in mappings:
            lf = str(mapping.get("left_field") or mapping.get("source_field") or mapping.get("field") or "")
            rf = str(mapping.get("right_field") or mapping.get("target_field") or mapping.get("field") or lf)
            if lf and rf:
                normalized_mappings.append({"left_field": lf, "right_field": rf})
        if not left_id or not right_id:
            gaps.append({"candidate_id": f"MSR_CROSS_GAP_{len(gaps)+1:03d}", "title": "跨系统 Oracle 缺少可比业务主键", "severity": "P2", "risk_type": "cross_system_oracle", "detail": f"{left_path} 与 {right_path} 需要 left_identity_field/right_identity_field。", "source": "enterprise_config"})
            continue
        contract = {
            "contract_id": f"MSR_CROSS_{len(contracts)+1:03d}",
            "oracle_family": "cross_system",
            "title": str(row.get("title") or f"跨系统一致性：{left.get('system')} / {right.get('system')} {left.get('resource') or right.get('resource')}") ,
            "left": {**left, "identity_field": left_id, "sample_query": dict(row.get("left_query") or left.get("sample_query") or {})},
            "right": {**right, "identity_field": right_id, "sample_query": dict(row.get("right_query") or right.get("sample_query") or {})},
            "field_mappings": normalized_mappings,
            "require_same_coverage": bool(row.get("require_same_coverage", True)),
            "execution_policy": "safe_read_only",
            "discovery": "enterprise_config",
            "source_evidence": ["configuration", "openapi", "prd"],
        }
        contracts.append(contract)
        known.add((left_path, right_path))

    # High-precision automatic inference: the same resource exposed under different
    # OpenAPI systems/tags, each declaring an identity field.  It is executable only
    # where the service boundary is explicit; otherwise it becomes a candidate.
    for idx, left in enumerate(catalog):
        for right in catalog[idx + 1:]:
            if left.get("resource") != right.get("resource") or left.get("path") == right.get("path"):
                continue
            if (str(left.get("path")), str(right.get("path"))) in known or (str(right.get("path")), str(left.get("path"))) in known:
                continue
            if not left.get("identity_field") or not right.get("identity_field"):
                continue
            mappings = _meaningful_common_fields(left, right, left.get("identity_field"), right.get("identity_field"))
            if not mappings:
                continue
            explicit_boundary = left.get("system") not in {"", "default", "unknown"} and right.get("system") not in {"", "default", "unknown"} and left.get("system") != right.get("system")
            if explicit_boundary:
                contracts.append({
                    "contract_id": f"MSR_CROSS_{len(contracts)+1:03d}", "oracle_family": "cross_system",
                    "title": f"自动发现跨系统一致性：{left.get('system')} / {right.get('system')} {left.get('resource')}",
                    "left": left, "right": right, "field_mappings": mappings,
                    "require_same_coverage": False, "execution_policy": "safe_read_only", "discovery": "openapi_service_boundary",
                    "source_evidence": ["openapi_service_boundary", "schema"],
                })
            else:
                gaps.append({"candidate_id": f"MSR_CROSS_GAP_{len(gaps)+1:03d}", "title": f"疑似跨系统数据关系：{left.get('path')} ↔ {right.get('path')}", "severity": "P3", "risk_type": "cross_system_oracle", "detail": "两个集合具有相同资源和可比较字段，但系统边界不明确；配置 system 与确认映射后可执行 Oracle。", "source": "schema_inference"})
    if any(word in str(prd).lower() for word in _CROSS_SYSTEM_WORDS) and not contracts:
        gaps.append({"candidate_id": "MSR_PRD_CROSS_SYSTEM_UNMAPPED", "title": "PRD 提到跨系统同步/对账，但尚未映射可执行跨系统 Oracle", "severity": "P2", "risk_type": "cross_system_oracle", "detail": "在 multi_source_reasoning.cross_system_oracles 中声明两侧接口、主键与对比字段。", "source": "prd"})
    return contracts[:100], gaps[:100]


def _enum_or_numeric_invalids(operation: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for parameter in operation.get("parameters") or []:
        if not isinstance(parameter, dict) or str(parameter.get("in") or "").lower() != "query":
            continue
        name = str(parameter.get("name") or "").strip()
        if not name:
            continue
        schema = parameter.get("schema") if isinstance(parameter.get("schema"), dict) else {}
        enum = list(schema.get("enum") or [])
        if enum:
            results.append({"parameter": name, "invalid_value": "__qualibug_invalid_enum__", "expected_statuses": [400, 422], "origin": "openapi_enum"})
            continue
        try:
            minimum = schema.get("minimum")
            maximum = schema.get("maximum")
            if minimum is not None:
                results.append({"parameter": name, "invalid_value": float(minimum) - 1, "expected_statuses": [400, 422], "origin": "openapi_minimum"})
            elif maximum is not None:
                results.append({"parameter": name, "invalid_value": float(maximum) + 1, "expected_statuses": [400, 422], "origin": "openapi_maximum"})
        except Exception:
            continue
    return results[:12]


def _abnormal_path_contracts(openapi: dict[str, Any], catalog: list[dict[str, Any]], section: dict[str, Any], prd: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    operations_by_path = {(str(op.get("path")), str(op.get("method"))): op for op in _operations(openapi)}
    contracts: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    explicit = _as_list(section.get("exception_paths") or section.get("abnormal_paths"))
    for row in explicit:
        path = str(row.get("path") or "")
        op = operations_by_path.get((path, "GET"))
        if not op:
            gaps.append({"candidate_id": f"MSR_EXCEPTION_GAP_{len(gaps)+1:03d}", "title": "异常路径未映射到 GET 接口", "severity": "P2", "risk_type": "exception_path", "detail": f"path={path} 需要对应 GET OpenAPI operation 才能在 safe_live 执行。", "source": "enterprise_config"})
            continue
        parameter = str(row.get("parameter") or "")
        if not parameter:
            continue
        contracts.append({
            "contract_id": f"MSR_EXCEPTION_{len(contracts)+1:03d}", "oracle_family": "exception_path",
            "title": str(row.get("title") or f"异常输入不可静默回退：{path} {parameter}"),
            "path": path, "method": "GET", "parameter": parameter, "invalid_value": row.get("invalid_value", "__qualibug_invalid__"),
            "baseline_query": dict(row.get("baseline_query") or row.get("query") or {}),
            "expected_statuses": [int(x) for x in (row.get("expected_statuses") or [400, 422]) if str(x).strip().lstrip("-").isdigit()],
            "allow_empty_success": bool(row.get("allow_empty_success", True)),
            "execution_policy": "safe_read_only", "discovery": "enterprise_config", "source_evidence": ["configuration", "openapi", "prd"],
        })
    known = {(row.get("path"), row.get("parameter")) for row in contracts}
    for collection in catalog:
        op = operations_by_path.get((str(collection.get("path")), "GET"))
        if not op:
            continue
        for invalid in _enum_or_numeric_invalids(op):
            key = (collection.get("path"), invalid.get("parameter"))
            if key in known:
                continue
            contracts.append({
                "contract_id": f"MSR_EXCEPTION_{len(contracts)+1:03d}", "oracle_family": "exception_path",
                "title": f"异常输入不可被静默忽略：{collection.get('path')} {invalid.get('parameter')}",
                "path": collection.get("path"), "method": "GET", "parameter": invalid.get("parameter"), "invalid_value": invalid.get("invalid_value"),
                "baseline_query": dict(collection.get("sample_query") or {}), "expected_statuses": invalid.get("expected_statuses") or [400, 422],
                "allow_empty_success": True, "execution_policy": "safe_read_only", "discovery": str(invalid.get("origin")), "source_evidence": ["openapi", "runtime"],
            })
    if re.search(r"异常|错误|非法|无效|失败|不得|禁止|invalid|error", str(prd), re.I) and not contracts:
        gaps.append({"candidate_id": "MSR_PRD_EXCEPTION_UNMAPPED", "title": "PRD 含异常处理要求，但 OpenAPI 未提供可安全验证的查询参数", "severity": "P2", "risk_type": "exception_path", "detail": "为异常输入补充 query 参数、失败状态码或专用只读校验接口。", "source": "prd"})
    return contracts[:160], gaps[:100]


def _concurrency_contracts(openapi: dict[str, Any], section: dict[str, Any], prd: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contracts: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    explicit = _as_list(section.get("concurrency_paths") or section.get("concurrent_paths"))
    known: set[tuple[str, str]] = set()
    for row in explicit:
        path = str(row.get("path") or "")
        method = str(row.get("method") or "POST").upper()
        if not path:
            continue
        contracts.append({
            "contract_id": f"MSR_CONCURRENT_{len(contracts)+1:03d}", "oracle_family": "concurrency_path",
            "title": str(row.get("title") or f"并发/重放一致性：{method} {path}"), "path": path, "method": method,
            "expected": str(row.get("expected") or "相同业务意图并发触发后，应至多产生一次业务结果，并保持金额、库存、状态一致。"),
            "idempotency_key": row.get("idempotency_key") or row.get("idempotency_header"),
            "resource_key": row.get("resource_key"), "safe_sandbox": bool(row.get("safe_sandbox")),
            "execution_policy": "sandbox_required", "discovery": "enterprise_config", "source_evidence": ["configuration", "prd", "openapi"],
        })
        known.add((path, method))
    prd_lower = str(prd).lower()
    for operation in _operations(openapi):
        method = str(operation.get("method") or "").upper()
        if method not in _WRITE_METHODS:
            continue
        path = str(operation.get("path") or "")
        text = " ".join([path, str(operation.get("summary") or ""), str(operation.get("description") or ""), prd_lower]).lower()
        if not any(word in text for word in _CONCURRENT_WORDS) or (path, method) in known:
            continue
        raw = operation.get("raw_operation") if isinstance(operation.get("raw_operation"), dict) else {}
        headers = [str(param.get("name") or "") for param in (operation.get("parameters") or []) if isinstance(param, dict) and str(param.get("in") or "").lower() == "header"]
        idempotency = next((name for name in headers if "idempot" in name.lower() or "request" in name.lower()), None)
        contracts.append({
            "contract_id": f"MSR_CONCURRENT_{len(contracts)+1:03d}", "oracle_family": "concurrency_path",
            "title": f"自动推导并发路径：{method} {path}", "path": path, "method": method,
            "expected": "相同业务意图的重放/并发请求不得造成重复业务实体、重复扣减、重复资金变化或非法状态竞争。",
            "idempotency_key": idempotency, "resource_key": None,
            "safe_sandbox": bool(raw.get("x-qualibug-safe-concurrent") or raw.get("x-safe-sandbox")),
            "execution_policy": "sandbox_required", "discovery": "prd_openapi_semantics", "source_evidence": ["prd", "openapi"],
        })
    if any(word in prd_lower for word in _CONCURRENT_WORDS) and not contracts:
        gaps.append({"candidate_id": "MSR_PRD_CONCURRENCY_UNMAPPED", "title": "PRD 含幂等/并发要求，但 OpenAPI 未识别写操作路径", "severity": "P1", "risk_type": "concurrency_path", "detail": "提供测试沙箱请求模板、幂等键字段及可回收测试数据后，可执行真正并发验证。", "source": "prd"})
    return contracts[:120], gaps[:100]


def _load_historical_snapshots(project: str, root: Path, section: dict[str, Any]) -> list[dict[str, Any]]:
    paths = config_paths(project, root)
    snapshots: list[dict[str, Any]] = []
    snapshots.extend(_as_list(section.get("historical_data_paths") or section.get("historical_snapshots")))
    snapshot_dir = paths["input_dir"] / "historical_snapshots"
    if snapshot_dir.exists():
        for file in sorted(snapshot_dir.glob("*.json")):
            data = _load_json(file, {})
            rows = _as_list(data)
            if rows:
                snapshots.extend(rows)
            elif isinstance(data, dict):
                snapshots.append({**data, "source_file": str(file.relative_to(root)).replace("\\", "/")})
    return [item for item in snapshots if isinstance(item, dict)]


def _historical_contracts(catalog: list[dict[str, Any]], project: str, root: Path, section: dict[str, Any], prd: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    snapshots = _load_historical_snapshots(project, root, section)
    contracts: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for row in snapshots:
        path = str(row.get("path") or row.get("collection_path") or "")
        collection = _find_collection(catalog, path)
        if not collection:
            gaps.append({"candidate_id": f"MSR_HISTORY_GAP_{len(gaps)+1:03d}", "title": "历史数据快照未映射当前读取接口", "severity": "P2", "risk_type": "historical_data_path", "detail": f"path={path} 需要对应 GET collection。", "source": row.get("source_file") or "enterprise_config"})
            continue
        records = _as_list(row.get("records") or row.get("items") or row.get("historical_records"))
        identity = str(row.get("identity_field") or collection.get("identity_field") or "") or None
        fields = [str(item) for item in (row.get("compatibility_fields") or row.get("required_fields") or []) if str(item).strip()]
        contracts.append({
            "contract_id": f"MSR_HISTORY_{len(contracts)+1:03d}", "oracle_family": "historical_data_path",
            "title": str(row.get("title") or f"历史数据兼容性：{path}"), "collection": collection,
            "historical_records": records[:1000], "identity_field": identity, "compatibility_fields": fields,
            "require_presence": bool(row.get("require_presence", False)), "sample_query": dict(row.get("sample_query") or row.get("query") or collection.get("sample_query") or {}),
            "execution_policy": "safe_read_only", "discovery": "historical_snapshot", "source_evidence": ["historical_snapshot", "prd", "openapi"],
        })
    if any(word in str(prd).lower() for word in _HISTORY_WORDS) and not contracts:
        gaps.append({"candidate_id": "MSR_PRD_HISTORY_UNMAPPED", "title": "PRD 提到历史/迁移/兼容数据，但未提供可执行历史快照", "severity": "P2", "risk_type": "historical_data_path", "detail": "将脱敏历史样本放入 platform_inputs/<project>/historical_snapshots/*.json，配置 path、identity_field 与 compatibility_fields。", "source": "prd"})
    return contracts[:120], gaps[:100]


def _load_page_observations(project: str, root: Path, section: dict[str, Any]) -> list[dict[str, Any]]:
    paths = config_paths(project, root)
    rows: list[dict[str, Any]] = []
    rows.extend(_as_list(section.get("page_oracles") or section.get("ui_oracles")))
    for name in ("ui_observations.json", "page_observations.json", "ui_oracles.json"):
        data = _load_json(paths["input_dir"] / name, {})
        rows.extend(_as_list(data))
    return rows


def _page_contracts(project: str, root: Path, section: dict[str, Any], prd: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contracts: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for row in _load_page_observations(project, root, section):
        path = str(row.get("api_path") or row.get("path") or "")
        field = str(row.get("api_field") or row.get("field") or "")
        value = row.get("observed_value", row.get("value"))
        if not path or not field or value is None:
            gaps.append({"candidate_id": f"MSR_PAGE_GAP_{len(gaps)+1:03d}", "title": "页面观测缺少 API 绑定或可比值", "severity": "P3", "risk_type": "page_api_oracle", "detail": "页面 Oracle 需要 api_path、api_field 与 observed_value；可由浏览器采集器写入 ui_observations.json。", "source": "ui_observation"})
            continue
        contracts.append({
            "contract_id": f"MSR_PAGE_{len(contracts)+1:03d}", "oracle_family": "page_api_oracle",
            "title": str(row.get("title") or f"页面/API Oracle：{path} {field}"), "api_path": path, "api_field": field,
            "observed_value": value, "query": dict(row.get("query") or {}), "tolerance": float(row.get("tolerance") or 0),
            "page_url": row.get("page_url"), "page_label": row.get("page_label") or row.get("metric"), "execution_policy": "safe_read_only",
            "discovery": "ui_observation", "source_evidence": ["page", "runtime", "openapi"],
        })
    if re.search(r"页面|看板|报表|展示|列表|dashboard|ui", str(prd), re.I) and not contracts:
        gaps.append({"candidate_id": "MSR_PRD_UI_ORACLE_UNMAPPED", "title": "PRD 描述页面展示，但尚无页面/API Oracle 绑定", "severity": "P3", "risk_type": "page_api_oracle", "detail": "采集页面可见指标并声明其 API 来源，才能自动发现“页面正常但展示口径错误”。", "source": "prd"})
    return contracts[:100], gaps[:100]


def _iter_feedback_paths(project: str, root: Path) -> list[Path]:
    paths = config_paths(project, root)
    workspace = root / "platform_workspace" / _safe_project_id(project) / "defect_discovery"
    candidates = [
        paths["input_dir"] / "confirmed_bug_feedback.jsonl",
        paths["input_dir"] / "confirmed_bugs.json",
        paths["input_dir"] / "qa_feedback.jsonl",
        workspace / "confirmed_bug_feedback.jsonl",
        workspace / "human_feedback" / "feedback.jsonl",
    ]
    return [path for path in candidates if path.exists() and path.is_file()]


def _read_feedback_file(path: Path) -> list[dict[str, Any]]:
    try:
        if path.suffix.lower() == ".jsonl":
            rows: list[dict[str, Any]] = []
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    item = json.loads(line)
                    if isinstance(item, dict):
                        rows.append(item)
                except Exception:
                    continue
            return rows
        return _as_list(_load_json(path, {}))
    except Exception:
        return []


def _is_confirmed(row: dict[str, Any]) -> bool:
    if row.get("is_valid_bug") is True or row.get("confirmed") is True or row.get("is_confirmed") is True:
        return not bool(row.get("is_false_positive"))
    status = _norm(row.get("status") or row.get("review_status") or row.get("result"))
    return status in {"confirmed", "validbug", "accepted", "已确认", "确认缺陷", "真实缺陷"}


def _keywords(value: Any) -> list[str]:
    text = str(value or "")
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", text)
    stop = {"this", "that", "with", "from", "接口", "系统", "功能", "问题", "数据", "用户", "需要", "正常"}
    return sorted({_norm(token) for token in tokens if _norm(token) and _norm(token) not in stop})[:30]


def ingest_confirmed_bug_feedback(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    """Ingest only explicitly confirmed defects into a redacted enterprise memory."""
    root = root or ROOT
    project = _safe_project_id(project_id)
    output = _output_paths(project, root)
    old = _load_json(output["memory"], {})
    old = old if isinstance(old, dict) else {}
    seen = set(str(item) for item in (old.get("seen_feedback_ids") or []))
    entries = old.get("entries") if isinstance(old.get("entries"), dict) else {}
    added: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    for path in _iter_feedback_paths(project, root):
        rows = _read_feedback_file(path)
        confirmed = 0
        for index, row in enumerate(rows):
            if not _is_confirmed(row):
                continue
            confirmed += 1
            source_id = str(row.get("feedback_id") or row.get("review_item_id") or row.get("source_bug_key") or row.get("issue_id") or f"{path.name}:{index}")
            stable = _hash({"source": str(path), "id": source_id, "title": row.get("title"), "risk": row.get("risk_type")})
            if stable in seen:
                continue
            risk = str(row.get("risk_type") or row.get("predicted_risk_type") or "business_rule")
            family = str(row.get("oracle_family") or row.get("business_invariant_type") or row.get("business_reconciliation_type") or row.get("business_outcome_type") or "confirmed_bug")
            title = str(row.get("title") or row.get("summary") or risk)
            entry = {
                "feedback_fingerprint": stable,
                "source_id": source_id,
                "risk_type": risk,
                "oracle_family": family,
                "severity": str(row.get("human_severity") or row.get("severity") or "P2"),
                "root_cause": str(row.get("root_cause") or "unknown"),
                "keywords": _keywords(" ".join([title, risk, family, str(row.get("affected_api") or row.get("path") or "")])),
                "confirmed_at_utc": str(row.get("reviewed_at_utc") or row.get("confirmed_at") or _now()),
                # Evidence is purposefully not copied; source systems may contain PII.
                "evidence_policy": "redacted_metadata_only",
            }
            entries[stable] = entry
            seen.add(stable)
            added.append(entry)
        files.append({"file": str(path.relative_to(root)).replace("\\", "/"), "row_count": len(rows), "confirmed_count": confirmed})
    all_entries = list(entries.values())
    pattern_counter: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in all_entries:
        key = (str(entry.get("risk_type") or "business_rule"), str(entry.get("oracle_family") or "confirmed_bug"))
        data = pattern_counter.setdefault(key, {"risk_type": key[0], "oracle_family": key[1], "confirmed_count": 0, "keywords": Counter(), "severities": Counter()})
        data["confirmed_count"] += 1
        data["keywords"].update(entry.get("keywords") or [])
        data["severities"].update([entry.get("severity") or "P2"])
    patterns = []
    for value in pattern_counter.values():
        patterns.append({"risk_type": value["risk_type"], "oracle_family": value["oracle_family"], "confirmed_count": value["confirmed_count"], "keywords": [item for item, _ in value["keywords"].most_common(12)], "severity_distribution": dict(value["severities"])})
    memory = {"phase": "phase46_confirmed_bug_learning", "project_id": project, "updated_at_utc": _now(), "seen_feedback_ids": sorted(seen), "entries": entries, "patterns": sorted(patterns, key=lambda item: (-int(item["confirmed_count"]), item["risk_type"], item["oracle_family"]))}
    output["workspace"].mkdir(parents=True, exist_ok=True)
    _write_json(output["memory"], memory)
    # Training data is compact, redacted metadata suitable for later controlled model work.
    with output["training"].open("w", encoding="utf-8") as handle:
        for entry in all_entries:
            handle.write(json.dumps({"label": "confirmed_bug", "risk_type": entry.get("risk_type"), "oracle_family": entry.get("oracle_family"), "severity": entry.get("severity"), "root_cause": entry.get("root_cause"), "keywords": entry.get("keywords"), "evidence_policy": "redacted_metadata_only"}, ensure_ascii=False) + "\n")
    return {"memory": memory, "summary": {"feedback_file_count": len(files), "new_confirmed_bug_count": len(added), "confirmed_bug_memory_count": len(all_entries), "learned_pattern_count": len(patterns)}, "files": files}


def _learning_bonus(contract: dict[str, Any], memory: dict[str, Any]) -> tuple[float, list[dict[str, Any]]]:
    family = _norm(contract.get("oracle_family"))
    text = " ".join([str(contract.get("title") or ""), str(contract.get("path") or ""), str(contract.get("resource") or ""), _json(contract.get("source_evidence") or [])])
    token_set = set(_keywords(text))
    matches: list[dict[str, Any]] = []
    bonus = 0.0
    for pattern in memory.get("patterns") or []:
        if not isinstance(pattern, dict):
            continue
        pattern_family = _norm(pattern.get("oracle_family"))
        pattern_risk = _norm(pattern.get("risk_type"))
        keys = set(pattern.get("keywords") or [])
        overlap = sorted(token_set & keys)
        family_match = bool(family and (family == pattern_family or family in pattern_family or pattern_family in family))
        if family_match or len(overlap) >= 2:
            current = min(0.22, 0.05 + 0.03 * min(int(pattern.get("confirmed_count") or 0), 5) + 0.02 * min(len(overlap), 3))
            bonus = max(bonus, current)
            matches.append({"risk_type": pattern_risk or pattern.get("risk_type"), "oracle_family": pattern.get("oracle_family"), "confirmed_count": pattern.get("confirmed_count"), "keyword_overlap": overlap[:6], "bonus": round(current, 3)})
    return round(min(0.25, bonus), 3), matches[:5]


def _profile_contract_summary(contracts: dict[str, list[dict[str, Any]]]) -> dict[str, int]:
    return {
        "cross_system_oracle_count": len(contracts.get("cross_system") or []),
        "page_api_oracle_count": len(contracts.get("page_api") or []),
        "exception_path_count": len(contracts.get("exception_path") or []),
        "concurrency_path_count": len(contracts.get("concurrency") or []),
        "historical_data_path_count": len(contracts.get("historical") or []),
    }


def _profile_for_persistence(profile: dict[str, Any]) -> dict[str, Any]:
    """Remove historical business rows before profile/run JSON leaves the process.

    Runtime execution keeps the source snapshot in memory.  Persisted artifacts
    retain only a count and non-reversible identity fingerprints so reports can
    explain coverage without copying enterprise history into output folders.
    """
    safe = json.loads(json.dumps(profile, ensure_ascii=False, default=str))
    for contract in ((safe.get("contracts") or {}).get("historical") or []):
        records = contract.pop("historical_records", [])
        if not isinstance(records, list):
            records = []
        identity = str(contract.get("identity_field") or "")
        keys = []
        for record in records[:50]:
            if not isinstance(record, dict):
                continue
            raw = _canonical(_value(record, identity)) if identity else ""
            if raw:
                keys.append(_short({"identity": identity, "value": raw}))
        contract["historical_snapshot_metadata"] = {
            "record_count": len(records),
            "identity_field": identity or None,
            "identity_fingerprints_sample": keys[:20],
            "raw_records_persisted": False,
        }
    return safe


def build_multi_source_reasoning_profile(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    paths = config_paths(project, root)
    cfg = load_real_project_config(project, root)
    section = _section(cfg)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    openapi = openapi if isinstance(openapi, dict) else {}
    prd = _read_text(paths["input_dir"] / "prd.md")
    catalog = _collection_catalog(openapi, cfg)
    learning = ingest_confirmed_bug_feedback(project, root)
    memory = learning.get("memory") or {}
    cross, cross_gaps = _cross_system_contracts(catalog, section, prd)
    page, page_gaps = _page_contracts(project, root, section, prd)
    exception, exception_gaps = _abnormal_path_contracts(openapi, catalog, section, prd)
    concurrency, concurrency_gaps = _concurrency_contracts(openapi, section, prd)
    historical, history_gaps = _historical_contracts(catalog, project, root, section, prd)
    contracts = {"cross_system": cross, "page_api": page, "exception_path": exception, "concurrency": concurrency, "historical": historical}
    all_contracts = [item for group in contracts.values() for item in group]
    for contract in all_contracts:
        bonus, matches = _learning_bonus(contract, memory)
        contract["learning_bonus"] = bonus
        contract["learning_matches"] = matches
    candidates = [*cross_gaps, *page_gaps, *exception_gaps, *concurrency_gaps, *history_gaps]
    summary = {
        **_profile_contract_summary(contracts),
        "collection_catalog_count": len(catalog),
        "total_contract_count": len(all_contracts),
        "contract_gap_count": len(candidates),
        "confirmed_bug_memory_count": int((learning.get("summary") or {}).get("confirmed_bug_memory_count") or 0),
        "new_confirmed_bug_count": int((learning.get("summary") or {}).get("new_confirmed_bug_count") or 0),
        "learned_pattern_count": int((learning.get("summary") or {}).get("learned_pattern_count") or 0),
    }
    result = {
        "phase": "phase46_multi_source_business_reasoning",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "source_inventory": {"prd_available": bool(prd.strip()), "api_operation_count": len(_operations(openapi)), "collection_count": len(catalog), "page_observation_count": len(_load_page_observations(project, root, section)), "historical_snapshot_count": len(_load_historical_snapshots(project, root, section)), "confirmed_feedback_file_count": int((learning.get("summary") or {}).get("feedback_file_count") or 0)},
        "collection_catalog": catalog,
        "contracts": contracts,
        "candidates": candidates,
        "confirmed_bug_learning": {"summary": learning.get("summary") or {}, "patterns": (memory.get("patterns") or [])[:50]},
        "summary": summary,
        "governance": {"default_execution": "plan_only", "safe_live_only_uses_get": True, "concurrent_write_paths_are_sandbox_candidates": True, "historical_snapshot_data_is_local_and_should_be_desensitized": True, "confirmed_feedback_is_metadata_only": True, "unconfirmed_findings_remain_needs_human_review": True, "raw_business_payloads_are_not_persisted": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    persisted = _profile_for_persistence(result)
    output = _output_paths(project, root)
    _write_json(output["out"] / "multi_source_reasoning_profile.json", persisted)
    _write_json(output["workspace"] / "multi_source_reasoning_profile.json", persisted)
    output["out"].mkdir(parents=True, exist_ok=True)
    (output["out"] / "multi_source_reasoning_profile_report.html").write_text(render_multi_source_profile_report(persisted), encoding="utf-8")
    return result


def load_multi_source_reasoning_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _load_json(_output_paths(project, root)["workspace"] / "multi_source_reasoning_profile.json", {})
    return data if isinstance(data, dict) and data else None


def generate_multi_source_reasoning_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    profile = load_multi_source_reasoning_profile(project_id, root) or build_multi_source_reasoning_profile(project_id, root)
    probes: list[dict[str, Any]] = []
    limit = int(max_count or cfg.get("max_probe_count") or 120)
    mapping = {
        "cross_system": ("cross_system_oracle", "跨系统数据 Oracle"),
        "page_api": ("page_api_oracle", "页面/API Oracle"),
        "exception_path": ("exception_path", "异常路径"),
        "concurrency": ("concurrency_path", "并发路径"),
        "historical": ("historical_data_path", "历史数据路径"),
    }
    for group, rows in (profile.get("contracts") or {}).items():
        risk_type, prefix = mapping.get(group, ("business_reasoning", "业务推理"))
        for contract in rows or []:
            if not isinstance(contract, dict):
                continue
            path = str(contract.get("path") or (contract.get("left") or {}).get("path") or (contract.get("api_path") or (contract.get("collection") or {}).get("path") or ""))
            method = str(contract.get("method") or (contract.get("left") or {}).get("method") or "GET").upper()
            probes.append({
                "probe_id": f"MSR_PROBE_{len(probes)+1:04d}", "source": "multi_source_business_reasoning", "risk_type": risk_type,
                "reasoning_type": group, "title": f"{prefix}：{contract.get('title')}", "severity": "P1" if group in {"cross_system", "exception_path", "concurrency"} else "P2",
                "method": method, "path": path, "actor": "normal_user", "expected": contract.get("expected") or "业务 Oracle 应持续成立。",
                "execution_policy": contract.get("execution_policy") or "candidate_only", "destructive": group == "concurrency", "contract_id": contract.get("contract_id"),
                "learning_bonus": contract.get("learning_bonus") or 0.0, "learning_matches": contract.get("learning_matches") or [],
            })
            if len(probes) >= limit:
                return probes
    for gap in profile.get("candidates") or []:
        probes.append({"probe_id": f"MSR_PROBE_{len(probes)+1:04d}", "source": "multi_source_business_reasoning", "risk_type": gap.get("risk_type") or "business_reasoning", "reasoning_type": "contract_gap", "title": gap.get("title"), "severity": gap.get("severity") or "P2", "method": "GET", "path": "", "actor": "normal_user", "expected": gap.get("detail") or "补充可执行业务 Oracle。", "execution_policy": "candidate_only", "destructive": False, "contract_id": gap.get("candidate_id")})
        if len(probes) >= limit:
            break
    return probes[:limit]


def _fetch_collection(base_url: str, contract: dict[str, Any], token: str | None, timeout: int, max_bytes: int, max_pages: int, query_override: dict[str, Any] | None = None) -> dict[str, Any]:
    source = {"path": contract.get("path"), "method": "GET", "parameters": contract.get("parameters") or []}
    return _fetch_source_pages(base_url, {"source": source, "sample_query": dict(query_override if query_override is not None else contract.get("sample_query") or {}), "pagination": contract.get("pagination") or {}}, token, timeout, max_bytes, max_pages)


def _value(row: dict[str, Any], field: str | None) -> Any:
    if not isinstance(row, dict) or not field:
        return None
    if field in row:
        return row.get(field)
    wanted = _norm(field)
    for key, value in row.items():
        if _norm(key) == wanted:
            return value
    return None


def _canonical(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value).strip()


def _path_value(payload: Any, dotted_path: str) -> Any:
    value = payload
    for part in [item for item in str(dotted_path or "").split(".") if item]:
        if not isinstance(value, dict):
            return None
        if part in value:
            value = value.get(part)
            continue
        target = _norm(part)
        matched = next((key for key in value if _norm(key) == target), None)
        if matched is None:
            return None
        value = value.get(matched)
    return value


def _numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"^[¥$€£]\s*", "", str(value).strip().replace(",", ""))
    try:
        return float(text)
    except Exception:
        return None


def _finding(contract: dict[str, Any], kind: str, title: str, expected: str, actual: str, evidence: dict[str, Any], severity: str = "P1", confidence: float = 0.84, key: Any | None = None) -> dict[str, Any]:
    fingerprint = _hash({"contract_id": contract.get("contract_id"), "kind": kind, "key": key or title})
    return {
        "issue_id": f"MSR_{fingerprint[:12].upper()}", "fingerprint": fingerprint, "source": "multi_source_business_reasoning",
        "risk_type": str(contract.get("oracle_family") or "business_reasoning"), "reasoning_type": str(contract.get("oracle_family") or "business_reasoning"),
        "contract_id": contract.get("contract_id"), "title": title, "severity": severity, "status": "needs_human_review",
        "confidence": round(min(0.96, confidence + float(contract.get("learning_bonus") or 0)), 3), "expected": expected, "actual": actual,
        "evidence": _redact(evidence), "learning_matches": contract.get("learning_matches") or [],
    }


def audit_cross_system_oracle(contract: dict[str, Any], left_context: dict[str, Any], right_context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    left = contract.get("left") or {}
    right = contract.get("right") or {}
    left_id = str(left.get("identity_field") or "")
    right_id = str(right.get("identity_field") or "")
    left_rows = [row for row in (left_context.get("records") or []) if isinstance(row, dict)]
    right_rows = [row for row in (right_context.get("records") or []) if isinstance(row, dict)]
    left_index = {_canonical(_value(row, left_id)): row for row in left_rows if _canonical(_value(row, left_id))}
    right_index = {_canonical(_value(row, right_id)): row for row in right_rows if _canonical(_value(row, right_id))}
    if bool(contract.get("require_same_coverage")) and left_context.get("complete") and right_context.get("complete"):
        missing_left = sorted(set(right_index) - set(left_index))
        missing_right = sorted(set(left_index) - set(right_index))
        observations.append({"kind": "coverage", "left_count": len(left_index), "right_count": len(right_index), "missing_left": len(missing_left), "missing_right": len(missing_right)})
        if missing_left or missing_right:
            findings.append(_finding(contract, "cross_system_missing_entity", f"跨系统记录集合不一致：{contract.get('title')}", "两个系统同口径业务主键集合应一致。", f"左系统缺少 {len(missing_left)} 条，右系统缺少 {len(missing_right)} 条。", {"left_path": left.get("path"), "right_path": right.get("path"), "missing_from_left_sample": missing_left[:10], "missing_from_right_sample": missing_right[:10], "left_coverage": {"complete": left_context.get("complete"), "total": left_context.get("total")}, "right_coverage": {"complete": right_context.get("complete"), "total": right_context.get("total")}}, severity="P1", confidence=0.91, key={"missing_left": missing_left[:10], "missing_right": missing_right[:10]}))
    shared = sorted(set(left_index) & set(right_index))
    for mapping in contract.get("field_mappings") or []:
        left_field = str(mapping.get("left_field") or "")
        right_field = str(mapping.get("right_field") or "")
        mismatches: list[dict[str, Any]] = []
        for key in shared:
            actual_left = _canonical(_value(left_index[key], left_field))
            actual_right = _canonical(_value(right_index[key], right_field))
            if actual_left != actual_right:
                mismatches.append({"business_key": key, "left": actual_left, "right": actual_right})
        observations.append({"kind": "field_equality", "left_field": left_field, "right_field": right_field, "shared_count": len(shared), "mismatch_count": len(mismatches)})
        if mismatches:
            findings.append(_finding(contract, "cross_system_field_mismatch", f"跨系统字段不一致：{contract.get('title')} {left_field}", f"相同业务主键下 {left_field} 与 {right_field} 应一致。", f"发现 {len(mismatches)} 条跨系统字段不一致。", {"left_path": left.get("path"), "right_path": right.get("path"), "left_field": left_field, "right_field": right_field, "mismatch_sample": mismatches[:12], "shared_business_key_count": len(shared)}, severity="P1", confidence=0.92, key={"field": [left_field, right_field], "keys": [item["business_key"] for item in mismatches[:12]]}))
    return findings, observations


def _stable_collection_signature(context: dict[str, Any]) -> dict[str, Any]:
    rows = [row for row in (context.get("records") or []) if isinstance(row, dict)]
    cleaned: list[dict[str, Any]] = []
    for row in rows[:200]:
        cleaned.append({str(key): value for key, value in row.items() if not _DYNAMIC_FIELD_RE.search(str(key)) and not _PRIVATE_FIELD_RE.search(str(key))})
    return {"total": context.get("total"), "rows": cleaned}


def audit_exception_path(contract: dict[str, Any], baseline: dict[str, Any], invalid_response: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    status = invalid_response.get("status_code")
    observation = {"parameter": contract.get("parameter"), "invalid_value": contract.get("invalid_value"), "status_code": status, "expected_statuses": contract.get("expected_statuses"), "result": "pass"}
    expected_statuses = {int(item) for item in (contract.get("expected_statuses") or []) if str(item).lstrip("-").isdigit()}
    if status in expected_statuses:
        return [], observation
    if not invalid_response.get("ok"):
        observation["result"] = "nonstandard_error_status"
        return [], observation
    invalid_payload = _parse_json(invalid_response)
    invalid_rows, invalid_total = _extract_records(invalid_payload)
    invalid_context = {"records": invalid_rows, "total": invalid_total}
    same_as_baseline = _hash(_stable_collection_signature(baseline)) == _hash(_stable_collection_signature(invalid_context))
    if same_as_baseline and (invalid_rows or invalid_total not in {0, None}):
        observation["result"] = "silently_ignored"
        finding = _finding(contract, "invalid_input_silently_ignored", f"异常路径被静默忽略：{contract.get('title')}", f"非法 {contract.get('parameter')} 应返回明确参数错误，或至少不能回退为未过滤的成功结果。", f"接口返回 HTTP {status}，结果与未携带该非法参数的基线结果一致。", {"request": {"method": "GET", "path": contract.get("path"), "query": {str(contract.get("parameter")): contract.get("invalid_value")}}, "response_status": status, "baseline_signature": _short(_stable_collection_signature(baseline)), "invalid_signature": _short(_stable_collection_signature(invalid_context)), "baseline_total": baseline.get("total"), "invalid_total": invalid_total}, severity="P1", confidence=0.89, key={"path": contract.get("path"), "parameter": contract.get("parameter")})
        return [finding], observation
    if not invalid_rows and bool(contract.get("allow_empty_success")):
        observation["result"] = "accepted_empty_result"
        return [], observation
    observation["result"] = "accepted_distinct_result_needs_review"
    return [], observation


def audit_page_api_oracle(contract: dict[str, Any], payload: Any, url: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    actual = _path_value(payload, str(contract.get("api_field") or ""))
    expected = contract.get("observed_value")
    tolerance = float(contract.get("tolerance") or 0)
    left_num, right_num = _numeric(actual), _numeric(expected)
    same = abs(left_num - right_num) <= tolerance if left_num is not None and right_num is not None else _canonical(actual) == _canonical(expected)
    observation = {"api_path": contract.get("api_path"), "api_field": contract.get("api_field"), "page_value": _redact(expected), "api_value": _redact(actual), "result": "pass" if same else "mismatch"}
    if same:
        return [], observation
    return [_finding(contract, "page_api_metric_mismatch", f"页面与 API 展示口径不一致：{contract.get('title')}", "页面可见指标应与绑定 API 的同一指标一致。", f"页面值为 {expected}，API 字段 {contract.get('api_field')} 返回 {actual}。", {"page_url": contract.get("page_url"), "page_label": contract.get("page_label"), "api_request": {"method": "GET", "url": url, "path": contract.get("api_path"), "query": _redact(contract.get("query") or {})}, "page_value": expected, "api_value": actual}, severity="P1", confidence=0.9, key={"path": contract.get("api_path"), "field": contract.get("api_field"), "page": contract.get("page_url")})], observation


def audit_historical_data_path(contract: dict[str, Any], current: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    identity = str(contract.get("identity_field") or "")
    historical = [row for row in (contract.get("historical_records") or []) if isinstance(row, dict)]
    present = [row for row in (current.get("records") or []) if isinstance(row, dict)]
    if not identity:
        observations.append({"result": "skipped_missing_identity"})
        return findings, observations
    index = {_canonical(_value(row, identity)): row for row in present if _canonical(_value(row, identity))}
    historic_index = {_canonical(_value(row, identity)): row for row in historical if _canonical(_value(row, identity))}
    if bool(contract.get("require_presence")) and current.get("complete"):
        missing = sorted(set(historic_index) - set(index))
        observations.append({"kind": "historical_presence", "historical_count": len(historic_index), "current_count": len(index), "missing_count": len(missing)})
        if missing:
            findings.append(_finding(contract, "historical_record_not_readable", f"历史记录不可读取：{contract.get('title')}", "配置要求的历史业务记录在升级后仍可被查询到。", f"当前集合缺少 {len(missing)} 条历史业务记录。", {"path": (contract.get("collection") or {}).get("path"), "identity_field": identity, "missing_historical_ids": missing[:20], "historical_snapshot_count": len(historic_index), "current_source_complete": current.get("complete")}, severity="P1", confidence=0.87, key={"identity": identity, "missing": missing[:20]}))
    for field in contract.get("compatibility_fields") or []:
        absent: list[str] = []
        mismatched: list[dict[str, Any]] = []
        for key, prior in historic_index.items():
            now = index.get(key)
            if not now:
                continue
            old_value = _value(prior, field)
            current_value = _value(now, field)
            if current_value is None or _canonical(current_value) == "":
                absent.append(key)
            elif old_value is not None and _canonical(old_value) != _canonical(current_value):
                # Only reported where snapshot explicitly declares a preservation field.
                mismatched.append({"business_key": key, "historical": _canonical(old_value), "current": _canonical(current_value)})
        observations.append({"kind": "compatibility_field", "field": field, "missing_field_value_count": len(absent), "changed_value_count": len(mismatched)})
        if absent:
            findings.append(_finding(contract, "historical_field_loss", f"历史字段丢失：{contract.get('title')} {field}", f"升级后历史数据的兼容字段 {field} 应保留可读值。", f"发现 {len(absent)} 条历史记录的兼容字段为空或缺失。", {"path": (contract.get("collection") or {}).get("path"), "identity_field": identity, "field": field, "missing_value_ids": absent[:20]}, severity="P1", confidence=0.86, key={"field": field, "ids": absent[:20]}))
        if mismatched:
            findings.append(_finding(contract, "historical_value_corruption", f"历史字段值漂移：{contract.get('title')} {field}", f"配置声明为兼容字段的 {field} 在迁移后应保持历史语义。", f"发现 {len(mismatched)} 条历史记录的值被改变。", {"path": (contract.get("collection") or {}).get("path"), "identity_field": identity, "field": field, "mismatch_sample": mismatched[:12]}, severity="P1", confidence=0.83, key={"field": field, "ids": [item["business_key"] for item in mismatched[:20]]}))
    return findings, observations


def run_multi_source_reasoning(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    section = _section(cfg)
    profile = build_multi_source_reasoning_profile(project, root, options)
    execution_mode = str(options.get("execution_mode") or cfg.get("multi_source_reasoning_execution_mode") or section.get("execution_mode") or "plan_only").lower()
    if execution_mode not in {"plan_only", "safe_live"}:
        execution_mode = "plan_only"
    max_bytes = max(100_000, min(int(options.get("max_response_bytes") or section.get("max_response_bytes") or 3_000_000), 20_000_000))
    max_pages = max(1, min(int(options.get("max_source_pages") or section.get("max_source_pages") or 12), 100))
    timeout = max(1, min(int(cfg.get("request_timeout_seconds") or 10), 30))
    base_url = str(cfg.get("base_url") or "")
    token = _normal_token(cfg, project, root, timeout) if execution_mode == "safe_live" and base_url else None
    findings: list[dict[str, Any]] = []
    semantic_hypotheses: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []

    for contract in (profile.get("contracts") or {}).get("cross_system") or []:
        if execution_mode != "safe_live" or not base_url:
            executions.append({"contract_id": contract.get("contract_id"), "family": "cross_system", "status": "planned", "reason": "plan_only_or_missing_base_url"})
            continue
        left = _fetch_collection(base_url, contract.get("left") or {}, token, timeout, max_bytes, max_pages)
        right = _fetch_collection(base_url, contract.get("right") or {}, token, timeout, max_bytes, max_pages)
        if not (left.get("responses") and right.get("responses")):
            executions.append({"contract_id": contract.get("contract_id"), "family": "cross_system", "status": "error", "reason": "collection_fetch_failed"})
            continue
        emitted, observations = audit_cross_system_oracle(contract, left, right)
        findings.extend(emitted)
        executions.append({"contract_id": contract.get("contract_id"), "family": "cross_system", "status": "executed", "left_coverage": {"complete": left.get("complete"), "total": left.get("total"), "row_count": len(left.get("records") or [])}, "right_coverage": {"complete": right.get("complete"), "total": right.get("total"), "row_count": len(right.get("records") or [])}, "observations": observations, "finding_count": len(emitted)})

    for contract in (profile.get("contracts") or {}).get("page_api") or []:
        if execution_mode != "safe_live" or not base_url:
            executions.append({"contract_id": contract.get("contract_id"), "family": "page_api", "status": "planned", "reason": "plan_only_or_missing_base_url"})
            continue
        url = _build_url(base_url, str(contract.get("api_path") or ""), dict(contract.get("query") or {}))
        response = _http_get(url, token, timeout, max_bytes)
        if not response.get("ok"):
            executions.append({"contract_id": contract.get("contract_id"), "family": "page_api", "status": "error", "url": url, "status_code": response.get("status_code"), "error": response.get("error")})
            continue
        emitted, observation = audit_page_api_oracle(contract, _parse_json(response), url)
        findings.extend(emitted)
        executions.append({"contract_id": contract.get("contract_id"), "family": "page_api", "status": "executed", "url": url, "observation": observation, "finding_count": len(emitted)})

    for contract in (profile.get("contracts") or {}).get("exception_path") or []:
        if execution_mode != "safe_live" or not base_url:
            executions.append({"contract_id": contract.get("contract_id"), "family": "exception_path", "status": "planned", "reason": "plan_only_or_missing_base_url"})
            continue
        baseline_query = dict(contract.get("baseline_query") or {})
        baseline_url = _build_url(base_url, str(contract.get("path") or ""), baseline_query)
        baseline_response = _http_get(baseline_url, token, timeout, max_bytes)
        if not baseline_response.get("ok"):
            executions.append({"contract_id": contract.get("contract_id"), "family": "exception_path", "status": "error", "reason": "baseline_fetch_failed", "url": baseline_url, "status_code": baseline_response.get("status_code")})
            continue
        baseline_payload = _parse_json(baseline_response)
        base_rows, base_total = _extract_records(baseline_payload)
        invalid_query = {**baseline_query, str(contract.get("parameter")): contract.get("invalid_value")}
        invalid_url = _build_url(base_url, str(contract.get("path") or ""), invalid_query)
        invalid_response = _http_get(invalid_url, token, timeout, max_bytes)
        emitted, observation = audit_exception_path(contract, {"records": base_rows, "total": base_total}, invalid_response)
        findings.extend(emitted)
        executions.append({"contract_id": contract.get("contract_id"), "family": "exception_path", "status": "executed", "baseline_url": baseline_url, "invalid_url": invalid_url, "observation": observation, "finding_count": len(emitted)})

    # Concurrency writes are deliberately not performed here.  The output is an
    # explicit mutation plan that must be approved and run against a disposable sandbox.
    for contract in (profile.get("contracts") or {}).get("concurrency") or []:
        executions.append({"contract_id": contract.get("contract_id"), "family": "concurrency", "status": "candidate_only", "reason": "write_concurrency_requires_disposable_sandbox_and_explicit_executor", "safe_sandbox_declared": bool(contract.get("safe_sandbox")), "mutation_plan": {"method": contract.get("method"), "path": contract.get("path"), "idempotency_key": contract.get("idempotency_key"), "expected": contract.get("expected")}})

    for contract in (profile.get("contracts") or {}).get("historical") or []:
        if execution_mode != "safe_live" or not base_url:
            executions.append({"contract_id": contract.get("contract_id"), "family": "historical", "status": "planned", "reason": "plan_only_or_missing_base_url"})
            continue
        collection = dict(contract.get("collection") or {})
        collection["sample_query"] = dict(contract.get("sample_query") or collection.get("sample_query") or {})
        current = _fetch_collection(base_url, collection, token, timeout, max_bytes, max_pages)
        emitted, observations = audit_historical_data_path(contract, current)
        findings.extend(emitted)

    # --- LLM-powered semantic reasoning (Phase61 moat upgrade) ---
    if execution_mode == "safe_live" and findings:
        try:
            import json as _json
            llm_result = _llm_reason("multi_source", {
                "prd_text": "", "api_schema": "", "observed_data": _json.dumps(executions[-5:] if "executions" in dir() else [], ensure_ascii=False, default=str)[:4000],
                "heuristic_findings": _json.dumps(findings[:15], ensure_ascii=False, default=str)[:4000],
            })
            semantic_hypotheses.extend(compile_unverified_semantic_hypotheses(
                (llm_result or {}).get("findings"),
                engine="multi_source",
                type_field="multi_source_type",
            ))
        except Exception:
            pass

        executions.append({"contract_id": contract.get("contract_id"), "family": "historical", "status": "executed", "source_coverage": {"complete": current.get("complete"), "total": current.get("total"), "row_count": len(current.get("records") or [])}, "observations": observations, "finding_count": len(emitted)})

    output = _output_paths(project, root)
    registry, findings = _update_registry(output["registry"], findings)
    summary = {
        **(profile.get("summary") or {}), "execution_mode": execution_mode,
        "executed_contract_count": sum(1 for item in executions if item.get("status") == "executed"),
        "candidate_only_concurrency_count": sum(1 for item in executions if item.get("family") == "concurrency"),
        "finding_count": len(findings), "persistent_finding_count": sum(1 for item in findings if (item.get("evidence_stability") or {}).get("persistent")),
        "memory_fingerprint_count": len((registry or {}).get("entries") or {}),
    }
    result = {"phase": "phase46_multi_source_business_reasoning", "project_id": project, "project_name": cfg.get("project_name") or project, "generated_at_utc": _now(), "summary": summary, "profile": _profile_for_persistence(profile), "executions": executions, "semantic_hypotheses": semantic_hypotheses, "llm_governance": {"status": "unverified_hypothesis_only", "does_not_affect_finding_counts": True, "requires_deterministic_replay": True}, "findings": findings, "confirmed_bug_learning": profile.get("confirmed_bug_learning") or {}, "memory_summary": {"counterexample_fingerprint_count": len((registry or {}).get("entries") or {}), "learning_policy": "仅人工明确确认的 Bug 进入企业记忆；相似新假设仅获优先级加分，未确认发现始终需要人工审核。"}, "governance": {"execution_mode": execution_mode, "safe_live_only_uses_get": True, "concurrency_write_requests_not_sent": True, "disposable_sandbox_required_for_concurrency": True, "evidence_redacted_before_persistence": True, "historical_data_not_exported": True, "confirmed_feedback_metadata_only": True}}
    result["private_leak_check"] = _private_leak_check(result)
    _write_json(output["out"] / "multi_source_reasoning_run.json", result)
    _write_json(output["workspace"] / "multi_source_reasoning_run.json", result)
    output["out"].mkdir(parents=True, exist_ok=True)
    (output["out"] / "multi_source_reasoning_run_report.html").write_text(render_multi_source_run_report(result), encoding="utf-8")
    return result


def _cards(summary: dict[str, Any]) -> str:
    labels = [("跨系统 Oracle", summary.get("cross_system_oracle_count", 0)), ("异常路径", summary.get("exception_path_count", 0)), ("并发候选", summary.get("concurrency_path_count", 0)), ("历史路径", summary.get("historical_data_path_count", 0)), ("确认缺陷记忆", summary.get("confirmed_bug_memory_count", 0)), ("发现问题", summary.get("finding_count", 0))]
    return "".join(f"<div class='card'><span>{_html_escape(label)}</span><b>{_html_escape(value)}</b></div>" for label, value in labels)


def _render_html(title: str, badge: str, subtitle: str, cards: str, body: str) -> str:
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>{_html_escape(title)}</title><style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#eef2ff;color:#3730a3}}</style></head><body><section class='hero'><span class='badge'>{_html_escape(badge)}</span><h1>{_html_escape(title)}</h1><p>{_html_escape(subtitle)}</p></section><section class='panel'><div class='grid'>{cards}</div></section>{body}</body></html>"""


def _table(headers: list[str], rows: Iterable[Iterable[Any]]) -> str:
    head = "".join(f"<th>{_html_escape(item)}</th>" for item in headers)
    body = "".join("<tr>" + "".join(f"<td>{_html_escape(item)}</td>" for item in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def render_multi_source_profile_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    rows = []
    for group, contracts in (data.get("contracts") or {}).items():
        for contract in contracts or []:
            rows.append((group, contract.get("contract_id"), contract.get("title"), contract.get("execution_policy"), contract.get("learning_bonus"), contract.get("discovery")))
    body = "<section class='panel'><h2>可执行推理契约</h2>" + _table(["族", "ID", "标题", "执行策略", "确认缺陷加分", "来源"], rows[:300]) + "</section>"
    return _render_html("Phase46 多源业务推理引擎", "PROFILE", "从 PRD、接口、页面、历史快照和确认缺陷记忆中生成可证伪 Oracle。", _cards(summary), body)


def render_multi_source_run_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    findings = data.get("findings") or []
    rows = [(item.get("severity"), item.get("reasoning_type"), item.get("title"), item.get("expected"), item.get("actual"), item.get("confidence"), (item.get("evidence_stability") or {}).get("observations", 1)) for item in findings]
    body = "<section class='panel'><h2>发现结果</h2>" + _table(["级别", "推理路径", "标题", "期望", "实际", "置信度", "观测次数"], rows[:300]) + "</section>"
    return _render_html("Phase46 多源业务推理执行报告", "RUN", "safe_live 仅 GET；并发写路径保持为需审批的沙箱候选。", _cards(summary), body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase46 multi-source business reasoning")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--mode", choices=["profile", "run", "learn"], default="profile")
    parser.add_argument("--execution-mode", choices=["plan_only", "safe_live"], default=None)
    args = parser.parse_args(argv)
    if args.mode == "learn":
        result = ingest_confirmed_bug_feedback(args.project)
    elif args.mode == "run":
        result = run_multi_source_reasoning(args.project, options={"execution_mode": args.execution_mode} if args.execution_mode else {})
    else:
        result = build_multi_source_reasoning_profile(args.project)
    print(json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
