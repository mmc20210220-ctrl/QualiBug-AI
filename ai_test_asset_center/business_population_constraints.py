from __future__ import annotations

"""Phase52: cross-record business population constraints and cohort anomalies.

Most expensive enterprise defects are not invalid values on a single API row.
They arise when a *population* of individually valid rows breaks a shared
business rule: a customer splits orders to exceed a quota, one meeting room is
booked twice, a bulk import reports success while only part of its payload was
processed, or high-value requests bypass the approval gate.

This module turns those cross-record rules into GET-only executable Oracles.
It accepts explicit enterprise contracts and conservatively infers safe
candidates from OpenAPI / PRD semantics.  It validates:

* group limit / quota breaches (count or numeric sum, optionally time-windowed);
* interval overlap and excessive simultaneous allocation in the same cohort;
* composite-business-key duplicates that a surrogate row id cannot reveal;
* batch terminal completeness and processed/success/failure count arithmetic;
* approval gates for amounts above a configurable threshold.

Evidence is intentionally aggregate-first: group identities and record
identities are hashed before persistence; no raw business rows, credentials or
request tokens are stored.  ``safe_live`` only uses GET requests.  Any split
submission, racing approval or quota-bypass mutation is emitted solely as a
sandbox-required candidate.
"""

import argparse
import hashlib
import json
import math
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .business_invariant_mining import _infer_identity, _is_collection_read, _item_fields, _numeric, _parse_time, _value
from .business_outcome_validation import _normal_token, _private_leak_check, _redact, _update_registry
from .business_reconciliation import _fetch_source_pages
from .multisource_reasoning import _learning_bonus, ingest_confirmed_bug_feedback
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


GROUP_FIELD_RE = re.compile(
    r"(?:tenant|org|organization|customer|client|account|user|owner|department|team|project|product|sku|item|resource|room|seat|employee|staff|vehicle|warehouse|store|channel|租户|组织|客户|账户|用户|部门|项目|商品|库存|资源|房间|座位|员工|车辆|仓库|门店|渠道)",
    re.I,
)
START_RE = re.compile(r"(?:^|[_\-.])(start|begin|from|effective_from|valid_from|check_in)(?:$|[_\-.])|开始|起始|生效|入住", re.I)
END_RE = re.compile(r"(?:^|[_\-.])(end|finish|to|effective_to|valid_to|check_out|expire)(?:$|[_\-.])|结束|截止|失效|退房", re.I)
TIME_RE = re.compile(r"time|date|at|timestamp|时间|日期", re.I)
AMOUNT_RE = re.compile(r"amount|total|price|cost|fee|budget|quota|limit|balance|count|quantity|金额|总额|价格|费用|预算|额度|余额|数量|名额", re.I)
APPROVAL_RE = re.compile(r"approval|approve|review|audit|审批|审核", re.I)
BATCH_RESOURCE_RE = re.compile(r"batch|import|export|job|task|sync|migration|bulk|批次|导入|导出|任务|同步|迁移|批量", re.I)
INTERVAL_RESOURCE_RE = re.compile(r"booking|reservation|appointment|schedule|shift|assignment|allocation|occupancy|entitlement|calendar|预订|预约|排班|分配|占用|授权|日历", re.I)
LIMIT_PRD_RE = re.compile(r"限额|额度|配额|上限|名额|不得超过|不超过|quota|limit|max(?:imum)?|capacity", re.I)
OVERLAP_PRD_RE = re.compile(r"不可重叠|不能重叠|不得冲突|不允许冲突|overlap|conflict", re.I)
BATCH_PRD_RE = re.compile(r"批量|导入|导出|同步|任务|batch|import|export|job", re.I)


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


def _section(cfg: dict[str, Any]) -> dict[str, Any]:
    value = (
        cfg.get("business_population_constraints")
        or cfg.get("business_population_constraint_reasoning")
        or cfg.get("cross_record_business_constraints")
        or cfg.get("cohort_anomaly_reasoning")
        or {}
    )
    return value if isinstance(value, dict) else {}


def _output_paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "defect_discovery"
    return {
        "out": root / "platform_outputs" / project / "business_population_constraints",
        "workspace": workspace,
        "profile": workspace / "business_population_constraints_profile.json",
        "registry": workspace / "business_population_constraints_evidence_registry.json",
    }


def _field_name(fields: dict[str, Any], wanted: Any) -> str | None:
    target = _norm(wanted)
    if not target:
        return None
    for name in fields:
        if _norm(name) == target:
            return str(name)
    for name in fields:
        current = _norm(name)
        if target and (target in current or current in target):
            return str(name)
    return None


def _fields(raw: Any) -> list[str]:
    if isinstance(raw, str):
        raw = [raw]
    return [str(item).strip() for item in (raw or []) if str(item).strip()]


def _catalog(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    components = openapi.get("components") or {}
    rows: list[dict[str, Any]] = []
    for operation in _operations(openapi):
        if not _is_collection_read(operation, components):
            continue
        path = str(operation.get("path") or "")
        resource = _resource_key(path)
        fields = _item_fields(operation, components)
        if not fields:
            continue
        rows.append({
            "path": path,
            "method": "GET",
            "resource": resource,
            "summary": str(operation.get("summary") or operation.get("operation_id") or resource),
            "parameters": list(operation.get("parameters") or []),
            "fields": fields,
        })
    return rows


def _resource_key(path: str) -> str:
    parts = [part for part in str(path or "").split("/") if part and not part.startswith("{")]
    value = _norm(parts[-1] if parts else "resource")
    if value.endswith("ies") and len(value) > 4:
        return value[:-3] + "y"
    return value.rstrip("s") or "resource"


def _find_collection(catalog: list[dict[str, Any]], value: Any) -> dict[str, Any] | None:
    wanted = str(value or "").strip()
    if not wanted:
        return None
    target = wanted.rstrip("/") or "/"
    for item in catalog:
        if str(item.get("path") or "").rstrip("/") == target:
            return item
    wanted_norm = _norm(wanted)
    for item in catalog:
        if _norm(item.get("resource")) == wanted_norm:
            return item
    return None


def _first_matching(fields: dict[str, Any], expression: re.Pattern[str], excluded: set[str] | None = None) -> str | None:
    excluded = excluded or set()
    for name in fields:
        if name in excluded:
            continue
        if expression.search(str(name)):
            return str(name)
    return None


def _group_fields(fields: dict[str, Any], configured: Any, limit: int = 4) -> list[str]:
    output: list[str] = []
    for candidate in _fields(configured):
        value = _field_name(fields, candidate) or candidate
        if value not in output:
            output.append(value)
    if output:
        return output[:limit]
    for name in fields:
        if GROUP_FIELD_RE.search(str(name)) and re.search(r"id|code|no|key|编号|编码|号", str(name), re.I):
            output.append(str(name))
    return output[:limit]


def _identity_fields(resource: str, fields: dict[str, Any], configured: Any = None) -> list[str]:
    configured_values = _fields(configured)
    values: list[str] = []
    for value in configured_values:
        match = _field_name(fields, value) or value
        if match not in values:
            values.append(match)
    if values:
        return values[:4]
    inferred = _infer_identity(resource, fields, {})
    return [inferred] if inferred else []


def _resolved_fields(fields: dict[str, Any], raw: Any) -> list[str]:
    output: list[str] = []
    for value in _fields(raw):
        match = _field_name(fields, value) or value
        if match not in output:
            output.append(match)
    return output


def _number(raw: Any) -> float | None:
    value = _numeric(raw)
    if value is None or not math.isfinite(value):
        return None
    return float(value)


def _row_value(row: dict[str, Any], field: str | None, mappings: dict[str, Any]) -> Any:
    return _value(row, str(field or ""), mappings)


def _row_identity(row: dict[str, Any], fields: list[str], mappings: dict[str, Any], fallback_index: int) -> str:
    values = [_canon(_row_value(row, field, mappings)) for field in fields]
    if any(values):
        return _short({"fields": fields, "values": values})
    return _short({"index": fallback_index, "shape": sorted(map(str, row.keys()))})


def _window_label(value: Any, contract: dict[str, Any]) -> str | None:
    field = str(contract.get("window_field") or "")
    granularity = str(contract.get("window") or contract.get("window_granularity") or "").lower()
    seconds = contract.get("window_seconds")
    if not field and not granularity and not seconds:
        return "all"
    parsed = _parse_time(value)
    if not parsed:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if seconds is not None:
        try:
            span = max(1, min(int(seconds), 31_536_000))
            return f"s:{int(parsed.timestamp()) // span}"
        except Exception:
            pass
    if granularity in {"month", "monthly", "月"}:
        return parsed.strftime("%Y-%m")
    if granularity in {"year", "yearly", "年"}:
        return parsed.strftime("%Y")
    if granularity in {"hour", "hourly", "小时"}:
        return parsed.strftime("%Y-%m-%dT%H")
    return parsed.strftime("%Y-%m-%d")


def _filter_row(row: dict[str, Any], filters: dict[str, Any], mappings: dict[str, Any]) -> bool:
    for field, expected in (filters or {}).items():
        actual = _row_value(row, str(field), mappings)
        if isinstance(expected, (list, tuple, set)):
            accepted = {_canon(item) for item in expected}
            if _canon(actual) not in accepted:
                return False
        elif _canon(actual) != _canon(expected):
            return False
    return True


def _normal_values(raw: Any) -> set[str]:
    return {_norm(item) for item in _fields(raw) if _norm(item)}


def _contract_from_config(row: dict[str, Any], catalog: list[dict[str, Any]], number: int) -> dict[str, Any] | None:
    raw_type = str(row.get("contract_kind") or row.get("type") or row.get("kind") or "").strip().lower()
    aliases = {
        "quota": "group_limit",
        "limit": "group_limit",
        "cohort_limit": "group_limit",
        "capacity": "group_limit",
        "overlap": "interval_overlap",
        "schedule_overlap": "interval_overlap",
        "composite_unique": "composite_unique",
        "composite_key": "composite_unique",
        "batch_completion": "batch_integrity",
        "batch_status": "batch_integrity",
        "approval_gate": "approval_threshold",
        "threshold_approval": "approval_threshold",
    }
    kind = aliases.get(raw_type, raw_type or "group_limit")
    if kind not in {"group_limit", "interval_overlap", "composite_unique", "batch_integrity", "approval_threshold"}:
        return None
    source = _find_collection(catalog, row.get("path") or row.get("source_path") or row.get("collection_path") or row.get("resource"))
    if not source:
        return None
    fields = source.get("fields") or {}
    mappings = dict(row.get("field_mappings") or {})
    contract: dict[str, Any] = {
        "contract_id": f"BPC_CONTRACT_{number:04d}",
        "contract_kind": kind,
        "resource": str(row.get("resource_name") or source.get("resource") or "resource"),
        "source": {key: source.get(key) for key in ("path", "method", "parameters", "summary", "resource")},
        "sample_query": dict(row.get("sample_query") or row.get("query") or {}),
        "pagination": dict(row.get("pagination") or {}),
        "field_mappings": mappings,
        "filters": dict(row.get("filters") or {}),
        "identity_fields": _identity_fields(str(source.get("resource") or "resource"), fields, row.get("identity_fields")),
        "group_by": _group_fields(fields, row.get("group_by") or row.get("group_fields")),
        "window_field": _field_name(fields, row.get("window_field")) if row.get("window_field") else None,
        "window": str(row.get("window") or row.get("window_granularity") or ""),
        "window_seconds": row.get("window_seconds"),
        "metric_field": _field_name(fields, row.get("metric_field") or row.get("amount_field") or row.get("sum_field")) if (row.get("metric_field") or row.get("amount_field") or row.get("sum_field")) else None,
        "max_sum": row.get("max_sum") if row.get("max_sum") is not None else row.get("limit_amount"),
        "max_count": row.get("max_count") if row.get("max_count") is not None else row.get("limit_count"),
        "start_field": _field_name(fields, row.get("start_field")) if row.get("start_field") else None,
        "end_field": _field_name(fields, row.get("end_field")) if row.get("end_field") else None,
        "max_concurrent": row.get("max_concurrent") if row.get("max_concurrent") is not None else row.get("max_overlap"),
        "unique_fields": _resolved_fields(fields, row.get("unique_fields") or row.get("fields") or row.get("composite_fields")),
        "batch_id_field": _field_name(fields, row.get("batch_id_field")) if row.get("batch_id_field") else None,
        "expected_count_field": _field_name(fields, row.get("expected_count_field") or row.get("total_count_field")) if (row.get("expected_count_field") or row.get("total_count_field")) else None,
        "processed_count_field": _field_name(fields, row.get("processed_count_field")) if row.get("processed_count_field") else None,
        "success_count_field": _field_name(fields, row.get("success_count_field")) if row.get("success_count_field") else None,
        "failed_count_field": _field_name(fields, row.get("failed_count_field")) if row.get("failed_count_field") else None,
        "status_field": _field_name(fields, row.get("status_field")) if row.get("status_field") else None,
        "terminal_statuses": _fields(row.get("terminal_statuses") or row.get("completed_statuses") or []),
        "amount_field": _field_name(fields, row.get("amount_field") or row.get("metric_field")) if (row.get("amount_field") or row.get("metric_field")) else None,
        "threshold": row.get("threshold") if row.get("threshold") is not None else row.get("approval_threshold"),
        "approval_status_field": _field_name(fields, row.get("approval_status_field") or row.get("approval_field")) if (row.get("approval_status_field") or row.get("approval_field")) else None,
        "approved_values": _fields(row.get("approved_values") or row.get("approval_values") or ["approved", "passed", "completed", "已审批", "已通过"]),
        "approval_reference_field": _field_name(fields, row.get("approval_reference_field") or row.get("approval_id_field")) if (row.get("approval_reference_field") or row.get("approval_id_field")) else None,
        "tolerance": abs(float(row.get("tolerance") or 0.001)),
        "execution_policy": "safe_read_only",
        "discovery": "enterprise_config",
        "source_evidence": ["enterprise_config", "openapi"],
    }
    if kind == "group_limit":
        if not contract["group_by"] or (contract["max_sum"] is None and contract["max_count"] is None):
            return None
        if contract["max_sum"] is not None:
            try:
                contract["max_sum"] = float(contract["max_sum"])
            except Exception:
                return None
        if contract["max_count"] is not None:
            try:
                contract["max_count"] = int(contract["max_count"])
            except Exception:
                return None
    elif kind == "interval_overlap":
        contract["start_field"] = contract["start_field"] or _first_matching(fields, START_RE)
        contract["end_field"] = contract["end_field"] or _first_matching(fields, END_RE, {contract["start_field"]} if contract["start_field"] else set())
        if not contract["group_by"] or not contract["start_field"] or not contract["end_field"]:
            return None
        try:
            contract["max_concurrent"] = max(1, min(int(contract["max_concurrent"] or 1), 100))
        except Exception:
            contract["max_concurrent"] = 1
    elif kind == "composite_unique":
        if len(contract["unique_fields"]) < 2:
            return None
    elif kind == "batch_integrity":
        contract["batch_id_field"] = contract["batch_id_field"] or _field_name(fields, "batch_id") or _field_name(fields, "job_id")
        contract["expected_count_field"] = contract["expected_count_field"] or _field_name(fields, "total_count") or _field_name(fields, "expected_count")
        contract["processed_count_field"] = contract["processed_count_field"] or _field_name(fields, "processed_count")
        contract["success_count_field"] = contract["success_count_field"] or _field_name(fields, "success_count") or _field_name(fields, "succeeded_count")
        contract["failed_count_field"] = contract["failed_count_field"] or _field_name(fields, "failed_count") or _field_name(fields, "failure_count")
        contract["status_field"] = contract["status_field"] or _field_name(fields, "status") or _field_name(fields, "state")
        if not contract["expected_count_field"] or not any([contract["processed_count_field"], contract["success_count_field"], contract["failed_count_field"]]):
            return None
    elif kind == "approval_threshold":
        if not contract["amount_field"] or contract["threshold"] is None:
            return None
        try:
            contract["threshold"] = float(contract["threshold"])
        except Exception:
            return None
        if not contract["approval_status_field"] and not contract["approval_reference_field"]:
            return None
    return contract


def _auto_contracts(catalog: list[dict[str, Any]], configured: list[dict[str, Any]], prd: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contracts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    configured_paths = {
        str(row.get("path") or row.get("source_path") or row.get("collection_path") or "").rstrip("/")
        for row in configured
        if isinstance(row, dict)
    }
    for source in catalog:
        path = str(source.get("path") or "")
        fields = source.get("fields") or {}
        resource_text = f"{source.get('resource')} {source.get('summary')} {path}"
        groups = _group_fields(fields, None)
        start = _first_matching(fields, START_RE)
        end = _first_matching(fields, END_RE, {start} if start else set())
        if path not in configured_paths and groups and start and end and (INTERVAL_RESOURCE_RE.search(resource_text) or OVERLAP_PRD_RE.search(prd or "")):
            contracts.append({
                "contract_id": f"BPC_CONTRACT_{len(contracts)+1:04d}", "contract_kind": "interval_overlap", "resource": source.get("resource"),
                "source": {key: source.get(key) for key in ("path", "method", "parameters", "summary", "resource")}, "sample_query": {}, "pagination": {}, "field_mappings": {}, "filters": {},
                "identity_fields": _identity_fields(str(source.get("resource") or "resource"), fields), "group_by": groups[:1], "start_field": start, "end_field": end, "max_concurrent": 1,
                "execution_policy": "safe_read_only", "discovery": "openapi_interval_semantics", "source_evidence": ["openapi", "resource_semantics"],
            })
        expected = _field_name(fields, "total_count") or _field_name(fields, "expected_count")
        processed = _field_name(fields, "processed_count")
        success = _field_name(fields, "success_count") or _field_name(fields, "succeeded_count")
        failed = _field_name(fields, "failed_count") or _field_name(fields, "failure_count")
        status = _field_name(fields, "status") or _field_name(fields, "state")
        if path not in configured_paths and expected and any([processed, success, failed]) and (BATCH_RESOURCE_RE.search(resource_text) or BATCH_PRD_RE.search(prd or "")):
            contracts.append({
                "contract_id": f"BPC_CONTRACT_{len(contracts)+1:04d}", "contract_kind": "batch_integrity", "resource": source.get("resource"),
                "source": {key: source.get(key) for key in ("path", "method", "parameters", "summary", "resource")}, "sample_query": {}, "pagination": {}, "field_mappings": {}, "filters": {},
                "identity_fields": _identity_fields(str(source.get("resource") or "resource"), fields), "batch_id_field": _field_name(fields, "batch_id") or _field_name(fields, "job_id"),
                "expected_count_field": expected, "processed_count_field": processed, "success_count_field": success, "failed_count_field": failed, "status_field": status,
                "terminal_statuses": ["completed", "success", "succeeded", "failed", "done", "已完成", "成功", "失败"],
                "execution_policy": "safe_read_only", "discovery": "openapi_batch_semantics", "source_evidence": ["openapi", "resource_semantics"],
            })
        # A limit without a numeric threshold should remain an explicit candidate,
        # not an unsafe guessed production assertion.
        if groups and (LIMIT_PRD_RE.search(prd or "") or any(re.search(r"quota|limit|capacity|额度|限额|配额|名额", str(name), re.I) for name in fields)):
            candidates.append({
                "candidate_id": f"BPC_CANDIDATE_{len(candidates)+1:04d}", "kind": "group_limit_contract_gap", "resource": source.get("resource"), "path": path,
                "title": f"业务群体限额待确认：{source.get('resource')}", "severity": "P2", "risk_type": "business_population_contract_gap",
                "detail": "检测到额度/配额/容量语义，但缺少按哪个主体、哪个时间窗口、以数量还是金额计算的明确阈值；请在 business_population_constraints.contracts 中补充。",
            })
    return contracts, candidates


def _summary(contracts: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for item in contracts:
        counts[str(item.get("contract_kind") or "unknown")] += 1
    return {
        "population_contract_count": len(contracts),
        "group_limit_contract_count": counts["group_limit"],
        "interval_overlap_contract_count": counts["interval_overlap"],
        "composite_unique_contract_count": counts["composite_unique"],
        "batch_integrity_contract_count": counts["batch_integrity"],
        "approval_threshold_contract_count": counts["approval_threshold"],
        "contract_gap_candidate_count": len(candidates),
    }


def _profile_for_persistence(profile: dict[str, Any]) -> dict[str, Any]:
    clean = json.loads(json.dumps(profile, ensure_ascii=False, default=str))
    for contract in clean.get("contracts") or []:
        if not isinstance(contract, dict):
            continue
        contract["sample_query"] = _redact(contract.get("sample_query") or {})
        contract["filters"] = _redact(contract.get("filters") or {})
    clean["governance"] = {
        "no_live_execution_during_profile": True,
        "raw_business_rows_not_persisted": True,
        "query_and_filter_values_redacted_before_persistence": True,
        "uses_no_benchmark_answer_files": True,
    }
    return clean


def build_business_population_constraint_profile(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    paths = config_paths(project, root)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    openapi = openapi if isinstance(openapi, dict) else {}
    prd = _read_text(paths["input_dir"] / "prd.md")
    section = _section(cfg)
    configured = [item for item in (section.get("contracts") or section.get("collections") or []) if isinstance(item, dict)]
    catalog = _catalog(openapi)
    contracts: list[dict[str, Any]] = []
    for item in configured:
        contract = _contract_from_config(item, catalog, len(contracts) + 1)
        if contract:
            contracts.append(contract)
    automatic, candidates = _auto_contracts(catalog, configured, prd)
    existing = {(str(item.get("contract_kind")), str((item.get("source") or {}).get("path"))) for item in contracts}
    for contract in automatic:
        key = (str(contract.get("contract_kind")), str((contract.get("source") or {}).get("path")))
        if key not in existing:
            contract["contract_id"] = f"BPC_CONTRACT_{len(contracts)+1:04d}"
            contracts.append(contract)
            existing.add(key)
    learning = ingest_confirmed_bug_feedback(project, root)
    memory = learning.get("memory") or {}
    for contract in contracts:
        contract["oracle_family"] = f"business_population_{contract.get('contract_kind') or 'constraint'}"
        contract["title"] = f"{contract.get('resource') or 'resource'} {contract.get('contract_kind') or 'constraint'}"
        bonus, matches = _learning_bonus(contract, memory)
        contract["learning_bonus"] = bonus
        contract["learning_matches"] = matches
    profile = {
        "phase": "phase52_business_population_constraints",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "catalog": [{"path": item.get("path"), "resource": item.get("resource"), "field_count": len(item.get("fields") or {})} for item in catalog],
        "contracts": contracts,
        "candidates": candidates,
        "summary": {**_summary(contracts, candidates), "confirmed_bug_memory_count": int((learning.get("summary") or {}).get("confirmed_bug_memory_count") or 0)},
        "governance": {"safe_live_get_only": True, "raw_business_rows_not_persisted": True, "uses_no_benchmark_answer_files": True},
    }
    output = _output_paths(project, root)
    _write_json(output["profile"], _profile_for_persistence(profile))
    _write_json(output["out"] / "business_population_constraints_profile.json", _profile_for_persistence(profile))
    (output["out"] / "business_population_constraints_profile_report.html").write_text(render_business_population_constraint_profile_report(profile), encoding="utf-8")
    return profile


def load_business_population_constraint_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    data = _load_json(_output_paths(project_id, root)["profile"], None)
    return data if isinstance(data, dict) and data.get("phase") == "phase52_business_population_constraints" else None


def _probe(contract: dict[str, Any], number: int, kind: str, title: str, risk_type: str, expected: str, severity: str = "P1", execution_policy: str = "safe_read_only", method: str = "GET") -> dict[str, Any]:
    source = contract.get("source") or {}
    return {
        "probe_id": f"BPC_{number:04d}", "source": "business_population_constraints", "contract_id": contract.get("contract_id"),
        "business_population_type": kind, "risk_type": risk_type, "title": title, "severity": severity,
        "expected": expected, "method": method, "path": source.get("path") or "", "actor": "normal_user", "destructive": execution_policy == "sandbox_required",
        "execution_policy": execution_policy, "business_resource": contract.get("resource"), "discovery": contract.get("discovery"),
    }


def generate_business_population_constraint_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    profile = load_business_population_constraint_profile(project_id, root) or build_business_population_constraint_profile(project_id, root)
    probes: list[dict[str, Any]] = []
    for contract in profile.get("contracts") or []:
        kind = str(contract.get("contract_kind") or "")
        resource = str(contract.get("resource") or "资源")
        if kind == "group_limit":
            probes.append(_probe(contract, len(probes)+1, "group_limit", f"群体额度/配额：{resource} 不得突破共享上限", "business_cohort_limit", "同一主体和时间窗口内的累计数量/金额不得超过企业配置阈值。"))
            probes.append(_probe(contract, len(probes)+1, "quota_bypass_sandbox", f"沙箱拆分绕过：{resource} 多笔提交不得绕过共享额度", "business_cohort_limit_bypass", "在隔离环境将同一总额拆分为多笔请求，结果不得超过额度且不得绕过审批。", execution_policy="sandbox_required", method="POST"))
        elif kind == "interval_overlap":
            probes.append(_probe(contract, len(probes)+1, "interval_overlap", f"资源冲突：{resource} 的同一资源时间段不得重叠", "business_interval_overlap", "同一资源/人员/房间在允许并发数内不得发生时间区间重叠。"))
            probes.append(_probe(contract, len(probes)+1, "interval_race_sandbox", f"沙箱并发预订：{resource} 并发占用不得双成功", "business_interval_race", "在隔离环境并发提交相同资源相同时间段，超过容量的请求必须被拒绝。", execution_policy="sandbox_required", method="POST"))
        elif kind == "composite_unique":
            probes.append(_probe(contract, len(probes)+1, "composite_unique", f"复合业务键唯一：{resource} 不得出现逻辑重复记录", "business_composite_duplicate", "同一复合业务键组合在同一业务口径中最多出现一次。"))
        elif kind == "batch_integrity":
            probes.append(_probe(contract, len(probes)+1, "batch_integrity", f"批次完整性：{resource} 终态统计必须闭合", "business_batch_integrity", "批次终态时 total、processed、success、failed 等统计必须满足完整处理和算术闭合。"))
        elif kind == "approval_threshold":
            probes.append(_probe(contract, len(probes)+1, "approval_threshold", f"审批阈值：{resource} 高额业务必须走审批链", "business_approval_threshold", "金额达到审批阈值的业务记录必须具有已批准状态或审批凭据。", severity="P0"))
        if max_count and len(probes) >= max_count:
            return probes[:max_count]
    for candidate in profile.get("candidates") or []:
        probes.append({"probe_id": f"BPC_GAP_{len(probes)+1:04d}", "source": "business_population_constraints", "contract_id": candidate.get("candidate_id"), "business_population_type": "contract_gap", "risk_type": candidate.get("risk_type") or "business_population_contract_gap", "title": candidate.get("title"), "severity": candidate.get("severity") or "P2", "expected": candidate.get("detail"), "method": "GET", "path": candidate.get("path") or "", "actor": "normal_user", "destructive": False, "execution_policy": "candidate_only"})
        if max_count and len(probes) >= max_count:
            break
    return probes[:max_count] if max_count else probes


def _finding(contract: dict[str, Any], kind: str, title: str, expected: str, actual: str, evidence: dict[str, Any], confidence: float, key: Any) -> dict[str, Any]:
    risk = {
        "group_limit_breach": "business_cohort_limit",
        "interval_overlap": "business_interval_overlap",
        "composite_duplicate": "business_composite_duplicate",
        "batch_integrity_mismatch": "business_batch_integrity",
        "approval_missing_above_threshold": "business_approval_threshold",
    }.get(kind, "business_population_constraint")
    severity = "P0" if kind == "approval_missing_above_threshold" else "P1"
    fingerprint = _hash({"phase": "bpc", "contract": contract.get("contract_id"), "kind": kind, "key": key})
    issue_id = f"BPC_{fingerprint[:12].upper()}"
    return {
        "issue_id": issue_id, "finding_id": issue_id, "fingerprint": fingerprint, "source": "business_population_constraints",
        "contract_id": contract.get("contract_id"), "business_population_type": kind, "risk_type": risk, "severity": severity,
        "title": title, "expected": expected, "actual": actual, "confidence": round(min(0.98, float(confidence) + float(contract.get("learning_bonus") or 0.0)), 3), "status": "needs_human_review",
        "evidence": _redact(evidence), "learning_matches": contract.get("learning_matches") or [],
    }


def _audit_group_limit(contract: dict[str, Any], context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [row for row in (context.get("records") or []) if isinstance(row, dict)]
    mappings = dict(contract.get("field_mappings") or {})
    group_fields = list(contract.get("group_by") or [])
    metric = contract.get("metric_field")
    max_sum = contract.get("max_sum")
    max_count = contract.get("max_count")
    window_field = contract.get("window_field")
    buckets: dict[tuple[str, ...], dict[str, Any]] = {}
    for index, row in enumerate(rows):
        if not _filter_row(row, contract.get("filters") or {}, mappings):
            continue
        values = [_canon(_row_value(row, field, mappings)) for field in group_fields]
        if not all(values):
            continue
        window = _window_label(_row_value(row, window_field, mappings), contract) if window_field else "all"
        if window is None:
            continue
        key = tuple(values + [window])
        bucket = buckets.setdefault(key, {"count": 0, "sum": 0.0, "numeric_count": 0, "sample_ids": []})
        bucket["count"] += 1
        if metric:
            value = _number(_row_value(row, metric, mappings))
            if value is not None:
                bucket["sum"] += value
                bucket["numeric_count"] += 1
        identity = _row_identity(row, list(contract.get("identity_fields") or []), mappings, index)
        if len(bucket["sample_ids"]) < 8:
            bucket["sample_ids"].append(identity)
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    tolerance = float(contract.get("tolerance") or 0.001)
    for key, bucket in buckets.items():
        group_hash = _short({"fields": group_fields, "values": key[:-1]})
        window = key[-1]
        observations.append({"group_hash": group_hash, "window": window, "count": bucket["count"], "sum": round(bucket["sum"], 6), "numeric_count": bucket["numeric_count"]})
        breach_sum = max_sum is not None and bucket["numeric_count"] > 0 and bucket["sum"] > float(max_sum) + tolerance
        breach_count = max_count is not None and bucket["count"] > int(max_count)
        if not (breach_sum or breach_count):
            continue
        metrics: list[str] = []
        if breach_sum:
            metrics.append(f"累计值 {round(bucket['sum'], 6)} > 上限 {max_sum}")
        if breach_count:
            metrics.append(f"记录数 {bucket['count']} > 上限 {max_count}")
        findings.append(_finding(
            contract, "group_limit_breach", f"共享额度/配额被突破：{contract.get('resource')}",
            "按主体与窗口聚合后的累计数量/金额必须不超过业务配置上限。", "；".join(metrics),
            {"request": {"method": "GET", "path": (contract.get("source") or {}).get("path"), "query": _redact(contract.get("sample_query") or {})}, "group_hash": group_hash, "window": window, "group_fields": group_fields, "metric_field": metric, "aggregate": {"count": bucket["count"], "sum": round(bucket["sum"], 6), "numeric_count": bucket["numeric_count"]}, "limits": {"max_sum": max_sum, "max_count": max_count}, "sample_record_hashes": bucket["sample_ids"], "coverage": {"complete": bool(context.get("complete")), "fetched_rows": len(rows), "reported_total": context.get("total")}},
            0.96 if bool(context.get("complete")) else 0.89, {"group": group_hash, "window": window, "limit": {"sum": max_sum, "count": max_count}}
        ))
    return findings, observations


def _audit_interval_overlap(contract: dict[str, Any], context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [row for row in (context.get("records") or []) if isinstance(row, dict)]
    mappings = dict(contract.get("field_mappings") or {})
    group_fields = list(contract.get("group_by") or [])
    start_field, end_field = str(contract.get("start_field") or ""), str(contract.get("end_field") or "")
    allowed = max(1, int(contract.get("max_concurrent") or 1))
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for index, row in enumerate(rows):
        if not _filter_row(row, contract.get("filters") or {}, mappings):
            continue
        group = tuple(_canon(_row_value(row, field, mappings)) for field in group_fields)
        if not all(group):
            continue
        start = _parse_time(_row_value(row, start_field, mappings))
        end = _parse_time(_row_value(row, end_field, mappings))
        if not start or not end or end <= start:
            continue
        groups[group].append({"start": start, "end": end, "id": _row_identity(row, list(contract.get("identity_fields") or []), mappings, index)})
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for group, entries in groups.items():
        entries.sort(key=lambda item: (item["start"], item["end"], item["id"]))
        active: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        for current in entries:
            active = [item for item in active if item["end"] > current["start"]]
            if len(active) >= allowed:
                conflicts.append({"active_count": len(active) + 1, "record_hashes": [item["id"] for item in (active + [current])[:8]], "start": current["start"].isoformat(), "end": current["end"].isoformat()})
            active.append(current)
        group_hash = _short({"fields": group_fields, "values": group})
        observations.append({"group_hash": group_hash, "interval_count": len(entries), "conflict_count": len(conflicts), "max_concurrent_allowed": allowed})
        if conflicts:
            findings.append(_finding(
                contract, "interval_overlap", f"资源时间冲突：{contract.get('resource')}",
                f"同一资源组的同时占用数不得超过 {allowed}。", f"发现 {len(conflicts)} 个重叠点，至少有 {max(item['active_count'] for item in conflicts)} 条记录同时有效。",
                {"request": {"method": "GET", "path": (contract.get("source") or {}).get("path"), "query": _redact(contract.get("sample_query") or {})}, "group_hash": group_hash, "group_fields": group_fields, "time_fields": {"start": start_field, "end": end_field}, "max_concurrent_allowed": allowed, "conflicts": conflicts[:10], "coverage": {"complete": bool(context.get("complete")), "fetched_rows": len(rows), "reported_total": context.get("total")}},
                0.96 if bool(context.get("complete")) else 0.9, {"group": group_hash, "conflicts": [(item["start"], item["end"], tuple(item["record_hashes"])) for item in conflicts[:5]]}
            ))
    return findings, observations


def _audit_composite_unique(contract: dict[str, Any], context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [row for row in (context.get("records") or []) if isinstance(row, dict)]
    mappings = dict(contract.get("field_mappings") or {})
    fields = list(contract.get("unique_fields") or [])
    groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for index, row in enumerate(rows):
        if not _filter_row(row, contract.get("filters") or {}, mappings):
            continue
        key = tuple(_canon(_row_value(row, field, mappings)) for field in fields)
        if not all(key):
            continue
        groups[key].append(_row_identity(row, list(contract.get("identity_fields") or []), mappings, index))
    duplicates = []
    for key, hashes in groups.items():
        if len(hashes) > 1:
            duplicates.append({"key_hash": _short({"fields": fields, "values": key}), "count": len(hashes), "record_hashes": hashes[:8]})
    observations = [{"field_count": len(fields), "duplicate_group_count": len(duplicates), "row_count": len(rows)}]
    if not duplicates:
        return [], observations
    finding = _finding(
        contract, "composite_duplicate", f"复合业务键重复：{contract.get('resource')}",
        "同一复合业务键组合在同一业务口径中最多出现一次。", f"发现 {len(duplicates)} 组逻辑重复记录，最大重复次数 {max(item['count'] for item in duplicates)}。",
        {"request": {"method": "GET", "path": (contract.get("source") or {}).get("path"), "query": _redact(contract.get("sample_query") or {})}, "unique_fields": fields, "duplicates": duplicates[:12], "coverage": {"complete": bool(context.get("complete")), "fetched_rows": len(rows), "reported_total": context.get("total")}},
        0.95 if bool(context.get("complete")) else 0.88, {"fields": fields, "keys": [item["key_hash"] for item in duplicates[:20]]}
    )
    return [finding], observations


def _audit_batch_integrity(contract: dict[str, Any], context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [row for row in (context.get("records") or []) if isinstance(row, dict)]
    mappings = dict(contract.get("field_mappings") or {})
    expected_field = contract.get("expected_count_field")
    processed_field = contract.get("processed_count_field")
    success_field = contract.get("success_count_field")
    failed_field = contract.get("failed_count_field")
    status_field = contract.get("status_field")
    terminal = _normal_values(contract.get("terminal_statuses") or [])
    batch_id_field = contract.get("batch_id_field")
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    tolerance = float(contract.get("tolerance") or 0.001)
    for index, row in enumerate(rows):
        if not _filter_row(row, contract.get("filters") or {}, mappings):
            continue
        expected = _number(_row_value(row, expected_field, mappings))
        processed = _number(_row_value(row, processed_field, mappings)) if processed_field else None
        success = _number(_row_value(row, success_field, mappings)) if success_field else None
        failed = _number(_row_value(row, failed_field, mappings)) if failed_field else None
        status = _norm(_row_value(row, status_field, mappings)) if status_field else ""
        is_terminal = bool(status and status in terminal) if terminal else True
        issues: list[str] = []
        if expected is not None and success is not None and failed is not None:
            settled = success + failed
            if settled > expected + tolerance:
                issues.append(f"success+failed={settled:g} > total={expected:g}")
            elif is_terminal and abs(settled - expected) > tolerance:
                issues.append(f"终态 success+failed={settled:g} != total={expected:g}")
        if expected is not None and processed is not None:
            if processed > expected + tolerance:
                issues.append(f"processed={processed:g} > total={expected:g}")
            elif is_terminal and abs(processed - expected) > tolerance:
                issues.append(f"终态 processed={processed:g} != total={expected:g}")
        if processed is not None and success is not None and failed is not None and abs((success + failed) - processed) > tolerance:
            issues.append(f"success+failed={success + failed:g} != processed={processed:g}")
        record_hash = _row_identity(row, list(contract.get("identity_fields") or []), mappings, index)
        batch_hash = _short(_canon(_row_value(row, batch_id_field, mappings))) if batch_id_field and _row_value(row, batch_id_field, mappings) is not None else record_hash
        observations.append({"batch_hash": batch_hash, "terminal": is_terminal, "issues": len(issues)})
        if issues:
            findings.append(_finding(
                contract, "batch_integrity_mismatch", f"批次结果不完整或统计失真：{contract.get('resource')}",
                "批次终态时 total、processed、success、failed 必须满足完整处理与算术闭合。", "；".join(issues),
                {"request": {"method": "GET", "path": (contract.get("source") or {}).get("path"), "query": _redact(contract.get("sample_query") or {})}, "batch_hash": batch_hash, "field_map": {"expected": expected_field, "processed": processed_field, "success": success_field, "failed": failed_field, "status": status_field}, "values": {"expected": expected, "processed": processed, "success": success, "failed": failed, "status": status}, "terminal": is_terminal, "coverage": {"complete": bool(context.get("complete")), "fetched_rows": len(rows), "reported_total": context.get("total")}},
                0.94, {"batch": batch_hash, "issues": issues}
            ))
    return findings, observations


def _audit_approval_threshold(contract: dict[str, Any], context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [row for row in (context.get("records") or []) if isinstance(row, dict)]
    mappings = dict(contract.get("field_mappings") or {})
    amount_field = contract.get("amount_field")
    status_field = contract.get("approval_status_field")
    reference_field = contract.get("approval_reference_field")
    threshold = float(contract.get("threshold") or 0)
    approved = _normal_values(contract.get("approved_values") or [])
    findings: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if not _filter_row(row, contract.get("filters") or {}, mappings):
            continue
        amount = _number(_row_value(row, amount_field, mappings))
        if amount is None or amount < threshold:
            continue
        status = _norm(_row_value(row, status_field, mappings)) if status_field else ""
        reference = _canon(_row_value(row, reference_field, mappings)) if reference_field else ""
        status_ok = bool(status and status in approved) if status_field else False
        reference_ok = bool(reference) if reference_field else False
        approved_ok = status_ok or reference_ok
        record_hash = _row_identity(row, list(contract.get("identity_fields") or []), mappings, index)
        observations.append({"record_hash": record_hash, "amount": amount, "approved": approved_ok})
        if approved_ok:
            continue
        findings.append(_finding(
            contract, "approval_missing_above_threshold", f"高额业务绕过审批：{contract.get('resource')}",
            f"金额达到 {threshold:g} 的业务记录必须存在已批准状态或审批凭据。", f"金额 {amount:g} 已达到阈值，但未发现有效审批状态/凭据。",
            {"request": {"method": "GET", "path": (contract.get("source") or {}).get("path"), "query": _redact(contract.get("sample_query") or {})}, "record_hash": record_hash, "amount_field": amount_field, "amount": amount, "threshold": threshold, "approval_status_field": status_field, "approval_reference_field": reference_field, "approval_status_normalized": status, "approved_values": sorted(approved), "has_approval_reference": reference_ok, "coverage": {"complete": bool(context.get("complete")), "fetched_rows": len(rows), "reported_total": context.get("total")}},
            0.97, {"record": record_hash, "threshold": threshold, "amount": amount}
        ))
    return findings, observations


def _fetch_context(base_url: str, contract: dict[str, Any], token: str | None, timeout: int, max_bytes: int, max_pages: int, max_records: int) -> dict[str, Any]:
    source = contract.get("source") or {}
    fetch_contract = {"source": source, "sample_query": dict(contract.get("sample_query") or {}), "pagination": dict(contract.get("pagination") or {})}
    context = _fetch_source_pages(base_url, fetch_contract, token, timeout, max_bytes, max_pages)
    rows = list(context.get("records") or [])
    if len(rows) > max_records:
        context = {**context, "records": rows[:max_records], "complete": False, "truncated_by_policy": True}
    return context


def run_business_population_constraints(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    section = _section(cfg)
    profile = build_business_population_constraint_profile(project, root, options)
    mode = str(options.get("execution_mode") or cfg.get("business_population_constraints_execution_mode") or cfg.get("business_population_constraint_execution_mode") or "plan_only").lower()
    if mode not in {"plan_only", "safe_live"}:
        mode = "plan_only"
    base_url = str(cfg.get("base_url") or "")
    timeout = max(1, min(int(cfg.get("request_timeout_seconds") or 10), 30))
    max_bytes = max(100_000, min(int(options.get("max_response_bytes") or section.get("max_response_bytes") or 3_000_000), 20_000_000))
    max_pages = max(1, min(int(options.get("max_pages") or section.get("max_pages") or 12), 100))
    max_records = max(10, min(int(options.get("max_records") or section.get("max_records") or 2000), 10_000))
    token = _normal_token(cfg, project, root, timeout) if mode == "safe_live" and base_url else None
    cache: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []
    semantic_hypotheses: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []

    def fetch(contract: dict[str, Any]) -> dict[str, Any]:
        key = _hash({"path": (contract.get("source") or {}).get("path"), "query": contract.get("sample_query") or {}, "pagination": contract.get("pagination") or {}})
        if key not in cache:
            cache[key] = _fetch_context(base_url, contract, token, timeout, max_bytes, max_pages, max_records)
        return cache[key]

    auditors = {
        "group_limit": _audit_group_limit,
        "interval_overlap": _audit_interval_overlap,
        "composite_unique": _audit_composite_unique,
        "batch_integrity": _audit_batch_integrity,
        "approval_threshold": _audit_approval_threshold,
    }
    for contract in profile.get("contracts") or []:
        cid = str(contract.get("contract_id") or "")
        kind = str(contract.get("contract_kind") or "")
        if mode != "safe_live" or not base_url:
            executions.append({"contract_id": cid, "contract_kind": kind, "status": "planned", "reason": "plan_only_or_missing_base_url"})
            continue
        context = fetch(contract)
        response = (context.get("responses") or [{}])[0]
        if not response.get("status_code") or response.get("error"):
            executions.append({"contract_id": cid, "contract_kind": kind, "status": "error", "reason": "source_fetch_failed", "responses": context.get("responses")})
            continue
        audit = auditors.get(kind)
        if not audit:
            executions.append({"contract_id": cid, "contract_kind": kind, "status": "skipped", "reason": "unsupported_contract_kind"})
            continue
        emitted, observations = audit(contract, context)
        findings.extend(emitted)

    # --- LLM-powered semantic reasoning (Phase61 moat upgrade) ---
    if mode == "safe_live" and findings:
        try:
            import json as _json
            llm_result = _llm_reason("population", {
                "prd_text": "", "api_schema": "", "observed_data": _json.dumps(executions[-5:] if "executions" in dir() else [], ensure_ascii=False, default=str)[:4000],
                "heuristic_findings": _json.dumps(findings[:15], ensure_ascii=False, default=str)[:4000],
            })
            semantic_hypotheses.extend(compile_unverified_semantic_hypotheses(
                (llm_result or {}).get("findings"),
                engine="population",
                type_field="business_population_type",
            ))
        except Exception:
            pass

        executions.append({"contract_id": cid, "contract_kind": kind, "status": "executed", "finding_count": len(emitted), "fetched_rows": len(context.get("records") or []), "reported_total": context.get("total"), "source_complete": bool(context.get("complete")), "truncated_by_policy": bool(context.get("truncated_by_policy")), "observations": observations[:60]})

    output = _output_paths(project, root)
    registry, findings = _update_registry(output["registry"], findings)
    result = {
        "phase": "phase52_business_population_constraints",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "summary": {**(profile.get("summary") or {}), "execution_mode": mode, "executed_contract_count": sum(1 for item in executions if item.get("status") == "executed"), "business_population_finding_count": len(findings), "persistent_business_population_count": sum(1 for item in findings if (item.get("evidence_stability") or {}).get("persistent")), "memory_fingerprint_count": len((registry or {}).get("entries") or {})},
        "profile": _profile_for_persistence(profile),
        "executions": executions,
        "semantic_hypotheses": semantic_hypotheses, "llm_governance": {"status": "unverified_hypothesis_only", "does_not_affect_finding_counts": True, "requires_deterministic_replay": True}, "findings": findings,
        "memory_summary": {"fingerprint_count": len((registry or {}).get("entries") or {}), "updated_at_utc": _now(), "learning_policy": "同一群体约束反例跨运行持续出现时提高置信度；只有人工确认后才进入企业缺陷模式回灌。"},
        "governance": {"execution_mode": mode, "live_requests_limited_to_get": True, "write_execution_disabled": True, "quota_split_and_race_validation_is_sandbox_required": True, "evidence_uses_hashed_identities": True, "raw_business_rows_not_persisted": True, "uses_no_benchmark_answer_files": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    _write_json(output["out"] / "business_population_constraints_run.json", result)
    _write_json(output["workspace"] / "business_population_constraints_run.json", result)
    (output["out"] / "business_population_constraints_run_report.html").write_text(render_business_population_constraint_run_report(result), encoding="utf-8")
    return result


def _render_html(title: str, badge: str, subtitle: str, cards: str, body: str) -> str:
    return f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>{_html_escape(title)}</title><style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#07111d;color:#eaf2ff;margin:0;padding:28px}}.hero,.panel{{background:#101d2c;border:1px solid #2b4260;border-radius:16px;padding:20px;margin-bottom:16px}}.badge{{display:inline-block;background:#174e52;color:#b6fff4;border-radius:999px;padding:4px 10px;font-size:12px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}.card{{background:#132638;border:1px solid #2b4260;border-radius:12px;padding:12px}}.card b{{display:block;font-size:24px;margin-top:5px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #2b4260;text-align:left;vertical-align:top;word-break:break-word}}th{{color:#9dc4ee}}</style><section class='hero'><span class='badge'>{_html_escape(badge)}</span><h1>{_html_escape(title)}</h1><p>{_html_escape(subtitle)}</p></section><section class='panel'><div class='grid'>{cards}</div></section><section class='panel'>{body}</section></html>"""


def render_business_population_constraint_profile_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(label)}</span><b>{_html_escape(value)}</b></div>" for label, value in [("群体契约", summary.get("population_contract_count", 0)), ("额度/配额", summary.get("group_limit_contract_count", 0)), ("时间冲突", summary.get("interval_overlap_contract_count", 0)), ("批次完整性", summary.get("batch_integrity_contract_count", 0)), ("审批阈值", summary.get("approval_threshold_contract_count", 0))])
    rows = "".join(f"<tr><td>{_html_escape(item.get('contract_id'))}</td><td>{_html_escape(item.get('contract_kind'))}</td><td>{_html_escape(item.get('resource'))}</td><td>{_html_escape((item.get('source') or {}).get('path'))}</td><td>{_html_escape(item.get('discovery'))}</td></tr>" for item in (data.get("contracts") or [])[:160])
    return _render_html("Phase52 跨记录业务约束画像", "GET-only · 群体事实", "从企业口径、接口结构与运行数据中建立“多条记录必须共同满足”的业务 Oracle。", cards, f"<h2>可执行契约</h2><table><thead><tr><th>ID</th><th>类型</th><th>资源</th><th>读取接口</th><th>推导来源</th></tr></thead><tbody>{rows or '<tr><td colspan=5>暂无可执行群体约束；可在 business_population_constraints.contracts 显式配置。</td></tr>'}</tbody></table>")


def render_business_population_constraint_run_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(label)}</span><b>{_html_escape(value)}</b></div>" for label, value in [("已执行", summary.get("executed_contract_count", 0)), ("发现问题", summary.get("business_population_finding_count", 0)), ("稳定复现", summary.get("persistent_business_population_count", 0)), ("证据指纹", summary.get("memory_fingerprint_count", 0))])
    rows = "".join(f"<tr><td>{_html_escape(item.get('severity'))}</td><td>{_html_escape(item.get('business_population_type'))}</td><td>{_html_escape(item.get('title'))}</td><td>{_html_escape(item.get('actual'))}</td><td>{_html_escape((item.get('evidence_stability') or {}).get('observations', 1))}</td></tr>" for item in (data.get("findings") or [])[:160])
    return _render_html("Phase52 跨记录业务约束运行报告", str(summary.get("execution_mode") or "plan_only"), "只读验证额度、时间冲突、逻辑重复、批次完整性和高额审批等群体业务事实。", cards, f"<h2>已证伪群体关系</h2><table><thead><tr><th>级别</th><th>类型</th><th>问题</th><th>实际</th><th>观测次数</th></tr></thead><tbody>{rows or '<tr><td colspan=5>未发现已证伪的群体业务约束</td></tr>'}</tbody></table>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase52 cross-record business population constraints")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--mode", default="plan_only", choices=["plan_only", "safe_live"])
    args = parser.parse_args(argv)
    result = run_business_population_constraints(args.project, options={"execution_mode": args.mode})
    print(json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
