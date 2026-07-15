from __future__ import annotations

"""Phase44: cross-view business reconciliation for real enterprise APIs.

The engine treats dashboard, statistics and report APIs as business claims rather
than ordinary 200 responses.  A claimed total/count/amount is reconciled against
its underlying read-only collection with the same business filter.  This catches
high-value defects such as a dashboard showing 98 paid orders while the order
list has 100, or a revenue card double-counting duplicated rows.

Safety model:
* ``plan_only`` is the default;
* ``safe_live`` only issues GET requests;
* source pagination is bounded and evidence is redacted;
* a metric is only asserted when the source snapshot is complete enough to
  make the comparison trustworthy.
"""

import hashlib
import json
import os
import re
import time
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any

from .business_outcome_validation import (
    DETAIL_CONTAINER_KEYS,
    LIST_CONTAINER_KEYS,
    _array_item_schema,
    _build_url,
    _http_get,
    _normal_token,
    _operation_parameters,
    _private_leak_check,
    _redact,
    _resource_key,
    _source_may_paginate,
    _update_registry,
)
from .llm_reasoning import compile_unverified_semantic_hypotheses, reason as _llm_reason
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
from .universal_defect_mining import _operations, _resolve_ref, _schema_type

SUMMARY_WORDS = {
    "summary", "summaries", "stat", "stats", "statistics", "metric", "metrics",
    "dashboard", "overview", "analytic", "analytics", "report", "reports",
    "aggregate", "aggregation", "total", "totals", "汇总", "统计", "看板",
    "概览", "指标", "报表", "总览", "总计",
}
SUMMARY_PATH_WORDS = {
    "summary", "summaries", "stat", "stats", "statistics", "metric", "metrics",
    "dashboard", "overview", "analytic", "analytics", "report", "reports",
    "aggregate", "aggregation", "total", "totals", "汇总", "统计", "看板",
    "概览", "指标", "报表", "总览", "总计",
}
COUNT_RE = re.compile(r"(?:^|[_\-.])(count|totalcount|recordcount|rowcount|itemcount|number|数量|总数|条数|笔数)(?:$|[_\-.])", re.I)
SUM_RE = re.compile(r"(?:amount|price|cost|fee|tax|balance|revenue|gmv|quantity|qty|volume|金额|价格|费用|税|余额|收入|销售额|数量)", re.I)
NUMERIC_RE = re.compile(r"(?:amount|price|cost|fee|tax|balance|revenue|gmv|quantity|qty|count|number|total|金额|价格|费用|税|余额|收入|销售额|数量|总数|条数)", re.I)
DYNAMIC_RE = re.compile(r"(?:^|[_\-.])(time|timestamp|updated|created|trace|request|nonce|token|version|etag|cursor|next|last)(?:$|[_\-.])", re.I)
ID_RE = re.compile(r"(?:^|[_\-.])(id|uuid|guid|code|number|no|serial)(?:$|[_\-.])", re.I)


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


def _output_paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    return {
        "out": root / "platform_outputs" / project / "business_reconciliation",
        "workspace": root / "platform_workspace" / project / "defect_discovery",
        "registry": root / "platform_workspace" / project / "defect_discovery" / "business_reconciliation_evidence_registry.json",
    }


def _response_content_schema(operation: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    return _resolve_ref(operation.get("response_schema") or {}, components)


def _object_properties(schema: Any, components: dict[str, Any], prefix: str = "", depth: int = 0) -> list[dict[str, Any]]:
    """Flatten numeric/object response fields without descending into records arrays."""
    if depth > 4:
        return []
    node = _resolve_ref(schema, components)
    if not isinstance(node, dict):
        return []
    kind = _schema_type(node, components)
    if kind == "array":
        return []
    if kind != "object":
        return []
    fields: list[dict[str, Any]] = []
    for name, child in (node.get("properties") or {}).items():
        if not isinstance(child, dict):
            continue
        resolved = _resolve_ref(child, components)
        path = f"{prefix}.{name}" if prefix else str(name)
        typ = _schema_type(resolved, components)
        fields.append({"name": str(name), "path": path, "type": typ, "schema": resolved})
        # data/result/summary wrappers commonly hold the actual KPI object.
        if typ == "object" and (depth < 2 or _norm(name) in {"data", "result", "summary", "payload", "statistics", "metrics"}):
            fields.extend(_object_properties(resolved, components, path, depth + 1))
    return fields


def _collection_item_fields(operation: dict[str, Any], components: dict[str, Any]) -> list[dict[str, Any]]:
    _, item = _array_item_schema(operation.get("response_schema") or {}, components)
    if not item:
        return []
    return _object_properties(item, components)


def _is_numeric_field(field: dict[str, Any]) -> bool:
    typ = str(field.get("type") or "").lower()
    return typ in {"number", "integer"} or bool(NUMERIC_RE.search(str(field.get("name") or "")))


def _resource_for_operation(operation: dict[str, Any], summary: bool = False) -> str:
    path = str(operation.get("path") or "")
    pieces = [p for p in path.split("/") if p and not p.startswith("{")]
    ignored = {"api", "v1", "v2", "v3", "public", "private", "internal", "open", "service", "services"}
    if summary:
        ignored |= SUMMARY_PATH_WORDS
    useful = [piece for piece in pieces if _norm(piece) not in {_norm(x) for x in ignored}]
    if useful:
        return _resource_key("/" + "/".join(useful))
    return _resource_key(path)


def _path_match(left: str, right: str) -> bool:
    a, b = _norm(left).rstrip("s"), _norm(right).rstrip("s")
    return bool(a and b and (a == b or a in b or b in a))


def _is_collection_read(operation: dict[str, Any], components: dict[str, Any]) -> bool:
    if str(operation.get("method") or "").upper() != "GET":
        return False
    path = str(operation.get("path") or "")
    if "{" in path and "}" in path:
        return False
    _, item = _array_item_schema(operation.get("response_schema") or {}, components)
    return bool(item)


def _looks_like_summary_operation(operation: dict[str, Any], components: dict[str, Any]) -> bool:
    if str(operation.get("method") or "").upper() != "GET":
        return False
    if _is_collection_read(operation, components):
        return False
    text = " ".join(str(operation.get(key) or "") for key in ("path", "operation_id", "summary", "description", "tags")).lower()
    if any(word in text for word in SUMMARY_WORDS):
        return True
    numeric_fields = [field for field in _object_properties(operation.get("response_schema") or {}, components) if _is_numeric_field(field)]
    return len(numeric_fields) >= 2 and any("total" in _norm(field.get("name")) or "count" in _norm(field.get("name")) for field in numeric_fields)


def _configured_contracts(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    section = cfg.get("business_reconciliation") or cfg.get("business_reconcile") or {}
    if not isinstance(section, dict):
        return []
    rows = section.get("metric_contracts") or section.get("contracts") or []
    return [row for row in rows if isinstance(row, dict)]


def _find_operation(operations: list[dict[str, Any]], path: str, method: str = "GET") -> dict[str, Any] | None:
    path_n = str(path or "").rstrip("/") or "/"
    method_u = str(method or "GET").upper()
    for operation in operations:
        if str(operation.get("method") or "").upper() == method_u and (str(operation.get("path") or "").rstrip("/") or "/") == path_n:
            return operation
    return None


def _best_source(summary: dict[str, Any], collections: list[dict[str, Any]], components: dict[str, Any]) -> dict[str, Any] | None:
    resource = _resource_for_operation(summary, summary=True)
    scored: list[tuple[float, dict[str, Any]]] = []
    for candidate in collections:
        candidate_resource = _resource_for_operation(candidate)
        score = 0.0
        if _path_match(resource, candidate_resource):
            score += 8.0
        if str(summary.get("path") or "").split("/")[1:2] == str(candidate.get("path") or "").split("/")[1:2]:
            score += 2.0
        summary_text = " ".join(str(summary.get(key) or "") for key in ("operation_id", "summary", "description")).lower()
        candidate_text = " ".join(str(candidate.get(key) or "") for key in ("operation_id", "summary", "description")).lower()
        common = set(re.findall(r"[a-z][a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", summary_text)) & set(re.findall(r"[a-z][a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", candidate_text))
        score += min(2.0, 0.3 * len(common))
        if score > 0:
            scored.append((score, candidate))
    return max(scored, key=lambda item: item[0])[1] if scored else None


def _metrics_from_config(configured: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics: list[dict[str, Any]] = []
    for row in configured:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or row.get("metric_type") or "").lower()
        if kind not in {"count", "sum", "group_count", "group_sum"}:
            continue
        summary_field = str(row.get("summary_field") or row.get("field") or "").strip()
        if not summary_field:
            continue
        metrics.append({
            "summary_field": summary_field,
            "kind": kind,
            "source_field": str(row.get("source_field") or "").strip() or None,
            "group_field": str(row.get("group_field") or "").strip() or None,
            "filters": dict(row.get("filters") or {}),
            "tolerance": row.get("tolerance"),
            "title": row.get("title"),
        })
    return metrics


def _best_source_field(summary_name: str, source_fields: list[dict[str, Any]]) -> str | None:
    target = _norm(summary_name)
    target = re.sub(r"^(total|sum|aggregate|all|overall)", "", target)
    target = re.sub(r"(total|sum|amount|value)$", "", target) or _norm(summary_name)
    candidates: list[tuple[float, str]] = []
    for field in source_fields:
        name = str(field.get("name") or "")
        if not _is_numeric_field(field):
            continue
        norm = _norm(name)
        score = 0.0
        if norm == target:
            score += 8.0
        if target and (target in norm or norm in target):
            score += 4.0
        if SUM_RE.search(name):
            score += 1.0
        if score:
            candidates.append((score, name))
    return max(candidates, key=lambda row: row[0])[1] if candidates else None


def _infer_metrics(summary: dict[str, Any], source: dict[str, Any], components: dict[str, Any]) -> list[dict[str, Any]]:
    source_fields = _collection_item_fields(source, components)
    metrics: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for field in _object_properties(summary.get("response_schema") or {}, components):
        name = str(field.get("name") or "")
        path = str(field.get("path") or name)
        normalized = _norm(name)
        if not _is_numeric_field(field):
            continue
        kind = None
        source_field = None
        if COUNT_RE.search(name) or normalized.endswith("count") or normalized in {"total", "totalrecords", "totalrows", "totalitems"}:
            kind = "count"
        elif SUM_RE.search(name) or ("total" in normalized and any(SUM_RE.search(str(x.get("name") or "")) for x in source_fields)):
            source_field = _best_source_field(name, source_fields)
            if source_field:
                kind = "sum"
        if kind and (path, kind) not in seen:
            seen.add((path, kind))
            metrics.append({"summary_field": path, "kind": kind, "source_field": source_field, "group_field": None, "filters": {}, "tolerance": None, "title": None})
    return metrics[:24]


def _prd_contract_candidates(prd_text: str, summaries: list[dict[str, Any]], collections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    text = str(prd_text or "")
    if not text or not any(word in text.lower() for word in SUMMARY_WORDS):
        return []
    candidates: list[dict[str, Any]] = []
    if not summaries:
        candidates.append({"severity": "P2", "title": "PRD 提到统计/看板结果，但 OpenAPI 未发现可对账的只读统计接口", "detail": "建议为统计结果提供可访问的 GET 接口，或在 business_reconciliation.metric_contracts 显式配置统计与明细数据源。"})
    elif not collections:
        candidates.append({"severity": "P2", "title": "PRD 提到统计/看板结果，但 OpenAPI 未发现可作为事实源的列表接口", "detail": "建议提供可分页读取的只读明细接口，或在配置中指定 source_path。"})
    return candidates


def build_reconciliation_contracts(openapi: dict[str, Any], cfg: dict[str, Any], prd_text: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    components = openapi.get("components") or {}
    operations = _operations(openapi)
    collections = [operation for operation in operations if _is_collection_read(operation, components)]
    summaries = [operation for operation in operations if _looks_like_summary_operation(operation, components)]
    contracts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for row in _configured_contracts(cfg):
        summary = _find_operation(operations, str(row.get("summary_path") or row.get("dashboard_path") or row.get("metric_path") or ""), str(row.get("summary_method") or "GET"))
        source = _find_operation(operations, str(row.get("source_path") or ""), str(row.get("source_method") or "GET"))
        if not summary or not source:
            candidates.append({"severity": "P2", "title": "业务对账契约无法映射到 OpenAPI", "detail": f"summary_path={row.get('summary_path') or row.get('dashboard_path')}, source_path={row.get('source_path')}"})
            continue
        metrics = _metrics_from_config(row.get("metrics") or []) or _infer_metrics(summary, source, components)
        if not metrics:
            candidates.append({"severity": "P2", "title": "统计接口已识别，但无法推断可验证指标", "detail": f"{summary.get('method')} {summary.get('path')}；请在 business_reconciliation.metric_contracts.metrics 显式配置。"})
            continue
        key = (str(summary.get("path")), str(source.get("path")))
        if key in seen:
            continue
        seen.add(key)
        contracts.append(_contract(len(contracts) + 1, summary, source, metrics, row))

    for summary in summaries:
        source = _best_source(summary, collections, components)
        if not source:
            continue
        key = (str(summary.get("path")), str(source.get("path")))
        if key in seen:
            continue
        metrics = _infer_metrics(summary, source, components)
        if not metrics:
            continue
        seen.add(key)
        contracts.append(_contract(len(contracts) + 1, summary, source, metrics, {}))

    return contracts, [*_prd_contract_candidates(prd_text, summaries, collections), *candidates]


def _contract(number: int, summary: dict[str, Any], source: dict[str, Any], metrics: list[dict[str, Any]], configured: dict[str, Any]) -> dict[str, Any]:
    summary_method = str(summary.get("method") or "GET").upper()
    source_method = str(source.get("method") or "GET").upper()
    resource = str(configured.get("resource") or _resource_for_operation(source) or _resource_for_operation(summary, summary=True))
    return {
        "contract_id": f"BRE_CONTRACT_{number:04d}",
        "resource": resource,
        "summary": {"path": summary.get("path"), "method": summary_method, "operation_id": summary.get("operation_id"), "summary": summary.get("summary")},
        "source": {"path": source.get("path"), "method": source_method, "operation_id": source.get("operation_id"), "summary": source.get("summary"), "parameters": _operation_parameters(source)},
        "metrics": metrics,
        "sample_query": dict(configured.get("sample_query") or configured.get("query") or {}),
        "field_mappings": dict(configured.get("field_mappings") or {}),
        "pagination": dict(configured.get("pagination") or {}),
        "execution_policy": "safe_read_only" if summary_method == "GET" and source_method == "GET" else "candidate_only",
        "discovery": "configured" if configured else "openapi_inferred",
    }


def _probe(contract: dict[str, Any], number: int, metric: dict[str, Any]) -> dict[str, Any]:
    kind = str(metric.get("kind") or "count")
    title = metric.get("title") or f"业务对账：{contract.get('resource')} {metric.get('summary_field')} 与明细{kind}一致"
    return {
        "probe_id": f"BRE_PROBE_{number:04d}",
        "source": "business_reconciliation",
        "risk_type": "business_reconciliation",
        "business_reconciliation_type": kind,
        "contract_id": contract.get("contract_id"),
        "title": title,
        "severity": "P1",
        "expected": "统计/看板/报表指标必须与同口径明细数据重新计算的结果一致。",
        "method": (contract.get("summary") or {}).get("method") or "GET",
        "path": (contract.get("summary") or {}).get("path") or "",
        "actor": "normal_user",
        "destructive": False,
        "execution_policy": contract.get("execution_policy") or "candidate_only",
        "metric": metric,
    }


def generate_business_reconciliation_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    paths = config_paths(_safe_project_id(project_id), root or ROOT)
    contracts, candidates = build_reconciliation_contracts(openapi, cfg, _read_text(paths["input_dir"] / "prd.md"))
    probes: list[dict[str, Any]] = []
    for contract in contracts:
        for metric in contract.get("metrics") or []:
            probes.append(_probe(contract, len(probes) + 1, metric))
    for candidate in candidates:
        probes.append({
            "probe_id": f"BRE_GAP_{len(probes)+1:04d}", "source": "business_reconciliation",
            "risk_type": "business_reconciliation", "business_reconciliation_type": "contract_gap",
            "title": candidate.get("title"), "severity": candidate.get("severity") or "P2",
            "expected": candidate.get("detail"), "method": "GET", "path": "", "actor": "normal_user",
            "destructive": False, "execution_policy": "candidate_only",
        })
    return probes[: int(max_count or cfg.get("max_probe_count") or 100)]


def _parse_json(response: dict[str, Any]) -> Any:
    try:
        return json.loads((response.get("body") or b"").decode("utf-8-sig", errors="replace"))
    except Exception:
        return None


def _extract_records(value: Any) -> tuple[list[dict[str, Any]], int | None]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)], len(value)
    if not isinstance(value, dict):
        return [], None
    for key in LIST_CONTAINER_KEYS:
        child = value.get(key)
        if isinstance(child, list):
            total = _extract_total(value)
            return [row for row in child if isinstance(row, dict)], total
        if isinstance(child, dict):
            rows, total = _extract_records(child)
            if rows:
                return rows, total if total is not None else _extract_total(value)
    return [], _extract_total(value)


def _extract_total(value: Any) -> int | None:
    if not isinstance(value, dict):
        return None
    for key in ("total", "total_count", "totalCount", "record_count", "records_total", "count"):
        raw = value.get(key)
        try:
            if raw is not None and not isinstance(raw, (dict, list, bool)):
                return int(float(raw))
        except Exception:
            pass
    for key in DETAIL_CONTAINER_KEYS:
        nested = value.get(key)
        if isinstance(nested, dict):
            total = _extract_total(nested)
            if total is not None:
                return total
    return None


def _path_value(value: Any, path: str) -> Any:
    current = value
    for part in [piece for piece in str(path or "").split(".") if piece]:
        if isinstance(current, dict):
            if part in current:
                current = current.get(part)
                continue
            target = _norm(part)
            match = next((key for key in current if _norm(key) == target), None)
            if match is None:
                return None
            current = current.get(match)
        else:
            return None
    return current


def _numeric(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "").replace("¥", "").replace("$", "")
    if not text:
        return None
    try:
        return float(text)
    except Exception:
        return None


def _row_value(row: dict[str, Any], field: str | None, mappings: dict[str, Any] | None = None) -> Any:
    if not field:
        return None
    if field in row:
        return row.get(field)
    target = _norm(field)
    mapping = mappings or {}
    aliases = [field, mapping.get(field), *[key for key, value in mapping.items() if _norm(value) == target]]
    for alias in aliases:
        if alias is None:
            continue
        alias_norm = _norm(alias)
        for key, value in row.items():
            if _norm(key) == alias_norm:
                return value
    return None


def _matches_filters(row: dict[str, Any], filters: dict[str, Any], mappings: dict[str, Any]) -> bool:
    for name, expected in (filters or {}).items():
        actual = _row_value(row, str(name), mappings)
        if isinstance(expected, (list, tuple, set)):
            if str(actual) not in {str(item) for item in expected}:
                return False
        elif str(actual) != str(expected):
            return False
    return True


def _page_spec(contract: dict[str, Any]) -> dict[str, Any]:
    configured = dict(contract.get("pagination") or {})
    source = contract.get("source") or {}
    names = [str(param.get("name") or "") for param in source.get("parameters") or [] if str(param.get("in") or "query").lower() == "query"]
    normalized = {_norm(name): name for name in names}
    page = configured.get("page_param") or next((normalized[key] for key in ("page", "pageno", "pagenum", "pageindex", "页码") if key in normalized), None)
    size = configured.get("size_param") or next((normalized[key] for key in ("size", "pagesize", "limit", "perpage", "per_page", "rows", "条数") if key in normalized), None)
    offset = configured.get("offset_param") or next((normalized[key] for key in ("offset", "start", "skip") if key in normalized), None)
    return {
        "page_param": page,
        "size_param": size,
        "offset_param": offset,
        "page_start": int(configured.get("page_start") or 1),
        "page_size": max(1, min(int(configured.get("page_size") or 100), 1000)),
    }


def _fetch_source_pages(base_url: str, contract: dict[str, Any], token: str | None, timeout: int, max_bytes: int, max_pages: int) -> dict[str, Any]:
    source = contract.get("source") or {}
    query = dict(contract.get("sample_query") or {})
    spec = _page_spec(contract)
    records: list[dict[str, Any]] = []
    total: int | None = None
    responses: list[dict[str, Any]] = []
    seen_pages: set[str] = set()
    page_mode = bool(spec.get("page_param") or spec.get("offset_param"))
    for index in range(max_pages if page_mode else 1):
        current = dict(query)
        if spec.get("page_param"):
            current[str(spec["page_param"])] = spec["page_start"] + index
            if spec.get("size_param"):
                current[str(spec["size_param"])] = spec["page_size"]
        elif spec.get("offset_param"):
            current[str(spec["offset_param"])] = index * spec["page_size"]
            if spec.get("size_param"):
                current[str(spec["size_param"])] = spec["page_size"]
        url = _build_url(base_url, str(source.get("path") or ""), current)
        response = _http_get(url, token, timeout, max_bytes)
        summary = {"url": url, "status_code": response.get("status_code"), "error": response.get("error"), "truncated": bool(response.get("truncated"))}
        responses.append(summary)
        if not response.get("ok"):
            break
        decoded = _parse_json(response)
        rows, discovered_total = _extract_records(decoded)
        if total is None and discovered_total is not None:
            total = discovered_total
        marker = _hash(rows[:10])
        if page_mode and marker in seen_pages and rows:
            # A repeated page may itself be a production bug; do not loop forever.
            break
        seen_pages.add(marker)
        records.extend(rows)
        if not page_mode or not rows:
            break
        if total is not None and len(records) >= total:
            break
        if len(rows) < spec["page_size"] and total is None:
            break
    complete = bool(total is not None and len(records) >= total)
    if not page_mode and total is None:
        complete = True
    elif not page_mode and total is not None:
        complete = len(records) >= total
    return {"records": records, "total": total, "complete": complete, "responses": responses, "page_mode": page_mode, "page_spec": spec}


def _expected_metric(metric: dict[str, Any], source: dict[str, Any], contract: dict[str, Any]) -> tuple[Any, bool, dict[str, Any]]:
    rows = list(source.get("records") or [])
    mappings = dict(contract.get("field_mappings") or {})
    filters = dict(metric.get("filters") or {})
    if filters:
        rows = [row for row in rows if _matches_filters(row, filters, mappings)]
    kind = str(metric.get("kind") or "count")
    complete = bool(source.get("complete"))
    if kind == "count":
        if not filters and source.get("total") is not None:
            return int(source.get("total")), True, {"source_row_count": len(rows), "source_total": source.get("total"), "filters": _redact(filters)}
        return len(rows), complete, {"source_row_count": len(rows), "source_total": source.get("total"), "filters": _redact(filters)}
    if kind == "sum":
        field = metric.get("source_field")
        values = [_numeric(_row_value(row, field, mappings)) for row in rows]
        values = [value for value in values if value is not None]
        return round(sum(values), 6), complete, {"source_row_count": len(rows), "numeric_value_count": len(values), "source_total": source.get("total"), "source_field": field, "filters": _redact(filters)}
    if kind in {"group_count", "group_sum"}:
        group_field = metric.get("group_field")
        groups: dict[str, float] = {}
        for row in rows:
            group = str(_row_value(row, group_field, mappings) or "<blank>")
            if kind == "group_count":
                groups[group] = groups.get(group, 0.0) + 1.0
            else:
                value = _numeric(_row_value(row, metric.get("source_field"), mappings))
                if value is not None:
                    groups[group] = groups.get(group, 0.0) + value
        return groups, complete, {"source_row_count": len(rows), "source_total": source.get("total"), "group_field": group_field, "source_field": metric.get("source_field"), "filters": _redact(filters)}
    return None, False, {"reason": "unsupported_metric_kind"}


def _equal_metric(actual: Any, expected: Any, metric: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    tolerance = _numeric(metric.get("tolerance"))
    tolerance = 0.000001 if tolerance is None else abs(tolerance)
    kind = str(metric.get("kind") or "count")
    if kind in {"count", "sum"}:
        actual_num = _numeric(actual)
        expected_num = _numeric(expected)
        if actual_num is None or expected_num is None:
            return False, {"reason": "non_numeric_metric", "actual": actual, "expected": expected}
        delta = round(actual_num - expected_num, 6)
        return abs(delta) <= tolerance, {"actual": actual_num, "expected": expected_num, "delta": delta, "tolerance": tolerance}
    if kind in {"group_count", "group_sum"}:
        if not isinstance(actual, dict) or not isinstance(expected, dict):
            return False, {"reason": "non_object_group_metric", "actual": actual, "expected": expected}
        keys = sorted(set(map(str, actual)) | set(map(str, expected)))
        mismatches = []
        for key in keys:
            actual_num = _numeric(actual.get(key))
            expected_num = _numeric(expected.get(key))
            if actual_num is None or expected_num is None or abs(actual_num - expected_num) > tolerance:
                mismatches.append({"group": key, "actual": actual.get(key), "expected": expected.get(key), "delta": None if actual_num is None or expected_num is None else round(actual_num - expected_num, 6)})
        return not mismatches, {"mismatches": mismatches[:30], "tolerance": tolerance}
    return False, {"reason": "unsupported_metric_kind"}


def _finding(contract: dict[str, Any], metric: dict[str, Any], actual: Any, expected: Any, comparison: dict[str, Any], source_context: dict[str, Any], summary_url: str) -> dict[str, Any]:
    metric_name = str(metric.get("summary_field") or "metric")
    kind = str(metric.get("kind") or "count")
    fingerprint = _hash({"contract": contract.get("contract_id"), "metric": metric_name, "kind": kind, "actual": actual, "expected": expected})
    return {
        "issue_id": f"BRE_{fingerprint[:12].upper()}",
        "fingerprint": fingerprint,
        "source": "business_reconciliation",
        "risk_type": "business_reconciliation",
        "business_reconciliation_type": "group_metric_mismatch" if kind.startswith("group_") else "metric_mismatch",
        "contract_id": contract.get("contract_id"),
        "title": f"业务统计口径不一致：{contract.get('resource')} {metric_name}",
        "severity": "P1",
        "status": "needs_human_review",
        "confidence": 0.94 if source_context.get("complete") else 0.76,
        "expected": f"{metric_name} 应等于同口径明细重新计算结果 {expected}",
        "actual": f"统计接口返回 {actual}，明细重算为 {expected}",
        "evidence": {
            "summary_request": {"method": "GET", "path": (contract.get("summary") or {}).get("path"), "url": summary_url, "query": _redact(contract.get("sample_query") or {})},
            "source_request": {"method": "GET", "path": (contract.get("source") or {}).get("path"), "query": _redact(contract.get("sample_query") or {}), "page_mode": source_context.get("page_mode"), "page_spec": source_context.get("page_spec"), "responses": source_context.get("responses")},
            "metric": _redact(metric),
            "comparison": _redact(comparison),
            "source_coverage": {"complete": source_context.get("complete"), "source_total": source_context.get("total"), "fetched_row_count": len(source_context.get("records") or [])},
        },
    }


def audit_reconciliation(contract: dict[str, Any], summary_payload: Any, source_context: dict[str, Any], summary_url: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for metric in contract.get("metrics") or []:
        actual = _path_value(summary_payload, str(metric.get("summary_field") or ""))
        expected, comparable, detail = _expected_metric(metric, source_context, contract)
        record = {"metric": _redact(metric), "actual": _redact(actual), "expected": _redact(expected), "source_detail": detail, "comparable": comparable}
        if actual is None:
            record["result"] = "skipped_summary_field_missing"
            observations.append(record)
            continue
        if not comparable:
            record["result"] = "skipped_incomplete_source"
            observations.append(record)
            continue
        equal, comparison = _equal_metric(actual, expected, metric)
        record["comparison"] = comparison
        record["result"] = "pass" if equal else "mismatch"
        observations.append(record)
        if not equal:
            findings.append(_finding(contract, metric, actual, expected, comparison, source_context, summary_url))
    return findings, observations


def build_business_reconciliation_profile(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    paths = config_paths(project, root)
    cfg = load_real_project_config(project, root)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    openapi = openapi if isinstance(openapi, dict) else {}
    contracts, candidates = build_reconciliation_contracts(openapi, cfg, _read_text(paths["input_dir"] / "prd.md"))
    probes = generate_business_reconciliation_probes(openapi, cfg, project, root, options.get("preview_probe_count") or 100)
    result = {
        "phase": "phase44_business_reconciliation",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "contracts": contracts,
        "prd_candidates": candidates,
        "preview_probes": probes,
        "summary": {
            "reconciliation_contract_count": len(contracts),
            "safe_read_only_contract_count": sum(1 for row in contracts if row.get("execution_policy") == "safe_read_only"),
            "metric_count": sum(len(row.get("metrics") or []) for row in contracts),
            "preview_probe_count": len(probes),
            "contract_gap_count": len(candidates),
        },
        "governance": {"default_execution": "plan_only", "safe_live_only_uses_GET": True, "source_pagination_bounded": True, "requires_complete_source_for_row_derived_metrics": True, "stores_redacted_evidence_only": True, "uses_no_benchmark_answer_files": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    output = _output_paths(project, root)
    _write_json(output["out"] / "business_reconciliation_profile.json", result)
    _write_json(output["workspace"] / "business_reconciliation_profile.json", result)
    output["out"].mkdir(parents=True, exist_ok=True)
    (output["out"] / "business_reconciliation_profile_report.html").write_text(render_business_reconciliation_profile_report(result), encoding="utf-8")
    return result


def load_business_reconciliation_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    data = _load_json(_output_paths(_safe_project_id(project_id), root)["workspace"] / "business_reconciliation_profile.json", {})
    return data if isinstance(data, dict) and data else None


def run_business_reconciliation(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    profile = build_business_reconciliation_profile(project, root, options)
    execution_mode = str(options.get("execution_mode") or cfg.get("business_reconciliation_execution_mode") or "plan_only").lower()
    if execution_mode not in {"plan_only", "safe_live"}:
        execution_mode = "plan_only"
    section = cfg.get("business_reconciliation") or cfg.get("business_reconcile") or {}
    section = section if isinstance(section, dict) else {}
    max_bytes = max(100_000, min(int(options.get("max_response_bytes") or section.get("max_response_bytes") or 3_000_000), 20_000_000))
    max_pages = max(1, min(int(options.get("max_source_pages") or section.get("max_source_pages") or 12), 100))
    timeout = max(1, min(int(cfg.get("request_timeout_seconds") or 10), 30))
    base_url = str(cfg.get("base_url") or "")
    token = _normal_token(cfg, project, root, timeout) if execution_mode == "safe_live" and base_url else None
    executions: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    semantic_hypotheses: list[dict[str, Any]] = []
    for contract in profile.get("contracts") or []:
        summary = contract.get("summary") or {}
        if execution_mode != "safe_live" or not base_url:
            executions.append({"contract_id": contract.get("contract_id"), "status": "planned", "reason": "plan_only_or_missing_base_url"})
            continue
        if contract.get("execution_policy") != "safe_read_only":
            executions.append({"contract_id": contract.get("contract_id"), "status": "skipped", "reason": "non_get_contract_candidate_only"})
            continue
        query = dict(contract.get("sample_query") or {})
        summary_url = _build_url(base_url, str(summary.get("path") or ""), query)
        summary_response = _http_get(summary_url, token, timeout, max_bytes)
        if not summary_response.get("ok"):
            executions.append({"contract_id": contract.get("contract_id"), "status": "error", "summary_url": summary_url, "summary_status_code": summary_response.get("status_code"), "error": summary_response.get("error")})
            continue
        summary_payload = _parse_json(summary_response)
        if summary_payload is None:
            executions.append({"contract_id": contract.get("contract_id"), "status": "error", "summary_url": summary_url, "summary_status_code": summary_response.get("status_code"), "error": "summary_response_not_json"})
            continue
        source_context = _fetch_source_pages(base_url, contract, token, timeout, max_bytes, max_pages)
        if not source_context.get("responses") or not (source_context.get("responses") or [{}])[0].get("status_code"):
            executions.append({"contract_id": contract.get("contract_id"), "status": "error", "summary_url": summary_url, "error": "source_fetch_failed", "source_responses": source_context.get("responses")})
            continue
        current_findings, observations = audit_reconciliation(contract, summary_payload, source_context, summary_url)
        findings.extend(current_findings)
        executions.append({"contract_id": contract.get("contract_id"), "status": "executed", "summary_url": summary_url, "summary_status_code": summary_response.get("status_code"), "source_complete": source_context.get("complete"), "source_total": source_context.get("total"), "fetched_source_rows": len(source_context.get("records") or []), "finding_count": len(current_findings), "metric_observations": observations})

    # --- LLM-powered semantic reconciliation (Phase61 moat upgrade) ---
    if execution_mode == "safe_live" and findings:
        try:
            llm_result = _llm_reason("reconciliation", {
                "prd_text": "", "api_schema": "", "observed_data": json.dumps(executions[-5:], ensure_ascii=False, default=str)[:4000],
                "heuristic_findings": json.dumps(findings[:15], ensure_ascii=False, default=str)[:4000],
                "primary_view": json.dumps([e.get("summary_url","") for e in executions[-3:]], ensure_ascii=False),
                "secondary_view": json.dumps([e.get("contract_id","") for e in executions[-3:]], ensure_ascii=False),
                "schema_context": "{}",
            })
            semantic_hypotheses.extend(compile_unverified_semantic_hypotheses(
                (llm_result or {}).get("findings"),
                engine="reconciliation",
                type_field="business_reconciliation_type",
            ))
        except Exception:
            pass

    output = _output_paths(project, root)
    registry, findings = _update_registry(output["registry"], findings)
    result = {
        "phase": "phase44_business_reconciliation",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "summary": {
            **profile.get("summary", {}),
            "execution_mode": execution_mode,
            "executed_contract_count": sum(1 for row in executions if row.get("status") == "executed"),
            "business_reconciliation_finding_count": len(findings),
            "persistent_business_reconciliation_count": sum(1 for row in findings if (row.get("evidence_stability") or {}).get("persistent")),
            "memory_fingerprint_count": len((registry or {}).get("entries") or {}),
        },
        "profile": profile,
        "executions": executions,
        "semantic_hypotheses": semantic_hypotheses, "llm_governance": {"status": "unverified_hypothesis_only", "does_not_affect_finding_counts": True, "requires_deterministic_replay": True}, "findings": findings,
        "memory_summary": {"fingerprint_count": len((registry or {}).get("entries") or {}), "updated_at_utc": _now(), "learning_policy": "跨运行重复的同一对账偏差提升置信度；仍需人工确认业务口径。"},
        "governance": {"execution_mode": execution_mode, "live_requests_limited_to_get": True, "source_pagination_bounded": True, "write_execution_disabled": True, "evidence_redacted_before_persistence": True, "uses_no_benchmark_answer_files": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    _write_json(output["out"] / "business_reconciliation_run.json", result)
    _write_json(output["workspace"] / "business_reconciliation_run.json", result)
    (output["out"] / "business_reconciliation_run_report.html").write_text(render_business_reconciliation_run_report(result), encoding="utf-8")
    return result


def render_business_reconciliation_profile_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(key)}</span><b>{_html_escape(value)}</b></div>" for key, value in summary.items())
    rows = "".join(
        f"<tr><td>{_html_escape(row.get('contract_id'))}</td><td>{_html_escape(row.get('resource'))}</td><td>{_html_escape((row.get('summary') or {}).get('method'))} {_html_escape((row.get('summary') or {}).get('path'))}</td><td>{_html_escape((row.get('source') or {}).get('method'))} {_html_escape((row.get('source') or {}).get('path'))}</td><td>{_html_escape(', '.join(str(metric.get('summary_field')) for metric in (row.get('metrics') or [])))}</td><td>{_html_escape(row.get('execution_policy'))}</td></tr>"
        for row in (data.get("contracts") or [])[:100]
    )
    return _render_html("业务对账契约", "Phase44 · Business Reconciliation", "让统计、看板、报表的业务口径接受明细数据的主动复算验证。", cards, "<h2>已发现对账契约</h2><table><thead><tr><th>ID</th><th>资源</th><th>统计接口</th><th>事实源</th><th>指标</th><th>执行</th></tr></thead><tbody>" + (rows or "<tr><td colspan='6'>暂无可自动对账的统计接口；可在 business_reconciliation.metric_contracts 显式配置。</td></tr>") + "</tbody></table>")


def render_business_reconciliation_run_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(key)}</span><b>{_html_escape(value)}</b></div>" for key, value in summary.items())
    rows = "".join(
        f"<tr><td>{_html_escape(row.get('severity'))}</td><td>{_html_escape(row.get('title'))}</td><td>{_html_escape(row.get('actual'))}</td><td>{_html_escape(row.get('confidence'))}</td><td>{_html_escape((row.get('evidence_stability') or {}).get('observations'))}</td></tr>"
        for row in (data.get("findings") or [])[:100]
    )
    return _render_html("业务对账运行", "Phase44 · Business Reconciliation", "统计/看板与明细数据复算比对结果；所有发现均附带脱敏、可复现证据。", cards, "<h2>发现的业务口径偏差</h2><table><thead><tr><th>等级</th><th>问题</th><th>实际</th><th>置信度</th><th>重复观测</th></tr></thead><tbody>" + (rows or "<tr><td colspan='5'>未发现可确认的不一致</td></tr>") + "</tbody></table>")


def _render_html(title: str, badge: str, description: str, cards: str, body: str) -> str:
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>{_html_escape(title)}</title><style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top;word-break:break-word}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#eef2ff;color:#3730a3}}</style></head><body><section class='hero'><span class='badge'>{_html_escape(badge)}</span><h1>{_html_escape(title)}</h1><p>{_html_escape(description)}</p></section><section class='panel'><h2>覆盖概览</h2><div class='grid'>{cards}</div></section><section class='panel'>{body}</section></body></html>"""


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    mode = os.environ.get("BUSINESS_RECONCILIATION_MODE") or (argv[1] if len(argv) > 1 else "plan_only")
    result = run_business_reconciliation(project, options={"execution_mode": mode})
    print(json.dumps({"ok": True, "project_id": project, "summary": result.get("summary"), "private_leak_check": result.get("private_leak_check")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
