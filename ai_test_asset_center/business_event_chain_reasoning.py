from __future__ import annotations

"""Phase53: enterprise event-chain and message consistency counterexample engine.

A large class of enterprise defects sits between otherwise healthy APIs: a
business document is marked paid, approved or shipped, but its outbox event was
never produced; the message is published twice; a consumer result is missing;
events arrive out of order; or a failed/dead-letter message has no diagnostic
information.  These defects are usually invisible to endpoint-level testing.

This module derives executable, GET-only Oracles from OpenAPI, PRD hints and
enterprise configuration.  It validates event delivery, duplicate publication,
event ordering, consumer completion, orphan events and dead-letter diagnostics.
It never publishes, retries or replays messages in a live environment.  Those
write/race experiments are emitted only as sandbox-required plans.
"""

import argparse
import hashlib
import json
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .business_invariant_mining import _infer_identity, _is_collection_read, _item_fields
from .business_outcome_validation import _normal_token, _private_leak_check, _redact, _update_registry
from .business_reconciliation import _fetch_source_pages, _numeric, _row_value
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


EVENT_RESOURCE_RE = re.compile(r"event|message|outbox|queue|stream|topic|webhook|notification|事件|消息|发件箱|队列|主题|通知", re.I)
CONSUMER_RESOURCE_RE = re.compile(r"consumer|delivery|fulfillment|handler|processor|projection|subscriber|消费|投递|履约|处理器|订阅", re.I)
STATUS_RE = re.compile(r"(?:^|[_\-.])(status|state|phase)(?:$|[_\-.])|状态|阶段", re.I)
EVENT_TYPE_RE = re.compile(r"event[_\-]?type|message[_\-]?type|topic|eventname|event|type|事件类型|消息类型|主题", re.I)
EVENT_ID_RE = re.compile(r"event[_\-]?id|message[_\-]?id|idempotency[_\-]?key|trace[_\-]?id|eventid|messageid|事件id|消息id", re.I)
ERROR_RE = re.compile(r"error|reason|detail|exception|failure|message|错误|原因|异常|失败", re.I)
RETRY_RE = re.compile(r"retry|attempt|delivery[_\-]?count|重试|尝试|投递次数", re.I)
SEQUENCE_RE = re.compile(r"sequence|seq|offset|position|order|序号|顺序|偏移", re.I)
TIME_RE = re.compile(r"occurred|created|published|emitted|sent|time|timestamp|at|发生时间|创建时间|发布时间|发送时间", re.I)
ID_RE = re.compile(r"(?:^|[_\-.])(id|uuid|guid|code|number|no|serial)(?:$|[_\-.])|编号|编码|单号", re.I)
SUCCESS_VALUES = {"success", "succeeded", "processed", "completed", "delivered", "published", "done", "handled", "成功", "已成功", "已处理", "已完成", "已投递", "已发布"}
FAILURE_VALUES = {"failed", "failure", "error", "deadletter", "dead_letter", "dlq", "rejected", "cancelled", "失败", "错误", "死信", "已取消", "拒绝"}


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


def _values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [str(item).strip() for item in value if str(item).strip()] if isinstance(value, (list, tuple, set)) else []


def _section(cfg: dict[str, Any]) -> dict[str, Any]:
    value = (
        cfg.get("business_event_chain_reasoning")
        or cfg.get("event_chain_reasoning")
        or cfg.get("message_consistency_reasoning")
        or cfg.get("event_message_oracles")
        or {}
    )
    return value if isinstance(value, dict) else {}


def _output_paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "defect_discovery"
    return {
        "out": root / "platform_outputs" / project / "business_event_chain_reasoning",
        "workspace": workspace,
        "profile": workspace / "business_event_chain_profile.json",
        "registry": workspace / "business_event_chain_evidence_registry.json",
    }


def _resource_key(path: str) -> str:
    parts = [part for part in str(path or "").split("/") if part and not part.startswith("{")]
    value = _norm(parts[-1] if parts else "resource")
    if value.endswith("ies") and len(value) > 4:
        return value[:-3] + "y"
    return value.rstrip("s") or "resource"


def _field_name(fields: dict[str, Any], desired: Any) -> str | None:
    target = _norm(desired)
    if not target:
        return None
    for field in fields:
        if _norm(field) == target:
            return str(field)
    for field in fields:
        current = _norm(field)
        if target in current or current in target:
            return str(field)
    return None


def _first_field(fields: dict[str, Any], explicit: Any, pattern: re.Pattern[str]) -> str | None:
    for item in _values(explicit):
        match = _field_name(fields, item)
        if match:
            return match
    for field in fields:
        if pattern.search(str(field)):
            return str(field)
    return None


def _catalog(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    components = openapi.get("components") or {}
    rows: list[dict[str, Any]] = []
    for operation in _operations(openapi):
        if not _is_collection_read(operation, components):
            continue
        path = str(operation.get("path") or "")
        fields = _item_fields(operation, components)
        if not fields:
            continue
        resource = _resource_key(path)
        rows.append({
            "path": path,
            "method": "GET",
            "resource": resource,
            "fields": fields,
            "parameters": list(operation.get("parameters") or []),
            "summary": str(operation.get("summary") or operation.get("operation_id") or resource),
            "identity_field": _infer_identity(resource, fields, {}),
        })
    return rows


def _find_collection(catalog: list[dict[str, Any]], value: Any) -> dict[str, Any] | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    target = raw.rstrip("/") or "/"
    for row in catalog:
        if str(row.get("path") or "").rstrip("/") == target:
            return row
    wanted = _norm(raw)
    for row in catalog:
        if _norm(row.get("resource")) == wanted:
            return row
    return None


def _foreign_key_for_source(fields: dict[str, Any], source: dict[str, Any], configured: Any = None) -> str | None:
    explicit = _field_name(fields, configured)
    if explicit:
        return explicit
    resource = _norm(source.get("resource"))
    identity = _norm(source.get("identity_field"))
    candidates: list[tuple[int, str]] = []
    for field in fields:
        norm = _norm(field)
        score = 0
        if resource and resource in norm and norm.endswith("id"):
            score += 8
        if identity and identity == norm:
            score += 6
        if norm in {"sourceid", "entityid", "aggregateid", "businessid", "recordid"}:
            score += 3
        if score:
            candidates.append((-score, str(field)))
    return sorted(candidates)[0][1] if candidates else None


def _field_value(row: dict[str, Any], field: str | None, mappings: dict[str, Any]) -> Any:
    return _row_value(row, field, mappings) if isinstance(row, dict) and field else None


def _row_hash(row: dict[str, Any], fields: list[str], mappings: dict[str, Any], index: int) -> str:
    values = [_canon(_field_value(row, field, mappings)) for field in fields]
    if any(values):
        return _short({"fields": fields, "values": values})
    return _short({"index": index, "shape": sorted(map(str, row.keys()))})


def _state_set(raw: Any, fallback: set[str]) -> set[str]:
    values = {_norm(item) for item in _values(raw) if _norm(item)}
    return values or set(fallback)


def _configured_contracts(section: dict[str, Any]) -> list[dict[str, Any]]:
    raw = section.get("contracts") or section.get("event_contracts") or section.get("oracles") or []
    if isinstance(raw, dict):
        raw = [raw]
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _contract_kind(raw: Any) -> str | None:
    text = _norm(raw)
    aliases = {
        "eventdelivery": "event_delivery",
        "deliverycoverage": "event_delivery",
        "requiredelivery": "event_delivery",
        "eventrequired": "event_delivery",
        "eventduplicate": "event_duplicate",
        "duplicatepublish": "event_duplicate",
        "duplicateevent": "event_duplicate",
        "eventordering": "event_ordering",
        "eventorder": "event_ordering",
        "eventsequence": "event_ordering",
        "consumeroutcome": "consumer_outcome",
        "consumercoverage": "consumer_outcome",
        "consumercompletion": "consumer_outcome",
        "eventorphan": "event_orphan",
        "orphanmessage": "event_orphan",
        "deadletter": "dead_letter_diagnostics",
        "dlq": "dead_letter_diagnostics",
        "deadletterdiagnostics": "dead_letter_diagnostics",
    }
    return aliases.get(text, text if text in {"event_delivery", "event_duplicate", "event_ordering", "consumer_outcome", "event_orphan", "dead_letter_diagnostics"} else None)


def _collection_ref(row: dict[str, Any], query: dict[str, Any] | None = None, pagination: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"path": row.get("path"), "method": "GET", "resource": row.get("resource"), "fields": row.get("fields") or {}, "parameters": row.get("parameters") or [], "query": dict(query or {}), "pagination": dict(pagination or {})}


def _contract_from_config(row: dict[str, Any], catalog: list[dict[str, Any]], number: int) -> dict[str, Any] | None:
    kind = _contract_kind(row.get("type") or row.get("contract_kind") or row.get("kind"))
    if not kind:
        return None
    source = _find_collection(catalog, row.get("source_path") or row.get("source") or row.get("path"))
    event = _find_collection(catalog, row.get("event_path") or row.get("events_path") or row.get("message_path") or row.get("event_resource"))
    consumer = _find_collection(catalog, row.get("consumer_path") or row.get("outcome_path") or row.get("consumer_resource"))
    if kind in {"event_delivery", "consumer_outcome", "event_orphan"} and (not source or not event):
        return None
    if kind == "consumer_outcome" and not consumer:
        return None
    if kind in {"event_duplicate", "event_ordering", "dead_letter_diagnostics"} and not event:
        return None
    source_fields = dict((source or {}).get("fields") or {})
    event_fields = dict((event or {}).get("fields") or {})
    consumer_fields = dict((consumer or {}).get("fields") or {})
    mappings = dict(row.get("field_mappings") or {})
    source_id = _first_field(source_fields, row.get("source_id_field") or row.get("source_identity_field"), ID_RE) or (source or {}).get("identity_field")
    event_fk = _foreign_key_for_source(event_fields, source or {}, row.get("event_foreign_key_field") or row.get("source_foreign_key")) if source else _field_name(event_fields, row.get("event_foreign_key_field") or row.get("source_foreign_key"))
    consumer_fk = _foreign_key_for_source(consumer_fields, source or {}, row.get("consumer_foreign_key_field")) if source and consumer else None
    event_id = _first_field(event_fields, row.get("event_id_field") or row.get("message_id_field"), EVENT_ID_RE) or _first_field(event_fields, row.get("identity_field"), ID_RE) or (event or {}).get("identity_field")
    event_type = _first_field(event_fields, row.get("event_type_field") or row.get("message_type_field"), EVENT_TYPE_RE)
    event_status = _first_field(event_fields, row.get("event_status_field") or row.get("status_field"), STATUS_RE)
    consumer_status = _first_field(consumer_fields, row.get("consumer_status_field") or row.get("outcome_status_field"), STATUS_RE)
    source_status = _first_field(source_fields, row.get("source_status_field") or row.get("source_state_field"), STATUS_RE)
    error_field = _first_field(event_fields, row.get("error_field") or row.get("diagnostic_field"), ERROR_RE)
    retry_field = _first_field(event_fields, row.get("retry_field") or row.get("attempt_field"), RETRY_RE)
    sequence_field = _first_field(event_fields, row.get("sequence_field"), SEQUENCE_RE)
    event_time_field = _first_field(event_fields, row.get("event_time_field") or row.get("occurred_at_field"), TIME_RE)
    return {
        "contract_id": f"BEC_CONTRACT_{number:04d}",
        "contract_kind": kind,
        "resource": str((source or event or consumer or {}).get("resource") or "event_chain"),
        "source": _collection_ref(source, row.get("source_query"), row.get("source_pagination")) if source else None,
        "event": _collection_ref(event, row.get("event_query"), row.get("event_pagination")) if event else None,
        "consumer": _collection_ref(consumer, row.get("consumer_query"), row.get("consumer_pagination")) if consumer else None,
        "source_identity_field": source_id,
        "source_status_field": source_status,
        "eligible_source_states": _values(row.get("eligible_source_states") or row.get("source_states") or row.get("states")),
        "event_foreign_key_field": event_fk,
        "event_id_field": event_id,
        "event_type_field": event_type,
        "event_status_field": event_status,
        "required_event_types": _values(row.get("required_event_types") or row.get("event_types") or row.get("expected_event_types")),
        "delivered_event_statuses": _values(row.get("delivered_event_statuses") or row.get("delivered_statuses")),
        "consumer_foreign_key_field": consumer_fk,
        "consumer_status_field": consumer_status,
        "consumer_success_statuses": _values(row.get("consumer_success_statuses") or row.get("consumer_success_values")),
        "error_field": error_field,
        "retry_field": retry_field,
        "max_retries": row.get("max_retries"),
        "dead_letter_statuses": _values(row.get("dead_letter_statuses") or row.get("failure_statuses")),
        "sequence_field": sequence_field,
        "event_time_field": event_time_field,
        "expected_event_types": _values(row.get("expected_event_types") or row.get("event_order")),
        "max_events_per_source_type": row.get("max_events_per_source_type"),
        "field_mappings": mappings,
        "discovery": "enterprise_config",
        "source_evidence": ["enterprise_config", "openapi"],
    }


def _auto_contracts(catalog: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contracts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    events = [row for row in catalog if EVENT_RESOURCE_RE.search(str(row.get("resource")) + " " + str(row.get("summary")))]
    sources = [row for row in catalog if row not in events]
    for event in events:
        fields = dict(event.get("fields") or {})
        event_id = _first_field(fields, None, EVENT_ID_RE) or event.get("identity_field")
        status = _first_field(fields, None, STATUS_RE)
        source_matches: list[tuple[dict[str, Any], str]] = []
        for source in sources:
            fk = _foreign_key_for_source(fields, source)
            if fk:
                source_matches.append((source, fk))
        if event_id:
            contracts.append({
                "contract_id": "", "contract_kind": "event_duplicate", "resource": event.get("resource"), "event": _collection_ref(event),
                "event_id_field": event_id, "event_type_field": _first_field(fields, None, EVENT_TYPE_RE), "event_foreign_key_field": source_matches[0][1] if source_matches else None,
                "event_status_field": status, "field_mappings": {}, "discovery": "openapi_event_schema", "source_evidence": ["openapi", "event_resource_semantics"],
            })
        if status:
            contracts.append({
                "contract_id": "", "contract_kind": "dead_letter_diagnostics", "resource": event.get("resource"), "event": _collection_ref(event),
                "event_id_field": event_id, "event_status_field": status, "error_field": _first_field(fields, None, ERROR_RE), "retry_field": _first_field(fields, None, RETRY_RE),
                "dead_letter_statuses": [], "field_mappings": {}, "discovery": "openapi_event_schema", "source_evidence": ["openapi", "event_resource_semantics"],
            })
        for source, fk in source_matches[:2]:
            contracts.append({
                "contract_id": "", "contract_kind": "event_orphan", "resource": source.get("resource"), "source": _collection_ref(source), "event": _collection_ref(event),
                "source_identity_field": source.get("identity_field"), "event_foreign_key_field": fk, "event_id_field": event_id,
                "field_mappings": {}, "discovery": "openapi_event_foreign_key", "source_evidence": ["openapi", "foreign_key_shape"],
            })
    if not events:
        candidates.append({"candidate_id": "BEC_NO_EVENT_COLLECTION", "risk_type": "event_chain_contract_gap", "severity": "P2", "title": "未发现可读取的事件/消息集合", "detail": "为 outbox、event、message、queue、dead-letter 或消费结果接口补充 GET schema，或显式配置 business_event_chain_reasoning.contracts。"})
    return contracts[:180], candidates[:80]


def build_business_event_chain_contracts(openapi: dict[str, Any], cfg: dict[str, Any], prd_text: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    section = _section(cfg)
    catalog = _catalog(openapi)
    configured = _configured_contracts(section)
    contracts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for row in configured:
        item = _contract_from_config(row, catalog, len(contracts) + 1)
        if item:
            contracts.append(item)
        else:
            candidates.append({"candidate_id": f"BEC_CONFIG_GAP_{len(candidates)+1:04d}", "risk_type": "event_chain_contract_gap", "severity": "P2", "title": "事件链契约无法映射到可读取集合", "detail": f"检查 source_path/event_path/consumer_path 是否存在 GET schema：{str(row.get('type') or row.get('contract_kind') or 'unknown')}。"})
    auto, auto_candidates = _auto_contracts(catalog)
    configured_keys = {(str(item.get("contract_kind")), str((item.get("source") or {}).get("path")), str((item.get("event") or {}).get("path")), str((item.get("consumer") or {}).get("path"))) for item in contracts}
    for item in auto:
        key = (str(item.get("contract_kind")), str((item.get("source") or {}).get("path")), str((item.get("event") or {}).get("path")), str((item.get("consumer") or {}).get("path")))
        if key not in configured_keys:
            item["contract_id"] = f"BEC_CONTRACT_{len(contracts)+1:04d}"
            contracts.append(item)
            configured_keys.add(key)
    candidates.extend(auto_candidates)
    if re.search(r"事件|消息|队列|异步|消费者|死信|outbox|event|message|queue|consumer|dead letter", prd_text or "", re.I) and not contracts:
        candidates.append({"candidate_id": "BEC_PRD_UNMAPPED", "risk_type": "event_chain_contract_gap", "severity": "P2", "title": "PRD 包含事件/消息一致性要求，但未形成可执行 Oracle", "detail": "补充事件、消费结果或业务源数据的 GET 接口 schema，并在 business_event_chain_reasoning.contracts 中配置关联字段。"})
    for contract in contracts:
        kind = contract.get("contract_kind")
        if kind in {"event_delivery", "event_orphan", "consumer_outcome"} and not contract.get("event_foreign_key_field"):
            candidates.append({"candidate_id": f"{contract['contract_id']}_NO_EVENT_FK", "risk_type": "event_chain_contract_gap", "severity": "P2", "title": "事件缺少业务关联键，无法验证因果链", "detail": "配置 event_foreign_key_field，例如 order_id、invoice_id、ticket_id。"})
        if kind == "event_delivery" and not contract.get("source_identity_field"):
            candidates.append({"candidate_id": f"{contract['contract_id']}_NO_SOURCE_ID", "risk_type": "event_chain_contract_gap", "severity": "P2", "title": "业务源集合缺少稳定主键，无法验证事件覆盖", "detail": "配置 source_id_field，例如 order_id、payment_id、approval_id。"})
    return contracts[:300], candidates[:160]


def _summary(contracts: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, int]:
    kinds = [str(item.get("contract_kind") or "") for item in contracts]
    return {
        "event_chain_contract_count": len(contracts),
        "event_delivery_contract_count": kinds.count("event_delivery"),
        "event_duplicate_contract_count": kinds.count("event_duplicate"),
        "event_ordering_contract_count": kinds.count("event_ordering"),
        "consumer_outcome_contract_count": kinds.count("consumer_outcome"),
        "event_orphan_contract_count": kinds.count("event_orphan"),
        "dead_letter_contract_count": kinds.count("dead_letter_diagnostics"),
        "contract_gap_count": len(candidates),
    }


def build_business_event_chain_profile(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    paths = config_paths(project, root)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    openapi = openapi if isinstance(openapi, dict) else {}
    contracts, candidates = build_business_event_chain_contracts(openapi, cfg, _read_text(paths["input_dir"] / "prd.md"))
    learning = ingest_confirmed_bug_feedback(project, root)
    memory = learning.get("memory") or {}
    for contract in contracts:
        bonus, matches = _learning_bonus(contract, memory)
        contract["learning_bonus"] = bonus
        contract["learning_matches"] = matches
    profile = {
        "phase": "phase53_business_event_chain_reasoning",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "contracts": contracts,
        "candidates": candidates,
        "summary": {**_summary(contracts, candidates), "confirmed_bug_memory_count": int((learning.get("summary") or {}).get("confirmed_bug_memory_count") or 0)},
        "governance": {"default_execution": "plan_only", "safe_live_only_uses_get": True, "message_publish_retry_replay_are_sandbox_required": True, "evidence_uses_hashed_identities": True, "raw_business_rows_are_not_persisted": True, "findings_need_human_review": True},
    }
    profile["private_leak_check"] = _private_leak_check(profile)
    output = _output_paths(project, root)
    output["out"].mkdir(parents=True, exist_ok=True)
    output["workspace"].mkdir(parents=True, exist_ok=True)
    _write_json(output["out"] / "business_event_chain_profile.json", profile)
    _write_json(output["workspace"] / "business_event_chain_profile.json", profile)
    (output["out"] / "business_event_chain_profile_report.html").write_text(render_business_event_chain_profile_report(profile), encoding="utf-8")
    return profile


def load_business_event_chain_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _load_json(_output_paths(project, root)["profile"], {})
    return data if isinstance(data, dict) and data.get("phase") == "phase53_business_event_chain_reasoning" else None


def _probe(contract: dict[str, Any], number: int, kind: str, title: str, risk_type: str, **extra: Any) -> dict[str, Any]:
    return {
        "probe_id": f"BEC_PROBE_{number:04d}",
        "source": "business_event_chain_reasoning",
        "business_event_chain_type": kind,
        "contract_id": contract.get("contract_id"),
        "title": title,
        "risk_type": risk_type,
        "severity": extra.pop("severity", "P1"),
        "expected": extra.pop("expected", "业务状态、事件消息和消费者结果必须形成完整一致的因果链。"),
        "method": "GET",
        "path": extra.pop("path", str((contract.get("event") or contract.get("source") or {}).get("path") or "")),
        "actor": "normal_user",
        "destructive": False,
        "execution_policy": extra.pop("execution_policy", "safe_read_only"),
        "learning_bonus": contract.get("learning_bonus") or 0.0,
        "learning_matches": contract.get("learning_matches") or [],
        **extra,
    }


def generate_business_event_chain_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    profile = load_business_event_chain_profile(project_id, root) or build_business_event_chain_profile(project_id, root)
    probes: list[dict[str, Any]] = []
    for contract in profile.get("contracts") or []:
        kind = str(contract.get("contract_kind") or "")
        resource = str(contract.get("resource") or "业务事件")
        if kind == "event_delivery":
            probes.append(_probe(contract, len(probes) + 1, "event_delivery", f"事件投递覆盖：{resource} 状态变化必须产生业务事件", "event_delivery_missing"))
            probes.append(_probe(contract, len(probes) + 1, "event_replay_sandbox", f"事件重放幂等：{resource} 重放不得重复产生副作用", "event_replay_idempotency", execution_policy="sandbox_required", expected="在隔离环境重放同一事件时，业务副作用只能生效一次。"))
        elif kind == "event_duplicate":
            probes.append(_probe(contract, len(probes) + 1, "event_duplicate", f"消息幂等：{resource} 不得重复发布/投递", "event_duplicate_publish"))
            probes.append(_probe(contract, len(probes) + 1, "event_retry_race_sandbox", f"消息重试竞态：{resource} 重试不得放大业务副作用", "event_retry_race", execution_policy="sandbox_required", expected="隔离环境中的重复投递/超时重试不得造成重复扣款、重复库存或重复通知。"))
        elif kind == "event_ordering":
            probes.append(_probe(contract, len(probes) + 1, "event_ordering", f"事件顺序：{resource} 生命周期事件不得乱序", "event_ordering_violation"))
        elif kind == "consumer_outcome":
            probes.append(_probe(contract, len(probes) + 1, "consumer_outcome", f"消费者闭环：{resource} 已投递事件必须有成功处理结果", "event_consumer_missing"))
            probes.append(_probe(contract, len(probes) + 1, "consumer_replay_sandbox", f"消费者重入：{resource} 重复消费不得重复落库", "event_consumer_idempotency", execution_policy="sandbox_required", expected="隔离环境中同一事件被消费者重入时，处理结果不得重复写入。"))
        elif kind == "event_orphan":
            probes.append(_probe(contract, len(probes) + 1, "event_orphan", f"事件可追溯：{resource} 消息不得引用不存在业务实体", "event_orphan"))
        elif kind == "dead_letter_diagnostics":
            probes.append(_probe(contract, len(probes) + 1, "dead_letter_diagnostics", f"死信可诊断：{resource} 失败消息必须保留原因与重试信息", "event_dead_letter_diagnostics", severity="P2"))
    return probes[: max(1, int(max_count or 180))]


def _fetch_collection(base_url: str, collection: dict[str, Any] | None, token: str | None, timeout: int, max_bytes: int, max_pages: int, max_records: int) -> dict[str, Any]:
    collection = collection or {}
    request_contract = {"source": collection, "sample_query": dict(collection.get("query") or {}), "pagination": dict(collection.get("pagination") or {})}
    context = _fetch_source_pages(base_url, request_contract, token, timeout, max_bytes, max_pages)
    rows = list(context.get("records") or [])
    if len(rows) > max_records:
        context = {**context, "records": rows[:max_records], "complete": False, "truncated_by_policy": True}
    return context


def _finding(contract: dict[str, Any], kind: str, title: str, risk_type: str, expected: str, actual: str, evidence: dict[str, Any], confidence: float, key: Any) -> dict[str, Any]:
    fingerprint = _hash({"phase": "phase53", "contract": contract.get("contract_id"), "kind": kind, "key": key})
    return {
        "fingerprint": fingerprint,
        "contract_id": contract.get("contract_id"),
        "business_event_chain_type": kind,
        "risk_type": risk_type,
        "severity": "P1" if kind in {"event_delivery_missing", "event_duplicate", "event_orphan", "consumer_outcome_missing"} else "P2",
        "title": title,
        "expected": expected,
        "actual": actual,
        "confidence": round(min(0.98, confidence + float(contract.get("learning_bonus") or 0.0)), 3),
        "evidence": evidence,
        "needs_human_review": True,
        "learning_matches": contract.get("learning_matches") or [],
    }


def _coverage(context: dict[str, Any]) -> dict[str, Any]:
    return {"complete": bool(context.get("complete")), "fetched_rows": len(context.get("records") or []), "reported_total": context.get("total"), "truncated_by_policy": bool(context.get("truncated_by_policy"))}


def _source_index(rows: list[dict[str, Any]], field: str | None, mappings: dict[str, Any]) -> dict[str, list[tuple[int, dict[str, Any]]]]:
    out: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, row in enumerate(rows):
        key = _canon(_field_value(row, field, mappings))
        if key:
            out[key].append((index, row))
    return out


def _event_rows_for_source(rows: list[dict[str, Any]], fk: str | None, source_id: str, mappings: dict[str, Any]) -> list[tuple[int, dict[str, Any]]]:
    return [(index, row) for index, row in enumerate(rows) if _canon(_field_value(row, fk, mappings)) == source_id]


def _matches_type(row: dict[str, Any], field: str | None, wanted: set[str], mappings: dict[str, Any]) -> bool:
    return not wanted or _norm(_field_value(row, field, mappings)) in wanted


def _is_delivered(row: dict[str, Any], field: str | None, delivered: set[str], mappings: dict[str, Any]) -> bool:
    return not field or _norm(_field_value(row, field, mappings)) in delivered


def _audit_event_delivery(contract: dict[str, Any], source_ctx: dict[str, Any], event_ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_rows = [row for row in source_ctx.get("records") or [] if isinstance(row, dict)]
    event_rows = [row for row in event_ctx.get("records") or [] if isinstance(row, dict)]
    mappings = dict(contract.get("field_mappings") or {})
    source_id_field = contract.get("source_identity_field")
    state_field = contract.get("source_status_field")
    eligible_states = _state_set(contract.get("eligible_source_states"), set())
    required_types = _state_set(contract.get("required_event_types"), set())
    delivered = _state_set(contract.get("delivered_event_statuses"), SUCCESS_VALUES)
    missing: list[str] = []
    eligible = 0
    for index, row in enumerate(source_rows):
        state = _norm(_field_value(row, state_field, mappings)) if state_field else ""
        if eligible_states and state not in eligible_states:
            continue
        source_id = _canon(_field_value(row, source_id_field, mappings))
        if not source_id:
            continue
        eligible += 1
        matched = [event for _, event in _event_rows_for_source(event_rows, contract.get("event_foreign_key_field"), source_id, mappings) if _matches_type(event, contract.get("event_type_field"), required_types, mappings) and _is_delivered(event, contract.get("event_status_field"), delivered, mappings)]
        if not matched:
            missing.append(_row_hash(row, [str(source_id_field or "")], mappings, index))
    observations = [{"eligible_source_count": eligible, "missing_event_source_count": len(missing), "required_event_types": sorted(required_types)}]
    if not missing:
        return [], observations
    confidence = 0.96 if bool(source_ctx.get("complete")) and bool(event_ctx.get("complete")) else 0.88
    return [_finding(contract, "event_delivery_missing", f"业务事件缺失：{contract.get('resource')} 已达成状态但未产生事件", "event_delivery_missing", "满足状态条件的业务实体必须产生并成功投递所需事件。", f"发现 {len(missing)} 条业务实体没有可匹配的已投递事件。", {"source_request": {"method": "GET", "path": (contract.get("source") or {}).get("path"), "query": _redact((contract.get("source") or {}).get("query") or {})}, "event_request": {"method": "GET", "path": (contract.get("event") or {}).get("path"), "query": _redact((contract.get("event") or {}).get("query") or {})}, "source_state_field": state_field, "eligible_states": sorted(eligible_states), "required_event_types": sorted(required_types), "sample_source_hashes": missing[:12], "coverage": {"source": _coverage(source_ctx), "event": _coverage(event_ctx)}}, confidence, {"missing": missing[:30], "types": sorted(required_types)})], observations


def _audit_event_duplicate(contract: dict[str, Any], event_ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [row for row in event_ctx.get("records") or [] if isinstance(row, dict)]
    mappings = dict(contract.get("field_mappings") or {})
    event_id = contract.get("event_id_field")
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        key = _canon(_field_value(row, event_id, mappings))
        if key:
            groups[key].append(index)
    duplicate_entries = [{"event_hash": _short(key), "count": len(indices), "row_hashes": [_row_hash(rows[i], [str(event_id or "")], mappings, i) for i in indices[:8]]} for key, indices in groups.items() if len(indices) > 1]
    max_per_source = _numeric(contract.get("max_events_per_source_type"))
    if max_per_source is not None and contract.get("event_foreign_key_field"):
        grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
        for index, row in enumerate(rows):
            fk = _canon(_field_value(row, contract.get("event_foreign_key_field"), mappings))
            typ = _canon(_field_value(row, contract.get("event_type_field"), mappings))
            if fk:
                grouped[(fk, typ)].append(index)
        for (fk, typ), indices in grouped.items():
            if len(indices) > int(max_per_source):
                duplicate_entries.append({"source_hash": _short(fk), "type_hash": _short(typ), "count": len(indices), "row_hashes": [_row_hash(rows[i], [str(event_id or contract.get("event_foreign_key_field") or "")], mappings, i) for i in indices[:8]], "kind": "source_type_over_limit"})
    observations = [{"event_count": len(rows), "duplicate_group_count": len(duplicate_entries)}]
    if not duplicate_entries:
        return [], observations
    confidence = 0.96 if bool(event_ctx.get("complete")) else 0.89
    return [_finding(contract, "event_duplicate", f"重复消息/事件：{contract.get('resource')} 同一事件被重复发布或保留", "event_duplicate_publish", "同一事件标识只能出现一次；一对一事件类型不得因重试而重复投递。", f"发现 {len(duplicate_entries)} 组重复事件标识或超限投递。", {"event_request": {"method": "GET", "path": (contract.get("event") or {}).get("path"), "query": _redact((contract.get("event") or {}).get("query") or {})}, "event_id_field": event_id, "duplicates": duplicate_entries[:12], "coverage": _coverage(event_ctx)}, confidence, {"duplicates": [(item.get("event_hash"), item.get("source_hash"), item.get("type_hash")) for item in duplicate_entries[:30]]})], observations


def _audit_event_ordering(contract: dict[str, Any], event_ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [row for row in event_ctx.get("records") or [] if isinstance(row, dict)]
    mappings = dict(contract.get("field_mappings") or {})
    expected = [_norm(item) for item in _values(contract.get("expected_event_types")) if _norm(item)]
    if len(expected) < 2:
        return [], [{"reason": "expected_event_types_not_configured", "event_count": len(rows)}]
    fk = contract.get("event_foreign_key_field")
    type_field = contract.get("event_type_field")
    sequence_field = contract.get("sequence_field")
    rank = {name: index for index, name in enumerate(expected)}
    grouped: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    for index, row in enumerate(rows):
        source = _canon(_field_value(row, fk, mappings))
        event_type = _norm(_field_value(row, type_field, mappings))
        if source and event_type in rank:
            grouped[source].append((index, row))
    violations: list[dict[str, Any]] = []
    for source, entries in grouped.items():
        if sequence_field:
            entries.sort(key=lambda item: (_numeric(_field_value(item[1], sequence_field, mappings)) if _numeric(_field_value(item[1], sequence_field, mappings)) is not None else 10**18, item[0]))
        else:
            entries.sort(key=lambda item: item[0])
        ranks = [rank[_norm(_field_value(row, type_field, mappings))] for _, row in entries]
        if any(current < previous for previous, current in zip(ranks, ranks[1:])):
            violations.append({"source_hash": _short(source), "type_order": [expected[item] for item in ranks], "event_hashes": [_row_hash(row, [str(contract.get("event_id_field") or fk or "")], mappings, index) for index, row in entries[:8]]})
    observations = [{"ordered_source_count": len(grouped), "ordering_violation_count": len(violations), "expected_event_types": expected}]
    if not violations:
        return [], observations
    confidence = 0.94 if bool(event_ctx.get("complete")) else 0.86
    return [_finding(contract, "event_ordering", f"事件乱序：{contract.get('resource')} 生命周期事件违反既定顺序", "event_ordering_violation", "同一业务实体的事件顺序必须符合定义的生命周期顺序。", f"发现 {len(violations)} 个业务实体出现事件回退或乱序。", {"event_request": {"method": "GET", "path": (contract.get("event") or {}).get("path"), "query": _redact((contract.get("event") or {}).get("query") or {})}, "expected_event_types": expected, "sequence_field": sequence_field, "violations": violations[:12], "coverage": _coverage(event_ctx)}, confidence, {"sources": [item["source_hash"] for item in violations[:30]], "order": expected})], observations


def _audit_consumer_outcome(contract: dict[str, Any], event_ctx: dict[str, Any], consumer_ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events = [row for row in event_ctx.get("records") or [] if isinstance(row, dict)]
    consumers = [row for row in consumer_ctx.get("records") or [] if isinstance(row, dict)]
    mappings = dict(contract.get("field_mappings") or {})
    delivered = _state_set(contract.get("delivered_event_statuses"), SUCCESS_VALUES)
    required_types = _state_set(contract.get("required_event_types"), set())
    consumer_success = _state_set(contract.get("consumer_success_statuses"), SUCCESS_VALUES)
    consumer_index = _source_index(consumers, contract.get("consumer_foreign_key_field"), mappings)
    missing: list[str] = []
    eligible = 0
    for index, event in enumerate(events):
        if not _is_delivered(event, contract.get("event_status_field"), delivered, mappings):
            continue
        if not _matches_type(event, contract.get("event_type_field"), required_types, mappings):
            continue
        source = _canon(_field_value(event, contract.get("event_foreign_key_field"), mappings))
        if not source:
            continue
        eligible += 1
        outcomes = consumer_index.get(source, [])
        succeeded = [row for _, row in outcomes if not contract.get("consumer_status_field") or _norm(_field_value(row, contract.get("consumer_status_field"), mappings)) in consumer_success]
        if not succeeded:
            missing.append(_row_hash(event, [str(contract.get("event_id_field") or contract.get("event_foreign_key_field") or "")], mappings, index))
    observations = [{"eligible_delivered_event_count": eligible, "missing_consumer_outcome_count": len(missing)}]
    if not missing:
        return [], observations
    confidence = 0.95 if bool(event_ctx.get("complete")) and bool(consumer_ctx.get("complete")) else 0.87
    return [_finding(contract, "consumer_outcome_missing", f"消费者结果缺失：{contract.get('resource')} 事件已投递但未形成成功处理结果", "event_consumer_missing", "已投递的业务事件必须在消费结果集合中形成可追溯的成功处理记录。", f"发现 {len(missing)} 个已投递事件没有匹配的成功消费者结果。", {"event_request": {"method": "GET", "path": (contract.get("event") or {}).get("path")}, "consumer_request": {"method": "GET", "path": (contract.get("consumer") or {}).get("path")}, "sample_event_hashes": missing[:12], "consumer_success_statuses": sorted(consumer_success), "coverage": {"event": _coverage(event_ctx), "consumer": _coverage(consumer_ctx)}}, confidence, {"missing": missing[:30]})], observations


def _audit_event_orphan(contract: dict[str, Any], source_ctx: dict[str, Any], event_ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not bool(source_ctx.get("complete")):
        return [], [{"reason": "source_snapshot_incomplete", "source_coverage": _coverage(source_ctx)}]
    sources = [row for row in source_ctx.get("records") or [] if isinstance(row, dict)]
    events = [row for row in event_ctx.get("records") or [] if isinstance(row, dict)]
    mappings = dict(contract.get("field_mappings") or {})
    source_ids = set(_source_index(sources, contract.get("source_identity_field"), mappings))
    orphans: list[str] = []
    for index, event in enumerate(events):
        ref = _canon(_field_value(event, contract.get("event_foreign_key_field"), mappings))
        if ref and ref not in source_ids:
            orphans.append(_row_hash(event, [str(contract.get("event_id_field") or contract.get("event_foreign_key_field") or "")], mappings, index))
    observations = [{"source_id_count": len(source_ids), "event_count": len(events), "orphan_event_count": len(orphans)}]
    if not orphans:
        return [], observations
    confidence = 0.97 if bool(event_ctx.get("complete")) else 0.91
    return [_finding(contract, "event_orphan", f"孤儿事件：{contract.get('resource')} 事件引用不存在业务实体", "event_orphan", "每个业务事件必须关联到可读取且存在的主业务实体。", f"发现 {len(orphans)} 条事件引用了不存在的业务实体。", {"source_request": {"method": "GET", "path": (contract.get("source") or {}).get("path")}, "event_request": {"method": "GET", "path": (contract.get("event") or {}).get("path")}, "sample_event_hashes": orphans[:12], "coverage": {"source": _coverage(source_ctx), "event": _coverage(event_ctx)}}, confidence, {"orphans": orphans[:30]})], observations


def _audit_dead_letter(contract: dict[str, Any], event_ctx: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = [row for row in event_ctx.get("records") or [] if isinstance(row, dict)]
    mappings = dict(contract.get("field_mappings") or {})
    failures = _state_set(contract.get("dead_letter_statuses"), FAILURE_VALUES)
    status_field = contract.get("event_status_field")
    error_field = contract.get("error_field")
    retry_field = contract.get("retry_field")
    max_retries = _numeric(contract.get("max_retries"))
    missing_diag: list[str] = []
    retry_over: list[str] = []
    affected = 0
    for index, row in enumerate(rows):
        status = _norm(_field_value(row, status_field, mappings)) if status_field else ""
        if status not in failures:
            continue
        affected += 1
        row_hash = _row_hash(row, [str(contract.get("event_id_field") or "")], mappings, index)
        if error_field and not _canon(_field_value(row, error_field, mappings)):
            missing_diag.append(row_hash)
        retry = _numeric(_field_value(row, retry_field, mappings)) if retry_field else None
        if max_retries is not None and retry is not None and retry > max_retries:
            retry_over.append(row_hash)
    observations = [{"failed_or_dead_letter_count": affected, "missing_diagnostic_count": len(missing_diag), "retry_over_limit_count": len(retry_over)}]
    if not missing_diag and not retry_over:
        return [], observations
    issues: list[str] = []
    if missing_diag:
        issues.append(f"{len(missing_diag)} 条失败/死信事件缺少诊断信息")
    if retry_over:
        issues.append(f"{len(retry_over)} 条事件重试次数超过上限")
    confidence = 0.93 if bool(event_ctx.get("complete")) else 0.85
    return [_finding(contract, "dead_letter_diagnostics", f"死信/失败事件不可诊断：{contract.get('resource')}", "event_dead_letter_diagnostics", "失败或死信事件必须包含可追溯失败原因，且重试次数不得无界增长。", "；".join(issues), {"event_request": {"method": "GET", "path": (contract.get("event") or {}).get("path")}, "status_field": status_field, "error_field": error_field, "retry_field": retry_field, "max_retries": max_retries, "sample_missing_diagnostic_hashes": missing_diag[:12], "sample_retry_over_limit_hashes": retry_over[:12], "coverage": _coverage(event_ctx)}, confidence, {"missing": missing_diag[:30], "retry": retry_over[:30]})], observations


def run_business_event_chain_reasoning(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    section = _section(cfg)
    profile = build_business_event_chain_profile(project, root, options)
    mode = str(options.get("execution_mode") or cfg.get("business_event_chain_execution_mode") or cfg.get("event_chain_execution_mode") or "plan_only").lower()
    if mode not in {"plan_only", "safe_live"}:
        mode = "plan_only"
    base_url = str(cfg.get("base_url") or "")
    timeout = max(1, min(int(cfg.get("request_timeout_seconds") or 10), 30))
    max_bytes = max(100_000, min(int(options.get("max_response_bytes") or section.get("max_response_bytes") or 3_000_000), 20_000_000))
    max_pages = max(1, min(int(options.get("max_pages") or section.get("max_pages") or 12), 100))
    max_records = max(10, min(int(options.get("max_records") or section.get("max_records") or 3000), 10_000))
    token = _normal_token(cfg, project, root, timeout) if mode == "safe_live" and base_url else None
    cache: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []
    semantic_hypotheses: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []

    def fetch(collection: dict[str, Any] | None) -> dict[str, Any]:
        key = _hash({"path": (collection or {}).get("path"), "query": (collection or {}).get("query") or {}, "pagination": (collection or {}).get("pagination") or {}})
        if key not in cache:
            cache[key] = _fetch_collection(base_url, collection, token, timeout, max_bytes, max_pages, max_records)
        return cache[key]

    auditors = {
        "event_delivery": lambda c, s, e, u: _audit_event_delivery(c, s, e),
        "event_duplicate": lambda c, s, e, u: _audit_event_duplicate(c, e),
        "event_ordering": lambda c, s, e, u: _audit_event_ordering(c, e),
        "consumer_outcome": lambda c, s, e, u: _audit_consumer_outcome(c, e, u),
        "event_orphan": lambda c, s, e, u: _audit_event_orphan(c, s, e),
        "dead_letter_diagnostics": lambda c, s, e, u: _audit_dead_letter(c, e),
    }
    for contract in profile.get("contracts") or []:
        cid = str(contract.get("contract_id") or "")
        kind = str(contract.get("contract_kind") or "")
        if mode != "safe_live" or not base_url:
            executions.append({"contract_id": cid, "contract_kind": kind, "status": "planned", "reason": "plan_only_or_missing_base_url"})
            continue
        source_ctx = fetch(contract.get("source")) if contract.get("source") else {"records": [], "complete": True, "responses": []}
        event_ctx = fetch(contract.get("event")) if contract.get("event") else {"records": [], "complete": True, "responses": []}
        consumer_ctx = fetch(contract.get("consumer")) if contract.get("consumer") else {"records": [], "complete": True, "responses": []}
        contexts = {"source": source_ctx, "event": event_ctx, "consumer": consumer_ctx}
        failure = next((name for name, context in contexts.items() if context.get("responses") and any(item.get("error") or not item.get("status_code") for item in context.get("responses") or [])), None)
        if failure:
            executions.append({"contract_id": cid, "contract_kind": kind, "status": "error", "reason": f"{failure}_fetch_failed", "coverage": {name: _coverage(context) for name, context in contexts.items()}})
            continue
        audit = auditors.get(kind)
        if not audit:
            executions.append({"contract_id": cid, "contract_kind": kind, "status": "skipped", "reason": "unsupported_contract_kind"})
            continue
        emitted, observations = audit(contract, source_ctx, event_ctx, consumer_ctx)
        findings.extend(emitted)

    # --- LLM-powered semantic reasoning (Phase61 moat upgrade) ---
    if mode == "safe_live" and findings:
        try:
            import json as _json
            llm_result = _llm_reason("event_chain", {
                "prd_text": "", "api_schema": "", "observed_data": _json.dumps(executions[-5:] if "executions" in dir() else [], ensure_ascii=False, default=str)[:4000],
                "heuristic_findings": _json.dumps(findings[:15], ensure_ascii=False, default=str)[:4000],
            })
            semantic_hypotheses.extend(compile_unverified_semantic_hypotheses(
                (llm_result or {}).get("findings"),
                engine="business_event_chain",
                type_field="business_event_chain_type",
            ))
        except Exception:
            pass

        executions.append({"contract_id": cid, "contract_kind": kind, "status": "executed", "finding_count": len(emitted), "coverage": {name: _coverage(context) for name, context in contexts.items()}, "observations": observations[:80]})
    output = _output_paths(project, root)
    output["out"].mkdir(parents=True, exist_ok=True)
    output["workspace"].mkdir(parents=True, exist_ok=True)
    registry, findings = _update_registry(output["registry"], findings)
    result = {
        "phase": "phase53_business_event_chain_reasoning",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "summary": {**(profile.get("summary") or {}), "execution_mode": mode, "executed_contract_count": sum(1 for item in executions if item.get("status") == "executed"), "business_event_chain_finding_count": len(findings), "semantic_hypothesis_count": len(semantic_hypotheses), "persistent_business_event_chain_count": sum(1 for item in findings if (item.get("evidence_stability") or {}).get("persistent")), "memory_fingerprint_count": len((registry or {}).get("entries") or {})},
        "profile": {"contracts": profile.get("contracts") or [], "candidates": profile.get("candidates") or []},
        "executions": executions,
        "findings": findings,
        "semantic_hypotheses": semantic_hypotheses,
        "llm_governance": {"status": "unverified_hypothesis_only", "does_not_affect_finding_counts": True, "requires_deterministic_replay": True},
        "memory_summary": {"fingerprint_count": len((registry or {}).get("entries") or {}), "updated_at_utc": _now(), "learning_policy": "同一事件链反例跨运行持续出现时提高置信度；只有人工确认后才进入企业缺陷模式回灌。"},
        "governance": {"execution_mode": mode, "live_requests_limited_to_get": True, "message_publish_retry_replay_disabled": True, "sandbox_required_for_write_and_replay": True, "evidence_uses_hashed_identities": True, "raw_business_rows_not_persisted": True, "llm_output_is_unverified_hypothesis_only": True, "uses_no_benchmark_answer_files": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    _write_json(output["out"] / "business_event_chain_run.json", result)
    _write_json(output["workspace"] / "business_event_chain_run.json", result)
    (output["out"] / "business_event_chain_run_report.html").write_text(render_business_event_chain_run_report(result), encoding="utf-8")
    return result


def _render_html(title: str, badge: str, subtitle: str, cards: str, body: str) -> str:
    return f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>{_html_escape(title)}</title><style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#07111d;color:#eaf2ff;margin:0;padding:28px}}.hero,.panel{{background:#101d2c;border:1px solid #2b4260;border-radius:16px;padding:20px;margin-bottom:16px}}.badge{{display:inline-block;background:#174e52;color:#b6fff4;border-radius:999px;padding:4px 10px;font-size:12px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}.card{{background:#132638;border:1px solid #2b4260;border-radius:12px;padding:12px}}.card b{{display:block;font-size:24px;margin-top:5px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #2b4260;text-align:left;vertical-align:top;word-break:break-word}}th{{color:#9dc4ee}}</style><section class='hero'><span class='badge'>{_html_escape(badge)}</span><h1>{_html_escape(title)}</h1><p>{_html_escape(subtitle)}</p></section><section class='panel'><div class='grid'>{cards}</div></section><section class='panel'>{body}</section></html>"""


def render_business_event_chain_profile_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(label)}</span><b>{_html_escape(value)}</b></div>" for label, value in [("事件链契约", summary.get("event_chain_contract_count", 0)), ("投递覆盖", summary.get("event_delivery_contract_count", 0)), ("重复消息", summary.get("event_duplicate_contract_count", 0)), ("消费者闭环", summary.get("consumer_outcome_contract_count", 0)), ("死信诊断", summary.get("dead_letter_contract_count", 0))])
    rows = "".join(f"<tr><td>{_html_escape(item.get('contract_id'))}</td><td>{_html_escape(item.get('contract_kind'))}</td><td>{_html_escape(item.get('resource'))}</td><td>{_html_escape((item.get('event') or {}).get('path'))}</td><td>{_html_escape(item.get('discovery'))}</td></tr>" for item in (data.get("contracts") or [])[:180])
    return _render_html("Phase53 事件链与消息一致性画像", "GET-only · 跨服务因果链", "从业务状态、事件、消费结果与死信记录中构建可执行 Oracle。", cards, f"<h2>可执行事件契约</h2><table><thead><tr><th>ID</th><th>类型</th><th>业务资源</th><th>事件接口</th><th>推导来源</th></tr></thead><tbody>{rows or '<tr><td colspan=5>暂无可执行事件链契约；可在 business_event_chain_reasoning.contracts 显式配置。</td></tr>'}</tbody></table>")


def render_business_event_chain_run_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(label)}</span><b>{_html_escape(value)}</b></div>" for label, value in [("已执行", summary.get("executed_contract_count", 0)), ("发现问题", summary.get("business_event_chain_finding_count", 0)), ("稳定复现", summary.get("persistent_business_event_chain_count", 0)), ("证据指纹", summary.get("memory_fingerprint_count", 0))])
    rows = "".join(f"<tr><td>{_html_escape(item.get('severity'))}</td><td>{_html_escape(item.get('business_event_chain_type'))}</td><td>{_html_escape(item.get('title'))}</td><td>{_html_escape(item.get('actual'))}</td><td>{_html_escape((item.get('evidence_stability') or {}).get('observations', 1))}</td></tr>" for item in (data.get("findings") or [])[:180])
    return _render_html("Phase53 事件链与消息一致性运行报告", str(summary.get("execution_mode") or "plan_only"), "只读验证业务状态、消息投递、事件顺序、消费结果和死信诊断是否真正形成闭环。", cards, f"<h2>已证伪事件关系</h2><table><thead><tr><th>级别</th><th>类型</th><th>问题</th><th>实际</th><th>观测次数</th></tr></thead><tbody>{rows or '<tr><td colspan=5>未发现已证伪的事件链关系</td></tr>'}</tbody></table>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase53 business event chain and message consistency")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--mode", default="plan_only", choices=["plan_only", "safe_live"])
    args = parser.parse_args(argv)
    result = run_business_event_chain_reasoning(args.project, options={"execution_mode": args.mode})
    print(json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
