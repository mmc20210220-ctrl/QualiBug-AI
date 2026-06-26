from __future__ import annotations

"""Phase45: business invariant mining and metamorphic counterexample search.

This module is intentionally not a catalog of fixed bug cases.  It turns the
combination of OpenAPI, PRD and observed read-only data into executable
business invariants, then looks for counterexamples:

* a valid filter must restrict the returned records and match the requested field;
* business identities must be unique across the observed collection;
* runtime rows must honor documented required/enum/numeric constraints;
* temporal pairs such as start/end and created/updated must not be inverted;
* a referenced resource (for example customer_id on an order) must resolve.

The engine only performs GET requests in ``safe_live`` mode.  Findings are
redacted, reproducible and kept as evidence fingerprints across runs.  A
finding remains ``needs_human_review`` because real enterprise systems can have
explicit business exceptions that are not visible in an API contract.
"""

import argparse
import hashlib
import json
import re
import time
import urllib.parse
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from .business_outcome_validation import (
    DETAIL_CONTAINER_KEYS,
    LIST_CONTAINER_KEYS,
    _array_item_schema,
    _build_url,
    _http_get,
    _normal_token,
    _private_leak_check,
    _redact,
    _resource_key,
    _update_registry,
)
from .business_reconciliation import _extract_records, _fetch_source_pages, _parse_json
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
from .universal_defect_mining import _operations, _resolve_ref, _schema_type

ID_CANDIDATES = ("id", "uuid", "guid", "code", "number", "no", "serial_no")
FILTER_NAME_RE = re.compile(r"(?:status|state|type|category|tenant|org|organization|owner|user|department|region|channel|status|状态|类型|租户|部门|区域|渠道)", re.I)
FOREIGN_KEY_RE = re.compile(r"(?:^|[_\-.])([a-zA-Z][a-zA-Z0-9]*)(?:_?id|_?code|_?no)$", re.I)
NUMERIC_TYPES = {"integer", "number"}
DYNAMIC_FIELD_RE = re.compile(r"(?:^|[_\-.])(time|timestamp|updated|created|trace|request|nonce|version|etag|cursor|next|last)(?:$|[_\-.])", re.I)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8", errors="replace")).hexdigest()


def _short_hash(value: Any) -> str:
    return _hash(value)[:12]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _output_paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "defect_discovery"
    return {
        "out": root / "platform_outputs" / project / "business_invariant_mining",
        "workspace": workspace,
        "registry": workspace / "business_invariant_evidence_registry.json",
    }


def _value(row: dict[str, Any], field: str, mappings: dict[str, Any] | None = None) -> Any:
    if not isinstance(row, dict) or not field:
        return None
    mapping = mappings or {}
    aliases = [field, mapping.get(field)]
    aliases.extend(key for key, value in mapping.items() if _norm(value) == _norm(field))
    wanted = {_norm(value) for value in aliases if value is not None and _norm(value)}
    for key, value in row.items():
        if _norm(key) in wanted:
            return value
    return None


def _canon(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value).strip()


def _numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().replace(",", "")
    raw = re.sub(r"^[¥$€£]", "", raw)
    if not raw:
        return None
    try:
        return float(raw)
    except Exception:
        return None


def _parse_time(value: Any) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except Exception:
        pass
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern)
        except Exception:
            continue
    return None


def _object_properties(schema: Any, components: dict[str, Any]) -> dict[str, dict[str, Any]]:
    node = _resolve_ref(schema, components)
    if _schema_type(node, components) != "object":
        return {}
    required = {str(name) for name in (node.get("required") or [])}
    result: dict[str, dict[str, Any]] = {}
    for name, child in (node.get("properties") or {}).items():
        if not isinstance(child, dict):
            continue
        resolved = dict(_resolve_ref(child, components))
        resolved["_qualibug_required"] = str(name) in required
        result[str(name)] = resolved
    return result


def _item_fields(operation: dict[str, Any], components: dict[str, Any]) -> dict[str, dict[str, Any]]:
    _, item = _array_item_schema(operation.get("response_schema") or {}, components)
    return _object_properties(item, components) if item else {}


def _is_collection_read(operation: dict[str, Any], components: dict[str, Any]) -> bool:
    if str(operation.get("method") or "").upper() != "GET":
        return False
    path = str(operation.get("path") or "")
    if "{" in path and "}" in path:
        return False
    _, item = _array_item_schema(operation.get("response_schema") or {}, components)
    return bool(item)


def _is_detail_read(operation: dict[str, Any]) -> bool:
    return str(operation.get("method") or "").upper() == "GET" and "{" in str(operation.get("path") or "")


def _operation_parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (operation.get("parameters") or []) if isinstance(item, dict)]


def _query_parameter_info(operation: dict[str, Any], components: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for param in _operation_parameters(operation):
        if str(param.get("in") or "").lower() != "query":
            continue
        name = str(param.get("name") or "").strip()
        if not name:
            continue
        schema = _resolve_ref(param.get("schema") or {}, components)
        rows.append({
            "name": name,
            "type": _schema_type(schema, components),
            "enum": list(schema.get("enum") or [])[:20],
            "minimum": schema.get("minimum"),
            "maximum": schema.get("maximum"),
            "required": bool(param.get("required")),
        })
    return rows


def _find_operation(operations: list[dict[str, Any]], path: str, method: str = "GET") -> dict[str, Any] | None:
    target = str(path or "").rstrip("/") or "/"
    wanted = str(method or "GET").upper()
    for operation in operations:
        if str(operation.get("method") or "").upper() != wanted:
            continue
        current = str(operation.get("path") or "").rstrip("/") or "/"
        if current == target:
            return operation
    return None


def _configured_collections(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    section = cfg.get("business_invariant_mining") or cfg.get("business_invariants") or {}
    if not isinstance(section, dict):
        return []
    rows = section.get("collections") or section.get("contracts") or []
    return [row for row in rows if isinstance(row, dict)]


def _config_for_operation(rows: list[dict[str, Any]], operation: dict[str, Any]) -> dict[str, Any]:
    path = str(operation.get("path") or "").rstrip("/") or "/"
    for row in rows:
        target = str(row.get("path") or row.get("collection_path") or "").rstrip("/") or "/"
        method = str(row.get("method") or row.get("collection_method") or "GET").upper()
        if target == path and method == str(operation.get("method") or "GET").upper():
            return row
    return {}


def _field_by_name(fields: dict[str, dict[str, Any]], wanted: str) -> str | None:
    target = _norm(wanted)
    if not target:
        return None
    for name in fields:
        if _norm(name) == target:
            return name
    for name in fields:
        norm = _norm(name)
        if target in norm or norm in target:
            return name
    return None


def _infer_identity(resource: str, fields: dict[str, dict[str, Any]], configured: dict[str, Any]) -> str | None:
    candidates = [str(item) for item in (configured.get("identity_fields") or []) if str(item).strip()]
    candidates.extend([f"{resource}_id", f"{resource}id", f"{resource}_code", f"{resource}code", *ID_CANDIDATES])
    for candidate in candidates:
        matched = _field_by_name(fields, candidate)
        if matched:
            return matched
    return None


def _configured_filter_rows(configured: dict[str, Any]) -> list[dict[str, Any]]:
    raw = configured.get("filters") or configured.get("filter_contracts") or []
    if isinstance(raw, dict):
        raw = [raw]
    return [item for item in raw if isinstance(item, dict)]


def _infer_filters(operation: dict[str, Any], fields: dict[str, dict[str, Any]], configured: dict[str, Any], components: dict[str, Any]) -> list[dict[str, Any]]:
    configured_rows = _configured_filter_rows(configured)
    filters: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in configured_rows:
        parameter = str(row.get("parameter") or row.get("query") or "").strip()
        field = str(row.get("field") or parameter).strip()
        value = row.get("value")
        matched_field = _field_by_name(fields, field)
        if parameter and matched_field and value is not None:
            filters.append({"parameter": parameter, "field": matched_field, "value": value, "origin": "configured"})
            seen.add(_norm(parameter))
    for param in _query_parameter_info(operation, components):
        name = str(param.get("name") or "")
        if _norm(name) in seen:
            continue
        field = _field_by_name(fields, name)
        enum = [value for value in (param.get("enum") or []) if value is not None and str(value) != ""]
        if not field or not enum:
            continue
        # Only turn a query into an auto assertion when it semantically maps to a
        # returned field.  This avoids treating pagination/sort controls as filters.
        if not FILTER_NAME_RE.search(name) and _norm(name) != _norm(field):
            continue
        filters.append({"parameter": name, "field": field, "value": enum[0], "origin": "openapi_enum"})
    return filters[:8]


def _detail_path_resource(path: str) -> str:
    pieces = [piece for piece in str(path or "").split("/") if piece and not piece.startswith("{")]
    return _resource_key("/" + "/".join(pieces)) if pieces else "resource"


def _infer_foreign_keys(
    operation: dict[str, Any],
    fields: dict[str, dict[str, Any]],
    identity_field: str | None,
    details: list[dict[str, Any]],
    configured: dict[str, Any],
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw = configured.get("foreign_keys") or configured.get("relations") or []
    if isinstance(raw, dict):
        raw = [raw]
    for row in [item for item in raw if isinstance(item, dict)]:
        field = _field_by_name(fields, str(row.get("field") or row.get("source_field") or ""))
        target_path = str(row.get("target_path") or row.get("path") or "")
        if not field or not target_path:
            continue
        parameter = str(row.get("target_parameter") or row.get("parameter") or "")
        if not parameter:
            placeholder = re.search(r"\{([^{}]+)\}", target_path)
            parameter = placeholder.group(1) if placeholder else "id"
        result.append({"field": field, "target_path": target_path, "target_parameter": parameter, "origin": "configured"})
        seen.add(_norm(field))
    for name in fields:
        if name == identity_field or _norm(name) in seen:
            continue
        match = FOREIGN_KEY_RE.search(name)
        if not match:
            continue
        prefix = _norm(match.group(1)).rstrip("s")
        if not prefix:
            continue
        for detail in details:
            resource = _norm(_detail_path_resource(str(detail.get("path") or ""))).rstrip("s")
            if not resource or not (resource == prefix or resource in prefix or prefix in resource):
                continue
            target_path = str(detail.get("path") or "")
            placeholder = re.search(r"\{([^{}]+)\}", target_path)
            if not placeholder:
                continue
            result.append({"field": name, "target_path": target_path, "target_parameter": placeholder.group(1), "origin": "openapi_relation"})
            seen.add(_norm(name))
            break
    return result[:12]


def _infer_temporal_pairs(fields: dict[str, dict[str, Any]], configured: dict[str, Any]) -> list[dict[str, str]]:
    configured_pairs = configured.get("temporal_pairs") or []
    if isinstance(configured_pairs, dict):
        configured_pairs = [configured_pairs]
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in [item for item in configured_pairs if isinstance(item, dict)]:
        left = _field_by_name(fields, str(row.get("start") or row.get("left") or ""))
        right = _field_by_name(fields, str(row.get("end") or row.get("right") or ""))
        if left and right and left != right:
            result.append({"left": left, "right": right, "origin": "configured"})
            seen.add((left, right))
    pairs = [
        ("start_time", "end_time"), ("start_at", "end_at"), ("begin_time", "finish_time"),
        ("begin_at", "finish_at"), ("effective_from", "effective_to"),
        ("valid_from", "valid_to"), ("created_at", "updated_at"),
        ("created_time", "updated_time"), ("开始时间", "结束时间"), ("生效时间", "失效时间"),
    ]
    for left_name, right_name in pairs:
        left, right = _field_by_name(fields, left_name), _field_by_name(fields, right_name)
        if left and right and left != right and (left, right) not in seen:
            result.append({"left": left, "right": right, "origin": "field_semantics"})
            seen.add((left, right))
    return result[:8]


def _runtime_checks(fields: dict[str, dict[str, Any]], configured: dict[str, Any]) -> list[dict[str, Any]]:
    ignore = {_norm(item) for item in (configured.get("ignore_fields") or [])}
    checks: list[dict[str, Any]] = []
    for name, schema in fields.items():
        if _norm(name) in ignore:
            continue
        if bool(schema.get("_qualibug_required")):
            checks.append({"kind": "required_not_null", "field": name})
        enum = list(schema.get("enum") or [])
        if enum:
            checks.append({"kind": "enum_domain", "field": name, "allowed": enum[:50]})
        typ = _schema_type(schema, {})
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        if typ in NUMERIC_TYPES and (minimum is not None or maximum is not None):
            checks.append({"kind": "numeric_bounds", "field": name, "minimum": minimum, "maximum": maximum})
    return checks[:80]


def _prd_candidates(prd: str, contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text = str(prd or "")
    if not text:
        return []
    candidates: list[dict[str, Any]] = []
    if re.search(r"不得重复|不允许重复|唯一|unique|duplicate", text, re.I) and not any(row.get("identity_field") for row in contracts):
        candidates.append({"severity": "P2", "title": "PRD 包含唯一/去重要求，但未能从列表接口推断业务主键", "detail": "在 business_invariant_mining.collections 中配置 identity_fields，可将去重规则转为可运行断言。"})
    if re.search(r"筛选|过滤|filter|query", text, re.I) and not any(row.get("filters") for row in contracts):
        candidates.append({"severity": "P2", "title": "PRD 包含筛选要求，但 OpenAPI 未发现可绑定到返回字段的有效筛选契约", "detail": "为筛选参数补充 enum 或在 business_invariant_mining.collections.filters 中配置 parameter/field/value。"})
    return candidates


def build_business_invariant_contracts(openapi: dict[str, Any], cfg: dict[str, Any], prd_text: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    components = openapi.get("components") or {}
    operations = _operations(openapi)
    collections = [operation for operation in operations if _is_collection_read(operation, components)]
    details = [operation for operation in operations if _is_detail_read(operation)]
    configured_rows = _configured_collections(cfg)
    contracts: list[dict[str, Any]] = []
    for operation in collections:
        configured = _config_for_operation(configured_rows, operation)
        fields = _item_fields(operation, components)
        resource = str(configured.get("resource") or _resource_key(str(operation.get("path") or "")))
        identity = _infer_identity(resource, fields, configured)
        contract = {
            "contract_id": f"BIM_CONTRACT_{len(contracts) + 1:04d}",
            "resource": resource,
            "collection": {
                "path": operation.get("path"), "method": operation.get("method"), "operation_id": operation.get("operation_id"),
                "summary": operation.get("summary"), "parameters": _operation_parameters(operation),
            },
            # _fetch_source_pages uses this layout.
            "source": {"path": operation.get("path"), "method": operation.get("method"), "parameters": _operation_parameters(operation)},
            "sample_query": dict(configured.get("sample_query") or configured.get("query") or {}),
            "pagination": dict(configured.get("pagination") or {}),
            "field_mappings": dict(configured.get("field_mappings") or {}),
            "identity_field": identity,
            "runtime_checks": _runtime_checks(fields, configured),
            "filters": _infer_filters(operation, fields, configured, components),
            "foreign_keys": _infer_foreign_keys(operation, fields, identity, details, configured),
            "temporal_pairs": _infer_temporal_pairs(fields, configured),
            "field_names": list(fields),
            "execution_policy": "safe_read_only",
            "discovery": "configured" if configured else "openapi_inferred",
        }
        contracts.append(contract)
    return contracts, _prd_candidates(prd_text, contracts)


def _probe(contract: dict[str, Any], number: int, kind: str, title: str, severity: str = "P1", **extra: Any) -> dict[str, Any]:
    return {
        "probe_id": f"BIM_PROBE_{number:04d}",
        "source": "business_invariant_mining",
        "risk_type": "business_invariant",
        "business_invariant_type": kind,
        "contract_id": contract.get("contract_id"),
        "title": title,
        "severity": severity,
        "expected": extra.pop("expected", "业务不变量必须持续成立。"),
        "method": "GET",
        "path": (contract.get("collection") or {}).get("path") or "",
        "actor": "normal_user",
        "destructive": False,
        "execution_policy": contract.get("execution_policy") or "safe_read_only",
        **extra,
    }


def generate_business_invariant_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    prd = _read_text(config_paths(project, root)["input_dir"] / "prd.md")
    contracts, candidates = build_business_invariant_contracts(openapi, cfg, prd)
    probes: list[dict[str, Any]] = []
    for contract in contracts:
        if contract.get("identity_field"):
            probes.append(_probe(contract, len(probes) + 1, "identity_unique", f"业务不变量：{contract.get('resource')} 主键不可重复", expected="同一业务主键在同一查询口径中最多出现一次。", field=contract.get("identity_field")))
        for check in contract.get("runtime_checks") or []:
            kind = str(check.get("kind") or "runtime_contract")
            labels = {"required_not_null": "必填字段不得为空", "enum_domain": "枚举值不得漂移", "numeric_bounds": "数值必须落在文档范围"}
            probes.append(_probe(contract, len(probes) + 1, kind, f"业务不变量：{contract.get('resource')} {check.get('field')} {labels.get(kind, '满足契约')}", field=check.get("field"), check=check))
        for pair in contract.get("temporal_pairs") or []:
            probes.append(_probe(contract, len(probes) + 1, "temporal_order", f"业务不变量：{contract.get('resource')} {pair.get('left')} 不得晚于 {pair.get('right')}", field_pair=pair))
        for filt in contract.get("filters") or []:
            probes.append(_probe(contract, len(probes) + 1, "filter_scope", f"业务不变量：{contract.get('resource')} 筛选 {filt.get('parameter')} 必须真正收敛结果", expected="带有效筛选条件的结果必须全部满足该字段条件，且不得超出未筛选结果集合。", filter=filt))
        for relation in contract.get("foreign_keys") or []:
            probes.append(_probe(contract, len(probes) + 1, "referential_integrity", f"业务不变量：{contract.get('resource')} {relation.get('field')} 必须指向存在的资源", expected="业务明细中的外键必须可以通过只读目标资源接口解析。", relation=relation))
    for candidate in candidates:
        probes.append({
            "probe_id": f"BIM_GAP_{len(probes)+1:04d}", "source": "business_invariant_mining", "risk_type": "business_invariant",
            "business_invariant_type": "contract_gap", "title": candidate.get("title"), "severity": candidate.get("severity") or "P2",
            "expected": candidate.get("detail"), "method": "GET", "path": "", "actor": "normal_user", "destructive": False,
            "execution_policy": "candidate_only",
        })
    return probes[: max(1, int(max_count or 120))]


def _finding(contract: dict[str, Any], kind: str, title: str, expected: str, actual: str, evidence: dict[str, Any], confidence: float = 0.9, severity: str = "P1", key: Any | None = None) -> dict[str, Any]:
    fingerprint = _hash({"contract": contract.get("contract_id"), "kind": kind, "key": key or evidence.get("field") or evidence.get("relation")})
    return {
        "issue_id": f"BIM_{fingerprint[:12].upper()}",
        "fingerprint": fingerprint,
        "source": "business_invariant_mining",
        "risk_type": "business_invariant",
        "business_invariant_type": kind,
        "contract_id": contract.get("contract_id"),
        "title": title,
        "severity": severity,
        "status": "needs_human_review",
        "confidence": confidence,
        "expected": expected,
        "actual": actual,
        "evidence": _redact(evidence),
    }


def audit_collection_snapshot(contract: dict[str, Any], source_context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Audit data-derived invariants without issuing network requests."""
    rows = [row for row in (source_context.get("records") or []) if isinstance(row, dict)]
    mappings = dict(contract.get("field_mappings") or {})
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    coverage = {"complete": bool(source_context.get("complete")), "source_total": source_context.get("total"), "fetched_row_count": len(rows)}
    identity = contract.get("identity_field")
    if identity:
        groups: dict[str, list[int]] = {}
        for index, row in enumerate(rows, start=1):
            value = _canon(_value(row, str(identity), mappings))
            if value:
                groups.setdefault(value, []).append(index)
        duplicates = [{"value_hash": _short_hash(value), "row_indexes": indexes[:12], "count": len(indexes)} for value, indexes in groups.items() if len(indexes) > 1]
        observations.append({"kind": "identity_unique", "field": identity, "row_count": len(rows), "duplicate_group_count": len(duplicates)})
        if duplicates:
            findings.append(_finding(
                contract, "identity_unique", f"业务主键重复：{contract.get('resource')} {identity}", "同一业务主键在同一查询口径中最多出现一次。",
                f"发现 {len(duplicates)} 组重复主键，最多重复 {max(item['count'] for item in duplicates)} 次。",
                {"request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": _redact(contract.get("sample_query") or {})}, "field": identity, "duplicates": duplicates[:10], "coverage": coverage},
                confidence=0.97, key=identity,
            ))
    for check in contract.get("runtime_checks") or []:
        kind, field = str(check.get("kind") or ""), str(check.get("field") or "")
        violating: list[dict[str, Any]] = []
        if kind == "required_not_null":
            for index, row in enumerate(rows, start=1):
                if not _canon(_value(row, field, mappings)):
                    violating.append({"row_index": index})
            expected = f"文档标记为必填的字段 {field} 不得为空。"
            actual = f"发现 {len(violating)} 条记录缺失或为空。"
        elif kind == "enum_domain":
            allowed = {str(item) for item in (check.get("allowed") or [])}
            for index, row in enumerate(rows, start=1):
                value = _canon(_value(row, field, mappings))
                if value and value not in allowed:
                    violating.append({"row_index": index, "value_hash": _short_hash(value)})
            expected = f"字段 {field} 必须属于文档枚举 {sorted(allowed)[:20]}。"
            actual = f"发现 {len(violating)} 条记录使用文档外枚举值。"
        elif kind == "numeric_bounds":
            minimum, maximum = _numeric(check.get("minimum")), _numeric(check.get("maximum"))
            for index, row in enumerate(rows, start=1):
                value = _numeric(_value(row, field, mappings))
                if value is not None and ((minimum is not None and value < minimum) or (maximum is not None and value > maximum)):
                    violating.append({"row_index": index, "value": value})
            expected = f"字段 {field} 必须位于 [{minimum if minimum is not None else '-∞'}, {maximum if maximum is not None else '+∞'}]。"
            actual = f"发现 {len(violating)} 条记录超过文档数值边界。"
        else:
            continue
        observations.append({"kind": kind, "field": field, "row_count": len(rows), "violation_count": len(violating)})
        if violating:
            severity = "P1" if kind == "required_not_null" else "P2"
            confidence = 0.95 if bool(source_context.get("complete")) else 0.88
            findings.append(_finding(contract, kind, f"运行时契约被违反：{contract.get('resource')} {field}", expected, actual,
                {"request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": _redact(contract.get("sample_query") or {})}, "field": field, "check": check, "samples": violating[:12], "coverage": coverage}, confidence, severity, key={"kind": kind, "field": field}))
    for pair in contract.get("temporal_pairs") or []:
        left, right = str(pair.get("left") or ""), str(pair.get("right") or "")
        violating = []
        for index, row in enumerate(rows, start=1):
            lv, rv = _parse_time(_value(row, left, mappings)), _parse_time(_value(row, right, mappings))
            if lv is not None and rv is not None and lv > rv:
                violating.append({"row_index": index})
        observations.append({"kind": "temporal_order", "left": left, "right": right, "row_count": len(rows), "violation_count": len(violating)})
        if violating:
            findings.append(_finding(contract, "temporal_order", f"时间业务关系倒置：{contract.get('resource')} {left} > {right}", f"{left} 不得晚于 {right}。", f"发现 {len(violating)} 条记录的时间顺序倒置。", {"request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": _redact(contract.get("sample_query") or {})}, "field_pair": {"left": left, "right": right}, "samples": violating[:12], "coverage": coverage}, 0.91, "P1", key={"left": left, "right": right}))
    return findings, observations


def _audit_filter(contract: dict[str, Any], filter_spec: dict[str, Any], baseline: dict[str, Any], filtered: dict[str, Any], url: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows = [row for row in (filtered.get("records") or []) if isinstance(row, dict)]
    mappings = dict(contract.get("field_mappings") or {})
    field, value = str(filter_spec.get("field") or ""), filter_spec.get("value")
    requested = _canon(value)
    violating = [{"row_index": index, "value_hash": _short_hash(_canon(_value(row, field, mappings)))} for index, row in enumerate(rows, start=1) if _canon(_value(row, field, mappings)) != requested]
    evidence = {
        "request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "url": url, "query": _redact({**(contract.get("sample_query") or {}), str(filter_spec.get("parameter")): value})},
        "filter": _redact(filter_spec),
        "filtered_coverage": {"total": filtered.get("total"), "fetched": len(rows), "complete": filtered.get("complete")},
        "baseline_coverage": {"total": baseline.get("total"), "fetched": len(baseline.get("records") or []), "complete": baseline.get("complete")},
        "mismatched_rows": violating[:12],
    }
    findings: list[dict[str, Any]] = []
    if violating:
        findings.append(_finding(contract, "filter_scope", f"筛选条件被静默绕过：{contract.get('resource')} {filter_spec.get('parameter')}", f"携带 {filter_spec.get('parameter')}={value} 的结果必须全部满足字段 {field}={value}。", f"返回结果中有 {len(violating)} 条记录不符合筛选条件。", evidence, 0.96, "P1", key={"parameter": filter_spec.get("parameter"), "field": field}))
    # Metamorphic relation: a valid filtered result must be a subset of the
    # unfiltered result when the baseline snapshot is complete and an identity is available.
    identity = contract.get("identity_field")
    escaped: list[dict[str, Any]] = []
    if identity and baseline.get("complete"):
        baseline_ids = {_canon(_value(row, str(identity), mappings)) for row in (baseline.get("records") or []) if _canon(_value(row, str(identity), mappings))}
        for index, row in enumerate(rows, start=1):
            current = _canon(_value(row, str(identity), mappings))
            if current and current not in baseline_ids:
                escaped.append({"row_index": index, "identity_hash": _short_hash(current)})
        if escaped:
            subset_evidence = {**evidence, "escaped_rows": escaped[:12]}
            findings.append(_finding(contract, "filter_subset", f"筛选结果不属于未筛选事实集合：{contract.get('resource')}", "有效筛选后的业务记录必须是未筛选结果的子集。", f"发现 {len(escaped)} 条筛选结果不在完整未筛选集合中。", subset_evidence, 0.93, "P1", key={"parameter": filter_spec.get("parameter"), "identity": identity}))
    observation = {"kind": "filter_scope", "parameter": filter_spec.get("parameter"), "field": field, "requested": _redact(value), "filtered_row_count": len(rows), "mismatch_count": len(violating), "subset_escape_count": len(escaped), "baseline_complete": bool(baseline.get("complete"))}
    return findings, observation


def _target_url(base_url: str, target_path: str, target_parameter: str, value: Any) -> str:
    quoted = urllib.parse.quote(str(value), safe="")
    rendered = re.sub(r"\{" + re.escape(str(target_parameter)) + r"\}", quoted, target_path)
    if rendered == target_path:
        rendered = re.sub(r"\{[^{}]+\}", quoted, target_path, count=1)
    return _build_url(base_url, rendered, {})


def _audit_foreign_key(contract: dict[str, Any], relation: dict[str, Any], baseline: dict[str, Any], base_url: str, token: str | None, timeout: int, max_bytes: int, max_samples: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    field = str(relation.get("field") or "")
    mappings = dict(contract.get("field_mappings") or {})
    values: list[str] = []
    for row in baseline.get("records") or []:
        value = _canon(_value(row, field, mappings))
        if value and value not in values:
            values.append(value)
        if len(values) >= max_samples:
            break
    checks: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for value in values:
        url = _target_url(base_url, str(relation.get("target_path") or ""), str(relation.get("target_parameter") or "id"), value)
        response = _http_get(url, token, timeout, max_bytes)
        record = {"value_hash": _short_hash(value), "url": url, "status_code": response.get("status_code"), "error": response.get("error")}
        checks.append(record)
        if int(response.get("status_code") or 0) == 404:
            missing.append(record)
    evidence = {"source_request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": _redact(contract.get("sample_query") or {})}, "relation": _redact(relation), "target_checks": missing[:12], "source_coverage": {"complete": baseline.get("complete"), "source_total": baseline.get("total"), "fetched_row_count": len(baseline.get("records") or [])}}
    findings: list[dict[str, Any]] = []
    if missing:
        findings.append(_finding(contract, "referential_integrity", f"孤儿业务引用：{contract.get('resource')} {field} 指向不存在资源", f"字段 {field} 的非空引用必须可通过 {relation.get('target_path')} 解析。", f"抽样验证中发现 {len(missing)} 个外键目标返回 404。", evidence, 0.94, "P1", key={"field": field, "target": relation.get("target_path")}))
    return findings, {"kind": "referential_integrity", "field": field, "target_path": relation.get("target_path"), "checked_reference_count": len(checks), "missing_target_count": len(missing), "checks": checks[:12]}


def build_business_invariant_profile(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    paths = config_paths(project, root)
    cfg = load_real_project_config(project, root)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    openapi = openapi if isinstance(openapi, dict) else {}
    contracts, candidates = build_business_invariant_contracts(openapi, cfg, _read_text(paths["input_dir"] / "prd.md"))
    probes = generate_business_invariant_probes(openapi, cfg, project, root, options.get("preview_probe_count") or 120)
    summary = {
        "invariant_contract_count": len(contracts),
        "safe_read_only_contract_count": sum(1 for contract in contracts if contract.get("execution_policy") == "safe_read_only"),
        "identity_invariant_count": sum(1 for contract in contracts if contract.get("identity_field")),
        "runtime_contract_check_count": sum(len(contract.get("runtime_checks") or []) for contract in contracts),
        "filter_invariant_count": sum(len(contract.get("filters") or []) for contract in contracts),
        "referential_invariant_count": sum(len(contract.get("foreign_keys") or []) for contract in contracts),
        "temporal_invariant_count": sum(len(contract.get("temporal_pairs") or []) for contract in contracts),
        "preview_probe_count": len(probes),
        "contract_gap_count": len(candidates),
    }
    result = {
        "phase": "phase45_business_invariant_mining",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "contracts": contracts,
        "prd_candidates": candidates,
        "preview_probes": probes,
        "summary": summary,
        "governance": {"default_execution": "plan_only", "safe_live_only_uses_get": True, "requests_bounded": True, "foreign_key_sampling_bounded": True, "evidence_redacted_before_persistence": True, "uses_no_benchmark_answer_files": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    output = _output_paths(project, root)
    _write_json(output["out"] / "business_invariant_profile.json", result)
    _write_json(output["workspace"] / "business_invariant_profile.json", result)
    output["out"].mkdir(parents=True, exist_ok=True)
    (output["out"] / "business_invariant_profile_report.html").write_text(render_business_invariant_profile_report(result), encoding="utf-8")
    return result


def load_business_invariant_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    data = _load_json(_output_paths(_safe_project_id(project_id), root)["workspace"] / "business_invariant_profile.json", {})
    return data if isinstance(data, dict) and data else None


def run_business_invariant_mining(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    profile = build_business_invariant_profile(project, root, options)
    execution_mode = str(options.get("execution_mode") or cfg.get("business_invariant_execution_mode") or "plan_only").lower()
    if execution_mode not in {"plan_only", "safe_live"}:
        execution_mode = "plan_only"
    section = cfg.get("business_invariant_mining") or cfg.get("business_invariants") or {}
    section = section if isinstance(section, dict) else {}
    max_bytes = max(100_000, min(int(options.get("max_response_bytes") or section.get("max_response_bytes") or 3_000_000), 20_000_000))
    max_pages = max(1, min(int(options.get("max_source_pages") or section.get("max_source_pages") or 12), 100))
    max_relations = max(1, min(int(options.get("max_relation_samples") or section.get("max_relation_samples") or 12), 100))
    timeout = max(1, min(int(cfg.get("request_timeout_seconds") or 10), 30))
    base_url = str(cfg.get("base_url") or "")
    token = _normal_token(cfg, project, root, timeout) if execution_mode == "safe_live" and base_url else None
    executions: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    semantic_hypotheses: list[dict[str, Any]] = []
    for contract in profile.get("contracts") or []:
        if execution_mode != "safe_live" or not base_url:
            executions.append({"contract_id": contract.get("contract_id"), "status": "planned", "reason": "plan_only_or_missing_base_url"})
            continue
        baseline = _fetch_source_pages(base_url, contract, token, timeout, max_bytes, max_pages)
        response_rows = baseline.get("responses") or []
        if not response_rows or not response_rows[0].get("status_code"):
            executions.append({"contract_id": contract.get("contract_id"), "status": "error", "reason": "collection_fetch_failed", "responses": response_rows})
            continue
        current_findings, observations = audit_collection_snapshot(contract, baseline)
        filter_observations: list[dict[str, Any]] = []
        for filter_spec in contract.get("filters") or []:
            query = {**(contract.get("sample_query") or {}), str(filter_spec.get("parameter")): filter_spec.get("value")}
            url = _build_url(base_url, str((contract.get("collection") or {}).get("path") or ""), query)
            response = _http_get(url, token, timeout, max_bytes)
            if not response.get("ok"):
                filter_observations.append({"kind": "filter_scope", "parameter": filter_spec.get("parameter"), "result": "skipped_http_error", "status_code": response.get("status_code"), "error": response.get("error")})
                continue
            payload = _parse_json(response)
            rows, total = _extract_records(payload)
            filtered_context = {"records": rows, "total": total, "complete": total is None or len(rows) >= total}
            emitted, observation = _audit_filter(contract, filter_spec, baseline, filtered_context, url)
            current_findings.extend(emitted)
            filter_observations.append(observation)
        relation_observations: list[dict[str, Any]] = []
        for relation in contract.get("foreign_keys") or []:
            emitted, observation = _audit_foreign_key(contract, relation, baseline, base_url, token, timeout, max_bytes, max_relations)
            current_findings.extend(emitted)
            relation_observations.append(observation)
        findings.extend(current_findings)

    # --- LLM-powered semantic reasoning (Phase61 moat upgrade) ---
    if execution_mode == "safe_live" and findings:
        try:
            import json as _json
            llm_result = _llm_reason("invariant", {
                "prd_text": "", "api_schema": "", "observed_data": _json.dumps(executions[-5:] if "executions" in dir() else [], ensure_ascii=False, default=str)[:4000],
                "heuristic_findings": _json.dumps(findings[:15], ensure_ascii=False, default=str)[:4000],
            })
            semantic_hypotheses.extend(compile_unverified_semantic_hypotheses(
                (llm_result or {}).get("findings"),
                engine="invariant",
                type_field="business_invariant_type",
            ))
        except Exception:
            pass

        executions.append({"contract_id": contract.get("contract_id"), "status": "executed", "source_complete": bool(baseline.get("complete")), "source_total": baseline.get("total"), "fetched_source_rows": len(baseline.get("records") or []), "source_responses": response_rows, "runtime_observations": observations, "filter_observations": filter_observations, "relation_observations": relation_observations, "finding_count": len(current_findings)})
    output = _output_paths(project, root)
    registry, findings = _update_registry(output["registry"], findings)
    result = {
        "phase": "phase45_business_invariant_mining",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "summary": {
            **profile.get("summary", {}),
            "execution_mode": execution_mode,
            "executed_contract_count": sum(1 for item in executions if item.get("status") == "executed"),
            "business_invariant_finding_count": len(findings),
            "persistent_business_invariant_count": sum(1 for item in findings if (item.get("evidence_stability") or {}).get("persistent")),
            "memory_fingerprint_count": len((registry or {}).get("entries") or {}),
        },
        "profile": profile,
        "executions": executions,
        "semantic_hypotheses": semantic_hypotheses, "llm_governance": {"status": "unverified_hypothesis_only", "does_not_affect_finding_counts": True, "requires_deterministic_replay": True}, "findings": findings,
        "memory_summary": {"fingerprint_count": len((registry or {}).get("entries") or {}), "updated_at_utc": _now(), "learning_policy": "同一业务不变量的反例跨运行重复出现时提升置信度；未经人工确认始终保持 needs_human_review。"},
        "governance": {"execution_mode": execution_mode, "live_requests_limited_to_get": True, "write_execution_disabled": True, "source_pagination_bounded": True, "foreign_key_sampling_bounded": True, "evidence_redacted_before_persistence": True, "uses_no_benchmark_answer_files": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    _write_json(output["out"] / "business_invariant_run.json", result)
    _write_json(output["workspace"] / "business_invariant_run.json", result)
    (output["out"] / "business_invariant_run_report.html").write_text(render_business_invariant_run_report(result), encoding="utf-8")
    return result


def _render_html(title: str, badge: str, subtitle: str, cards: str, body: str) -> str:
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>{_html_escape(title)}</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#eef2ff;color:#3730a3}}</style></head><body>
<section class='hero'><span class='badge'>{_html_escape(badge)}</span><h1>{_html_escape(title)}</h1><p>{_html_escape(subtitle)}</p></section><section class='panel'><div class='grid'>{cards}</div></section><section class='panel'>{body}</section></body></html>"""


def render_business_invariant_profile_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(key)}</span><b>{_html_escape(value)}</b></div>" for key, value in summary.items())
    rows = []
    for contract in data.get("contracts") or []:
        kinds = []
        if contract.get("identity_field"):
            kinds.append("identity")
        kinds.extend(item.get("kind") for item in contract.get("runtime_checks") or [])
        kinds.extend("filter_scope" for _ in contract.get("filters") or [])
        kinds.extend("referential_integrity" for _ in contract.get("foreign_keys") or [])
        kinds.extend("temporal_order" for _ in contract.get("temporal_pairs") or [])
        rows.append(f"<tr><td>{_html_escape(contract.get('contract_id'))}</td><td>{_html_escape(contract.get('resource'))}</td><td>{_html_escape((contract.get('collection') or {}).get('method'))} {_html_escape((contract.get('collection') or {}).get('path'))}</td><td>{_html_escape(contract.get('identity_field') or '-')}</td><td>{_html_escape(', '.join(str(kind) for kind in kinds) or '-')}</td></tr>")
    return _render_html("业务不变量挖掘", "Phase45 · Business Invariant Mining", "把需求、接口和真实返回数据转为可证伪的业务约束，而非只执行模板化接口检查。", cards, "<h2>自动挖掘的业务不变量</h2><table><thead><tr><th>ID</th><th>资源</th><th>集合接口</th><th>业务主键</th><th>检查</th></tr></thead><tbody>" + ("".join(rows) or "<tr><td colspan='5'>暂无可执行集合契约</td></tr>") + "</tbody></table>")


def render_business_invariant_run_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(key)}</span><b>{_html_escape(value)}</b></div>" for key, value in summary.items())
    rows = []
    for finding in data.get("findings") or []:
        rows.append(f"<tr><td>{_html_escape(finding.get('severity'))}</td><td>{_html_escape(finding.get('business_invariant_type'))}</td><td>{_html_escape(finding.get('title'))}</td><td>{_html_escape(finding.get('actual'))}</td><td>{_html_escape(finding.get('confidence'))}</td><td>{_html_escape((finding.get('evidence_stability') or {}).get('observations', 1))}</td></tr>")
    return _render_html("业务不变量运行结果", "Phase45 · Business Invariant Mining", "每一条发现都来自真实返回数据对业务不变量的反例验证，保留脱敏、可复现证据。", cards, "<h2>发现的反例</h2><table><thead><tr><th>等级</th><th>类型</th><th>问题</th><th>实际</th><th>置信度</th><th>观测次数</th></tr></thead><tbody>" + ("".join(rows) or "<tr><td colspan='6'>未发现已证伪的业务不变量</td></tr>") + "</tbody></table>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QualiBug Phase45 business invariant mining")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--mode", choices=["plan_only", "safe_live"], default="plan_only")
    args = parser.parse_args(argv)
    result = run_business_invariant_mining(args.project, options={"execution_mode": args.mode})
    print(json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
