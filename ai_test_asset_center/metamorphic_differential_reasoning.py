from __future__ import annotations

"""Phase49: metamorphic and differential business-behavior discovery.

This engine is designed for the class of production defects that are invisible
when an API is checked in isolation.  Instead of relying on a preset defect
catalog, it derives *relationships between valid observations* from OpenAPI,
PRD text, configuration and observed rows, then actively searches for a
counterexample.

Examples of executable metamorphic relations:

* a conjunction of valid filters must satisfy every individual predicate and
  be a subset of each individual query;
* an explicitly declared exclusive filter domain must partition its baseline
  collection exactly: no record may disappear, appear twice, or appear only in
  the filtered variants;
* explicitly declared half-open time windows must partition their whole
  business range exactly, which exposes boundary loss/duplication in report,
  settlement and history queries;
* adjacent pages of the same stable query must not overlap, and a large page
  must cover the split pages when the product declares stable ordering;
* explicitly requested ascending/descending order must be monotonic and,
  when the whole collection is returned, reverse each other;
* a list projection and its detail resource must agree on shared stable fields;
* two configured equivalent query forms must return the same business set.

The implementation performs GET requests only in ``safe_live`` mode.  It
redacts evidence, bounds all samples, and persists only stable fingerprints so
recurring counterexamples gain confidence without being silently treated as
confirmed defects.
"""

import argparse
import hashlib
import json
import os
import re
import urllib.parse
from pathlib import Path
from typing import Any, Iterable

from .business_invariant_mining import (
    FILTER_NAME_RE,
    _field_by_name,
    _infer_identity,
    _is_collection_read,
    _is_detail_read,
    _item_fields,
    _query_parameter_info,
)
from .business_outcome_validation import (
    _build_url,
    _http_get,
    _normal_token,
    _private_leak_check,
    _redact,
    _resource_key,
    _update_registry,
)
from .business_reconciliation import _extract_records, _parse_json
from .llm_reasoning import reason as _llm_reason
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


PAGE_PARAM_RE = re.compile(r"^(?:page|pageno|pageindex|page_no|page_num|pagenumber|页码)$", re.I)
SIZE_PARAM_RE = re.compile(r"^(?:size|pagesize|page_size|limit|perpage|per_page|rows|count|每页)$", re.I)
OFFSET_PARAM_RE = re.compile(r"^(?:offset|start|skip|from|起始)$", re.I)
SORT_FIELD_RE = re.compile(r"(?:^|[_\-.])(sort|sortby|sort_by|orderby|order_by|field|排序|排序字段)(?:$|[_\-.])", re.I)
SORT_DIRECTION_RE = re.compile(r"(?:^|[_\-.])(direction|dir|order|sortorder|sort_order|排序方向|顺序)(?:$|[_\-.])", re.I)
DYNAMIC_FIELD_RE = re.compile(r"(?:^|[_\-.])(created|updated|modified|time|timestamp|trace|request|nonce|cursor|next|etag|version|token|refresh)(?:_?at|_?time|_?date)?(?:$|[_\-.])|时间|时间戳|版本|游标|请求", re.I)
SENSITIVE_FIELD_RE = re.compile(r"password|secret|token|authorization|cookie|session|phone|email|mobile|身份证|银行卡|密码|密钥|令牌", re.I)
SCALAR_TYPES = (str, int, float, bool)


def _now() -> str:
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8", errors="replace")).hexdigest()


def _short(value: Any, length: int = 12) -> str:
    return _hash(value)[:length]


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


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = str(value).strip().replace(",", "")
    if not raw:
        return None
    try:
        return float(raw)
    except Exception:
        return None


def _section(cfg: dict[str, Any]) -> dict[str, Any]:
    value = (
        cfg.get("metamorphic_differential_reasoning")
        or cfg.get("metamorphic_reasoning")
        or cfg.get("semantic_mutation_reasoning")
        or cfg.get("differential_behavior_reasoning")
        or {}
    )
    return value if isinstance(value, dict) else {}


def _output_paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "defect_discovery"
    return {
        "out": root / "platform_outputs" / project / "metamorphic_differential_reasoning",
        "workspace": workspace,
        "registry": workspace / "metamorphic_differential_evidence_registry.json",
    }


def _operation_parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in (operation.get("parameters") or []) if isinstance(item, dict)]


def _configured_contracts(section: dict[str, Any]) -> list[dict[str, Any]]:
    raw = section.get("contracts") or section.get("collections") or []
    if isinstance(raw, dict):
        raw = [raw]
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _configured_for_operation(rows: list[dict[str, Any]], operation: dict[str, Any]) -> dict[str, Any]:
    path = str(operation.get("path") or "").rstrip("/") or "/"
    method = str(operation.get("method") or "GET").upper()
    for row in rows:
        candidate = str(row.get("path") or row.get("collection_path") or "").rstrip("/") or "/"
        candidate_method = str(row.get("method") or row.get("collection_method") or "GET").upper()
        if candidate == path and candidate_method == method:
            return row
    return {}


def _plural_match(left: str, right: str) -> bool:
    a, b = _norm(left), _norm(right)
    if not a or not b:
        return False
    return a == b or a.rstrip("s") == b.rstrip("s") or a in b or b in a


def _detail_for_collection(operation: dict[str, Any], details: list[dict[str, Any]], configured: dict[str, Any]) -> dict[str, Any] | None:
    configured_path = str(configured.get("detail_path_template") or configured.get("detail_path") or "").strip()
    if configured_path:
        for detail in details:
            if (str(detail.get("path") or "").rstrip("/") or "/") == (configured_path.rstrip("/") or "/"):
                return detail
        return {"path": configured_path, "method": "GET", "parameters": []}
    resource = _resource_key(str(operation.get("path") or ""))
    for detail in details:
        if _plural_match(resource, _resource_key(str(detail.get("path") or ""))):
            return detail
    return None


def _find_param(parameters: list[dict[str, Any]], regex: re.Pattern[str]) -> str | None:
    for param in parameters:
        name = str(param.get("name") or "")
        if regex.search(_norm(name)):
            return name
    return None


def _scalar_values(values: Iterable[Any], limit: int = 4) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, SCALAR_TYPES) or isinstance(value, bool) and value is False:
            continue
        token = _canon(value)
        if not token or token in seen or len(token) > 100:
            continue
        seen.add(token)
        result.append(value)
        if len(result) >= limit:
            break
    return result


def _value(row: dict[str, Any], field: str | None, mappings: dict[str, Any] | None = None) -> Any:
    if not isinstance(row, dict) or not field:
        return None
    mappings = mappings or {}
    wanted = {_norm(field)}
    mapped = mappings.get(str(field))
    if mapped:
        wanted.add(_norm(mapped))
    for key, mapped_name in mappings.items():
        if _norm(mapped_name) == _norm(field):
            wanted.add(_norm(key))
    for key, value in row.items():
        if _norm(key) in wanted:
            return value
    return None


def _identity(row: dict[str, Any], field: str | None, mappings: dict[str, Any]) -> str:
    return _canon(_value(row, field, mappings)) if field else ""


def _safe_field(name: str) -> bool:
    return bool(name) and not DYNAMIC_FIELD_RE.search(str(name)) and not SENSITIVE_FIELD_RE.search(str(name))


def _infer_filters(operation: dict[str, Any], fields: dict[str, Any], configured: dict[str, Any], components: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    raw = configured.get("filters") or configured.get("filter_contracts") or []
    if isinstance(raw, dict):
        raw = [raw]
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        parameter = str(item.get("parameter") or item.get("query") or "").strip()
        field = _field_by_name(fields, str(item.get("field") or parameter))
        values = item.get("values") if item.get("values") is not None else ([item.get("value")] if item.get("value") is not None else [])
        if parameter and field:
            result.append({
                "parameter": parameter,
                "field": field,
                "values": _scalar_values(values),
                "origin": "configured",
                # This is intentionally opt-in.  A finite enum does not prove
                # that its values are exhaustive or mutually exclusive.
                "complete_partition": bool(item.get("complete_partition") or item.get("partition_complete") or item.get("partition_of_baseline")),
                "partition_baseline_query": dict(item.get("partition_baseline_query") or item.get("baseline_query") or {}),
            })
            seen.add(_norm(parameter))
    for parameter in _query_parameter_info(operation, components):
        name = str(parameter.get("name") or "")
        if not name or _norm(name) in seen:
            continue
        field = _field_by_name(fields, name)
        enum = _scalar_values(parameter.get("enum") or [])
        if not field:
            continue
        # A query is considered a semantic filter only where it has a response
        # field counterpart and a finite valid value domain.
        if not enum or (not FILTER_NAME_RE.search(name) and _norm(name) != _norm(field)):
            continue
        result.append({"parameter": name, "field": field, "values": enum, "origin": "openapi_enum"})
        seen.add(_norm(name))
    return result[:8]


def _infer_pagination(operation: dict[str, Any], configured: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    raw = configured.get("pagination") or {}
    raw = raw if isinstance(raw, dict) else {}
    parameters = _query_parameter_info(operation, components)
    names = [str(item.get("name") or "") for item in parameters]
    page_param = str(raw.get("page_param") or raw.get("page") or "") or _find_param([{"name": name} for name in names], PAGE_PARAM_RE)
    size_param = str(raw.get("size_param") or raw.get("size") or raw.get("limit_param") or "") or _find_param([{"name": name} for name in names], SIZE_PARAM_RE)
    offset_param = str(raw.get("offset_param") or raw.get("offset") or "") or _find_param([{"name": name} for name in names], OFFSET_PARAM_RE)
    if not (page_param and size_param) and not (offset_param and size_param):
        return {}
    page_size = raw.get("page_size") or raw.get("size_value") or 20
    try:
        page_size = max(1, min(int(page_size), 200))
    except Exception:
        page_size = 20
    start = raw.get("first_page") if raw.get("first_page") is not None else raw.get("page_start", 1)
    try:
        start = int(start)
    except Exception:
        start = 1
    return {
        "page_param": page_param or None,
        "size_param": size_param,
        "offset_param": offset_param or None,
        "page_start": start,
        "page_size": page_size,
        "stable_order_required": bool(raw.get("stable_order_required") or raw.get("stable_order") or False),
        "origin": "configured" if raw else "openapi_name_inference",
    }


def _infer_sort(operation: dict[str, Any], fields: dict[str, Any], configured: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    raw = configured.get("sort") or configured.get("sorting") or {}
    raw = raw if isinstance(raw, dict) else {}
    parameters = _query_parameter_info(operation, components)
    name_map = {str(item.get("name") or ""): item for item in parameters}
    field_parameter = str(raw.get("field_parameter") or raw.get("sort_parameter") or raw.get("parameter") or "")
    direction_parameter = str(raw.get("direction_parameter") or raw.get("order_parameter") or "")
    if not field_parameter:
        for name in name_map:
            if SORT_FIELD_RE.search(_norm(name)):
                field_parameter = name
                break
    if not direction_parameter:
        for name in name_map:
            if SORT_DIRECTION_RE.search(_norm(name)) and name != field_parameter:
                direction_parameter = name
                break
    field = _field_by_name(fields, str(raw.get("field") or raw.get("sort_field") or ""))
    if not field and field_parameter and isinstance(name_map.get(field_parameter), dict):
        enum = name_map[field_parameter].get("enum") or []
        for item in enum:
            field = _field_by_name(fields, str(item))
            if field:
                break
    if not field_parameter or not direction_parameter or not field:
        return {}
    asc = raw.get("asc_value") or raw.get("ascending") or "asc"
    desc = raw.get("desc_value") or raw.get("descending") or "desc"
    return {
        "field_parameter": field_parameter,
        "direction_parameter": direction_parameter,
        "field": field,
        "asc_value": asc,
        "desc_value": desc,
        "origin": "configured" if raw else "openapi_enum_inference",
    }


def _infer_equivalences(configured: dict[str, Any]) -> list[dict[str, Any]]:
    raw = configured.get("equivalent_query_pairs") or configured.get("equivalences") or []
    if isinstance(raw, dict):
        raw = [raw]
    result: list[dict[str, Any]] = []
    for row in raw if isinstance(raw, list) else []:
        if not isinstance(row, dict):
            continue
        left = row.get("left_query") or row.get("left") or {}
        right = row.get("right_query") or row.get("right") or {}
        if isinstance(left, dict) and isinstance(right, dict):
            result.append({"name": str(row.get("name") or f"equivalence_{len(result)+1}"), "left_query": dict(left), "right_query": dict(right), "compare_fields": [str(v) for v in (row.get("compare_fields") or []) if str(v).strip()]})
    return result[:10]


def _infer_temporal_partitions(configured: dict[str, Any]) -> list[dict[str, Any]]:
    """Load explicit temporal range partitions without inferring semantics.

    Date-time ranges are deceptively dangerous: whether an API treats the end
    boundary as inclusive is a business contract, not something OpenAPI or a
    field name can prove.  Therefore this relation is opt-in and only accepts
    an explicit left-closed/right-open declaration plus a complete-response
    assertion for the bounded business range.
    """
    raw = (
        configured.get("temporal_partitions")
        or configured.get("time_window_partitions")
        or configured.get("date_range_partitions")
        or []
    )
    if isinstance(raw, dict):
        raw = [raw]
    result: list[dict[str, Any]] = []
    for index, item in enumerate(raw if isinstance(raw, list) else [], start=1):
        if not isinstance(item, dict):
            continue
        from_parameter = str(item.get("from_parameter") or item.get("start_parameter") or item.get("from_query") or "").strip()
        to_parameter = str(item.get("to_parameter") or item.get("end_parameter") or item.get("to_query") or "").strip()
        semantics = _norm(item.get("boundary_semantics") or item.get("semantics") or "")
        if semantics not in {"leftclosedrightopen", "halfopen", "startend"}:
            continue
        if not from_parameter or not to_parameter or not bool(item.get("complete_response") or item.get("complete_result") or item.get("range_complete")):
            continue
        windows_raw = item.get("windows") or item.get("ranges") or item.get("partitions") or []
        if isinstance(windows_raw, dict):
            windows_raw = [windows_raw]
        windows: list[dict[str, Any]] = []
        for row in windows_raw if isinstance(windows_raw, list) else []:
            if not isinstance(row, dict):
                continue
            start = row.get("from") if row.get("from") is not None else row.get("start")
            end = row.get("to") if row.get("to") is not None else row.get("end")
            if isinstance(start, SCALAR_TYPES) and isinstance(end, SCALAR_TYPES) and _canon(start) and _canon(end):
                windows.append({"from": start, "to": end})
        if len(windows) < 2:
            continue
        # Adjacent ranges are required.  We deliberately do not attempt to
        # parse or normalize time zones because an exact boundary spelling is
        # part of the endpoint contract provided by the enterprise.
        if any(_canon(left.get("to")) != _canon(right.get("from")) for left, right in zip(windows, windows[1:])):
            continue
        baseline_query = item.get("baseline_query") or item.get("range_baseline_query") or {}
        if not isinstance(baseline_query, dict):
            baseline_query = {}
        baseline_query = dict(baseline_query)
        baseline_query.setdefault(from_parameter, windows[0]["from"])
        baseline_query.setdefault(to_parameter, windows[-1]["to"])
        result.append({
            "name": str(item.get("name") or f"temporal_partition_{index}")[:120],
            "from_parameter": from_parameter,
            "to_parameter": to_parameter,
            "boundary_semantics": "left_closed_right_open",
            "complete_response": True,
            "baseline_query": baseline_query,
            "shared_query": dict(item.get("shared_query") or item.get("query") or {}),
            "windows": windows[:8],
        })
    return result[:6]


def _infer_compare_fields(fields: dict[str, Any], identity_field: str | None, configured: dict[str, Any]) -> list[str]:
    explicit = configured.get("compare_fields") or configured.get("detail_compare_fields") or []
    if isinstance(explicit, str):
        explicit = [explicit]
    selected = [_field_by_name(fields, str(value)) for value in explicit]
    selected = [value for value in selected if value and _safe_field(value)]
    if selected:
        return list(dict.fromkeys(selected))[:12]
    return [name for name in fields if name != identity_field and _safe_field(name)][:10]


def _contracts_from_inputs(openapi: dict[str, Any], cfg: dict[str, Any], prd: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    components = openapi.get("components") or {}
    operations = _operations(openapi)
    collections = [operation for operation in operations if _is_collection_read(operation, components)]
    details = [operation for operation in operations if _is_detail_read(operation)]
    configured = _configured_contracts(_section(cfg))
    contracts: list[dict[str, Any]] = []
    for operation in collections:
        row = _configured_for_operation(configured, operation)
        fields = _item_fields(operation, components)
        resource = str(row.get("resource") or _resource_key(str(operation.get("path") or "")))
        identity_field = _infer_identity(resource, fields, row)
        detail = _detail_for_collection(operation, details, row)
        detail_path = str((detail or {}).get("path") or "")
        detail_parameter = str(row.get("detail_parameter") or row.get("detail_path_parameter") or "")
        if not detail_parameter and detail_path:
            match = re.search(r"\{([^{}]+)\}", detail_path)
            detail_parameter = match.group(1) if match else "id"
        contract = {
            "contract_id": f"MDR_CONTRACT_{len(contracts)+1:04d}",
            "resource": resource,
            "collection": {"path": operation.get("path"), "method": operation.get("method"), "operation_id": operation.get("operation_id"), "summary": operation.get("summary"), "parameters": _operation_parameters(operation)},
            "sample_query": dict(row.get("sample_query") or row.get("query") or {}),
            "field_mappings": dict(row.get("field_mappings") or {}),
            "identity_field": identity_field,
            "filters": _infer_filters(operation, fields, row, components),
            "pagination": _infer_pagination(operation, row, components),
            "sort": _infer_sort(operation, fields, row, components),
            "equivalences": _infer_equivalences(row),
            "temporal_partitions": _infer_temporal_partitions(row),
            "detail": {"path": detail_path, "parameter": detail_parameter, "compare_fields": _infer_compare_fields(fields, identity_field, row)} if detail_path and identity_field else {},
            "field_names": list(fields),
            "execution_policy": "safe_read_only",
        }
        contracts.append(contract)
    candidates: list[dict[str, Any]] = []
    if re.search(r"筛选|分页|排序|查询|列表|filter|pagination|sort|query", str(prd or ""), re.I) and not contracts:
        candidates.append({"candidate_id": "MDR_PRD_NO_COLLECTION", "risk_type": "metamorphic_contract_gap", "severity": "P2", "title": "PRD 包含查询/筛选/分页语义，但无法从 OpenAPI 推导集合读取契约", "detail": "补充列表 GET 的响应 array schema，或在 metamorphic_differential_reasoning.contracts 中显式配置。"})
    for contract in contracts:
        types = sum(bool(contract.get(key)) for key in ("filters", "pagination", "sort", "equivalences", "detail"))
        if types == 0:
            candidates.append({"candidate_id": f"MDR_GAP_{contract['contract_id']}", "risk_type": "metamorphic_contract_gap", "severity": "P3", "title": f"无法为 {contract['resource']} 推导变形关系", "detail": "建议配置 filters、pagination、sort、equivalent_query_pairs 或 detail_path_template 中任意一项。"})
    return contracts, candidates


def _summary(contracts: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "metamorphic_contract_count": len(contracts),
        "filter_relation_count": sum(len(item.get("filters") or []) for item in contracts),
        "filter_partition_relation_count": sum(1 for item in contracts for spec in (item.get("filters") or []) if spec.get("complete_partition")),
        "pagination_relation_count": sum(1 for item in contracts if item.get("pagination")),
        "sort_relation_count": sum(1 for item in contracts if item.get("sort")),
        "detail_projection_relation_count": sum(1 for item in contracts if item.get("detail")),
        "equivalence_relation_count": sum(len(item.get("equivalences") or []) for item in contracts),
        "temporal_partition_relation_count": sum(len(item.get("temporal_partitions") or []) for item in contracts),
        "contract_gap_candidate_count": len(candidates),
    }


def _profile_for_persistence(profile: dict[str, Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(profile, ensure_ascii=False, default=str))
    for contract in clone.get("contracts") or []:
        contract["sample_query"] = _redact(contract.get("sample_query") or {})
        for equivalence in contract.get("equivalences") or []:
            equivalence["left_query"] = _redact(equivalence.get("left_query") or {})
            equivalence["right_query"] = _redact(equivalence.get("right_query") or {})
    return clone


def build_metamorphic_differential_profile(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    paths = config_paths(project, root)
    openapi = _load_json(paths["input_dir"] / "openapi.json", {})
    prd = _read_text(paths["input_dir"] / "prd.md")
    if not isinstance(openapi, dict):
        openapi = {}
    contracts, candidates = _contracts_from_inputs(openapi, cfg, prd)
    profile = {
        "phase": "phase49_metamorphic_differential_reasoning",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "summary": _summary(contracts, candidates),
        "contracts": contracts,
        "contract_gap_candidates": candidates,
        "governance": {"safe_live_get_only": True, "write_execution_disabled": True, "evidence_redacted_before_persistence": True, "uses_no_benchmark_answer_files": True},
    }
    persisted = _profile_for_persistence(profile)
    output = _output_paths(project, root)
    _write_json(output["out"] / "metamorphic_differential_profile.json", persisted)
    _write_json(output["workspace"] / "metamorphic_differential_profile.json", persisted)
    (output["out"] / "metamorphic_differential_profile_report.html").parent.mkdir(parents=True, exist_ok=True)
    (output["out"] / "metamorphic_differential_profile_report.html").write_text(render_metamorphic_differential_profile_report(persisted), encoding="utf-8")
    return persisted


def load_metamorphic_differential_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _load_json(_output_paths(project, root)["workspace"] / "metamorphic_differential_profile.json", {})
    return data if isinstance(data, dict) and data.get("phase") == "phase49_metamorphic_differential_reasoning" else None


def _probe(contract: dict[str, Any], number: int, relation: str, title: str, risk_type: str, **extra: Any) -> dict[str, Any]:
    return {
        "probe_id": f"MDR_PROBE_{number:04d}",
        "source": "metamorphic_differential_reasoning",
        "metamorphic_relation": relation,
        "contract_id": contract.get("contract_id"),
        "title": title,
        "risk_type": risk_type,
        "severity": extra.pop("severity", "P1"),
        "method": "GET",
        "path": (contract.get("collection") or {}).get("path"),
        "actor": "normal_user",
        "destructive": False,
        "execution_policy": "safe_read_only",
        "expected": extra.pop("expected", "有效业务查询变体之间必须满足可证伪的关系。"),
        **extra,
    }


def generate_metamorphic_differential_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    profile = load_metamorphic_differential_profile(project_id, root) or build_metamorphic_differential_profile(project_id, root)
    probes: list[dict[str, Any]] = []
    for contract in profile.get("contracts") or []:
        if contract.get("filters"):
            probes.append(_probe(contract, len(probes)+1, "filter_intersection", f"验证筛选组合语义：{contract.get('resource')}", "metamorphic_filter_relation"))
        if any(spec.get("complete_partition") for spec in (contract.get("filters") or [])):
            probes.append(_probe(contract, len(probes)+1, "filter_partition", f"验证筛选分区守恒：{contract.get('resource')}", "metamorphic_filter_relation"))
        if contract.get("pagination"):
            probes.append(_probe(contract, len(probes)+1, "pagination_partition", f"验证相邻分页分区：{contract.get('resource')}", "metamorphic_pagination_relation"))
        if contract.get("sort"):
            probes.append(_probe(contract, len(probes)+1, "sort_order", f"验证排序单调性：{contract.get('resource')}", "metamorphic_sort_relation"))
        for temporal in contract.get("temporal_partitions") or []:
            probes.append(_probe(contract, len(probes)+1, "temporal_partition", f"验证时间范围分区守恒：{contract.get('resource')} {temporal.get('name')}", "metamorphic_temporal_relation"))
        if contract.get("detail"):
            probes.append(_probe(contract, len(probes)+1, "detail_projection", f"验证列表与详情投影一致：{contract.get('resource')}", "metamorphic_detail_relation"))
        for relation in contract.get("equivalences") or []:
            probes.append(_probe(contract, len(probes)+1, "query_equivalence", f"验证等价查询关系：{contract.get('resource')} {relation.get('name')}", "metamorphic_equivalence_relation"))
    for gap in profile.get("contract_gap_candidates") or []:
        probes.append({"probe_id": f"MDR_GAP_{len(probes)+1:04d}", "source": "metamorphic_differential_reasoning", "metamorphic_relation": "contract_gap", "contract_id": gap.get("candidate_id"), "title": gap.get("title"), "risk_type": gap.get("risk_type") or "metamorphic_contract_gap", "severity": gap.get("severity") or "P2", "method": "GET", "path": "", "actor": "normal_user", "destructive": False, "execution_policy": "candidate_only", "expected": gap.get("detail")})
    limit = max_count if max_count is not None else int(cfg.get("max_probe_count") or 100)
    return probes[:max(0, int(limit))]


def _fetch_snapshot(base_url: str, contract: dict[str, Any], query: dict[str, Any], token: str | None, timeout: int, max_bytes: int) -> dict[str, Any]:
    url = _build_url(base_url, str((contract.get("collection") or {}).get("path") or ""), query)
    response = _http_get(url, token, timeout, max_bytes)
    if not response.get("ok"):
        return {"ok": False, "url": url, "query": query, "status_code": response.get("status_code"), "error": response.get("error"), "records": [], "total": None, "truncated": bool(response.get("truncated"))}
    payload = _parse_json(response)
    records, total = _extract_records(payload)
    return {"ok": True, "url": url, "query": query, "status_code": response.get("status_code"), "records": [item for item in records if isinstance(item, dict)], "total": total, "truncated": bool(response.get("truncated")), "payload_type": type(payload).__name__}


def _finding(contract: dict[str, Any], kind: str, title: str, expected: str, actual: str, evidence: dict[str, Any], severity: str = "P1", confidence: float = 0.9, key: Any | None = None) -> dict[str, Any]:
    risk_map = {
        "filter_semantics": "metamorphic_filter_relation",
        "filter_intersection": "metamorphic_filter_relation",
        "filter_subset": "metamorphic_filter_relation",
        "filter_partition": "metamorphic_filter_relation",
        "pagination_overlap": "metamorphic_pagination_relation",
        "pagination_total_drift": "metamorphic_pagination_relation",
        "pagination_cover": "metamorphic_pagination_relation",
        "sort_monotonic": "metamorphic_sort_relation",
        "sort_reverse": "metamorphic_sort_relation",
        "detail_projection": "metamorphic_detail_relation",
        "query_equivalence": "metamorphic_equivalence_relation",
        "temporal_partition": "metamorphic_temporal_relation",
    }
    fingerprint = _hash({"contract": contract.get("contract_id"), "kind": kind, "key": key, "expected": expected, "actual": actual})
    return {
        "issue_id": f"MDR_{fingerprint[:12].upper()}",
        "fingerprint": fingerprint,
        "source": "metamorphic_differential_reasoning",
        "risk_type": risk_map.get(kind, "metamorphic_relation"),
        "metamorphic_type": kind,
        "contract_id": contract.get("contract_id"),
        "title": title,
        "severity": severity,
        "status": "needs_human_review",
        "confidence": round(float(confidence), 3),
        "expected": expected,
        "actual": actual,
        "evidence": _redact(evidence),
        "business_impact": "接口单点返回正常，但等价业务观察之间的语义关系被破坏，可能造成筛选错误、时间边界漏数/重复、错误排序或详情展示不一致。",
        "suggested_fix": "对查询解析、时间边界、分页游标/排序稳定性及列表-详情投影共用同一业务查询/序列化逻辑，并为该关系补充回归断言。",
    }


def _ids(snapshot: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    mappings = dict(contract.get("field_mappings") or {})
    field = contract.get("identity_field")
    return [value for row in snapshot.get("records") or [] if (value := _identity(row, field, mappings))]


def _sample_filter_values(contract: dict[str, Any], baseline: dict[str, Any]) -> list[dict[str, Any]]:
    rows = baseline.get("records") or []
    result: list[dict[str, Any]] = []
    for item in contract.get("filters") or []:
        values = _scalar_values(item.get("values") or [])
        if not values:
            values = _scalar_values((_value(row, str(item.get("field") or ""), dict(contract.get("field_mappings") or {})) for row in rows), 3)
        if values:
            result.append({**item, "values": values})
    return result


def _row_mismatch(rows: list[dict[str, Any]], filters: list[dict[str, Any]], mappings: dict[str, Any]) -> list[dict[str, Any]]:
    problems: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        failed: list[str] = []
        for spec in filters:
            observed = _canon(_value(row, str(spec.get("field") or ""), mappings))
            wanted = _canon(spec.get("value"))
            if observed != wanted:
                failed.append(str(spec.get("field") or spec.get("parameter")))
        if failed:
            problems.append({"row_index": index, "failed_fields": failed})
    return problems


def _audit_filter_partitions(
    contract: dict[str, Any],
    baseline: dict[str, Any],
    usable: list[dict[str, Any]],
    single_results: dict[str, dict[str, Any]],
    base_url: str,
    token: str | None,
    timeout: int,
    max_bytes: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Verify an explicitly declared business classification is conserved.

    The relation is only enabled by project configuration because neither an
    OpenAPI enum nor a response field proves that the categories form a full,
    exclusive business partition.  This catches silent omissions that ordinary
    filter-validity checks cannot see.
    """
    identity_field = contract.get("identity_field")
    if not identity_field:
        return [], []

    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    sample_query = dict(contract.get("sample_query") or {})
    for spec in usable:
        values = list(spec.get("values") or [])
        if not spec.get("complete_partition") or len(values) < 2:
            continue

        baseline_query = {**sample_query, **dict(spec.get("partition_baseline_query") or {})}
        partition_baseline = baseline if dict(baseline.get("query") or {}) == baseline_query else _fetch_snapshot(
            base_url, contract, baseline_query, token, timeout, max_bytes
        )
        if not partition_baseline.get("ok") or partition_baseline.get("truncated"):
            observations.append({
                "kind": "filter_partition",
                "parameter": spec.get("parameter"),
                "result": "skipped_incomplete_baseline",
                "status_code": partition_baseline.get("status_code"),
            })
            continue

        memberships: dict[str, list[str]] = {}
        skipped = False
        for value in values:
            query = {**baseline_query, str(spec.get("parameter")): value}
            cached = single_results.get(f"{spec.get('parameter')}={_canon(value)}")
            snapshot = cached if cached and dict(cached.get("query") or {}) == query else _fetch_snapshot(
                base_url, contract, query, token, timeout, max_bytes
            )
            if not snapshot.get("ok") or snapshot.get("truncated"):
                skipped = True
                observations.append({
                    "kind": "filter_partition",
                    "parameter": spec.get("parameter"),
                    "result": "skipped_variant_error",
                    "status_code": snapshot.get("status_code"),
                })
                break
            for identity in set(_ids(snapshot, contract)):
                memberships.setdefault(identity, []).append(_canon(value))
        if skipped:
            continue

        baseline_ids = set(_ids(partition_baseline, contract))
        partition_ids = set(memberships)
        missing = sorted(baseline_ids - partition_ids)
        unexpected = sorted(partition_ids - baseline_ids)
        overlaps = sorted(identity for identity, owners in memberships.items() if len(owners) > 1)
        observations.append({
            "kind": "filter_partition",
            "parameter": spec.get("parameter"),
            "baseline_identity_count": len(baseline_ids),
            "partition_identity_count": len(partition_ids),
            "missing_count": len(missing),
            "unexpected_count": len(unexpected),
            "overlap_count": len(overlaps),
        })
        if missing or unexpected or overlaps:
            actual_parts: list[str] = []
            if missing:
                actual_parts.append(f"{len(missing)} 个基线业务主键未出现在任何合法筛选值中")
            if unexpected:
                actual_parts.append(f"{len(unexpected)} 个业务主键仅出现在筛选变体中")
            if overlaps:
                actual_parts.append(f"{len(overlaps)} 个业务主键同时落入多个互斥筛选值")
            findings.append(_finding(
                contract,
                "filter_partition",
                f"筛选分区不守恒：{contract.get('resource')} {spec.get('parameter')}",
                f"配置为完整互斥分类的 {spec.get('parameter')} 必须将基线业务集合完整且唯一地分区。",
                "；".join(actual_parts),
                {
                    "baseline_request": {
                        "method": "GET",
                        "path": (contract.get("collection") or {}).get("path"),
                        "query": baseline_query,
                    },
                    "filter": {
                        "parameter": spec.get("parameter"),
                        "field": spec.get("field"),
                        "value_count": len(values),
                    },
                    "missing_identity_hashes": [_short(value) for value in missing[:12]],
                    "unexpected_identity_hashes": [_short(value) for value in unexpected[:12]],
                    "overlapping_identity_hashes": [_short(value) for value in overlaps[:12]],
                },
                confidence=0.98,
                key={
                    "parameter": spec.get("parameter"),
                    "missing": missing,
                    "unexpected": unexpected,
                    "overlaps": overlaps,
                },
            ))
    return findings, observations


def _audit_filters(contract: dict[str, Any], baseline: dict[str, Any], base_url: str, token: str | None, timeout: int, max_bytes: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    mappings = dict(contract.get("field_mappings") or {})
    usable = _sample_filter_values(contract, baseline)
    single_results: dict[str, dict[str, Any]] = {}
    for spec in usable:
        for value in spec.get("values") or []:
            applied = {**spec, "value": value}
            query = {**(contract.get("sample_query") or {}), str(spec.get("parameter")): value}
            snapshot = _fetch_snapshot(base_url, contract, query, token, timeout, max_bytes)
            key = f"{spec.get('parameter')}={_canon(value)}"
            single_results[key] = snapshot
            if not snapshot.get("ok"):
                observations.append({"kind": "filter", "parameter": spec.get("parameter"), "result": "skipped_http_error", "status_code": snapshot.get("status_code")})
                continue
            mismatch = _row_mismatch(snapshot.get("records") or [], [applied], mappings)
            observations.append({"kind": "filter", "parameter": spec.get("parameter"), "row_count": len(snapshot.get("records") or []), "mismatch_count": len(mismatch)})
            if mismatch:
                findings.append(_finding(contract, "filter_semantics", f"筛选语义被破坏：{contract.get('resource')} {spec.get('parameter')}", f"携带 {spec.get('parameter')}={value} 的所有记录必须满足 {spec.get('field')}={value}。", f"发现 {len(mismatch)} 条返回记录不满足筛选条件。", {"request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": query}, "filter": {"parameter": spec.get("parameter"), "field": spec.get("field"), "value": value}, "mismatched_rows": mismatch[:12], "response_total": snapshot.get("total")}, confidence=0.97, key={"parameter": spec.get("parameter"), "value": _canon(value)}))
    # Cross-filter relation: each result of A∩B must satisfy both predicates
    # and its business identity set must be a subset of both individual queries.
    for left_index in range(len(usable)):
        for right_index in range(left_index + 1, len(usable)):
            left, right = usable[left_index], usable[right_index]
            if not left.get("values") or not right.get("values"):
                continue
            left_value, right_value = left["values"][0], right["values"][0]
            query = {**(contract.get("sample_query") or {}), str(left.get("parameter")): left_value, str(right.get("parameter")): right_value}
            combined = _fetch_snapshot(base_url, contract, query, token, timeout, max_bytes)
            if not combined.get("ok"):
                observations.append({"kind": "filter_intersection", "result": "skipped_http_error", "status_code": combined.get("status_code")})
                continue
            checks = [{**left, "value": left_value}, {**right, "value": right_value}]
            mismatch = _row_mismatch(combined.get("records") or [], checks, mappings)
            identity = contract.get("identity_field")
            escaped: list[dict[str, Any]] = []
            if identity:
                combined_ids = set(_ids(combined, contract))
                left_single = single_results.get(f"{left.get('parameter')}={_canon(left_value)}") or _fetch_snapshot(base_url, contract, {**(contract.get("sample_query") or {}), str(left.get("parameter")): left_value}, token, timeout, max_bytes)
                right_single = single_results.get(f"{right.get('parameter')}={_canon(right_value)}") or _fetch_snapshot(base_url, contract, {**(contract.get("sample_query") or {}), str(right.get("parameter")): right_value}, token, timeout, max_bytes)
                left_ids, right_ids = set(_ids(left_single, contract)), set(_ids(right_single, contract))
                for value in sorted(combined_ids - (left_ids & right_ids))[:12]:
                    escaped.append({"identity_hash": _short(value)})
            observations.append({"kind": "filter_intersection", "left": left.get("parameter"), "right": right.get("parameter"), "row_count": len(combined.get("records") or []), "mismatch_count": len(mismatch), "subset_escape_count": len(escaped)})
            if mismatch or escaped:
                actual_bits = []
                if mismatch:
                    actual_bits.append(f"{len(mismatch)} 条记录不同时满足两个筛选条件")
                if escaped:
                    actual_bits.append(f"{len(escaped)} 个业务主键不属于两个单筛选结果的交集")
                findings.append(_finding(contract, "filter_intersection", f"筛选组合关系被破坏：{contract.get('resource')} {left.get('parameter')} ∩ {right.get('parameter')}", "两个有效筛选条件组合后，结果必须同时满足两个字段条件，并且是各自单筛选结果的交集。", "；".join(actual_bits), {"request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": query}, "filters": [{"parameter": left.get("parameter"), "field": left.get("field"), "value": left_value}, {"parameter": right.get("parameter"), "field": right.get("field"), "value": right_value}], "mismatched_rows": mismatch[:12], "escaped_identities": escaped[:12]}, confidence=0.96, key={"left": left.get("parameter"), "right": right.get("parameter"), "values": [_canon(left_value), _canon(right_value)]}))
    current, partition_observations = _audit_filter_partitions(
        contract, baseline, usable, single_results, base_url, token, timeout, max_bytes
    )
    findings.extend(current)
    observations.extend(partition_observations)
    return findings, observations


def _snapshot_is_complete(snapshot: dict[str, Any]) -> bool:
    """Return whether a declared complete bounded read is actually complete."""
    if not snapshot.get("ok") or snapshot.get("truncated"):
        return False
    total = snapshot.get("total")
    if total is None:
        return True
    try:
        return len(snapshot.get("records") or []) >= int(total)
    except Exception:
        return False


def _audit_temporal_partitions(
    contract: dict[str, Any],
    baseline: dict[str, Any],
    base_url: str,
    token: str | None,
    timeout: int,
    max_bytes: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Verify explicit half-open time-range partitions against a whole range.

    The request remains GET-only.  No finding is emitted for incomplete or
    truncated responses because a partial range is not a valid business oracle.
    """
    if not contract.get("identity_field"):
        return [], []
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    sample_query = dict(contract.get("sample_query") or {})
    for spec in contract.get("temporal_partitions") or []:
        if not isinstance(spec, dict) or spec.get("boundary_semantics") != "left_closed_right_open":
            continue
        windows = [row for row in (spec.get("windows") or []) if isinstance(row, dict)]
        if len(windows) < 2:
            continue
        baseline_query = {**sample_query, **dict(spec.get("shared_query") or {}), **dict(spec.get("baseline_query") or {})}
        range_baseline = baseline if dict(baseline.get("query") or {}) == baseline_query else _fetch_snapshot(
            base_url, contract, baseline_query, token, timeout, max_bytes
        )
        if not _snapshot_is_complete(range_baseline):
            observations.append({
                "kind": "temporal_partition",
                "name": spec.get("name"),
                "result": "skipped_incomplete_baseline",
                "status_code": range_baseline.get("status_code"),
                "baseline_total": range_baseline.get("total"),
                "baseline_rows": len(range_baseline.get("records") or []),
            })
            continue

        memberships: dict[str, list[int]] = {}
        variant_requests: list[dict[str, Any]] = []
        skipped = False
        for index, window in enumerate(windows, start=1):
            query = {
                **sample_query,
                **dict(spec.get("shared_query") or {}),
                str(spec.get("from_parameter")): window.get("from"),
                str(spec.get("to_parameter")): window.get("to"),
            }
            snapshot = _fetch_snapshot(base_url, contract, query, token, timeout, max_bytes)
            variant_requests.append({
                "from": _canon(window.get("from")),
                "to": _canon(window.get("to")),
                "status_code": snapshot.get("status_code"),
                "row_count": len(snapshot.get("records") or []),
                "total": snapshot.get("total"),
            })
            if not _snapshot_is_complete(snapshot):
                observations.append({
                    "kind": "temporal_partition",
                    "name": spec.get("name"),
                    "result": "skipped_incomplete_window",
                    "window_index": index,
                    "status_code": snapshot.get("status_code"),
                })
                skipped = True
                break
            for identity in set(_ids(snapshot, contract)):
                memberships.setdefault(identity, []).append(index)
        if skipped:
            continue

        baseline_ids = set(_ids(range_baseline, contract))
        partition_ids = set(memberships)
        missing = sorted(baseline_ids - partition_ids)
        unexpected = sorted(partition_ids - baseline_ids)
        overlaps = sorted(identity for identity, owners in memberships.items() if len(owners) > 1)
        observations.append({
            "kind": "temporal_partition",
            "name": spec.get("name"),
            "baseline_identity_count": len(baseline_ids),
            "partition_identity_count": len(partition_ids),
            "missing_count": len(missing),
            "unexpected_count": len(unexpected),
            "overlap_count": len(overlaps),
            "window_count": len(windows),
        })
        if missing or unexpected or overlaps:
            actual: list[str] = []
            if missing:
                actual.append(f"{len(missing)} 个业务主键未落入任何时间窗口")
            if unexpected:
                actual.append(f"{len(unexpected)} 个业务主键仅出现在子时间窗口")
            if overlaps:
                actual.append(f"{len(overlaps)} 个业务主键同时出现在相邻时间窗口")
            findings.append(_finding(
                contract,
                "temporal_partition",
                f"时间范围分区不守恒：{contract.get('resource')} {spec.get('name')}",
                "已显式声明为左闭右开的相邻时间窗口，必须完整且唯一地分割同一全量业务范围。",
                "；".join(actual),
                {
                    "baseline_request": {
                        "method": "GET",
                        "path": (contract.get("collection") or {}).get("path"),
                        "query": baseline_query,
                    },
                    "boundary_semantics": "left_closed_right_open",
                    "window_requests": variant_requests,
                    "missing_identity_hashes": [_short(value) for value in missing[:12]],
                    "unexpected_identity_hashes": [_short(value) for value in unexpected[:12]],
                    "overlapping_identity_hashes": [_short(value) for value in overlaps[:12]],
                },
                confidence=0.985,
                key={
                    "name": spec.get("name"),
                    "baseline": baseline_query,
                    "missing": missing,
                    "unexpected": unexpected,
                    "overlaps": overlaps,
                },
            ))
    return findings, observations


def _page_query(contract: dict[str, Any], spec: dict[str, Any], index: int) -> dict[str, Any]:
    query = dict(contract.get("sample_query") or {})
    if spec.get("page_param"):
        query[str(spec["page_param"])] = int(spec.get("page_start") or 1) + index
    elif spec.get("offset_param"):
        query[str(spec["offset_param"])] = index * int(spec.get("page_size") or 20)
    query[str(spec.get("size_param") or "size")] = int(spec.get("page_size") or 20)
    return query


def _audit_pagination(contract: dict[str, Any], base_url: str, token: str | None, timeout: int, max_bytes: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    spec = contract.get("pagination") or {}
    if not spec or not contract.get("identity_field"):
        return [], []
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    first = _fetch_snapshot(base_url, contract, _page_query(contract, spec, 0), token, timeout, max_bytes)
    second = _fetch_snapshot(base_url, contract, _page_query(contract, spec, 1), token, timeout, max_bytes)
    if not first.get("ok") or not second.get("ok"):
        observations.append({"kind": "pagination", "result": "skipped_http_error", "first_status": first.get("status_code"), "second_status": second.get("status_code")})
        return findings, observations
    first_ids, second_ids = set(_ids(first, contract)), set(_ids(second, contract))
    overlap = sorted(first_ids & second_ids)
    observations.append({"kind": "pagination_partition", "page_size": spec.get("page_size"), "first_rows": len(first.get("records") or []), "second_rows": len(second.get("records") or []), "overlap_count": len(overlap), "first_total": first.get("total"), "second_total": second.get("total")})
    evidence = {"first_request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": first.get("query")}, "second_request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": second.get("query")}, "overlap_id_hashes": [_short(value) for value in overlap[:12]], "first_total": first.get("total"), "second_total": second.get("total")}
    if overlap:
        findings.append(_finding(contract, "pagination_overlap", f"相邻分页出现重复业务记录：{contract.get('resource')}", "同一稳定查询口径的相邻分页不得包含相同业务主键。", f"发现 {len(overlap)} 个业务主键同时出现在相邻分页。", evidence, confidence=0.97, key={"page_size": spec.get("page_size"), "overlap": sorted(overlap)}))
    if first.get("total") is not None and second.get("total") is not None and int(first.get("total")) != int(second.get("total")):
        findings.append(_finding(contract, "pagination_total_drift", f"相邻分页 total 口径漂移：{contract.get('resource')}", "同一稳定查询口径翻页时 total 必须保持一致。", f"第一页 total={first.get('total')}，第二页 total={second.get('total')}。", evidence, severity="P2", confidence=0.88, key={"first": first.get("total"), "second": second.get("total")}))
    # A product may explicitly declare that its default ordering is stable.  In
    # that case a bigger page must cover the two split pages, which catches
    # page-size dependent drops/duplicates not visible from adjacent pages.
    if bool(spec.get("stable_order_required")):
        large_query = dict(contract.get("sample_query") or {})
        if spec.get("page_param"):
            large_query[str(spec["page_param"])] = int(spec.get("page_start") or 1)
        elif spec.get("offset_param"):
            large_query[str(spec["offset_param"])] = 0
        large_query[str(spec.get("size_param") or "size")] = int(spec.get("page_size") or 20) * 2
        large = _fetch_snapshot(base_url, contract, large_query, token, timeout, max_bytes)
        if large.get("ok"):
            split_ids = first_ids | second_ids
            large_ids = set(_ids(large, contract))
            difference = sorted(split_ids ^ large_ids)
            observations.append({"kind": "pagination_cover", "split_identity_count": len(split_ids), "large_identity_count": len(large_ids), "difference_count": len(difference)})
            if difference:
                findings.append(_finding(contract, "pagination_cover", f"分页拆分与合并结果不一致：{contract.get('resource')}", "在产品声明默认排序稳定时，两个相邻分页的业务集合必须等于同范围的大页业务集合。", f"发现 {len(difference)} 个业务主键只出现在其中一种等价分页切分中。", {**evidence, "large_request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": large_query}, "difference_id_hashes": [_short(value) for value in difference[:12]]}, confidence=0.94, key={"page_size": spec.get("page_size"), "difference": sorted(difference)}))
    return findings, observations


def _is_monotonic(values: list[float], reverse: bool = False) -> bool:
    return all((left >= right if reverse else left <= right) for left, right in zip(values, values[1:]))


def _audit_sort(contract: dict[str, Any], base_url: str, token: str | None, timeout: int, max_bytes: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    spec = contract.get("sort") or {}
    if not spec:
        return [], []
    query_base = dict(contract.get("sample_query") or {})
    asc_query = {**query_base, str(spec.get("field_parameter")): spec.get("field"), str(spec.get("direction_parameter")): spec.get("asc_value")}
    desc_query = {**query_base, str(spec.get("field_parameter")): spec.get("field"), str(spec.get("direction_parameter")): spec.get("desc_value")}
    asc = _fetch_snapshot(base_url, contract, asc_query, token, timeout, max_bytes)
    desc = _fetch_snapshot(base_url, contract, desc_query, token, timeout, max_bytes)
    if not asc.get("ok") or not desc.get("ok"):
        return [], [{"kind": "sort", "result": "skipped_http_error", "asc_status": asc.get("status_code"), "desc_status": desc.get("status_code")}]
    mappings = dict(contract.get("field_mappings") or {})
    field = str(spec.get("field") or "")
    asc_values = [_number(_value(row, field, mappings)) for row in asc.get("records") or []]
    desc_values = [_number(_value(row, field, mappings)) for row in desc.get("records") or []]
    asc_numbers = [value for value in asc_values if value is not None]
    desc_numbers = [value for value in desc_values if value is not None]
    observations = [{"kind": "sort_monotonic", "field": field, "asc_row_count": len(asc.get("records") or []), "desc_row_count": len(desc.get("records") or []), "asc_numeric_count": len(asc_numbers), "desc_numeric_count": len(desc_numbers)}]
    findings: list[dict[str, Any]] = []
    evidence = {"asc_request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": asc_query}, "desc_request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": desc_query}, "field": field, "asc_values": asc_numbers[:30], "desc_values": desc_numbers[:30]}
    if len(asc_numbers) >= 2 and not _is_monotonic(asc_numbers):
        findings.append(_finding(contract, "sort_monotonic", f"升序排序不满足单调关系：{contract.get('resource')} {field}", f"请求 {spec.get('field_parameter')}={field} 且 {spec.get('direction_parameter')}={spec.get('asc_value')} 时，字段值必须非递减。", "真实返回中出现后一个字段值小于前一个字段值。", evidence, confidence=0.95, key={"direction": "asc", "field": field}))
    if len(desc_numbers) >= 2 and not _is_monotonic(desc_numbers, reverse=True):
        findings.append(_finding(contract, "sort_monotonic", f"降序排序不满足单调关系：{contract.get('resource')} {field}", f"请求 {spec.get('field_parameter')}={field} 且 {spec.get('direction_parameter')}={spec.get('desc_value')} 时，字段值必须非递增。", "真实返回中出现后一个字段值大于前一个字段值。", evidence, confidence=0.95, key={"direction": "desc", "field": field}))
    # Only compare reversal when both responses clearly cover the entire set.
    if contract.get("identity_field") and asc.get("total") is not None and desc.get("total") is not None and len(asc.get("records") or []) >= int(asc.get("total")) and len(desc.get("records") or []) >= int(desc.get("total")):
        asc_ids, desc_ids = _ids(asc, contract), _ids(desc, contract)
        if asc_ids and desc_ids and asc_ids != list(reversed(desc_ids)):
            findings.append(_finding(contract, "sort_reverse", f"升降序全量结果不互为反序：{contract.get('resource')} {field}", "同一完整业务集合在升序和降序下应拥有完全相反的业务主键顺序。", "升序业务主键序列与降序反转序列不一致。", {**evidence, "asc_id_hashes": [_short(value) for value in asc_ids[:30]], "desc_id_hashes": [_short(value) for value in desc_ids[:30]]}, severity="P2", confidence=0.9, key={"field": field, "asc": asc_ids, "desc": desc_ids}))
    return findings, observations


def _render_detail_path(template: str, parameter: str, value: str) -> str:
    encoded = urllib.parse.quote(str(value), safe="")
    rendered = re.sub(r"\{" + re.escape(parameter) + r"\}", encoded, template)
    if rendered == template:
        rendered = re.sub(r"\{[^{}]+\}", encoded, template, count=1)
    return rendered


def _detail_object(response: dict[str, Any]) -> dict[str, Any]:
    payload = _parse_json(response)
    if isinstance(payload, dict):
        for key in ("data", "item", "result", "record"):
            if isinstance(payload.get(key), dict):
                return payload[key]
        return payload
    return {}


def _audit_detail_projection(contract: dict[str, Any], baseline: dict[str, Any], base_url: str, token: str | None, timeout: int, max_bytes: int, max_samples: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    detail = contract.get("detail") or {}
    identity_field = contract.get("identity_field")
    if not detail or not identity_field:
        return [], []
    mappings = dict(contract.get("field_mappings") or {})
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    samples = []
    seen: set[str] = set()
    for row in baseline.get("records") or []:
        identity = _identity(row, identity_field, mappings)
        if identity and identity not in seen:
            seen.add(identity)
            samples.append((identity, row))
        if len(samples) >= max_samples:
            break
    for identity, list_row in samples:
        path = _render_detail_path(str(detail.get("path") or ""), str(detail.get("parameter") or "id"), identity)
        url = _build_url(base_url, path, {})
        response = _http_get(url, token, timeout, max_bytes)
        if not response.get("ok"):
            observations.append({"kind": "detail_projection", "identity_hash": _short(identity), "result": "skipped_http_error", "status_code": response.get("status_code")})
            continue
        detail_row = _detail_object(response)
        mismatches: list[dict[str, Any]] = []
        for field in detail.get("compare_fields") or []:
            left, right = _canon(_value(list_row, str(field), mappings)), _canon(_value(detail_row, str(field), mappings))
            if left and right and left != right:
                mismatches.append({"field": field, "list_value_hash": _short(left), "detail_value_hash": _short(right)})
        observations.append({"kind": "detail_projection", "identity_hash": _short(identity), "compare_field_count": len(detail.get("compare_fields") or []), "mismatch_count": len(mismatches)})
        if mismatches:
            findings.append(_finding(contract, "detail_projection", f"列表与详情业务投影不一致：{contract.get('resource')}", "同一业务主键在列表投影与详情资源中，共享的稳定业务字段必须一致。", f"业务记录存在 {len(mismatches)} 个共享字段值不一致。", {"list_request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": baseline.get("query")}, "detail_request": {"method": "GET", "path": path}, "identity_hash": _short(identity), "mismatches": mismatches[:12]}, confidence=0.94, key={"identity": identity, "fields": [item["field"] for item in mismatches]}))
    return findings, observations


def _audit_equivalences(contract: dict[str, Any], base_url: str, token: str | None, timeout: int, max_bytes: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for relation in contract.get("equivalences") or []:
        left_q = {**(contract.get("sample_query") or {}), **(relation.get("left_query") or {})}
        right_q = {**(contract.get("sample_query") or {}), **(relation.get("right_query") or {})}
        left, right = _fetch_snapshot(base_url, contract, left_q, token, timeout, max_bytes), _fetch_snapshot(base_url, contract, right_q, token, timeout, max_bytes)
        if not left.get("ok") or not right.get("ok"):
            observations.append({"kind": "query_equivalence", "name": relation.get("name"), "result": "skipped_http_error"})
            continue
        identity = contract.get("identity_field")
        mismatch = False
        evidence: dict[str, Any] = {"left_request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": left_q}, "right_request": {"method": "GET", "path": (contract.get("collection") or {}).get("path"), "query": right_q}, "relation": relation.get("name")}
        if identity:
            left_ids, right_ids = set(_ids(left, contract)), set(_ids(right, contract))
            difference = sorted(left_ids ^ right_ids)
            evidence["difference_id_hashes"] = [_short(value) for value in difference[:12]]
            mismatch = bool(difference)
        else:
            mismatch = _hash(left.get("records") or []) != _hash(right.get("records") or [])
        observations.append({"kind": "query_equivalence", "name": relation.get("name"), "mismatch": mismatch})
        if mismatch:
            findings.append(_finding(contract, "query_equivalence", f"等价查询返回不同业务集合：{contract.get('resource')} {relation.get('name')}", "配置为等价的两种查询表达必须返回相同业务集合。", "两种等价查询观察到不同的业务结果。", evidence, confidence=0.92, key={"name": relation.get("name"), "left": left_q, "right": right_q}))
    return findings, observations


def run_metamorphic_differential_reasoning(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    profile = build_metamorphic_differential_profile(project, root, options)
    section = _section(cfg)
    mode = str(options.get("execution_mode") or cfg.get("metamorphic_differential_execution_mode") or "plan_only").lower()
    if mode not in {"plan_only", "safe_live"}:
        mode = "plan_only"
    max_bytes = max(100_000, min(int(options.get("max_response_bytes") or section.get("max_response_bytes") or 3_000_000), 20_000_000))
    max_details = max(1, min(int(options.get("max_detail_samples") or section.get("max_detail_samples") or 5), 30))
    timeout = max(1, min(int(cfg.get("request_timeout_seconds") or 10), 30))
    base_url = str(cfg.get("base_url") or "").strip()
    accounts = _load_json(config_paths(project, root)["input_dir"] / "test_accounts.json", {})
    safety = execution_safety_verdict(project, cfg, accounts)
    live_execution_allowed = mode == "safe_live" and bool(safety.get("safe_to_proceed"))
    token = _normal_token(cfg, project, root, timeout) if live_execution_allowed and base_url else None

    executions: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    readiness_findings: list[dict[str, Any]] = []
    if mode == "safe_live" and not live_execution_allowed:
        readiness_findings.append({
            "kind": "safety_boundary",
            "status": "blocked",
            "message": "在线差分执行已被安全边界阻断；这不是产品缺陷。",
            "violations": safety.get("violations") or [],
        })

    for contract in profile.get("contracts") or []:
        if mode != "safe_live" or not base_url:
            executions.append({
                "contract_id": contract.get("contract_id"),
                "status": "planned",
                "reason": "plan_only_or_missing_base_url",
            })
            continue
        if not live_execution_allowed:
            executions.append({
                "contract_id": contract.get("contract_id"),
                "status": "blocked_by_safety_boundary",
                "reason": "unsafe_or_undeclared_target",
            })
            continue

        baseline = _fetch_snapshot(base_url, contract, dict(contract.get("sample_query") or {}), token, timeout, max_bytes)
        if not baseline.get("ok"):
            executions.append({
                "contract_id": contract.get("contract_id"),
                "status": "error",
                "reason": "baseline_fetch_failed",
                "status_code": baseline.get("status_code"),
                "error": baseline.get("error"),
            })
            continue

        emitted: list[dict[str, Any]] = []
        all_observations: list[dict[str, Any]] = []
        current, obs = _audit_filters(contract, baseline, base_url, token, timeout, max_bytes)
        emitted.extend(current); all_observations.extend(obs)
        current, obs = _audit_temporal_partitions(contract, baseline, base_url, token, timeout, max_bytes)
        emitted.extend(current); all_observations.extend(obs)
        current, obs = _audit_pagination(contract, base_url, token, timeout, max_bytes)
        emitted.extend(current); all_observations.extend(obs)
        current, obs = _audit_sort(contract, base_url, token, timeout, max_bytes)
        emitted.extend(current); all_observations.extend(obs)
        current, obs = _audit_detail_projection(contract, baseline, base_url, token, timeout, max_bytes, max_details)
        emitted.extend(current); all_observations.extend(obs)
        current, obs = _audit_equivalences(contract, base_url, token, timeout, max_bytes)
        emitted.extend(current); all_observations.extend(obs)
        findings.extend(emitted)
        # Always write one execution record per attempted contract.  A clean run
        # is evidence, not an absence of evidence.
        executions.append({
            "contract_id": contract.get("contract_id"),
            "status": "executed",
            "baseline_request": {
                "method": "GET",
                "path": (contract.get("collection") or {}).get("path"),
                "query": baseline.get("query"),
            },
            "baseline_row_count": len(baseline.get("records") or []),
            "baseline_total": baseline.get("total"),
            "observations": all_observations,
            "finding_count": len(emitted),
        })

    # LLM output may help propose where to observe next, but it never becomes a
    # customer-visible bug finding without deterministic evidence from this run.
    semantic_hypotheses: list[dict[str, Any]] = []
    if live_execution_allowed and findings:
        try:
            llm_result = _llm_reason("metamorphic", {
                "prd_text": "",
                "api_schema": "",
                "observed_data": _json(executions[-5:])[:4000],
                "heuristic_findings": _json(findings[:15])[:4000],
            })
            for index, item in enumerate((llm_result or {}).get("findings") or [], start=1):
                if not isinstance(item, dict):
                    continue
                hypothesis = _redact({
                    "hypothesis_id": f"MDR_HYP_{_short({'project': project, 'index': index, 'rule': item.get('rule'), 'title': item.get('title')})}",
                    "source": "llm_reasoning",
                    "status": "unverified_hypothesis",
                    "rule": str(item.get("rule") or "semantic_follow_up")[:120],
                    "title": str(item.get("title") or "建议补充业务观察")[:300],
                    "suggested_next_observation": str(item.get("expected") or item.get("observed") or "")[:500],
                })
                semantic_hypotheses.append(hypothesis)
        except Exception:
            pass

    output = _output_paths(project, root)
    registry, findings = _update_registry(output["registry"], findings)
    result = {
        "phase": "phase63_temporal_partition_conservation",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "summary": {
            **(profile.get("summary") or {}),
            "execution_mode": mode,
            "executed_contract_count": sum(1 for row in executions if row.get("status") == "executed"),
            "blocked_contract_count": sum(1 for row in executions if row.get("status") == "blocked_by_safety_boundary"),
            "metamorphic_differential_finding_count": len(findings),
            "semantic_hypothesis_count": len(semantic_hypotheses),
            "persistent_metamorphic_differential_count": sum(1 for row in findings if (row.get("evidence_stability") or {}).get("persistent")),
            "memory_fingerprint_count": len((registry or {}).get("entries") or {}),
        },
        "profile": profile,
        "executions": executions,
        "findings": findings,
        "semantic_hypotheses": semantic_hypotheses,
        "readiness_findings": readiness_findings,
        "safety_boundary": safety,
        "memory_summary": {
            "fingerprint_count": len((registry or {}).get("entries") or {}),
            "updated_at_utc": _now(),
            "learning_policy": "同一变形关系反例跨运行重复出现时提高置信度；未经人工确认始终保持 needs_human_review。",
        },
        "governance": {
            "execution_mode": mode,
            "live_requests_limited_to_get": True,
            "write_execution_disabled": True,
            "bounded_filter_samples": True,
            "bounded_detail_samples": True,
            "evidence_redacted_before_persistence": True,
            "llm_output_is_not_a_defect_finding": True,
            "uses_no_benchmark_answer_files": True,
        },
    }
    result["private_leak_check"] = _private_leak_check(result)
    _write_json(output["out"] / "metamorphic_differential_run.json", result)
    _write_json(output["workspace"] / "metamorphic_differential_run.json", result)
    (output["out"] / "metamorphic_differential_run_report.html").write_text(render_metamorphic_differential_run_report(result), encoding="utf-8")
    return result


def _render_html(title: str, badge: str, subtitle: str, cards: str, body: str) -> str:
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>{_html_escape(title)}</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#eef2ff;color:#3730a3}}</style></head><body>
<section class='hero'><span class='badge'>{_html_escape(badge)}</span><h1>{_html_escape(title)}</h1><p>{_html_escape(subtitle)}</p></section><section class='panel'><div class='grid'>{cards}</div></section><section class='panel'>{body}</section></body></html>"""


def render_metamorphic_differential_profile_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(key)}</span><b>{_html_escape(value)}</b></div>" for key, value in summary.items())
    rows = []
    for contract in data.get("contracts") or []:
        types: list[str] = []
        if contract.get("filters"): types.append("filter intersection")
        if any(spec.get("complete_partition") for spec in (contract.get("filters") or [])): types.append("filter partition conservation")
        if contract.get("pagination"): types.append("pagination partition")
        if contract.get("sort"): types.append("sort relation")
        if contract.get("detail"): types.append("list/detail")
        if contract.get("equivalences"): types.append("query equivalence")
        rows.append(f"<tr><td>{_html_escape(contract.get('contract_id'))}</td><td>{_html_escape(contract.get('resource'))}</td><td>{_html_escape((contract.get('collection') or {}).get('method'))} {_html_escape((contract.get('collection') or {}).get('path'))}</td><td>{_html_escape(contract.get('identity_field') or '-')}</td><td>{_html_escape(', '.join(types) or '-')}</td></tr>")
    return _render_html("变形关系与差分行为发现", "Phase49 · Metamorphic Differential Reasoning", "让系统主动比较等价业务观察，而不是只检查每个接口是否返回成功。", cards, "<h2>可执行关系</h2><table><thead><tr><th>ID</th><th>资源</th><th>集合接口</th><th>业务主键</th><th>关系</th></tr></thead><tbody>" + ("".join(rows) or "<tr><td colspan='5'>暂无可执行变形关系</td></tr>") + "</tbody></table>")


def render_metamorphic_differential_run_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(key)}</span><b>{_html_escape(value)}</b></div>" for key, value in summary.items())
    rows = []
    for finding in data.get("findings") or []:
        rows.append(f"<tr><td>{_html_escape(finding.get('severity'))}</td><td>{_html_escape(finding.get('metamorphic_type'))}</td><td>{_html_escape(finding.get('title'))}</td><td>{_html_escape(finding.get('actual'))}</td><td>{_html_escape(finding.get('confidence'))}</td><td>{_html_escape((finding.get('evidence_stability') or {}).get('observations', 1))}</td></tr>")
    return _render_html("变形关系运行结果", "Phase49 · Metamorphic Differential Reasoning", "每一条发现都来自两个或多个有效业务观察之间被证伪的关系，保留脱敏、可复现证据。", cards, "<h2>发现的反例</h2><table><thead><tr><th>等级</th><th>关系类型</th><th>问题</th><th>实际</th><th>置信度</th><th>观测次数</th></tr></thead><tbody>" + ("".join(rows) or "<tr><td colspan='6'>未发现变形关系反例</td></tr>") + "</tbody></table>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QualiBug Phase49 metamorphic differential reasoning")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--mode", choices=["plan_only", "safe_live"], default="plan_only")
    args = parser.parse_args(argv)
    result = run_metamorphic_differential_reasoning(args.project, options={"execution_mode": args.mode})
    print(json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
