from __future__ import annotations

"""Phase43: business outcome validation for exports and data-bearing results.

This module turns a "successful export" into a verifiable business outcome.
It discovers export/download/report endpoints from OpenAPI and PRD text, then
validates the returned data file against durable invariants:

* unique business identity (or exact-row duplicate fallback);
* row shape and blank-key quality;
* source list ↔ export count, coverage and numeric aggregate consistency;
* valid export filters are actually reflected in exported rows;
* evidence is redacted and persisted with stable fingerprints.

The default is plan-only. ``safe_live`` performs only GET requests, never
creates export jobs or calls POST/PUT/PATCH/DELETE endpoints.  This makes it
safe to run in a production-like read-only environment while still catching
high-value business defects such as duplicated exported records.
"""

import csv
import hashlib
import io
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

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

EXPORT_WORDS = {
    "export", "download", "report", "extract", "dump", "file",
    "导出", "下载", "报表", "明细", "文件",
}
LIST_CONTAINER_KEYS = ("items", "data", "results", "records", "rows", "content", "list")
DETAIL_CONTAINER_KEYS = ("data", "result", "item", "record", "content")
DOWNLOAD_URL_KEYS = ("download_url", "downloadurl", "file_url", "fileurl", "url", "href", "link")
ID_EXACT_NAMES = {"id", "uuid", "guid", "code", "number", "no", "serial_no", "serialnumber"}
SENSITIVE_RE = re.compile(r"(?:password|passwd|secret|token|authorization|cookie|api[_-]?key|email|phone|mobile|id[_-]?card)", re.I)
DYNAMIC_RE = re.compile(r"(?:^|[_\-.])(time|timestamp|updated|created|trace|request|nonce|version|etag|cursor|next|last)(?:$|[_\-.])", re.I)
NUMERIC_RE = re.compile(r"(?:amount|total|price|cost|fee|tax|balance|quantity|qty|count|number|金额|数量|金额)", re.I)
EQUALITY_PARAM_RE = re.compile(r"(?:status|state|type|category|tenant|org|organization|owner|user|department|region|渠道|状态|类型|租户|部门|区域)", re.I)


# ---------------------------------------------------------------------------
# Small generic utilities
# ---------------------------------------------------------------------------

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


def _lower(value: Any) -> str:
    return str(value or "").strip().lower()


def _norm_name(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", _lower(value))


def _resource_key(path: str) -> str:
    parts = [p for p in str(path or "").split("/") if p and not p.startswith("{")]
    ignored = {"api", "v1", "v2", "v3", "public", "private", "internal", "open", "service", "services", "export", "exports", "download", "downloads", "report", "reports"}
    parts = [_lower(p) for p in parts if _lower(p) not in ignored]
    if not parts:
        return "resource"
    word = re.sub(r"[^a-z0-9_\-\u4e00-\u9fff]+", "", parts[-1]) or "resource"
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("s") and len(word) > 3:
        return word[:-1]
    return word


def _plural_match(left: str, right: str) -> bool:
    a, b = _norm_name(left), _norm_name(right)
    if not a or not b:
        return False
    return a == b or a.rstrip("s") == b.rstrip("s") or a in b or b in a


def _redact(value: Any, key: str = "", depth: int = 0) -> Any:
    if depth > 5:
        return "<truncated>"
    if SENSITIVE_RE.search(key or ""):
        return "***"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k), depth + 1) for k, v in list(value.items())[:50]}
    if isinstance(value, list):
        return [_redact(v, key, depth + 1) for v in value[:20]]
    if isinstance(value, str) and len(value) > 220:
        return value[:220] + "…"
    return value


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = _json(data).lower()
    markers = {"private_ground_truth", "ground_truth_bugs", "bug_sets", "enabled_bugs", "current_bug_set", "bug_instance_id"}
    found = sorted(marker for marker in markers if marker in text)
    return {"passed": not found, "leak_terms": found}


def _output_paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    return {
        "out": root / "platform_outputs" / project / "business_outcome_validation",
        "workspace": root / "platform_workspace" / project / "defect_discovery",
    }


# ---------------------------------------------------------------------------
# OpenAPI/PRD understanding
# ---------------------------------------------------------------------------

def _operation_text(operation: dict[str, Any]) -> str:
    return " ".join(
        str(operation.get(key) or "")
        for key in ("method", "path", "operation_id", "summary", "description", "tags")
    ).lower()


def _response_content_types(operation: dict[str, Any]) -> list[str]:
    raw = operation.get("raw_operation") or operation.get("raw") or {}
    responses = raw.get("responses") if isinstance(raw, dict) else {}
    values: list[str] = []
    for code, item in (responses or {}).items():
        if not str(code).startswith("2") or not isinstance(item, dict):
            continue
        values.extend(str(v).lower() for v in (item.get("content") or {}).keys())
    return sorted(set(values))


def _is_export_operation(operation: dict[str, Any]) -> bool:
    text = _operation_text(operation)
    if any(word in text for word in EXPORT_WORDS):
        return True
    content_types = _response_content_types(operation)
    return any("csv" in ct or "spreadsheet" in ct or "excel" in ct or "octet-stream" in ct for ct in content_types)


def _array_item_schema(schema: Any, components: dict[str, Any], depth: int = 0) -> tuple[str, dict[str, Any]]:
    if depth > 3:
        return "", {}
    node = _resolve_ref(schema, components)
    if not isinstance(node, dict):
        return "", {}
    typ = _schema_type(node, components)
    if typ == "array":
        item = node.get("items") or {}
        return "$", _resolve_ref(item, components) if isinstance(item, dict) else {}
    if typ == "object":
        props = node.get("properties") or {}
        if not isinstance(props, dict):
            return "", {}
        for key in LIST_CONTAINER_KEYS:
            child = _resolve_ref(props.get(key) or {}, components)
            if _schema_type(child, components) == "array":
                item = child.get("items") or {}
                return key, _resolve_ref(item, components) if isinstance(item, dict) else {}
        for key in DETAIL_CONTAINER_KEYS:
            child = _resolve_ref(props.get(key) or {}, components)
            if _schema_type(child, components) == "object":
                nested_key, nested = _array_item_schema(child, components, depth + 1)
                if nested:
                    return f"{key}.{nested_key}", nested
    return "", {}


def _is_collection_read(operation: dict[str, Any], components: dict[str, Any]) -> bool:
    if _lower(operation.get("method")) != "get":
        return False
    path = str(operation.get("path") or "")
    if "{" in path and "}" in path:
        return False
    _, item = _array_item_schema(operation.get("response_schema") or {}, components)
    return bool(item)


def _operation_parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
    raw = operation.get("raw_operation") or operation.get("raw") or {}
    return [p for p in (raw.get("parameters") or []) if isinstance(p, dict)] if isinstance(raw, dict) else []


def _parameter_schema(param: dict[str, Any], components: dict[str, Any]) -> dict[str, Any]:
    schema = param.get("schema") or {}
    return _resolve_ref(schema, components) if isinstance(schema, dict) else {}


def _source_may_paginate(operation: dict[str, Any] | None) -> bool:
    if not isinstance(operation, dict):
        return False
    parameters = operation.get("parameters") or _operation_parameters(operation)
    for param in parameters:
        if not isinstance(param, dict) or _lower(param.get("in")) != "query":
            continue
        name = _lower(param.get("name"))
        if re.search(r"(?:page|offset|limit|size|cursor|per_page|pagesize|页|条数)", name):
            return True
    return False


def _configured_contracts(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    section = cfg.get("business_outcome_validation") or cfg.get("business_outcome") or {}
    if not isinstance(section, dict):
        return []
    rows = section.get("export_contracts") or section.get("contracts") or []
    return [row for row in rows if isinstance(row, dict)]


def _pick_source(export_op: dict[str, Any], list_ops: list[dict[str, Any]]) -> dict[str, Any] | None:
    resource = _resource_key(str(export_op.get("path") or ""))
    scored: list[tuple[float, dict[str, Any]]] = []
    export_path = str(export_op.get("path") or "")
    for candidate in list_ops:
        path = str(candidate.get("path") or "")
        score = 0.0
        if _plural_match(resource, _resource_key(path)):
            score += 5.0
        if path.rstrip("/") and export_path.startswith(path.rstrip("/") + "/"):
            score += 4.0
        if _resource_key(path) in _operation_text(export_op):
            score += 1.0
        common = set(_norm_name(p) for p in path.split("/") if p) & set(_norm_name(p) for p in export_path.split("/") if p)
        score += min(2.0, len([x for x in common if x and x not in {"api", "v1", "v2", "v3"}]) * 0.5)
        if score > 0:
            scored.append((score, candidate))
    return max(scored, key=lambda row: (row[0], -len(str(row[1].get("path") or ""))))[1] if scored else None


def _identity_fields_from_schema(schema: dict[str, Any], resource: str, configured: list[str] | None = None) -> list[str]:
    if configured:
        return [str(x) for x in configured if str(x).strip()]
    props = schema.get("properties") if isinstance(schema, dict) else {}
    names = list(props.keys()) if isinstance(props, dict) else []
    target = _norm_name(resource)
    scored: list[tuple[int, str]] = []
    for name in names:
        norm = _norm_name(name)
        score = 0
        if norm == target + "id" or norm == target + "no" or norm == target + "number" or norm == target + "code":
            score += 100
        if norm in ID_EXACT_NAMES:
            score += 90
        if norm.endswith("id") and target and target in norm:
            score += 80
        if norm.endswith("no") and target and target in norm:
            score += 70
        if norm.endswith("code") and target and target in norm:
            score += 60
        if norm.endswith("id") and not any(word in norm for word in ("user", "customer", "tenant", "product", "parent", "owner", "creator", "operator")):
            score += 30
        if score:
            scored.append((score, str(name)))
    return [name for _, name in sorted(scored, key=lambda item: (-item[0], item[1]))[:3]]


def build_export_contracts(openapi: dict[str, Any], cfg: dict[str, Any], prd_text: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Discover actionable export contracts and PRD-only candidates.

    Contracts may be explicitly configured for complex enterprise APIs.  OpenAPI
    discovery is intentionally conservative: only collection GET endpoints are
    used as source-of-truth comparisons.
    """
    components = (openapi.get("components") or {}) if isinstance(openapi, dict) else {}
    ops = _operations(openapi if isinstance(openapi, dict) else {})
    export_ops = [op for op in ops if _is_export_operation(op)]
    list_ops = [op for op in ops if _is_collection_read(op, components)]
    overrides = _configured_contracts(cfg)
    by_export_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in overrides:
        path = str(row.get("export_path") or row.get("path") or "")
        method = str(row.get("export_method") or row.get("method") or "GET").upper()
        if path:
            by_export_key[(method, path)] = row

    contracts: list[dict[str, Any]] = []
    for index, export_op in enumerate(export_ops, start=1):
        path, method = str(export_op.get("path") or ""), str(export_op.get("method") or "GET").upper()
        override = by_export_key.get((method, path), {})
        source = None
        source_path = str(override.get("source_path") or "")
        if source_path:
            source_method = str(override.get("source_method") or "GET").upper()
            source = next((op for op in ops if str(op.get("path")) == source_path and str(op.get("method")).upper() == source_method), None)
        source = source or _pick_source(export_op, list_ops)
        resource = str(override.get("resource") or _resource_key(path))
        source_item_schema: dict[str, Any] = {}
        if source:
            _, source_item_schema = _array_item_schema(source.get("response_schema") or {}, components)
        identity_fields = _identity_fields_from_schema(source_item_schema, resource, override.get("identity_fields"))
        contracts.append({
            "contract_id": f"BOV_EXPORT_{index:03d}",
            "resource": resource,
            "export": {"method": method, "path": path, "operation_id": export_op.get("operation_id"), "summary": export_op.get("summary") or ""},
            "source": ({"method": str(source.get("method") or "GET").upper(), "path": str(source.get("path") or ""), "operation_id": source.get("operation_id"), "summary": source.get("summary") or ""} if source else None),
            "identity_fields": identity_fields,
            "sample_query": dict(override.get("sample_query") or override.get("query") or {}),
            "field_mappings": dict(override.get("field_mappings") or {}),
            "aggregate_fields": [str(x) for x in (override.get("aggregate_fields") or []) if str(x).strip()],
            "source_required": bool(source),
            "source_is_complete": bool(override.get("source_is_complete")) or bool(source and not _source_may_paginate(source)),
            "execution_policy": "safe_read_only" if method == "GET" else "candidate_only",
            "evidence": {"detected_by": "OpenAPI export/download/report semantic", "content_types": _response_content_types(export_op)},
        })

    # Explicit config should work even when OpenAPI is incomplete.
    known = {(c["export"]["method"], c["export"]["path"]) for c in contracts}
    for row in overrides:
        path = str(row.get("export_path") or row.get("path") or "")
        method = str(row.get("export_method") or row.get("method") or "GET").upper()
        if not path or (method, path) in known:
            continue
        source_path = str(row.get("source_path") or "")
        source_method = str(row.get("source_method") or "GET").upper()
        source = next((op for op in ops if str(op.get("path")) == source_path and str(op.get("method")).upper() == source_method), None)
        resource = str(row.get("resource") or _resource_key(path))
        source_schema: dict[str, Any] = {}
        if source:
            _, source_schema = _array_item_schema(source.get("response_schema") or {}, components)
        contracts.append({
            "contract_id": f"BOV_EXPORT_{len(contracts)+1:03d}",
            "resource": resource,
            "export": {"method": method, "path": path, "operation_id": row.get("operation_id"), "summary": row.get("title") or "配置导出契约"},
            "source": ({"method": source_method, "path": source_path, "operation_id": source.get("operation_id") if source else None, "summary": source.get("summary") if source else ""} if source_path else None),
            "identity_fields": _identity_fields_from_schema(source_schema, resource, row.get("identity_fields")),
            "sample_query": dict(row.get("sample_query") or row.get("query") or {}),
            "field_mappings": dict(row.get("field_mappings") or {}),
            "aggregate_fields": [str(x) for x in (row.get("aggregate_fields") or []) if str(x).strip()],
            "source_required": bool(source_path),
            "source_is_complete": bool(row.get("source_is_complete")) or bool(source and not _source_may_paginate(source)),
            "execution_policy": "safe_read_only" if method == "GET" else "candidate_only",
            "evidence": {"detected_by": "enterprise_config"},
        })

    candidates: list[dict[str, Any]] = []
    prd_lower = str(prd_text or "").lower()
    mentions_export = any(word in prd_lower for word in EXPORT_WORDS)
    if mentions_export and not contracts:
        candidates.append({
            "candidate_id": "BOV_PRD_EXPORT_UNMAPPED",
            "title": "需求存在导出/下载能力，但 OpenAPI 未映射到可审计导出接口",
            "severity": "P2",
            "risk_type": "export_data_quality",
            "execution_policy": "candidate_only",
            "expected": "为导出功能补充接口契约、唯一键和数据源映射后进行结果审计。",
        })
    return contracts, candidates


def _probe(contract: dict[str, Any], number: int, kind: str, title: str, expected: str, severity: str = "P1") -> dict[str, Any]:
    export = contract.get("export") or {}
    return {
        "probe_id": f"BOV_PROBE_{number:04d}",
        "source": "business_outcome_validation",
        "business_outcome_type": kind,
        "risk_type": "export_data_quality" if kind in {"duplicate_identity", "exact_duplicate_row", "source_coverage", "filter_scope", "aggregate_consistency"} else "data_consistency",
        "title": title,
        "severity": severity,
        "method": export.get("method") or "GET",
        "path": export.get("path") or "",
        "actor": "normal_user",
        "expected": expected,
        "execution_policy": contract.get("execution_policy") or "candidate_only",
        "destructive": False,
        "contract_id": contract.get("contract_id"),
        "resource": contract.get("resource"),
        "identity_fields": contract.get("identity_fields") or [],
        "source_endpoint": contract.get("source"),
        "sample_query": contract.get("sample_query") or {},
    }


def generate_business_outcome_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    prd_path = config_paths(project_id, root)["input_dir"] / "prd.md"
    contracts, candidates = build_export_contracts(openapi, cfg, _read_text(prd_path))
    limit = int(max_count or 80)
    probes: list[dict[str, Any]] = []
    for contract in contracts:
        resource = str(contract.get("resource") or "数据")
        identity = ", ".join(contract.get("identity_fields") or []) or "业务唯一键/完整行"
        for kind, title, expected, severity in [
            ("duplicate_identity", f"导出结果唯一性：{resource}", f"导出文件中 {identity} 不应重复；无可靠唯一键时完整业务行不应重复。", "P1"),
            ("source_coverage", f"导出与源列表覆盖一致性：{resource}", "同一筛选条件下，导出数量、关键标识集合应与源列表/总数一致。", "P1"),
            ("filter_scope", f"导出筛选条件生效：{resource}", "导出数据必须满足实际传入的合法筛选条件，不能静默忽略。", "P1"),
            ("aggregate_consistency", f"导出汇总一致性：{resource}", "同一记录集的金额、数量等可加总字段在导出与源数据间应一致。", "P1"),
            ("schema_quality", f"导出行质量：{resource}", "导出表头稳定、关键字段非空、无不可解析行。", "P2"),
        ]:
            probes.append(_probe(contract, len(probes) + 1, kind, title, expected, severity))
            if len(probes) >= limit:
                return probes
    for candidate in candidates:
        probes.append({
            "probe_id": f"BOV_PROBE_{len(probes)+1:04d}", "source": "business_outcome_validation", "business_outcome_type": "contract_gap",
            "risk_type": candidate.get("risk_type"), "title": candidate.get("title"), "severity": candidate.get("severity"),
            "method": "GET", "path": "", "actor": "normal_user", "expected": candidate.get("expected"),
            "execution_policy": "candidate_only", "destructive": False,
        })
    return probes[:limit]


# ---------------------------------------------------------------------------
# File and HTTP handling
# ---------------------------------------------------------------------------

def _http_get(url: str, token: str | None, timeout: int, max_bytes: int) -> dict[str, Any]:
    headers = {"Accept": "application/json, text/csv, application/octet-stream, application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, */*"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, method="GET", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read(max_bytes + 1)
            truncated = len(raw) > max_bytes
            if truncated:
                raw = raw[:max_bytes]
            response_headers = {str(k).lower(): str(v) for k, v in response.headers.items()}
            return {
                "ok": 200 <= int(response.status) < 400,
                "status_code": int(response.status),
                "body": raw,
                "headers": response_headers,
                "url": response.geturl(),
                "truncated": truncated,
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        raw = b""
        try:
            raw = exc.read(min(max_bytes, 300_000))
        except Exception:
            pass
        return {"ok": False, "status_code": int(exc.code), "body": raw, "headers": {}, "url": url, "truncated": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "status_code": None, "body": b"", "headers": {}, "url": url, "truncated": False, "error": str(exc)}


def _response_filename(headers: dict[str, Any], url: str) -> str:
    disposition = str((headers or {}).get("content-disposition") or "")
    match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.I)
    if match:
        return urllib.parse.unquote(match.group(1).strip())
    return urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]


def _guess_format(body: bytes, headers: dict[str, Any], url: str) -> str:
    content_type = _lower((headers or {}).get("content-type"))
    filename = _response_filename(headers or {}, url).lower()
    if "csv" in content_type or filename.endswith(".csv"):
        return "csv"
    if "spreadsheet" in content_type or "excel" in content_type or filename.endswith(".xlsx"):
        return "xlsx"
    if "json" in content_type or filename.endswith(".json"):
        return "json"
    stripped = body.lstrip()
    if stripped.startswith((b"{", b"[")):
        return "json"
    if body[:2] == b"PK":
        return "xlsx"
    if b"\n" in body and (b"," in body[:5000] or b"\t" in body[:5000] or b";" in body[:5000]):
        return "csv"
    return "unknown"


def _dedupe_headers(headers: list[str]) -> list[str]:
    counts: dict[str, int] = {}
    result: list[str] = []
    for index, value in enumerate(headers, start=1):
        key = str(value or "").strip() or f"column_{index}"
        count = counts.get(key, 0) + 1
        counts[key] = count
        result.append(key if count == 1 else f"{key}_{count}")
    return result


def _parse_csv(body: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    text = body.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:10_000], delimiters=",;\t|")
    except Exception:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    rows = list(reader)
    if not rows:
        return [], {"format": "csv", "header": [], "parse_error": None}
    header = _dedupe_headers([str(x).strip() for x in rows[0]])
    records = []
    for row in rows[1:]:
        if not any(str(cell).strip() for cell in row):
            continue
        data = {header[i]: row[i] if i < len(row) else "" for i in range(len(header))}
        records.append(data)
    return records, {"format": "csv", "header": header, "parse_error": None}


def _xlsx_column_index(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref or "")
    if not letters:
        return 0
    out = 0
    for char in letters.group(1):
        out = out * 26 + (ord(char) - 64)
    return max(0, out - 1)


def _xlsx_text(cell: ET.Element, shared: list[str], ns: dict[str, str]) -> str:
    cell_type = cell.attrib.get("t")
    value = cell.find("x:v", ns)
    if cell_type == "s" and value is not None:
        try:
            return shared[int(value.text or "0")]
        except Exception:
            return ""
    if cell_type == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(".//x:t", ns))
    return value.text if value is not None and value.text is not None else ""


def _parse_xlsx(body: bytes) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ns = {"x": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = set(archive.namelist())
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.text or "" for node in entry.findall(".//x:t", ns)) for entry in root.findall("x:si", ns)]
        sheet_names = sorted(name for name in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", name))
        if not sheet_names:
            raise ValueError("xlsx_missing_worksheet")
        root = ET.fromstring(archive.read(sheet_names[0]))
        matrix: list[list[str]] = []
        for row in root.findall(".//x:sheetData/x:row", ns):
            cells: dict[int, str] = {}
            for cell in row.findall("x:c", ns):
                cells[_xlsx_column_index(cell.attrib.get("r") or "A1")] = _xlsx_text(cell, shared, ns)
            if not cells:
                continue
            matrix.append([cells.get(i, "") for i in range(max(cells) + 1)])
        if not matrix:
            return [], {"format": "xlsx", "header": [], "parse_error": None}
        header = _dedupe_headers(matrix[0])
        records = []
        for row in matrix[1:]:
            if not any(str(cell).strip() for cell in row):
                continue
            records.append({header[i]: row[i] if i < len(row) else "" for i in range(len(header))})
        return records, {"format": "xlsx", "header": header, "parse_error": None}


def _extract_json_records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if not isinstance(value, dict):
        return []
    for key in LIST_CONTAINER_KEYS:
        child = value.get(key)
        if isinstance(child, list):
            return [item for item in child if isinstance(item, dict)]
        if isinstance(child, dict):
            nested = _extract_json_records(child)
            if nested:
                return nested
    return []


def _extract_total(value: Any) -> int | None:
    if isinstance(value, dict):
        for key in ("total", "total_count", "totalCount", "count", "records_total"):
            raw = value.get(key)
            try:
                if raw is not None:
                    return int(float(raw))
            except Exception:
                pass
        for key in DETAIL_CONTAINER_KEYS:
            child = value.get(key)
            if isinstance(child, dict):
                total = _extract_total(child)
                if total is not None:
                    return total
    return None


def parse_export_bytes(body: bytes, headers: dict[str, Any] | None = None, url: str = "") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    headers = headers or {}
    fmt = _guess_format(body, headers, url)
    try:
        if fmt == "csv":
            return _parse_csv(body)
        if fmt == "xlsx":
            return _parse_xlsx(body)
        if fmt == "json":
            decoded = json.loads(body.decode("utf-8-sig", errors="replace"))
            return _extract_json_records(decoded), {"format": "json", "header": sorted({str(k) for row in _extract_json_records(decoded) for k in row})[:500], "parse_error": None, "raw_json": decoded}
        # JSON Lines fallback.
        text = body.decode("utf-8-sig", errors="replace")
        rows = [json.loads(line) for line in text.splitlines() if line.strip().startswith("{")]
        if rows:
            return [row for row in rows if isinstance(row, dict)], {"format": "jsonl", "header": sorted({str(k) for row in rows if isinstance(row, dict) for k in row})[:500], "parse_error": None}
        return [], {"format": "unknown", "header": [], "parse_error": "unsupported_export_format"}
    except Exception as exc:
        return [], {"format": fmt, "header": [], "parse_error": str(exc)}


def _resolve_download_payload(response: dict[str, Any], base_url: str, token: str | None, timeout: int, max_bytes: int) -> dict[str, Any]:
    """Follow a GET-returned download URL without invoking any job-creation API."""
    body = response.get("body") or b""
    headers = response.get("headers") or {}
    fmt = _guess_format(body, headers, str(response.get("url") or ""))
    if fmt != "json":
        return response
    try:
        decoded = json.loads(body.decode("utf-8-sig", errors="replace"))
    except Exception:
        return response
    stack: list[Any] = [decoded]
    url_value = None
    while stack and url_value is None:
        node = stack.pop(0)
        if isinstance(node, dict):
            for key, value in node.items():
                if _norm_name(key) in {_norm_name(k) for k in DOWNLOAD_URL_KEYS} and isinstance(value, str) and value.strip():
                    url_value = value.strip()
                    break
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(node, list):
            stack.extend(node[:10])
    if not url_value:
        return response
    url = url_value if re.match(r"^https?://", url_value, re.I) else _join_url(base_url, url_value)
    downloaded = _http_get(url, token, timeout, max_bytes)
    downloaded["downloaded_from"] = str(response.get("url") or "")
    return downloaded


# ---------------------------------------------------------------------------
# Business invariants
# ---------------------------------------------------------------------------

def _value(row: dict[str, Any], field: str, mappings: dict[str, str] | None = None) -> Any:
    if not isinstance(row, dict):
        return None
    target = str((mappings or {}).get(field) or field)
    if target in row:
        return row.get(target)
    target_norm = _norm_name(target)
    for key, value in row.items():
        if _norm_name(key) == target_norm:
            return value
    return None


def _canon(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value).strip()


def _identity_from_rows(records: list[dict[str, Any]], contract: dict[str, Any]) -> tuple[str | None, list[str]]:
    configured = [str(x) for x in (contract.get("identity_fields") or []) if str(x).strip()]
    headers = sorted({str(k) for row in records[:50] if isinstance(row, dict) for k in row})
    resource = _norm_name(contract.get("resource"))
    candidate_order = configured[:]
    resource_candidates = [f"{resource}_id", f"{resource}id", f"{resource}_no", f"{resource}no", f"{resource}_code", f"{resource}code", "id", "uuid", "guid", "code", "number", "no"]
    candidate_order.extend(resource_candidates)
    seen: set[str] = set()
    mappings = contract.get("field_mappings") or {}
    for wanted in candidate_order:
        raw_candidates = [str(wanted)]
        mapped = mappings.get(str(wanted)) if isinstance(mappings, dict) else None
        if mapped:
            raw_candidates.append(str(mapped))
        norms = [_norm_name(item) for item in raw_candidates if _norm_name(item)]
        if not norms or all(norm in seen for norm in norms):
            continue
        seen.update(norms)
        actual = next((h for h in headers if _norm_name(h) in norms), None)
        if not actual:
            continue
        values = [_canon(_value(row, actual, {})) for row in records]
        filled = [value for value in values if value]
        if len(filled) >= 2 and len(set(filled)) >= max(1, len(filled) // 3):
            return actual, [actual]
    return None, []


def _row_digest(row: dict[str, Any]) -> str:
    stable = {str(key): value for key, value in row.items() if not DYNAMIC_RE.search(str(key)) and not SENSITIVE_RE.search(str(key))}
    return _short_hash(stable)


def _duplicate_groups(records: list[dict[str, Any]], key_field: str | None, contract: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(records, start=2):  # spreadsheet line number; header=1
        key = _canon(_value(row, key_field or "", contract.get("field_mappings"))) if key_field else _row_digest(row)
        if key:
            groups[key].append(index)
    out: list[dict[str, Any]] = []
    for value, lines in groups.items():
        if len(lines) > 1:
            out.append({"key_hash": _short_hash(value), "row_numbers": lines[:12], "count": len(lines)})
    return sorted(out, key=lambda item: (-int(item["count"]), item["key_hash"]))


def _blank_key_rows(records: list[dict[str, Any]], key_field: str | None, contract: dict[str, Any]) -> list[int]:
    if not key_field:
        return []
    return [index for index, row in enumerate(records, start=2) if not _canon(_value(row, key_field, contract.get("field_mappings")))]


def _numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    text = re.sub(r"^[¥$€£]", "", text)
    try:
        return float(text)
    except Exception:
        return None


def _numeric_fields(records: list[dict[str, Any]], contract: dict[str, Any]) -> list[str]:
    explicit = [str(x) for x in (contract.get("aggregate_fields") or []) if str(x).strip()]
    if explicit:
        return explicit
    headers = sorted({str(k) for row in records[:100] if isinstance(row, dict) for k in row})
    fields: list[str] = []
    for header in headers:
        if not NUMERIC_RE.search(header):
            continue
        numbers = [_numeric(_value(row, header, contract.get("field_mappings"))) for row in records[:200]]
        if sum(value is not None for value in numbers) >= 2:
            fields.append(header)
    return fields[:12]


def _sum_field(records: list[dict[str, Any]], field: str, contract: dict[str, Any]) -> float | None:
    values = [_numeric(_value(row, field, contract.get("field_mappings"))) for row in records]
    numeric = [value for value in values if value is not None]
    return round(sum(numeric), 6) if numeric else None


def _query_filter_violations(records: list[dict[str, Any]], query: dict[str, Any], contract: dict[str, Any]) -> list[dict[str, Any]]:
    if not query:
        return []
    violations: list[dict[str, Any]] = []
    for param, expected in query.items():
        if expected is None or isinstance(expected, (list, dict)) or not EQUALITY_PARAM_RE.search(str(param)):
            continue
        actual_field = next((key for row in records[:50] for key in row if _norm_name(key) == _norm_name(param)), None)
        if not actual_field:
            continue
        mismatch_lines = [index for index, row in enumerate(records, start=2) if _canon(_value(row, actual_field, contract.get("field_mappings"))) != _canon(expected)]
        if mismatch_lines:
            violations.append({"parameter": param, "field": actual_field, "expected": _redact(expected, param), "mismatch_count": len(mismatch_lines), "sample_row_numbers": mismatch_lines[:10]})
    return violations


def _source_records(body: bytes) -> tuple[list[dict[str, Any]], int | None, Any]:
    try:
        decoded = json.loads(body.decode("utf-8-sig", errors="replace"))
    except Exception:
        return [], None, None
    return _extract_json_records(decoded), _extract_total(decoded), decoded


def _set_of_ids(records: list[dict[str, Any]], field: str | None, contract: dict[str, Any]) -> set[str]:
    if not field:
        return set()
    return {_canon(_value(row, field, contract.get("field_mappings"))) for row in records if _canon(_value(row, field, contract.get("field_mappings")))}


def _finding(contract: dict[str, Any], kind: str, title: str, expected: str, actual: str, evidence: dict[str, Any], confidence: float, severity: str = "P1") -> dict[str, Any]:
    fingerprint_seed = {"contract": contract.get("contract_id"), "kind": kind, "path": (contract.get("export") or {}).get("path"), "signature": evidence.get("signature") or evidence.get("duplicate_groups") or evidence.get("field") or ""}
    return {
        "issue_id": f"BOV_ISSUE_{_short_hash(fingerprint_seed).upper()}",
        "fingerprint": _hash(fingerprint_seed),
        "title": title,
        "risk_type": "export_data_quality",
        "business_outcome_type": kind,
        "severity": severity,
        "confidence": round(float(confidence), 3),
        "status": "needs_human_review",
        "expected": expected,
        "actual": actual,
        "contract_id": contract.get("contract_id"),
        "resource": contract.get("resource"),
        "evidence": _redact(evidence),
    }


def audit_export_records(contract: dict[str, Any], records: list[dict[str, Any]], meta: dict[str, Any], source_records: list[dict[str, Any]] | None = None, source_total: int | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Audit parsed export rows. Pure and directly unit-testable."""
    source_records = source_records or []
    findings: list[dict[str, Any]] = []
    key_field, _ = _identity_from_rows(records, contract)
    duplicate_groups = _duplicate_groups(records, key_field, contract)
    if duplicate_groups:
        kind = "duplicate_identity" if key_field else "exact_duplicate_row"
        title = f"导出数据存在重复{'唯一键' if key_field else '完整业务行'}：{contract.get('resource') or '资源'}"
        actual = f"导出 {len(records)} 行，其中 {len(duplicate_groups)} 组重复；{'唯一字段 ' + key_field if key_field else '未识别可靠唯一键，按完整非动态字段行比对'}。"
        findings.append(_finding(contract, kind, title, "同一导出结果中每个业务对象只能出现一次。", actual, {"field": key_field, "duplicate_groups": duplicate_groups[:8], "record_count": len(records), "format": meta.get("format")}, 0.95 if key_field else 0.84))
    blank_rows = _blank_key_rows(records, key_field, contract)
    if blank_rows:
        findings.append(_finding(contract, "blank_identity", f"导出关键标识为空：{contract.get('resource') or '资源'}", "导出行的业务标识应非空。", f"字段 {key_field} 在 {len(blank_rows)} 行为空。", {"field": key_field, "sample_row_numbers": blank_rows[:10], "signature": f"blank:{key_field}"}, 0.82, "P2"))
    if meta.get("parse_error"):
        findings.append(_finding(contract, "unparseable_export", f"导出文件无法解析：{contract.get('resource') or '资源'}", "导出文件应为可读取的 CSV/Excel/JSON 数据文件。", f"解析失败：{meta.get('parse_error')}", {"format": meta.get("format"), "signature": str(meta.get("parse_error"))}, 0.78, "P1"))

    filter_violations = _query_filter_violations(records, contract.get("sample_query") or {}, contract)
    if filter_violations:
        findings.append(_finding(contract, "filter_scope", f"导出忽略或错误应用筛选条件：{contract.get('resource') or '资源'}", "导出行必须满足请求中的合法筛选条件。", f"发现 {sum(row['mismatch_count'] for row in filter_violations)} 行不满足筛选。", {"filter_violations": filter_violations, "signature": _short_hash(filter_violations)}, 0.9))

    coverage: dict[str, Any] = {"source_record_count": len(source_records), "source_total": source_total, "export_record_count": len(records), "export_truncated": bool(meta.get("truncated"))}
    source_contract = {**contract, "field_mappings": {}}
    source_key, _ = _identity_from_rows(source_records, source_contract)
    export_key = key_field
    source_complete = bool(contract.get("source_is_complete")) or (source_total is not None and source_total == len(source_records))
    # A bounded download can still prove a duplicate or an ignored filter, but it is not a complete population for count/coverage checks.
    if not meta.get("truncated") and source_total is not None and source_total >= 0 and source_total <= 20_000 and source_total != len(records):
        findings.append(_finding(contract, "source_count_mismatch", f"导出数量与源列表总数不一致：{contract.get('resource') or '资源'}", "同筛选条件下，导出行数应与源列表 total 一致。", f"源列表 total={source_total}，导出行数={len(records)}。", {**coverage, "signature": f"total:{source_total}:{len(records)}"}, 0.9))
    export_ids = _set_of_ids(records, export_key, contract)
    source_ids = _set_of_ids(source_records, source_key, source_contract)
    if not meta.get("truncated") and export_key and source_key and source_ids and export_ids:
        missing = sorted(source_ids - export_ids)
        # Only declare extras when the source response is complete, otherwise it may be a page.
        if missing:
            findings.append(_finding(contract, "source_coverage", f"源列表记录未出现在导出文件：{contract.get('resource') or '资源'}", "源列表中的同筛选记录应全部出现在导出文件。", f"至少 {len(missing)} 个源记录未导出。", {**coverage, "export_field": export_key, "source_field": source_key, "missing_key_hashes": [_short_hash(item) for item in missing[:12]], "signature": f"missing:{source_key}:{_short_hash(missing[:20])}"}, 0.88))
        if source_complete:
            extra = sorted(export_ids - source_ids)
            if extra:
                findings.append(_finding(contract, "export_extra_records", f"导出包含源列表不存在的记录：{contract.get('resource') or '资源'}", "导出结果不应包含当前筛选范围外的记录。", f"发现 {len(extra)} 个导出标识不在源列表中。", {**coverage, "export_field": export_key, "source_field": source_key, "extra_key_hashes": [_short_hash(item) for item in extra[:12]], "signature": f"extra:{source_key}:{_short_hash(extra[:20])}"}, 0.86))
        if source_complete:
            for field in _numeric_fields(records, contract):
                export_sum = _sum_field(records, field, contract)
                source_sum = _sum_field(source_records, field, source_contract)
                if export_sum is not None and source_sum is not None and abs(export_sum - source_sum) > 0.0001:
                    findings.append(_finding(contract, "aggregate_consistency", f"导出汇总字段与源数据不一致：{contract.get('resource') or '资源'}", "同一记录集的金额、数量等汇总值应一致。", f"字段 {field}：源数据汇总={source_sum}，导出汇总={export_sum}。", {"field": field, "source_sum": source_sum, "export_sum": export_sum, "signature": f"sum:{field}:{source_sum}:{export_sum}"}, 0.9))

    audit = {
        "record_count": len(records),
        "format": meta.get("format"),
        "headers": (meta.get("header") or [])[:100],
        "identity_field": key_field,
        "duplicate_group_count": len(duplicate_groups),
        "source_record_count": len(source_records),
        "source_total": source_total,
        "finding_count": len(findings),
    }
    return findings, audit


# ---------------------------------------------------------------------------
# Project profile/run lifecycle
# ---------------------------------------------------------------------------

def build_business_outcome_profile(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    paths = config_paths(project, root)
    cfg = load_real_project_config(project, root)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    openapi = openapi if isinstance(openapi, dict) else {}
    contracts, prd_candidates = build_export_contracts(openapi, cfg, _read_text(paths["input_dir"] / "prd.md"))
    probes = generate_business_outcome_probes(openapi, cfg, project, root, int(options.get("preview_probe_count") or 100))
    result = {
        "phase": "phase43_business_outcome_validation",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "contracts": contracts,
        "prd_candidates": prd_candidates,
        "preview_probes": probes,
        "summary": {
            "export_contract_count": len(contracts),
            "mapped_source_count": len([c for c in contracts if c.get("source")]),
            "safe_read_only_contract_count": len([c for c in contracts if c.get("execution_policy") == "safe_read_only"]),
            "candidate_only_contract_count": len([c for c in contracts if c.get("execution_policy") == "candidate_only"]),
            "preview_probe_count": len(probes),
            "prd_contract_gap_count": len(prd_candidates),
        },
        "governance": {"default_execution": "plan_only", "safe_live_only_uses_GET": True, "export_job_creation_disabled": True, "stores_redacted_evidence_only": True, "uses_no_benchmark_answer_files": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    output = _output_paths(project, root)
    _write_json(output["out"] / "business_outcome_profile.json", result)
    _write_json(output["workspace"] / "business_outcome_profile.json", result)
    (output["out"] / "business_outcome_profile_report.html").parent.mkdir(parents=True, exist_ok=True)
    (output["out"] / "business_outcome_profile_report.html").write_text(render_business_outcome_profile_report(result), encoding="utf-8")
    return result


def load_business_outcome_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    path = _output_paths(_safe_project_id(project_id), root)["workspace"] / "business_outcome_profile.json"
    data = _load_json(path, {})
    return data if isinstance(data, dict) and data else None


def _normal_token(cfg: dict[str, Any], project: str, root: Path, timeout: int) -> str | None:
    accounts = _load_json(config_paths(project, root)["input_dir"] / "test_accounts.json", {})
    normal = (accounts or {}).get("normal_user") or (accounts or {}).get("normal") or (accounts or {}).get("user") or {}
    if normal.get("token"):
        return str(normal.get("token"))
    username = normal.get("username") or normal.get("user")
    password = normal.get("password") or normal.get("pass")
    base_url = str(cfg.get("base_url") or "")
    login_api = str(cfg.get("login_api") or "")
    if not (base_url and username and password):
        return None
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    # A hardcoded "username" key returns 401 against any system that authenticates
    # by email, which reads here as "no token available" and silently degrades every
    # authenticated probe downstream. Try the declared shape, else probe.
    from .enterprise_credential_manager import (
        COMMON_LOGIN_PATH_CANDIDATES,
        _identity_field_candidates,
    )

    # Declared path first, then the shared generic probe safety net — an
    # undeclared project is discovered, never fabricated (the config layer
    # leaves login_api empty when the operator declared nothing).
    login_paths = [login_api] + list(COMMON_LOGIN_PATH_CANDIDATES) if login_api else list(COMMON_LOGIN_PATH_CANDIDATES)
    seen_paths: set[str] = set()
    unique_login_paths: list[str] = []
    for p in login_paths:
        key = str(p).strip().strip("/")
        if key and key not in seen_paths:
            seen_paths.add(key)
            unique_login_paths.append(key)
    for identity_field in _identity_field_candidates(
        username, str(normal.get("username_field") or cfg.get("username_field") or "")
    ):
        body = json.dumps(
            {identity_field: username, "password": password}, ensure_ascii=False
        ).encode("utf-8")
        for login_path in unique_login_paths:
            try:
                req = urllib.request.Request(_join_url(base_url, login_path), data=body, method="POST", headers=headers)
                with urllib.request.urlopen(req, timeout=timeout) as response:
                    data = json.loads(response.read(300_000).decode("utf-8", errors="replace"))
                    for key in ("token", "access_token", "jwt"):
                        if data.get(key):
                            return str(data[key])
            except Exception:
                continue
    return None


def _build_url(base_url: str, path: str, query: dict[str, Any] | None = None) -> str:
    url = _join_url(base_url, path)
    clean = {str(k): str(v) for k, v in (query or {}).items() if v is not None and str(v) != ""}
    return url + ("?" + urllib.parse.urlencode(clean, doseq=True) if clean else "")


def _filter_query_from_contract(contract: dict[str, Any]) -> dict[str, Any]:
    return {str(k): v for k, v in (contract.get("sample_query") or {}).items() if v is not None}


def _update_registry(path: Path, findings: list[dict[str, Any]]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    state = _load_json(path, {})
    state = state if isinstance(state, dict) else {}
    entries = state.get("entries") if isinstance(state.get("entries"), dict) else {}
    now = _now()
    enriched: list[dict[str, Any]] = []
    for finding in findings:
        fingerprint = str(finding.get("fingerprint") or _hash(finding))
        old = entries.get(fingerprint) if isinstance(entries.get(fingerprint), dict) else {}
        observations = int(old.get("observations") or 0) + 1
        entry = {"fingerprint": fingerprint, "first_seen": old.get("first_seen") or now, "last_seen": now, "observations": observations, "last_title": finding.get("title"), "last_actual": finding.get("actual")}
        entries[fingerprint] = entry
        enriched.append({**finding, "evidence_stability": {"observations": observations, "first_seen": entry["first_seen"], "last_seen": now, "persistent": observations >= 2}, "confidence": round(min(0.98, float(finding.get("confidence") or 0) + (0.04 if observations >= 2 else 0)), 3)})
    state = {"updated_at": now, "entries": entries}
    _write_json(path, state)
    return state, enriched


def run_business_outcome_validation(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    profile = build_business_outcome_profile(project, root, options)
    execution_mode = str(options.get("execution_mode") or cfg.get("business_outcome_execution_mode") or "plan_only").lower()
    if execution_mode not in {"plan_only", "safe_live"}:
        execution_mode = "plan_only"
    section = cfg.get("business_outcome_validation") or cfg.get("business_outcome") or {}
    section = section if isinstance(section, dict) else {}
    max_bytes = int(options.get("max_export_bytes") or section.get("max_export_bytes") or 12_000_000)
    timeout = int(cfg.get("request_timeout_seconds") or 10)
    base_url = str(cfg.get("base_url") or "")
    token = _normal_token(cfg, project, root, timeout) if execution_mode == "safe_live" and base_url else None
    executions: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    semantic_hypotheses: list[dict[str, Any]] = []
    for contract in profile.get("contracts") or []:
        export = contract.get("export") or {}
        if execution_mode != "safe_live" or not base_url:
            executions.append({"contract_id": contract.get("contract_id"), "status": "planned", "reason": "plan_only_or_missing_base_url"})
            continue
        if str(export.get("method") or "GET").upper() != "GET":
            executions.append({"contract_id": contract.get("contract_id"), "status": "skipped", "reason": "non_get_export_is_candidate_only"})
            continue
        query = _filter_query_from_contract(contract)
        export_url = _build_url(base_url, str(export.get("path") or ""), query)
        export_response = _resolve_download_payload(_http_get(export_url, token, timeout, max_bytes), base_url, token, timeout, max_bytes)
        if not export_response.get("ok"):
            executions.append({"contract_id": contract.get("contract_id"), "status": "error", "export_url": export_url, "status_code": export_response.get("status_code"), "error": export_response.get("error")})
            continue
        records, meta = parse_export_bytes(export_response.get("body") or b"", export_response.get("headers") or {}, str(export_response.get("url") or export_url))
        meta["truncated"] = bool(export_response.get("truncated"))
        meta["file_sha256"] = hashlib.sha256(export_response.get("body") or b"").hexdigest()
        source_rows: list[dict[str, Any]] = []
        source_total: int | None = None
        source_status = None
        source = contract.get("source") or {}
        if source and str(source.get("method") or "GET").upper() == "GET":
            source_url = _build_url(base_url, str(source.get("path") or ""), query)
            source_response = _http_get(source_url, token, timeout, max_bytes)
            source_status = source_response.get("status_code")
            if source_response.get("ok"):
                source_rows, source_total, _ = _source_records(source_response.get("body") or b"")
        current_findings, audit = audit_export_records(contract, records, meta, source_rows, source_total)
        request_evidence = {"method": "GET", "path": export.get("path"), "query": _redact(query), "status_code": export_response.get("status_code"), "content_type": (export_response.get("headers") or {}).get("content-type"), "file_sha256": meta.get("file_sha256"), "file_size_bytes": len(export_response.get("body") or b""), "truncated": bool(export_response.get("truncated")), "source_path": (source or {}).get("path"), "source_status_code": source_status}
        for item in current_findings:
            item["evidence"] = {**(item.get("evidence") or {}), "request": request_evidence}
        findings.extend(current_findings)

    # --- LLM-powered semantic reasoning (Phase61 moat upgrade) ---
    if execution_mode == "safe_live" and findings:
        try:
            import json as _json
            llm_result = _llm_reason("outcome", {
                "prd_text": "", "api_schema": "", "observed_data": _json.dumps(executions[-5:] if "executions" in dir() else [], ensure_ascii=False, default=str)[:4000],
                "heuristic_findings": _json.dumps(findings[:15], ensure_ascii=False, default=str)[:4000],
            })
            semantic_hypotheses.extend(compile_unverified_semantic_hypotheses(
                (llm_result or {}).get("findings"),
                engine="outcome",
                type_field="business_outcome_type",
            ))
        except Exception:
            pass

        executions.append({
            "contract_id": contract.get("contract_id"), "status": "executed", "export_url": export_url,
            "export_status": export_response.get("status_code"), "source_status": source_status,
            "file_sha256": hashlib.sha256(export_response.get("body") or b"").hexdigest(),
            "file_size_bytes": len(export_response.get("body") or b""), "truncated": bool(export_response.get("truncated")),
            "audit": audit,
        })
    output = _output_paths(project, root)
    registry, findings = _update_registry(output["workspace"] / "business_outcome_evidence_registry.json", findings)
    result = {
        "phase": "phase43_business_outcome_validation",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "summary": {**(profile.get("summary") or {}), "execution_mode": execution_mode, "executed_contract_count": len([x for x in executions if x.get("status") == "executed"]), "finding_count": len(findings), "high_confidence_finding_count": len([f for f in findings if float(f.get("confidence") or 0) >= 0.8])},
        "profile": profile,
        "executions": executions,
        "semantic_hypotheses": semantic_hypotheses, "llm_governance": {"status": "unverified_hypothesis_only", "does_not_affect_finding_counts": True, "requires_deterministic_replay": True}, "findings": findings,
        "evidence_registry_summary": {"entry_count": len((registry or {}).get("entries") or {})},
        "governance": {"execution_mode": execution_mode, "only_get_requests_in_safe_live": True, "does_not_create_export_jobs": True, "raw_export_files_not_persisted": True, "stores_redacted_evidence_only": True, "uses_no_benchmark_answer_files": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    _write_json(output["out"] / "business_outcome_validation_run.json", result)
    _write_json(output["workspace"] / "business_outcome_validation_run.json", result)
    (output["out"] / "business_outcome_validation_run_report.html").write_text(render_business_outcome_run_report(result), encoding="utf-8")
    return result


# ---------------------------------------------------------------------------
# Reports/CLI
# ---------------------------------------------------------------------------

def render_business_outcome_profile_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(k)}</span><b>{_html_escape(v)}</b></div>" for k, v in summary.items())
    rows = []
    for contract in data.get("contracts") or []:
        export = contract.get("export") or {}
        source = contract.get("source") or {}
        rows.append(f"<tr><td>{_html_escape(contract.get('contract_id'))}</td><td>{_html_escape(contract.get('resource'))}</td><td>{_html_escape(export.get('method'))} {_html_escape(export.get('path'))}</td><td>{_html_escape(source.get('method'))} {_html_escape(source.get('path')) if source else '未映射'}</td><td>{_html_escape(', '.join(contract.get('identity_fields') or []) or '自动推断')}</td><td>{_html_escape(contract.get('execution_policy'))}</td></tr>")
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>业务结果审计</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#ecfdf5;color:#065f46}}</style></head><body>
<section class='hero'><span class='badge'>Phase43 · Business Outcome Validation</span><h1>业务结果审计引擎</h1><p>验证“导出成功”之后的真实业务结果：重复、漏数、筛选失效、数据源覆盖和汇总差异。</p></section><section class='panel'><h2>覆盖概览</h2><div class='grid'>{cards}</div></section><section class='panel'><h2>已发现导出契约</h2><table><thead><tr><th>契约</th><th>资源</th><th>导出接口</th><th>源列表</th><th>唯一键</th><th>执行</th></tr></thead><tbody>{''.join(rows) or '<tr><td colspan="6">未发现可审计导出接口；可在 business_outcome_validation.export_contracts 显式配置。</td></tr>'}</tbody></table></section></body></html>"""


def render_business_outcome_run_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(k)}</span><b>{_html_escape(v)}</b></div>" for k, v in summary.items())
    findings = "".join(f"<tr><td>{_html_escape(f.get('severity'))}</td><td>{_html_escape(f.get('title'))}</td><td>{_html_escape(f.get('confidence'))}</td><td>{_html_escape(f.get('actual'))}</td><td>{_html_escape((f.get('evidence_stability') or {}).get('observations'))}</td></tr>" for f in data.get("findings") or [])
    exec_rows = "".join(f"<tr><td>{_html_escape(item.get('contract_id'))}</td><td>{_html_escape(item.get('status'))}</td><td>{_html_escape(item.get('export_status'))}</td><td>{_html_escape((item.get('audit') or {}).get('record_count'))}</td><td>{_html_escape(item.get('error') or item.get('reason'))}</td></tr>" for item in data.get("executions") or [])
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>业务结果审计执行报告</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#ecfdf5;color:#065f46}}</style></head><body>
<section class='hero'><span class='badge'>Phase43 Safe Live</span><h1>业务结果审计执行</h1><p>safe_live 仅发出 GET 请求；不会创建导出任务，不保存原始文件，仅保存脱敏证据摘要。</p></section><section class='panel'><h2>执行概览</h2><div class='grid'>{cards}</div></section><section class='panel'><h2>发现的业务结果问题</h2><table><thead><tr><th>等级</th><th>问题</th><th>置信度</th><th>实际</th><th>持续观测</th></tr></thead><tbody>{findings or '<tr><td colspan="5">暂无问题</td></tr>'}</tbody></table></section><section class='panel'><h2>导出执行记录</h2><table><thead><tr><th>契约</th><th>状态</th><th>导出状态</th><th>行数</th><th>原因</th></tr></thead><tbody>{exec_rows or '<tr><td colspan="5">plan_only 或暂无可执行契约</td></tr>'}</tbody></table></section></body></html>"""


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    mode = os.environ.get("BUSINESS_OUTCOME_EXECUTION_MODE") or "plan_only"
    result = run_business_outcome_validation(project, options={"execution_mode": mode})
    print(json.dumps({"ok": True, "project_id": project, "summary": result.get("summary"), "private_leak_check": result.get("private_leak_check")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
