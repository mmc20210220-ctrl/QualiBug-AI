from __future__ import annotations

"""Phase50: temporal data-integrity and regression discovery.

Most enterprise incidents are not visible in one request: an API can keep
returning 200 while a new release silently drops a legacy field, changes a
numeric unit from cents to dollars, duplicates business identities, rewrites
an immutable business attribute, or makes historically important records
vanish.  This module creates a privacy-preserving, read-only baseline from a
trusted observation and compares later observations against it.

The engine intentionally stores no raw business rows.  It persists only
identity/value hashes, aggregate metrics, type/presence distributions and
redacted evidence.  All live execution is GET-only.  A baseline is sticky by
default: an operator must explicitly reset it after an approved migration so a
real regression cannot silently become the new normal.
"""

import argparse
import hashlib
import json
import math
import os
import re
import statistics
import time
from pathlib import Path
from typing import Any

from .business_invariant_mining import _infer_identity, _is_collection_read, _item_fields
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


SENSITIVE_FIELD_RE = re.compile(r"password|secret|token|authorization|cookie|session|phone|email|mobile|身份证|银行卡|密码|密钥|令牌", re.I)
DYNAMIC_FIELD_RE = re.compile(r"(?:^|[_\-.])(updated|modified|accessed|viewed|time|timestamp|trace|request|nonce|cursor|etag|version|refresh)(?:_?at|_?time|_?date)?(?:$|[_\-.])|更新时间|时间戳|游标|请求", re.I)
IMMUTABLE_HINT_RE = re.compile(r"(?:^|[_\-.])(external|source|legacy|origin|createdby|creator|tenant|currency|sku|code)(?:_?id|_?code)?(?:$|[_\-.])|外部|来源|历史|旧|币种|租户|编码", re.I)
NUMERIC_HINT_RE = re.compile(r"amount|price|cost|fee|tax|balance|revenue|gmv|quantity|qty|volume|金额|价格|费用|税|余额|收入|销售额|数量", re.I)
ID_HINT_RE = re.compile(r"(?:^|[_\-.])(id|uuid|guid|code|number|no|serial)(?:$|[_\-.])|编号|编码|单号", re.I)


def _now() -> str:
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
    raw = re.sub(r"^[¥$€£]", "", raw)
    if not raw:
        return None
    try:
        return float(raw)
    except Exception:
        return None


def _kind(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _section(cfg: dict[str, Any]) -> dict[str, Any]:
    value = (
        cfg.get("temporal_data_regression_reasoning")
        or cfg.get("temporal_regression_reasoning")
        or cfg.get("data_regression_reasoning")
        or cfg.get("temporal_data_integrity")
        or {}
    )
    return value if isinstance(value, dict) else {}


def _output_paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "defect_discovery"
    return {
        "out": root / "platform_outputs" / project / "temporal_data_regression_reasoning",
        "workspace": workspace,
        "registry": workspace / "temporal_data_regression_evidence_registry.json",
        "baseline": workspace / "temporal_data_regression_baselines.json",
    }


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


def _value(row: dict[str, Any], field: str | None, mappings: dict[str, Any] | None = None) -> Any:
    if not field:
        return None
    mapped = (mappings or {}).get(str(field), field)
    candidates = [mapped] if isinstance(mapped, str) else list(mapped) if isinstance(mapped, list) else [field]
    for candidate in candidates:
        current: Any = row
        ok = True
        for part in str(candidate or "").split("."):
            if isinstance(current, dict) and part in current:
                current = current.get(part)
            else:
                ok = False
                break
        if ok:
            return current
    wanted = _norm(field)
    for key, value in row.items():
        if _norm(key) == wanted:
            return value
    return None


def _identity(row: dict[str, Any], fields: list[str], mappings: dict[str, Any]) -> str | None:
    values = [_canon(_value(row, field, mappings)) for field in fields]
    if not values or any(not value for value in values):
        return None
    return "|".join(values)


def _safe_field(name: str) -> bool:
    return bool(name) and not SENSITIVE_FIELD_RE.search(str(name)) and not DYNAMIC_FIELD_RE.search(str(name))


def _identity_fields(resource: str, fields: dict[str, dict[str, Any]], row: dict[str, Any]) -> list[str]:
    raw = row.get("identity_fields") or row.get("identity_field") or []
    if isinstance(raw, str):
        raw = [raw]
    values = [str(item) for item in raw if str(item).strip()] if isinstance(raw, list) else []
    if values:
        return values[:4]
    inferred = _infer_identity(resource, fields, row)
    return [str(inferred)] if inferred else []


def _field_names(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    return [str(item) for item in raw if str(item).strip()] if isinstance(raw, list) else []


def _required_fields(fields: dict[str, dict[str, Any]], row: dict[str, Any]) -> list[str]:
    configured = _field_names(row.get("required_fields") or row.get("presence_fields"))
    if configured:
        return configured[:30]
    return [name for name, schema in fields.items() if bool(schema.get("_qualibug_required")) and _safe_field(name)][:30]


def _immutable_fields(fields: dict[str, dict[str, Any]], identities: list[str], row: dict[str, Any]) -> list[str]:
    configured = _field_names(row.get("immutable_fields") or row.get("stable_fields") or row.get("unchanged_fields"))
    if configured:
        return [name for name in configured if name not in identities and _safe_field(name)][:30]
    return [name for name in fields if name not in identities and _safe_field(name) and IMMUTABLE_HINT_RE.search(name)][:12]


def _numeric_fields(fields: dict[str, dict[str, Any]], row: dict[str, Any]) -> list[str]:
    configured = _field_names(row.get("numeric_fields") or row.get("scale_fields") or row.get("aggregate_fields"))
    if configured:
        return [name for name in configured if _safe_field(name)][:20]
    return [name for name, schema in fields.items() if _safe_field(name) and str(schema.get("type") or "").lower() in {"number", "integer"} and NUMERIC_HINT_RE.search(name)][:12]


def _enum_fields(fields: dict[str, dict[str, Any]], row: dict[str, Any]) -> dict[str, list[str]]:
    configured = row.get("enum_fields") or row.get("allowed_values") or {}
    result: dict[str, list[str]] = {}
    if isinstance(configured, dict):
        for name, values in configured.items():
            if isinstance(values, (list, tuple, set)):
                result[str(name)] = [str(value) for value in values if str(value).strip()]
    for name, schema in fields.items():
        if name in result or not _safe_field(name):
            continue
        values = schema.get("enum") or []
        if isinstance(values, list) and values:
            result[name] = [str(value) for value in values if str(value).strip()]
    return {key: value[:40] for key, value in result.items() if value}


def _canary_hashes(raw: Any, identity_fields: list[str]) -> list[str]:
    if not identity_fields:
        return []
    items = raw if isinstance(raw, list) else [raw] if raw is not None else []
    result: list[str] = []
    for item in items:
        if isinstance(item, dict):
            values = [_canon(item.get(field)) for field in identity_fields]
            if all(values):
                result.append(_hash("|".join(values)))
        elif len(identity_fields) == 1 and _canon(item):
            result.append(_hash(_canon(item)))
        elif isinstance(item, (tuple, list)) and len(item) == len(identity_fields):
            values = [_canon(value) for value in item]
            if all(values):
                result.append(_hash("|".join(values)))
    return list(dict.fromkeys(result))[:100]


def _contracts_from_inputs(openapi: dict[str, Any], cfg: dict[str, Any], prd: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    components = openapi.get("components") or {}
    configured = _configured_contracts(_section(cfg))
    contracts: list[dict[str, Any]] = []
    for operation in _operations(openapi):
        if not _is_collection_read(operation, components):
            continue
        row = _configured_for_operation(configured, operation)
        fields = _item_fields(operation, components)
        resource = str(row.get("resource") or _resource_key(str(operation.get("path") or "")))
        identity_fields = _identity_fields(resource, fields, row)
        contract = {
            "contract_id": f"TDR_CONTRACT_{len(contracts)+1:04d}",
            "resource": resource,
            "collection": {"path": operation.get("path"), "method": operation.get("method"), "operation_id": operation.get("operation_id"), "summary": operation.get("summary")},
            "sample_query": dict(row.get("sample_query") or row.get("query") or {}),
            "field_mappings": dict(row.get("field_mappings") or {}),
            "identity_fields": identity_fields,
            "required_fields": _required_fields(fields, row),
            "immutable_fields": _immutable_fields(fields, identity_fields, row),
            "numeric_fields": _numeric_fields(fields, row),
            "enum_fields": _enum_fields(fields, row),
            "canary_identity_hashes": _canary_hashes(row.get("canary_identities") or row.get("required_identities"), identity_fields),
            "minimum_records": max(1, min(int(row.get("minimum_records") or 3), 5000)),
            "presence_floor": max(0.0, min(float(row.get("presence_floor") or 0.85), 1.0)),
            "presence_drop_delta": max(0.05, min(float(row.get("presence_drop_delta") or 0.2), 1.0)),
            "max_immutable_change_ratio": max(0.01, min(float(row.get("max_immutable_change_ratio") or 0.25), 1.0)),
            "numeric_scale_tolerance": max(0.01, min(float(row.get("numeric_scale_tolerance") or 0.12), 0.9)),
            "scale_factors": [float(value) for value in (row.get("scale_factors") or [0.01, 0.1, 10, 100, 1000]) if _number(value) not in {None, 0}],
            "max_records": max(10, min(int(row.get("max_records") or _section(cfg).get("max_records") or 300), 5000)),
            "execution_policy": "safe_read_only",
        }
        contracts.append(contract)
    candidates: list[dict[str, Any]] = []
    if re.search(r"迁移|升级|历史|存量|兼容|版本|字段|数据质量|回归|legacy|migration|upgrade|history|backward", str(prd or ""), re.I) and not contracts:
        candidates.append({"candidate_id": "TDR_PRD_NO_COLLECTION", "risk_type": "temporal_contract_gap", "severity": "P2", "title": "PRD 包含迁移/历史/兼容语义，但无法从 OpenAPI 推导可观测集合", "detail": "补充列表 GET 响应 schema，或在 temporal_data_regression_reasoning.contracts 中配置 path 与业务主键。"})
    for contract in contracts:
        if not contract.get("identity_fields"):
            candidates.append({"candidate_id": f"TDR_NO_ID_{contract['contract_id']}", "risk_type": "temporal_contract_gap", "severity": "P2", "title": f"{contract['resource']} 缺少稳定业务主键，无法可靠做跨运行回归对比", "detail": "在 identity_fields 中指定订单号、客户编码、SKU 等业务唯一键。"})
    return contracts, candidates


def _summary(contracts: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "temporal_contract_count": len(contracts),
        "temporal_identity_contract_count": sum(1 for item in contracts if item.get("identity_fields")),
        "temporal_presence_field_count": sum(len(item.get("required_fields") or []) for item in contracts),
        "temporal_immutable_field_count": sum(len(item.get("immutable_fields") or []) for item in contracts),
        "temporal_numeric_field_count": sum(len(item.get("numeric_fields") or []) for item in contracts),
        "temporal_canary_count": sum(len(item.get("canary_identity_hashes") or []) for item in contracts),
        "contract_gap_candidate_count": len(candidates),
    }


def _profile_for_persistence(profile: dict[str, Any]) -> dict[str, Any]:
    clone = json.loads(json.dumps(profile, ensure_ascii=False, default=str))
    for contract in clone.get("contracts") or []:
        contract["sample_query"] = _redact(contract.get("sample_query") or {})
    return clone


def build_temporal_data_regression_profile(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    paths = config_paths(project, root)
    openapi = _load_json(paths["input_dir"] / "openapi.json", {})
    if not isinstance(openapi, dict):
        openapi = {}
    contracts, candidates = _contracts_from_inputs(openapi, cfg, _read_text(paths["input_dir"] / "prd.md"))
    profile = {
        "phase": "phase50_temporal_data_regression_reasoning",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "summary": _summary(contracts, candidates),
        "contracts": contracts,
        "contract_gap_candidates": candidates,
        "governance": {"safe_live_get_only": True, "baseline_contains_no_raw_rows": True, "baseline_sticky_until_explicit_reset": True, "evidence_redacted_before_persistence": True, "uses_no_benchmark_answer_files": True},
    }
    persisted = _profile_for_persistence(profile)
    output = _output_paths(project, root)
    _write_json(output["out"] / "temporal_data_regression_profile.json", persisted)
    _write_json(output["workspace"] / "temporal_data_regression_profile.json", persisted)
    (output["out"] / "temporal_data_regression_profile_report.html").parent.mkdir(parents=True, exist_ok=True)
    (output["out"] / "temporal_data_regression_profile_report.html").write_text(render_temporal_data_regression_profile_report(persisted), encoding="utf-8")
    return persisted


def load_temporal_data_regression_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _load_json(_output_paths(project, root)["workspace"] / "temporal_data_regression_profile.json", {})
    return data if isinstance(data, dict) and data.get("phase") == "phase50_temporal_data_regression_reasoning" else None


def _probe(contract: dict[str, Any], number: int, kind: str, title: str, risk_type: str, expected: str) -> dict[str, Any]:
    return {
        "probe_id": f"TDR_PROBE_{number:04d}",
        "source": "temporal_data_regression_reasoning",
        "temporal_regression_type": kind,
        "contract_id": contract.get("contract_id"),
        "title": title,
        "risk_type": risk_type,
        "severity": "P1" if kind not in {"type_drift", "enum_contract_violation"} else "P2",
        "method": "GET",
        "path": (contract.get("collection") or {}).get("path"),
        "actor": "normal_user",
        "destructive": False,
        "execution_policy": "safe_read_only",
        "expected": expected,
    }


def generate_temporal_data_regression_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    profile = load_temporal_data_regression_profile(project_id, root) or build_temporal_data_regression_profile(project_id, root)
    probes: list[dict[str, Any]] = []
    for contract in profile.get("contracts") or []:
        if contract.get("required_fields"):
            probes.append(_probe(contract, len(probes)+1, "field_presence_collapse", f"验证关键字段跨运行存在性：{contract.get('resource')}", "temporal_field_presence_regression", "同一可信业务集合中的关键字段不应在发布后批量缺失/变空。"))
        if contract.get("identity_fields"):
            probes.append(_probe(contract, len(probes)+1, "identity_duplicate_regression", f"验证业务主键跨运行唯一性：{contract.get('resource')}", "temporal_identity_regression", "历史唯一的业务主键在后续版本中不得开始重复。"))
        if contract.get("immutable_fields"):
            probes.append(_probe(contract, len(probes)+1, "immutable_field_drift", f"验证不可变业务字段漂移：{contract.get('resource')}", "temporal_immutable_drift", "同一业务主键的来源、币种、外部编码等不可变字段不得被批量改写。"))
        if contract.get("numeric_fields"):
            probes.append(_probe(contract, len(probes)+1, "numeric_scale_shift", f"验证数值单位/精度回归：{contract.get('resource')}", "temporal_numeric_scale_regression", "金额、数量等字段不应因单位、精度或序列化变更整体放大/缩小。"))
        if contract.get("enum_fields"):
            probes.append(_probe(contract, len(probes)+1, "enum_contract_violation", f"验证枚举词表跨版本契约：{contract.get('resource')}", "temporal_contract_regression", "实际业务值必须仍属于声明/企业配置的合法词表。"))
        if contract.get("canary_identity_hashes"):
            probes.append(_probe(contract, len(probes)+1, "canary_missing", f"验证关键历史记录可见性：{contract.get('resource')}", "temporal_identity_regression", "企业指定的关键历史记录在稳定查询范围内不可无故消失。"))
    for gap in profile.get("contract_gap_candidates") or []:
        probes.append({"probe_id": f"TDR_GAP_{len(probes)+1:04d}", "source": "temporal_data_regression_reasoning", "temporal_regression_type": "contract_gap", "contract_id": gap.get("candidate_id"), "title": gap.get("title"), "risk_type": gap.get("risk_type") or "temporal_contract_gap", "severity": gap.get("severity") or "P2", "method": "GET", "path": "", "actor": "normal_user", "destructive": False, "execution_policy": "candidate_only", "expected": gap.get("detail")})
    limit = max_count if max_count is not None else int(cfg.get("max_probe_count") or 100)
    return probes[:max(0, int(limit))]


def _numeric_metrics(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "sum": 0.0, "mean": None, "median": None, "min": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(values),
        "sum": round(float(sum(values)), 8),
        "mean": round(float(sum(values) / len(values)), 8),
        "median": round(float(statistics.median(ordered)), 8),
        "min": round(float(ordered[0]), 8),
        "max": round(float(ordered[-1]), 8),
    }


def _snapshot_for_persistence(snapshot: dict[str, Any]) -> dict[str, Any]:
    clean = {key: value for key, value in snapshot.items() if key not in {"_records", "_runtime_enum_values"}}
    return json.loads(json.dumps(clean, ensure_ascii=False, default=str))


def _capture_snapshot(base_url: str, contract: dict[str, Any], token: str | None, timeout: int, max_bytes: int) -> dict[str, Any]:
    query = dict(contract.get("sample_query") or {})
    path = str((contract.get("collection") or {}).get("path") or "")
    url = _build_url(base_url, path, query)
    response = _http_get(url, token, timeout, max_bytes)
    if not response.get("ok"):
        return {"ok": False, "url": url, "query": query, "status_code": response.get("status_code"), "error": response.get("error"), "truncated": bool(response.get("truncated"))}
    records, total = _extract_records(_parse_json(response))
    rows = [row for row in records if isinstance(row, dict)][:int(contract.get("max_records") or 300)]
    mappings = dict(contract.get("field_mappings") or {})
    identity_fields = [str(item) for item in (contract.get("identity_fields") or [])]
    required_fields = [str(item) for item in (contract.get("required_fields") or [])]
    immutable_fields = [str(item) for item in (contract.get("immutable_fields") or [])]
    numeric_fields = [str(item) for item in (contract.get("numeric_fields") or [])]
    enum_fields = contract.get("enum_fields") if isinstance(contract.get("enum_fields"), dict) else {}
    identities: list[str] = []
    duplicate_counts: dict[str, int] = {}
    immutable_hashes: dict[str, dict[str, str]] = {}
    field_presence: dict[str, dict[str, Any]] = {}
    field_types: dict[str, dict[str, int]] = {}
    numeric_values: dict[str, list[float]] = {field: [] for field in numeric_fields}
    enum_values: dict[str, set[str]] = {str(field): set() for field in enum_fields}
    all_fields = list(dict.fromkeys([*required_fields, *immutable_fields, *numeric_fields, *list(enum_fields)]))
    for row in rows:
        identity = _identity(row, identity_fields, mappings)
        identity_hash = _hash(identity) if identity else None
        if identity_hash:
            identities.append(identity_hash)
            duplicate_counts[identity_hash] = duplicate_counts.get(identity_hash, 0) + 1
            immutable_hashes.setdefault(identity_hash, {})
        for field in all_fields:
            value = _value(row, field, mappings)
            state = field_presence.setdefault(field, {"non_null": 0, "total": 0})
            state["total"] += 1
            if value is not None and _canon(value) != "":
                state["non_null"] += 1
                kind = _kind(value)
                field_types.setdefault(field, {})[kind] = field_types.setdefault(field, {}).get(kind, 0) + 1
            if identity_hash and field in immutable_fields and value is not None and _canon(value) != "":
                immutable_hashes[identity_hash][field] = _hash(_canon(value))
            if field in numeric_values:
                parsed = _number(value)
                if parsed is not None and math.isfinite(parsed):
                    numeric_values[field].append(parsed)
            if field in enum_values and value is not None and _canon(value) != "":
                enum_values[field].add(_canon(value))
    for field in all_fields:
        state = field_presence.setdefault(field, {"non_null": 0, "total": len(rows)})
        total_count = max(1, int(state.get("total") or 0))
        state["rate"] = round(float(state.get("non_null") or 0) / total_count, 6)
    snapshot = {
        "ok": True,
        "captured_at_utc": _now(),
        "request": {"method": "GET", "path": path, "query": _redact(query), "status_code": response.get("status_code"), "truncated": bool(response.get("truncated"))},
        "record_count": len(rows),
        "reported_total": total,
        "identity_count": len(set(identities)),
        "identity_hashes": sorted(set(identities))[:int(contract.get("max_records") or 300)],
        "duplicate_identity_hashes": sorted([item for item, count in duplicate_counts.items() if count > 1])[:30],
        "field_presence": field_presence,
        "field_types": field_types,
        "immutable_hashes": immutable_hashes,
        "numeric_metrics": {field: _numeric_metrics(values) for field, values in numeric_values.items()},
        "enum_value_hashes": {field: sorted({_hash(value) for value in values})[:40] for field, values in enum_values.items()},
        "_runtime_enum_values": {field: sorted(values)[:40] for field, values in enum_values.items()},
        "_records": rows,
    }
    return snapshot


def _dominant_type(values: dict[str, Any]) -> tuple[str | None, int]:
    pairs = [(str(key), int(value or 0)) for key, value in (values or {}).items() if str(key) != "null"]
    if not pairs:
        return None, 0
    return sorted(pairs, key=lambda item: (-item[1], item[0]))[0]


def _ratio_matches_factor(ratio: float, factors: list[float], tolerance: float) -> float | None:
    if ratio <= 0 or not math.isfinite(ratio):
        return None
    for factor in factors:
        if factor <= 0:
            continue
        if abs(ratio - factor) / max(abs(factor), 1e-12) <= tolerance:
            return factor
    return None


def _finding(contract: dict[str, Any], kind: str, title: str, expected: str, actual: str, evidence: dict[str, Any], confidence: float = 0.9, key: Any | None = None) -> dict[str, Any]:
    risk_map = {
        "field_presence_collapse": "temporal_field_presence_regression",
        "schema_type_drift": "temporal_type_regression",
        "immutable_field_drift": "temporal_immutable_drift",
        "numeric_scale_shift": "temporal_numeric_scale_regression",
        "identity_duplicate_regression": "temporal_identity_regression",
        "canary_missing": "temporal_identity_regression",
        "enum_contract_violation": "temporal_contract_regression",
    }
    severity = "P1" if kind not in {"schema_type_drift", "enum_contract_violation"} else "P2"
    fingerprint = _hash({"contract": contract.get("contract_id"), "kind": kind, "key": key, "expected": expected, "actual": actual})
    return {
        "issue_id": f"TDR_{fingerprint[:12].upper()}",
        "fingerprint": fingerprint,
        "source": "temporal_data_regression_reasoning",
        "risk_type": risk_map.get(kind, "temporal_data_regression"),
        "temporal_regression_type": kind,
        "contract_id": contract.get("contract_id"),
        "title": title,
        "severity": severity,
        "status": "needs_human_review",
        "confidence": round(float(confidence), 3),
        "expected": expected,
        "actual": actual,
        "evidence": _redact(evidence),
        "business_impact": "接口可用但跨发布/跨运行的数据语义已回归，可能导致历史数据不可用、金额/数量错误、业务主键冲突或下游系统读取错误。",
        "suggested_fix": "将该契约加入发布前只读基线回归；检查迁移脚本、字段映射、序列化精度、缓存/索引重建与幂等写入逻辑。批准的业务迁移后应显式重建基线，而不是让异常自动覆盖旧基线。",
    }


def _compare_snapshots(contract: dict[str, Any], baseline: dict[str, Any], current: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    minimum = int(contract.get("minimum_records") or 3)
    if min(int(baseline.get("record_count") or 0), int(current.get("record_count") or 0)) < minimum:
        return findings
    # Key business fields cannot silently become absent across a stable observation.
    for field in contract.get("required_fields") or []:
        before = ((baseline.get("field_presence") or {}).get(field) or {})
        after = ((current.get("field_presence") or {}).get(field) or {})
        before_rate, after_rate = float(before.get("rate") or 0), float(after.get("rate") or 0)
        if before_rate >= 0.95 and after_rate < float(contract.get("presence_floor") or 0.85) and before_rate - after_rate >= float(contract.get("presence_drop_delta") or 0.2):
            findings.append(_finding(contract, "field_presence_collapse", f"关键字段批量缺失：{contract.get('resource')} · {field}", f"基线中 {field} 的非空率为 {before_rate:.1%}，后续运行不应明显降低。", f"当前非空率降至 {after_rate:.1%}（样本 {after.get('total', 0)} 条）。", {"field": field, "baseline_presence": before, "current_presence": after, "request": current.get("request")}, confidence=0.96, key={"field": field}))
    # A response type switch is often a serialization/breaking-change defect.
    for field in set([*(contract.get("required_fields") or []), *(contract.get("numeric_fields") or [])]):
        before_type, before_count = _dominant_type((baseline.get("field_types") or {}).get(field) or {})
        after_type, after_count = _dominant_type((current.get("field_types") or {}).get(field) or {})
        if before_type and after_type and before_type != after_type and min(before_count, after_count) >= minimum:
            findings.append(_finding(contract, "schema_type_drift", f"字段类型跨运行漂移：{contract.get('resource')} · {field}", f"字段 {field} 应继续以 {before_type} 语义返回。", f"当前主类型变为 {after_type}。", {"field": field, "baseline_types": (baseline.get("field_types") or {}).get(field), "current_types": (current.get("field_types") or {}).get(field), "request": current.get("request")}, confidence=0.88, key={"field": field, "from": before_type, "to": after_type}))
    # Only fields marked/inferred immutable participate, so ordinary state changes are not flagged.
    base_immutable = baseline.get("immutable_hashes") if isinstance(baseline.get("immutable_hashes"), dict) else {}
    curr_immutable = current.get("immutable_hashes") if isinstance(current.get("immutable_hashes"), dict) else {}
    shared = sorted(set(base_immutable) & set(curr_immutable))
    for field in contract.get("immutable_fields") or []:
        changed = [identity for identity in shared if (base_immutable.get(identity) or {}).get(field) and (curr_immutable.get(identity) or {}).get(field) and (base_immutable.get(identity) or {}).get(field) != (curr_immutable.get(identity) or {}).get(field)]
        compared = [identity for identity in shared if (base_immutable.get(identity) or {}).get(field) and (curr_immutable.get(identity) or {}).get(field)]
        ratio = len(changed) / len(compared) if compared else 0.0
        if len(compared) >= minimum and ratio >= float(contract.get("max_immutable_change_ratio") or 0.25):
            findings.append(_finding(contract, "immutable_field_drift", f"不可变业务字段被批量改写：{contract.get('resource')} · {field}", f"同一业务主键的 {field} 在基线与当前版本之间应保持不变。", f"{len(changed)}/{len(compared)} 个可比业务主键发生变化（{ratio:.1%}）。", {"field": field, "compared_identity_count": len(compared), "changed_identity_count": len(changed), "changed_identity_hashes": changed[:15], "request": current.get("request")}, confidence=0.95, key={"field": field, "ratio": round(ratio, 3)}))
    # Unit / decimal errors affect a whole data set and are highly damaging; use aggregate median only.
    base_numeric = baseline.get("numeric_metrics") if isinstance(baseline.get("numeric_metrics"), dict) else {}
    curr_numeric = current.get("numeric_metrics") if isinstance(current.get("numeric_metrics"), dict) else {}
    for field in contract.get("numeric_fields") or []:
        before, after = base_numeric.get(field) or {}, curr_numeric.get(field) or {}
        baseline_median, current_median = _number(before.get("median")), _number(after.get("median"))
        count_ratio = (float(after.get("count") or 0) / float(before.get("count") or 1)) if before.get("count") else 0.0
        if baseline_median in {None, 0} or current_median is None or min(int(before.get("count") or 0), int(after.get("count") or 0)) < minimum or not 0.7 <= count_ratio <= 1.3:
            continue
        ratio = current_median / baseline_median
        factor = _ratio_matches_factor(ratio, [float(value) for value in (contract.get("scale_factors") or [])], float(contract.get("numeric_scale_tolerance") or 0.12))
        if factor is not None and abs(math.log10(max(abs(factor), 1e-12))) >= 0.85:
            findings.append(_finding(contract, "numeric_scale_shift", f"数值单位/精度疑似整体漂移：{contract.get('resource')} · {field}", "同一稳定样本集合的数值分布不应整体按 10/100/1000 等比例变化。", f"当前与基线中位数比值约为 {ratio:.4g}，接近异常比例 {factor:g}。", {"field": field, "baseline_metric": before, "current_metric": after, "ratio": round(ratio, 8), "matched_scale_factor": factor, "request": current.get("request")}, confidence=0.93, key={"field": field, "factor": factor}))
    # If a previously unique business key starts duplicating, it is a concrete integrity regression.
    if not (baseline.get("duplicate_identity_hashes") or []) and (current.get("duplicate_identity_hashes") or []):
        duplicates = current.get("duplicate_identity_hashes") or []
        findings.append(_finding(contract, "identity_duplicate_regression", f"业务主键出现新增重复：{contract.get('resource')}", "基线中唯一的业务主键在后续运行中不得重复。", f"当前发现 {len(duplicates)} 个重复业务主键。", {"duplicate_identity_hashes": duplicates[:15], "baseline_identity_count": baseline.get("identity_count"), "current_identity_count": current.get("identity_count"), "request": current.get("request")}, confidence=0.97, key={"duplicate_count": len(duplicates)}))
    # Canary IDs are provided by the enterprise only for records expected to remain visible under the same query.
    baseline_ids = set(baseline.get("identity_hashes") or [])
    current_ids = set(current.get("identity_hashes") or [])
    canaries = set(contract.get("canary_identity_hashes") or [])
    missing = sorted((canaries & baseline_ids) - current_ids)
    if missing:
        findings.append(_finding(contract, "canary_missing", f"关键历史记录在稳定查询中消失：{contract.get('resource')}", "企业指定且基线可见的关键历史记录不应在同口径查询中无故消失。", f"当前缺少 {len(missing)} 条关键历史记录。", {"missing_identity_hashes": missing[:15], "baseline_identity_count": baseline.get("identity_count"), "current_identity_count": current.get("identity_count"), "request": current.get("request")}, confidence=0.94, key={"missing": missing[:15]}))
    # Keep enum evidence hashed: only configured/schema values are treated as a contract.
    runtime_enums = current.get("_runtime_enum_values") if isinstance(current.get("_runtime_enum_values"), dict) else {}
    for field, allowed in (contract.get("enum_fields") or {}).items():
        allowed_tokens = {_canon(value) for value in allowed}
        unexpected = sorted({_canon(value) for value in (runtime_enums.get(field) or []) if _canon(value) not in allowed_tokens})
        if unexpected:
            findings.append(_finding(contract, "enum_contract_violation", f"枚举词表出现未声明值：{contract.get('resource')} · {field}", f"字段 {field} 的值必须属于声明/企业配置词表。", f"发现 {len(unexpected)} 个未声明枚举值。", {"field": field, "unexpected_value_hashes": [_short(value) for value in unexpected[:15]], "allowed_value_count": len(allowed_tokens), "request": current.get("request")}, confidence=0.89, key={"field": field, "unexpected": [_short(value) for value in unexpected[:15]]}))
    return findings


def _load_baselines(path: Path) -> dict[str, Any]:
    data = _load_json(path, {})
    if not isinstance(data, dict) or data.get("phase") != "phase50_temporal_data_regression_baselines":
        return {"phase": "phase50_temporal_data_regression_baselines", "created_at_utc": _now(), "contracts": {}}
    data.setdefault("contracts", {})
    return data


def _save_baselines(path: Path, state: dict[str, Any]) -> None:
    state = dict(state)
    state["updated_at_utc"] = _now()
    _write_json(path, state)


def run_temporal_data_regression_reasoning(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    profile = build_temporal_data_regression_profile(project, root, options)
    section = _section(cfg)
    mode = str(options.get("execution_mode") or cfg.get("temporal_data_regression_execution_mode") or "plan_only").lower()
    if mode not in {"plan_only", "safe_live"}:
        mode = "plan_only"
    max_bytes = max(100_000, min(int(options.get("max_response_bytes") or section.get("max_response_bytes") or 3_000_000), 20_000_000))
    timeout = max(1, min(int(cfg.get("request_timeout_seconds") or 10), 30))
    base_url = str(cfg.get("base_url") or "")
    token = _normal_token(cfg, project, root, timeout) if mode == "safe_live" and base_url else None
    output = _output_paths(project, root)
    baselines = _load_baselines(output["baseline"])
    reset = bool(options.get("reset_baseline"))
    executions: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    semantic_hypotheses: list[dict[str, Any]] = []
    baseline_established = 0
    for contract in profile.get("contracts") or []:
        contract_id = str(contract.get("contract_id") or "")
        if mode != "safe_live" or not base_url:
            executions.append({"contract_id": contract_id, "status": "planned", "reason": "plan_only_or_missing_base_url"})
            continue
        current = _capture_snapshot(base_url, contract, token, timeout, max_bytes)
        if not current.get("ok"):
            executions.append({"contract_id": contract_id, "status": "error", "reason": "snapshot_fetch_failed", "status_code": current.get("status_code"), "error": current.get("error")})
            continue
        prior_entry = (baselines.get("contracts") or {}).get(contract_id)
        baseline = prior_entry.get("baseline") if isinstance(prior_entry, dict) else None
        if reset or not isinstance(baseline, dict):
            baselines.setdefault("contracts", {})[contract_id] = {"baseline": _snapshot_for_persistence(current), "baseline_established_at_utc": _now(), "observation_count": 1, "last_observation": _snapshot_for_persistence(current)}
            baseline_established += 1
            executions.append({"contract_id": contract_id, "status": "baseline_established" if not reset else "baseline_reset", "record_count": current.get("record_count"), "reported_total": current.get("reported_total"), "request": current.get("request")})
            continue
        emitted = _compare_snapshots(contract, baseline, current)
        findings.extend(emitted)

    # --- LLM-powered semantic reasoning (Phase61 moat upgrade) ---
    if mode == "safe_live" and findings:
        try:
            import json as _json
            llm_result = _llm_reason("temporal", {
                "prd_text": "", "api_schema": "", "observed_data": _json.dumps(executions[-5:] if "executions" in dir() else [], ensure_ascii=False, default=str)[:4000],
                "heuristic_findings": _json.dumps(findings[:15], ensure_ascii=False, default=str)[:4000],
            })
            semantic_hypotheses.extend(compile_unverified_semantic_hypotheses(
                (llm_result or {}).get("findings"),
                engine="temporal",
                type_field="temporal_regression_type",
            ))
        except Exception:
            pass

        entry = dict(prior_entry)
        entry["observation_count"] = int(entry.get("observation_count") or 1) + 1
        entry["last_observation"] = _snapshot_for_persistence(current)
        (baselines.get("contracts") or {})[contract_id] = entry
        executions.append({"contract_id": contract_id, "status": "executed", "baseline_established_at_utc": entry.get("baseline_established_at_utc"), "record_count": current.get("record_count"), "reported_total": current.get("reported_total"), "request": current.get("request"), "finding_count": len(emitted)})
    _save_baselines(output["baseline"], baselines)
    registry, findings = _update_registry(output["registry"], findings)
    result = {
        "phase": "phase50_temporal_data_regression_reasoning",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "summary": {**(profile.get("summary") or {}), "execution_mode": mode, "executed_contract_count": sum(1 for item in executions if item.get("status") == "executed"), "baseline_established_count": baseline_established, "temporal_data_regression_finding_count": len(findings), "persistent_temporal_data_regression_count": sum(1 for item in findings if (item.get("evidence_stability") or {}).get("persistent")), "memory_fingerprint_count": len((registry or {}).get("entries") or {})},
        "profile": profile,
        "executions": executions,
        "semantic_hypotheses": semantic_hypotheses, "llm_governance": {"status": "unverified_hypothesis_only", "does_not_affect_finding_counts": True, "requires_deterministic_replay": True}, "findings": findings,
        "baseline_summary": {"contract_baseline_count": len((baselines.get("contracts") or {})), "baseline_policy": "sticky_until_explicit_reset", "baseline_contains_no_raw_business_rows": True},
        "memory_summary": {"fingerprint_count": len((registry or {}).get("entries") or {}), "updated_at_utc": _now(), "learning_policy": "相同数据回归反例跨运行重复出现时提高置信度；未经人工确认始终保持 needs_human_review。"},
        "governance": {"execution_mode": mode, "live_requests_limited_to_get": True, "write_execution_disabled": True, "baseline_contains_no_raw_rows": True, "baseline_explicit_reset_required": True, "evidence_redacted_before_persistence": True, "uses_no_benchmark_answer_files": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    _write_json(output["out"] / "temporal_data_regression_run.json", result)
    _write_json(output["workspace"] / "temporal_data_regression_run.json", result)
    (output["out"] / "temporal_data_regression_run_report.html").write_text(render_temporal_data_regression_run_report(result), encoding="utf-8")
    return result


def _render_html(title: str, badge: str, subtitle: str, cards: str, body: str) -> str:
    return f"""<!doctype html><html><meta charset='utf-8'><title>{_html_escape(title)}</title><style>body{{font-family:Inter,Arial,sans-serif;background:#080b12;color:#e9eefc;margin:0;padding:28px}}.hero,.card,table{{background:#121824;border:1px solid #253149;border-radius:14px}}.hero{{padding:22px;margin-bottom:18px}}h1{{margin:0 0 8px}}.badge{{display:inline-block;background:#24375c;color:#b9d1ff;padding:4px 10px;border-radius:999px;font-size:12px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:14px 0}}.card{{padding:14px}}.n{{font-size:25px;font-weight:700}}table{{width:100%;border-collapse:collapse;overflow:hidden}}th,td{{padding:10px;border-bottom:1px solid #253149;text-align:left;vertical-align:top}}th{{color:#9eb8e9}}code{{white-space:pre-wrap}}</style><section class='hero'><span class='badge'>{_html_escape(badge)}</span><h1>{_html_escape(title)}</h1><p>{_html_escape(subtitle)}</p></section>{cards}{body}</html>"""


def render_temporal_data_regression_profile_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "<section class='grid'>" + "".join(f"<div class='card'><div>{_html_escape(label)}</div><div class='n'>{_html_escape(value)}</div></div>" for label, value in [("回归契约", summary.get("temporal_contract_count", 0)), ("不可变字段", summary.get("temporal_immutable_field_count", 0)), ("数值字段", summary.get("temporal_numeric_field_count", 0)), ("关键记录", summary.get("temporal_canary_count", 0))]) + "</section>"
    rows = "".join(f"<tr><td>{_html_escape(item.get('contract_id'))}</td><td>{_html_escape(item.get('resource'))}</td><td>{_html_escape(', '.join(item.get('identity_fields') or []))}</td><td>{_html_escape(', '.join(item.get('immutable_fields') or []))}</td><td>{_html_escape(', '.join(item.get('numeric_fields') or []))}</td></tr>" for item in (data.get("contracts") or []))
    return _render_html("Phase50 时间维度数据回归画像", "GET-only · 无原始业务行基线", "从 OpenAPI、PRD 和企业配置推导跨发布数据完整性契约。", cards, f"<table><thead><tr><th>契约</th><th>资源</th><th>业务主键</th><th>不可变字段</th><th>数值字段</th></tr></thead><tbody>{rows or '<tr><td colspan=5>未推导出契约</td></tr>'}</tbody></table>")


def render_temporal_data_regression_run_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "<section class='grid'>" + "".join(f"<div class='card'><div>{_html_escape(label)}</div><div class='n'>{_html_escape(value)}</div></div>" for label, value in [("已执行", summary.get("executed_contract_count", 0)), ("新建基线", summary.get("baseline_established_count", 0)), ("发现问题", summary.get("temporal_data_regression_finding_count", 0)), ("稳定复现", summary.get("persistent_temporal_data_regression_count", 0))]) + "</section>"
    rows = "".join(f"<tr><td>{_html_escape(item.get('severity'))}</td><td>{_html_escape(item.get('temporal_regression_type'))}</td><td>{_html_escape(item.get('title'))}</td><td>{_html_escape(item.get('actual'))}</td><td>{_html_escape((item.get('evidence_stability') or {}).get('observations', 1))}</td></tr>" for item in (data.get("findings") or []))
    return _render_html("Phase50 时间维度数据回归运行报告", str(summary.get("execution_mode") or "plan_only"), "基线只包含哈希和聚合指标；所有线上请求均为 GET。", cards, f"<table><thead><tr><th>级别</th><th>类型</th><th>问题</th><th>实际</th><th>观测次数</th></tr></thead><tbody>{rows or '<tr><td colspan=5>未发现已证伪的数据回归</td></tr>'}</tbody></table>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase50 temporal data regression reasoning")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--mode", default="plan_only", choices=["plan_only", "safe_live"])
    parser.add_argument("--reset-baseline", action="store_true")
    args = parser.parse_args(argv)
    result = run_temporal_data_regression_reasoning(args.project, options={"execution_mode": args.mode, "reset_baseline": args.reset_baseline})
    print(json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
