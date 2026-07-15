from __future__ import annotations

"""Phase54: saga compensation and business rollback integrity engine.

A large share of high-severity enterprise incidents occur after a distributed
business flow has failed or been cancelled.  Individual APIs can still return
200 while money was not refunded, inventory was not released, a compensation
ran twice, a rollback amount does not match the original document, or a saga
is stuck forever in a non-terminal state.

This module derives read-only, executable compensation Oracles from OpenAPI,
PRD terminology and optional enterprise configuration.  It validates:

* cancelled/failed business records have the required compensation records;
* compensation is idempotent and never duplicated for the same business key;
* compensation amount preserves the configured business amount relationship;
* compensation records are traceable to a real source record;
* cancelled flows do not retain an active residual side-effect such as an
  inventory reservation, payment authorization or entitlement;
* saga/workflow records reach an allowed terminal state after the source
  enters a rollback-required state; and
* pending compensations exceeding the configured SLA are surfaced as a
  business risk, not silently treated as a healthy asynchronous process.

Live execution is strictly GET-only.  Retrying compensation, forcing rollback
or racing duplicate cancellation requests are represented only as
``sandbox_required`` probes.
"""

import argparse
import json
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .business_causality_conservation import (
    _catalog,
    _canon,
    _collection_context,
    _field_name,
    _field_value,
    _find_collection,
    _hash,
    _identity,
    _identity_fields,
    _normal_states,
    _norm,
    _now,
    _short,
)
from .business_outcome_validation import _normal_token, _private_leak_check, _redact, _update_registry
from .business_reconciliation import _numeric
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


STATE_RE = re.compile(r"(?:^|[_\-.])(status|state|phase)(?:$|[_\-.])|状态|阶段", re.I)
AMOUNT_RE = re.compile(r"amount|total|price|cost|fee|tax|balance|金额|总额|价格|费用|余额|退款|扣款", re.I)
ID_RE = re.compile(r"(?:^|[_\-.])(id|uuid|guid|code|number|no|serial)(?:$|[_\-.])|编号|编码|单号", re.I)
TIME_RE = re.compile(r"created|updated|occurred|completed|finished|compensated|rollback|time|timestamp|at|创建时间|更新时间|发生时间|完成时间", re.I)
COMPENSATION_RESOURCE_RE = re.compile(r"refund|reversal|rollback|compensation|release|void|cancel(?:lation)?|return|退款|冲正|回滚|补偿|释放|撤销|退货", re.I)
SAGA_RESOURCE_RE = re.compile(r"saga|workflow|orchestrat|transaction.?flow|process|编排|工作流|事务流|流程实例", re.I)
RESIDUAL_RESOURCE_RE = re.compile(r"reservation|reserve|authorization|hold|lock|allocation|entitlement|inventory|预占|冻结|锁定|授权|库存|权益", re.I)

DEFAULT_TRIGGER_STATES = {
    "cancelled", "canceled", "failed", "failure", "reversed", "refunded",
    "returned", "rollback_required", "compensating", "expired", "closed",
    "已取消", "取消成功", "失败", "已失败", "已退款", "已退货", "已冲正", "待补偿", "补偿中", "已关闭", "已过期",
}
DEFAULT_COMPLETED_COMPENSATION_STATES = {
    "success", "succeeded", "completed", "compensated", "refunded", "released",
    "reversed", "rolled_back", "done", "成功", "已完成", "已补偿", "已退款", "已释放", "已冲正", "已回滚",
}
DEFAULT_PENDING_COMPENSATION_STATES = {
    "pending", "processing", "running", "compensating", "retrying", "queued",
    "处理中", "待处理", "补偿中", "重试中", "排队中",
}
DEFAULT_TERMINAL_SAGA_STATES = {
    "compensated", "rolled_back", "cancelled", "canceled", "failed", "closed", "completed",
    "已补偿", "已回滚", "已取消", "已失败", "已关闭", "已完成",
}
DEFAULT_ACTIVE_RESIDUAL_STATES = {
    "active", "reserved", "held", "authorized", "locked", "allocated", "processing",
    "有效", "预占", "冻结", "已授权", "锁定", "处理中",
}


def _section(cfg: dict[str, Any]) -> dict[str, Any]:
    value = (
        cfg.get("business_saga_compensation_reasoning")
        or cfg.get("saga_compensation_reasoning")
        or cfg.get("business_compensation_oracles")
        or cfg.get("rollback_compensation_reasoning")
        or {}
    )
    return value if isinstance(value, dict) else {}


def _output_paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "defect_discovery"
    return {
        "out": root / "platform_outputs" / project / "business_saga_compensation_reasoning",
        "workspace": workspace,
        "profile": workspace / "business_saga_compensation_profile.json",
        "registry": workspace / "business_saga_compensation_evidence_registry.json",
    }


def _values(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple, set)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _first_matching_field(fields: dict[str, Any], preferred: Any, pattern: re.Pattern[str]) -> str | None:
    names = _values(preferred)
    for wanted in names:
        match = _field_name(fields, wanted)
        if match:
            return match
    for name in fields:
        if pattern.search(str(name)):
            return str(name)
    return None


def _resource_ref(row: dict[str, Any] | None, query: dict[str, Any] | None = None, pagination: dict[str, Any] | None = None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "path": row.get("path"),
        "method": "GET",
        "parameters": row.get("parameters") or [],
        "resource": row.get("resource"),
        "fields": row.get("fields") or {},
        "query": dict(query or {}),
        "pagination": dict(pagination or {}),
    }


def _foreign_key_for_source(fields: dict[str, Any], source: dict[str, Any], configured: Any = None) -> str | None:
    explicit = _field_name(fields, str(configured or ""))
    if explicit:
        return explicit
    resource = _norm(source.get("resource"))
    # Resource-id shape is intentionally conservative for automatic relation discovery.
    for name in fields:
        token = _norm(name)
        if resource and resource in token and token.endswith("id"):
            return str(name)
    for name in fields:
        token = _norm(name)
        if token in {"sourceid", "entityid", "aggregateid", "businessid", "documentid", "recordid", "orderid"}:
            return str(name)
    return None


def _kind(value: Any) -> str | None:
    token = _norm(value)
    aliases = {
        "compensationcoverage": "compensation_coverage",
        "compensationrequired": "compensation_coverage",
        "rollbackcoverage": "compensation_coverage",
        "refundcoverage": "compensation_coverage",
        "compensationduplicate": "compensation_duplicate",
        "duplicatecompensation": "compensation_duplicate",
        "compensationamount": "compensation_amount",
        "rollbackamount": "compensation_amount",
        "compensationorphan": "compensation_orphan",
        "orphancompensation": "compensation_orphan",
        "residualeffect": "residual_effect_active",
        "residualeffectactive": "residual_effect_active",
        "sagaterminal": "saga_terminal_state",
        "sagaterminalstate": "saga_terminal_state",
        "sagastuck": "saga_terminal_state",
        "compensationstale": "compensation_stale",
        "stalependingcompensation": "compensation_stale",
    }
    return aliases.get(token, token if token in {
        "compensation_coverage", "compensation_duplicate", "compensation_amount",
        "compensation_orphan", "residual_effect_active", "saga_terminal_state", "compensation_stale",
    } else None)


def _configured_contracts(section: dict[str, Any]) -> list[dict[str, Any]]:
    raw = section.get("contracts") or section.get("saga_contracts") or section.get("compensation_contracts") or []
    if isinstance(raw, dict):
        raw = [raw]
    return [item for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []


def _trigger_states(resource: str, configured: Any = None) -> list[str]:
    explicit = _normal_states(_values(configured))
    if explicit:
        return explicit
    key = _norm(resource)
    if "refund" in key or "return" in key or "退款" in key or "退货" in key:
        return _normal_states(["refunded", "returned", "cancelled", "已退款", "已退货", "已取消"])
    if "release" in key or "reservation" in key or "库存" in key or "预占" in key:
        return _normal_states(["cancelled", "failed", "expired", "reversed", "已取消", "失败", "已过期", "已冲正"])
    return sorted(_normal_states(DEFAULT_TRIGGER_STATES))


def _contract_from_config(row: dict[str, Any], catalog: list[dict[str, Any]], number: int) -> dict[str, Any] | None:
    kind = _kind(row.get("type") or row.get("contract_kind") or row.get("kind"))
    if not kind:
        return None
    source = _find_collection(catalog, row.get("source_path") or row.get("source") or row.get("path"))
    compensation = _find_collection(catalog, row.get("compensation_path") or row.get("refund_path") or row.get("rollback_path") or row.get("dependent_path"))
    residual = _find_collection(catalog, row.get("residual_path") or row.get("effect_path") or row.get("reservation_path"))
    saga = _find_collection(catalog, row.get("saga_path") or row.get("workflow_path"))
    if kind in {"compensation_coverage", "compensation_duplicate", "compensation_amount", "compensation_orphan", "compensation_stale"} and (not source or not compensation):
        return None
    if kind == "residual_effect_active" and (not source or not residual):
        return None
    if kind == "saga_terminal_state" and (not source or not saga):
        return None
    source_fields = dict((source or {}).get("fields") or {})
    comp_fields = dict((compensation or {}).get("fields") or {})
    residual_fields = dict((residual or {}).get("fields") or {})
    saga_fields = dict((saga or {}).get("fields") or {})
    mappings = dict(row.get("field_mappings") or {})
    source_ids = _identity_fields(str((source or {}).get("resource") or ""), source_fields, row.get("source_identity_fields") or row.get("source_id_field"))
    source_status = _first_matching_field(source_fields, row.get("source_status_field") or row.get("state_field"), STATE_RE)
    comp_fk = _foreign_key_for_source(comp_fields, source or {}, row.get("compensation_foreign_key") or row.get("dependent_foreign_key")) if source else None
    residual_fk = _foreign_key_for_source(residual_fields, source or {}, row.get("residual_foreign_key")) if source else None
    saga_fk = _foreign_key_for_source(saga_fields, source or {}, row.get("saga_foreign_key")) if source else None
    return {
        "contract_id": f"BSC_CONTRACT_{number:04d}",
        "contract_kind": kind,
        "resource": str((source or compensation or residual or saga or {}).get("resource") or "business_flow"),
        "source": _resource_ref(source, row.get("source_query"), row.get("source_pagination")),
        "compensation": _resource_ref(compensation, row.get("compensation_query"), row.get("compensation_pagination")),
        "residual": _resource_ref(residual, row.get("residual_query"), row.get("residual_pagination")),
        "saga": _resource_ref(saga, row.get("saga_query"), row.get("saga_pagination")),
        "source_identity_fields": source_ids,
        "source_status_field": source_status,
        "trigger_states": _trigger_states(str((compensation or residual or saga or {}).get("resource") or ""), row.get("trigger_states") or row.get("source_states")),
        "compensation_foreign_key": comp_fk,
        "compensation_status_field": _first_matching_field(comp_fields, row.get("compensation_status_field"), STATE_RE),
        "completed_compensation_states": _normal_states(_values(row.get("completed_compensation_states") or row.get("compensation_success_states"))) or sorted(_normal_states(DEFAULT_COMPLETED_COMPENSATION_STATES)),
        "pending_compensation_states": _normal_states(_values(row.get("pending_compensation_states"))) or sorted(_normal_states(DEFAULT_PENDING_COMPENSATION_STATES)),
        "compensation_identity_fields": _identity_fields(str((compensation or {}).get("resource") or ""), comp_fields, row.get("compensation_identity_fields") or row.get("compensation_id_field")),
        "source_amount_field": _first_matching_field(source_fields, row.get("source_amount_field"), AMOUNT_RE),
        "compensation_amount_field": _first_matching_field(comp_fields, row.get("compensation_amount_field"), AMOUNT_RE),
        "amount_relation": str(row.get("amount_relation") or "sum_equal"),
        "tolerance": abs(float(row.get("tolerance") or 0.01)),
        "min_count": max(0, int(row.get("min_count") or 1)),
        "max_count": int(row["max_count"]) if row.get("max_count") is not None else 1,
        "residual_foreign_key": residual_fk,
        "residual_status_field": _first_matching_field(residual_fields, row.get("residual_status_field"), STATE_RE),
        "active_residual_states": _normal_states(_values(row.get("active_residual_states"))) or sorted(_normal_states(DEFAULT_ACTIVE_RESIDUAL_STATES)),
        "saga_foreign_key": saga_fk,
        "saga_status_field": _first_matching_field(saga_fields, row.get("saga_status_field"), STATE_RE),
        "terminal_saga_states": _normal_states(_values(row.get("terminal_saga_states"))) or sorted(_normal_states(DEFAULT_TERMINAL_SAGA_STATES)),
        "compensation_time_field": _first_matching_field(comp_fields, row.get("compensation_time_field") or row.get("updated_at_field"), TIME_RE),
        "max_pending_seconds": int(row.get("max_pending_seconds") or 0),
        "field_mappings": mappings,
        "execution_policy": "safe_read_only",
        "discovery": "enterprise_config",
        "source_evidence": ["enterprise_config", "openapi"],
    }


def _auto_contracts(catalog: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    contracts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    compensations = [row for row in catalog if COMPENSATION_RESOURCE_RE.search(f"{row.get('resource')} {row.get('summary')}")]
    sources = [row for row in catalog if row not in compensations and not SAGA_RESOURCE_RE.search(f"{row.get('resource')} {row.get('summary')}")]
    for comp in compensations:
        comp_fields = dict(comp.get("fields") or {})
        for source in sources:
            source_fields = dict(source.get("fields") or {})
            source_ids = _identity_fields(str(source.get("resource") or ""), source_fields)
            fk = _foreign_key_for_source(comp_fields, source)
            state = _first_matching_field(source_fields, None, STATE_RE)
            if not source_ids or not fk:
                continue
            base = {
                "contract_id": "",
                "resource": f"{source.get('resource')}->{comp.get('resource')}",
                "source": _resource_ref(source),
                "compensation": _resource_ref(comp),
                "residual": None,
                "saga": None,
                "source_identity_fields": source_ids,
                "source_status_field": state,
                "trigger_states": _trigger_states(str(comp.get("resource") or "")),
                "compensation_foreign_key": fk,
                "compensation_status_field": _first_matching_field(comp_fields, None, STATE_RE),
                "completed_compensation_states": sorted(_normal_states(DEFAULT_COMPLETED_COMPENSATION_STATES)),
                "pending_compensation_states": sorted(_normal_states(DEFAULT_PENDING_COMPENSATION_STATES)),
                "compensation_identity_fields": _identity_fields(str(comp.get("resource") or ""), comp_fields),
                "source_amount_field": _first_matching_field(source_fields, None, AMOUNT_RE),
                "compensation_amount_field": _first_matching_field(comp_fields, None, AMOUNT_RE),
                "amount_relation": "sum_equal",
                "tolerance": 0.01,
                "min_count": 1,
                "max_count": 1,
                "residual_foreign_key": None,
                "residual_status_field": None,
                "active_residual_states": sorted(_normal_states(DEFAULT_ACTIVE_RESIDUAL_STATES)),
                "saga_foreign_key": None,
                "saga_status_field": None,
                "terminal_saga_states": sorted(_normal_states(DEFAULT_TERMINAL_SAGA_STATES)),
                "compensation_time_field": _first_matching_field(comp_fields, None, TIME_RE),
                "max_pending_seconds": 0,
                "field_mappings": {},
                "execution_policy": "safe_read_only",
                "discovery": "openapi_compensation_semantics",
                "source_evidence": ["openapi", "resource_semantics", "foreign_key_shape"],
            }
            contracts.extend([
                {**base, "contract_kind": "compensation_orphan"},
                {**base, "contract_kind": "compensation_duplicate"},
            ])
            if state:
                contracts.append({**base, "contract_kind": "compensation_coverage"})
                if base["source_amount_field"] and base["compensation_amount_field"]:
                    contracts.append({**base, "contract_kind": "compensation_amount"})
            break
    # Residual resource relationships are intentionally candidate-only by
    # default.  A destructive-looking resource name alone does not prove the
    # business meaning; configuration can promote it to an executable Oracle.
    if not compensations:
        candidates.append({
            "candidate_id": "BSC_NO_COMPENSATION_COLLECTION",
            "risk_type": "saga_compensation_contract_gap",
            "severity": "P2",
            "title": "未发现可读取的补偿/退款/回滚集合",
            "detail": "为 refund、rollback、compensation、release、reversal 等资源补充 GET schema，或在 business_saga_compensation_reasoning.contracts 显式配置补偿关系。",
        })
    return contracts[:240], candidates[:80]


def build_business_saga_compensation_contracts(openapi: dict[str, Any], cfg: dict[str, Any], prd_text: str = "") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    section = _section(cfg)
    catalog = _catalog(openapi, cfg)
    contracts: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for row in _configured_contracts(section):
        item = _contract_from_config(row, catalog, len(contracts) + 1)
        if item:
            contracts.append(item)
        else:
            candidates.append({
                "candidate_id": f"BSC_CONFIG_GAP_{len(candidates)+1:04d}",
                "risk_type": "saga_compensation_contract_gap",
                "severity": "P2",
                "title": "补偿契约无法映射到可读取集合",
                "detail": f"检查 source_path、compensation_path、residual_path 或 saga_path 的 GET schema：{str(row.get('type') or row.get('contract_kind') or 'unknown')}。",
            })
    auto, auto_candidates = _auto_contracts(catalog)
    configured_keys = {
        (str(item.get("contract_kind")), str((item.get("source") or {}).get("path")), str((item.get("compensation") or {}).get("path")), str((item.get("residual") or {}).get("path")), str((item.get("saga") or {}).get("path")))
        for item in contracts
    }
    for item in auto:
        key = (str(item.get("contract_kind")), str((item.get("source") or {}).get("path")), str((item.get("compensation") or {}).get("path")), str((item.get("residual") or {}).get("path")), str((item.get("saga") or {}).get("path")))
        if key not in configured_keys:
            item["contract_id"] = f"BSC_CONTRACT_{len(contracts)+1:04d}"
            contracts.append(item)
            configured_keys.add(key)
    candidates.extend(auto_candidates)
    if re.search(r"补偿|回滚|退款|释放|冲正|撤销|saga|rollback|compensation|refund|release|reversal", prd_text or "", re.I) and not contracts:
        candidates.append({
            "candidate_id": "BSC_PRD_UNMAPPED",
            "risk_type": "saga_compensation_contract_gap",
            "severity": "P2",
            "title": "PRD 提到补偿/回滚，但尚未形成可执行 Oracle",
            "detail": "为业务主单、补偿记录、残留副作用或 Saga 状态补充可读取 GET schema，并在 business_saga_compensation_reasoning.contracts 配置关联字段。",
        })
    for contract in contracts:
        kind = str(contract.get("contract_kind") or "")
        if not contract.get("source_identity_fields"):
            candidates.append({"candidate_id": f"{contract['contract_id']}_NO_SOURCE_ID", "risk_type": "saga_compensation_contract_gap", "severity": "P2", "title": f"{contract.get('resource')} 缺少稳定主键", "detail": "配置 source_identity_fields，例如订单号、退款单号、审批单号。"})
        if kind in {"compensation_coverage", "compensation_duplicate", "compensation_amount", "compensation_orphan", "compensation_stale"} and not contract.get("compensation_foreign_key"):
            candidates.append({"candidate_id": f"{contract['contract_id']}_NO_COMP_FK", "risk_type": "saga_compensation_contract_gap", "severity": "P2", "title": f"{contract.get('resource')} 缺少补偿关联字段", "detail": "配置 compensation_foreign_key，例如 order_id、payment_id、reservation_id。"})
    return contracts[:320], candidates[:140]


def _summary(contracts: list[dict[str, Any]], candidates: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "saga_compensation_contract_count": len(contracts),
        "compensation_coverage_contract_count": sum(1 for item in contracts if item.get("contract_kind") == "compensation_coverage"),
        "compensation_duplicate_contract_count": sum(1 for item in contracts if item.get("contract_kind") == "compensation_duplicate"),
        "compensation_amount_contract_count": sum(1 for item in contracts if item.get("contract_kind") == "compensation_amount"),
        "residual_effect_contract_count": sum(1 for item in contracts if item.get("contract_kind") == "residual_effect_active"),
        "saga_terminal_contract_count": sum(1 for item in contracts if item.get("contract_kind") == "saga_terminal_state"),
        "compensation_stale_contract_count": sum(1 for item in contracts if item.get("contract_kind") == "compensation_stale"),
        "contract_gap_count": len(candidates),
    }


def build_business_saga_compensation_profile(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    paths = config_paths(project, root)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    openapi = openapi if isinstance(openapi, dict) else {}
    contracts, candidates = build_business_saga_compensation_contracts(openapi, cfg, _read_text(paths["input_dir"] / "prd.md"))
    learning = ingest_confirmed_bug_feedback(project, root)
    memory = learning.get("memory") or {}
    for contract in contracts:
        bonus, matches = _learning_bonus(contract, memory)
        contract["learning_bonus"] = bonus
        contract["learning_matches"] = matches
    profile = {
        "phase": "phase54_business_saga_compensation_reasoning",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "contracts": contracts,
        "candidates": candidates,
        "summary": {**_summary(contracts, candidates), "confirmed_bug_memory_count": int((learning.get("summary") or {}).get("confirmed_bug_memory_count") or 0)},
        "governance": {
            "default_execution": "plan_only",
            "safe_live_only_uses_get": True,
            "retry_rollback_and_cancel_race_are_sandbox_required": True,
            "evidence_uses_hashed_identities": True,
            "raw_business_rows_are_not_persisted": True,
            "findings_need_human_review": True,
        },
    }
    profile["private_leak_check"] = _private_leak_check(profile)
    output = _output_paths(project, root)
    output["out"].mkdir(parents=True, exist_ok=True)
    output["workspace"].mkdir(parents=True, exist_ok=True)
    _write_json(output["out"] / "business_saga_compensation_profile.json", profile)
    _write_json(output["profile"], profile)
    (output["out"] / "business_saga_compensation_profile_report.html").write_text(render_business_saga_compensation_profile_report(profile), encoding="utf-8")
    return profile


def load_business_saga_compensation_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    data = _load_json(_output_paths(_safe_project_id(project_id), root)["profile"], {})
    return data if isinstance(data, dict) and data.get("phase") == "phase54_business_saga_compensation_reasoning" else None


def _probe(contract: dict[str, Any], number: int, kind: str, title: str, risk_type: str, **extra: Any) -> dict[str, Any]:
    return {
        "probe_id": f"BSC_PROBE_{number:04d}",
        "source": "business_saga_compensation_reasoning",
        "business_saga_compensation_type": kind,
        "contract_id": contract.get("contract_id"),
        "title": title,
        "risk_type": risk_type,
        "severity": extra.pop("severity", "P1"),
        "expected": extra.pop("expected", "失败、取消或回滚后的业务补偿必须完整、幂等、可追溯且最终收敛。"),
        "method": extra.pop("method", "GET"),
        "path": extra.pop("path", str((contract.get("source") or {}).get("path") or "")),
        "actor": "normal_user",
        "destructive": bool(extra.pop("destructive", False)),
        "execution_policy": extra.pop("execution_policy", "safe_read_only"),
        "learning_bonus": contract.get("learning_bonus") or 0.0,
        "learning_matches": contract.get("learning_matches") or [],
        **extra,
    }


def generate_business_saga_compensation_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    profile = load_business_saga_compensation_profile(project_id, root) or build_business_saga_compensation_profile(project_id, root)
    probes: list[dict[str, Any]] = []
    for contract in profile.get("contracts") or []:
        kind = str(contract.get("contract_kind") or "")
        resource = str(contract.get("resource") or "业务补偿")
        if kind == "compensation_coverage":
            probes.append(_probe(contract, len(probes)+1, kind, f"补偿完整性：{resource} 失败/取消后必须生成补偿记录", "saga_compensation_missing"))
            probes.append(_probe(contract, len(probes)+1, "compensation_retry_sandbox", f"补偿重试幂等：{resource} 重试不得重复补偿", "saga_compensation_retry_idempotency", method="POST", destructive=True, execution_policy="sandbox_required", expected="隔离环境重试同一补偿任务时，只能产生一份最终补偿结果。"))
        elif kind == "compensation_duplicate":
            probes.append(_probe(contract, len(probes)+1, kind, f"补偿幂等：{resource} 不得重复退款/释放/冲正", "saga_compensation_duplicate"))
        elif kind == "compensation_amount":
            probes.append(_probe(contract, len(probes)+1, kind, f"补偿金额守恒：{resource} 补偿金额必须符合主单口径", "saga_compensation_amount"))
        elif kind == "compensation_orphan":
            probes.append(_probe(contract, len(probes)+1, kind, f"补偿可追溯：{resource} 不得引用不存在主单", "saga_compensation_orphan"))
        elif kind == "residual_effect_active":
            probes.append(_probe(contract, len(probes)+1, kind, f"回滚残留：{resource} 取消后不得保留活跃副作用", "saga_residual_effect"))
        elif kind == "saga_terminal_state":
            probes.append(_probe(contract, len(probes)+1, kind, f"Saga 收敛：{resource} 失败后必须进入终态", "saga_terminal_state"))
        elif kind == "compensation_stale":
            probes.append(_probe(contract, len(probes)+1, kind, f"补偿超时：{resource} 待补偿不得无限滞留", "saga_compensation_stale", severity="P2"))
    for candidate in profile.get("candidates") or []:
        probes.append({
            "probe_id": f"BSC_GAP_{len(probes)+1:04d}",
            "source": "business_saga_compensation_reasoning",
            "business_saga_compensation_type": "contract_gap",
            "contract_id": candidate.get("candidate_id"),
            "title": candidate.get("title"),
            "risk_type": candidate.get("risk_type") or "saga_compensation_contract_gap",
            "severity": candidate.get("severity") or "P2",
            "expected": candidate.get("detail"),
            "method": "GET", "path": "", "actor": "normal_user", "destructive": False, "execution_policy": "candidate_only",
        })
    return probes[:max(1, int(max_count or cfg.get("max_probe_count") or 180))]


def _finding(contract: dict[str, Any], kind: str, title: str, expected: str, actual: str, evidence: dict[str, Any], confidence: float = 0.92, severity: str = "P1", key: Any | None = None) -> dict[str, Any]:
    fingerprint = _hash({"contract": contract.get("contract_id"), "kind": kind, "key": key or evidence})
    risk_map = {
        "missing_compensation": "saga_compensation_missing",
        "duplicate_compensation": "saga_compensation_duplicate",
        "compensation_amount_mismatch": "saga_compensation_amount",
        "orphan_compensation": "saga_compensation_orphan",
        "residual_effect_active": "saga_residual_effect",
        "saga_not_terminal": "saga_terminal_state",
        "stale_compensation": "saga_compensation_stale",
    }
    return {
        "issue_id": f"BSC_{fingerprint[:12].upper()}",
        "fingerprint": fingerprint,
        "source": "business_saga_compensation_reasoning",
        "risk_type": risk_map.get(kind, "saga_compensation"),
        "business_saga_compensation_type": kind,
        "contract_id": contract.get("contract_id"),
        "title": title,
        "severity": severity,
        "status": "needs_human_review",
        "confidence": round(min(0.98, confidence + float(contract.get("learning_bonus") or 0.0)), 3),
        "expected": expected,
        "actual": actual,
        "evidence": _redact(evidence),
        "learning_matches": contract.get("learning_matches") or [],
    }


def _complete(*contexts: dict[str, Any]) -> bool:
    return all(bool(item.get("complete")) for item in contexts)


def _index(rows: list[dict[str, Any]], field: str | None, mappings: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not field:
        return result
    for row in rows:
        value = _canon(_field_value(row, field, mappings))
        if value:
            result[value].append(row)
    return dict(result)


def _eligible_sources(contract: dict[str, Any], source: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    mappings = dict(contract.get("field_mappings") or {})
    ids = list(contract.get("source_identity_fields") or [])
    state_field = str(contract.get("source_status_field") or "")
    trigger = set(_normal_states(contract.get("trigger_states") or []))
    rows: list[tuple[str, dict[str, Any]]] = []
    for row in source.get("records") or []:
        identity = _identity(row, ids, mappings)
        state = _norm(_field_value(row, state_field, mappings)) if state_field else ""
        if identity and (not trigger or state in trigger):
            rows.append((identity, row))
    return rows


def _base_evidence(source: dict[str, Any], dependent: dict[str, Any], extra_key: str = "compensation_request") -> dict[str, Any]:
    return {
        "source_request": {"method": "GET", "path": source.get("request_path"), "query": source.get("query")},
        extra_key: {"method": "GET", "path": dependent.get("request_path"), "query": dependent.get("query")},
        "source_coverage": {"complete": source.get("complete"), "total": source.get("total"), "fetched_rows": len(source.get("records") or [])},
        "dependent_coverage": {"complete": dependent.get("complete"), "total": dependent.get("total"), "fetched_rows": len(dependent.get("records") or [])},
    }


def _audit_compensation_coverage(contract: dict[str, Any], source: dict[str, Any], comp: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not _complete(source, comp):
        return [], [{"result": "skipped_incomplete_snapshot", "source_complete": source.get("complete"), "compensation_complete": comp.get("complete")}]
    mappings = dict(contract.get("field_mappings") or {})
    fk = str(contract.get("compensation_foreign_key") or "")
    status_field = str(contract.get("compensation_status_field") or "")
    success_states = set(_normal_states(contract.get("completed_compensation_states") or []))
    related = _index(list(comp.get("records") or []), fk, mappings)
    eligible = _eligible_sources(contract, source)
    missing: list[str] = []
    for identity, _row in eligible:
        rows = related.get(identity, [])
        done = [item for item in rows if not status_field or _norm(_field_value(item, status_field, mappings)) in success_states]
        if len(done) < int(contract.get("min_count") or 1):
            missing.append(_short(identity))
    observations = [{"result": "executed", "eligible_source_count": len(eligible), "missing_compensation_count": len(missing), "trigger_states": sorted(set(_normal_states(contract.get("trigger_states") or []))), "completed_compensation_states": sorted(success_states)}]
    if not missing:
        return [], observations
    evidence = {
        **_base_evidence(source, comp),
        "source_status_field": contract.get("source_status_field"), "trigger_states": sorted(set(_normal_states(contract.get("trigger_states") or []))),
        "compensation_foreign_key": fk, "compensation_status_field": status_field,
        "completed_compensation_states": sorted(success_states), "missing_source_identity_hashes": missing[:30],
    }
    return [_finding(contract, "missing_compensation", f"补偿缺失：{contract.get('resource')} 已取消/失败但未完成补偿", "进入取消、失败、回滚或退款状态的主业务必须拥有完成态补偿记录。", f"在 {len(eligible)} 条需要补偿的业务记录中发现 {len(missing)} 条没有完成态补偿。", evidence, confidence=0.95, key=missing)], observations


def _audit_compensation_duplicate(contract: dict[str, Any], source: dict[str, Any], comp: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not _complete(source, comp):
        return [], [{"result": "skipped_incomplete_snapshot", "source_complete": source.get("complete"), "compensation_complete": comp.get("complete")}]
    mappings = dict(contract.get("field_mappings") or {})
    fk = str(contract.get("compensation_foreign_key") or "")
    status_field = str(contract.get("compensation_status_field") or "")
    success_states = set(_normal_states(contract.get("completed_compensation_states") or []))
    max_count = max(1, int(contract.get("max_count") or 1))
    source_ids = {_identity(row, list(contract.get("source_identity_fields") or []), mappings) for row in source.get("records") or []}
    source_ids.discard(None)
    related = _index(list(comp.get("records") or []), fk, mappings)
    duplicates: list[dict[str, Any]] = []
    for identity, rows in related.items():
        if identity not in source_ids:
            continue
        done = [row for row in rows if not status_field or _norm(_field_value(row, status_field, mappings)) in success_states]
        if len(done) > max_count:
            duplicates.append({"source_identity_hash": _short(identity), "completed_compensation_count": len(done), "max_count": max_count})
    observations = [{"result": "executed", "source_identity_count": len(source_ids), "duplicate_compensation_count": len(duplicates), "max_count": max_count}]
    if not duplicates:
        return [], observations
    evidence = {**_base_evidence(source, comp), "compensation_foreign_key": fk, "max_count": max_count, "duplicates": duplicates[:30]}
    return [_finding(contract, "duplicate_compensation", f"重复补偿：{contract.get('resource')} 同一业务主单出现多次完成态补偿", "同一业务主单的同类完成态补偿次数不得超过配置上限。", f"发现 {len(duplicates)} 个业务主单的补偿次数超过 {max_count}。", evidence, confidence=0.96, key=duplicates)], observations


def _audit_compensation_amount(contract: dict[str, Any], source: dict[str, Any], comp: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not _complete(source, comp):
        return [], [{"result": "skipped_incomplete_snapshot", "source_complete": source.get("complete"), "compensation_complete": comp.get("complete")}]
    mappings = dict(contract.get("field_mappings") or {})
    source_amount = str(contract.get("source_amount_field") or "")
    comp_amount = str(contract.get("compensation_amount_field") or "")
    fk = str(contract.get("compensation_foreign_key") or "")
    if not source_amount or not comp_amount or not fk:
        return [], [{"result": "skipped_amount_mapping_missing", "source_amount_field": source_amount, "compensation_amount_field": comp_amount, "compensation_foreign_key": fk}]
    status_field = str(contract.get("compensation_status_field") or "")
    success_states = set(_normal_states(contract.get("completed_compensation_states") or []))
    related = _index(list(comp.get("records") or []), fk, mappings)
    eligible = _eligible_sources(contract, source)
    tolerance = abs(float(contract.get("tolerance") or 0.01))
    relation = _norm(contract.get("amount_relation") or "sumequal")
    mismatches: list[dict[str, Any]] = []
    checked = 0
    for identity, row in eligible:
        left = _numeric(_field_value(row, source_amount, mappings))
        children = [item for item in related.get(identity, []) if not status_field or _norm(_field_value(item, status_field, mappings)) in success_states]
        values = [_numeric(_field_value(item, comp_amount, mappings)) for item in children]
        if left is None or not children or any(value is None for value in values):
            continue
        checked += 1
        actual = round(sum(float(value) for value in values if value is not None), 6)
        expected = float(left)
        valid = abs(actual - expected) <= tolerance if relation in {"sumequal", "equal", "sum_equal"} else actual <= expected + tolerance
        if not valid:
            mismatches.append({"source_identity_hash": _short(identity), "source_amount": left, "compensation_amount_sum": actual, "delta": round(actual - expected, 6), "relation": relation, "compensation_count": len(children)})
    observations = [{"result": "executed", "checked_source_count": checked, "amount_mismatch_count": len(mismatches), "relation": relation, "tolerance": tolerance}]
    if not mismatches:
        return [], observations
    evidence = {**_base_evidence(source, comp), "source_amount_field": source_amount, "compensation_amount_field": comp_amount, "compensation_foreign_key": fk, "relation": relation, "tolerance": tolerance, "mismatches": mismatches[:30]}
    return [_finding(contract, "compensation_amount_mismatch", f"补偿金额错误：{contract.get('resource')} 主单与补偿金额不守恒", "补偿完成态金额必须满足配置的等额或不超额补偿关系。", f"发现 {len(mismatches)} 个主单的补偿金额与业务金额不一致。", evidence, confidence=0.96, key=mismatches)], observations


def _audit_compensation_orphan(contract: dict[str, Any], source: dict[str, Any], comp: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not _complete(source, comp):
        return [], [{"result": "skipped_incomplete_snapshot", "source_complete": source.get("complete"), "compensation_complete": comp.get("complete")}]
    mappings = dict(contract.get("field_mappings") or {})
    ids = {_identity(row, list(contract.get("source_identity_fields") or []), mappings) for row in source.get("records") or []}
    ids.discard(None)
    fk = str(contract.get("compensation_foreign_key") or "")
    missing: list[str] = []
    checked = 0
    for row in comp.get("records") or []:
        value = _canon(_field_value(row, fk, mappings))
        if not value:
            continue
        checked += 1
        if value not in ids:
            missing.append(_short(value))
    observations = [{"result": "executed", "checked_compensation_rows": checked, "orphan_count": len(missing), "source_identity_count": len(ids)}]
    if not missing:
        return [], observations
    evidence = {**_base_evidence(source, comp), "compensation_foreign_key": fk, "orphan_source_identity_hashes": missing[:30]}
    return [_finding(contract, "orphan_compensation", f"补偿孤儿数据：{contract.get('resource')} 存在不可追溯补偿记录", "每条补偿、退款、冲正或释放记录都必须关联一个真实主业务单据。", f"发现 {len(missing)} 条补偿记录关联不到完整业务事实集合。", evidence, confidence=0.94, key=missing)], observations


def _audit_residual_effect(contract: dict[str, Any], source: dict[str, Any], residual: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not _complete(source, residual):
        return [], [{"result": "skipped_incomplete_snapshot", "source_complete": source.get("complete"), "residual_complete": residual.get("complete")}]
    mappings = dict(contract.get("field_mappings") or {})
    fk = str(contract.get("residual_foreign_key") or "")
    status_field = str(contract.get("residual_status_field") or "")
    if not fk or not status_field:
        return [], [{"result": "skipped_residual_mapping_missing", "residual_foreign_key": fk, "residual_status_field": status_field}]
    active = set(_normal_states(contract.get("active_residual_states") or []))
    related = _index(list(residual.get("records") or []), fk, mappings)
    eligible = _eligible_sources(contract, source)
    leaking: list[dict[str, Any]] = []
    for identity, _row in eligible:
        active_rows = [item for item in related.get(identity, []) if _norm(_field_value(item, status_field, mappings)) in active]
        if active_rows:
            leaking.append({"source_identity_hash": _short(identity), "active_residual_count": len(active_rows), "active_states": sorted({_norm(_field_value(item, status_field, mappings)) for item in active_rows})})
    observations = [{"result": "executed", "eligible_source_count": len(eligible), "residual_leak_count": len(leaking), "active_residual_states": sorted(active)}]
    if not leaking:
        return [], observations
    evidence = {**_base_evidence(source, residual, "residual_request"), "residual_foreign_key": fk, "residual_status_field": status_field, "active_residual_states": sorted(active), "residual_leaks": leaking[:30]}
    return [_finding(contract, "residual_effect_active", f"回滚残留：{contract.get('resource')} 已失败/取消但仍保留活跃业务副作用", "已进入补偿触发状态的主单不得保留活跃库存预占、冻结、授权、锁定或权益。", f"发现 {len(leaking)} 个已取消/失败主单仍存在活跃副作用。", evidence, confidence=0.97, key=leaking)], observations


def _audit_saga_terminal(contract: dict[str, Any], source: dict[str, Any], saga: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not _complete(source, saga):
        return [], [{"result": "skipped_incomplete_snapshot", "source_complete": source.get("complete"), "saga_complete": saga.get("complete")}]
    mappings = dict(contract.get("field_mappings") or {})
    fk = str(contract.get("saga_foreign_key") or "")
    status_field = str(contract.get("saga_status_field") or "")
    if not fk or not status_field:
        return [], [{"result": "skipped_saga_mapping_missing", "saga_foreign_key": fk, "saga_status_field": status_field}]
    terminal = set(_normal_states(contract.get("terminal_saga_states") or []))
    related = _index(list(saga.get("records") or []), fk, mappings)
    eligible = _eligible_sources(contract, source)
    stuck: list[dict[str, Any]] = []
    for identity, _row in eligible:
        rows = related.get(identity, [])
        states = {_norm(_field_value(item, status_field, mappings)) for item in rows if _norm(_field_value(item, status_field, mappings))}
        if not rows or not (states & terminal):
            stuck.append({"source_identity_hash": _short(identity), "observed_saga_states": sorted(states), "saga_count": len(rows)})
    observations = [{"result": "executed", "eligible_source_count": len(eligible), "non_terminal_saga_count": len(stuck), "terminal_saga_states": sorted(terminal)}]
    if not stuck:
        return [], observations
    evidence = {**_base_evidence(source, saga, "saga_request"), "saga_foreign_key": fk, "saga_status_field": status_field, "terminal_saga_states": sorted(terminal), "stuck_sagas": stuck[:30]}
    return [_finding(contract, "saga_not_terminal", f"Saga 未收敛：{contract.get('resource')} 失败/取消后仍未进入允许终态", "失败、取消或补偿中的业务流必须有对应 Saga/工作流进入配置的终态。", f"发现 {len(stuck)} 条业务记录缺少终态 Saga，或 Saga 一直停留在中间状态。", evidence, confidence=0.94, key=stuck)], observations


def _parse_time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text[:-1] + "+00:00")
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _audit_stale_compensation(contract: dict[str, Any], source: dict[str, Any], comp: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not _complete(source, comp):
        return [], [{"result": "skipped_incomplete_snapshot", "source_complete": source.get("complete"), "compensation_complete": comp.get("complete")}]
    mappings = dict(contract.get("field_mappings") or {})
    time_field = str(contract.get("compensation_time_field") or "")
    status_field = str(contract.get("compensation_status_field") or "")
    max_pending = int(contract.get("max_pending_seconds") or 0)
    if not time_field or not status_field or max_pending <= 0:
        return [], [{"result": "skipped_stale_mapping_missing", "compensation_time_field": time_field, "compensation_status_field": status_field, "max_pending_seconds": max_pending}]
    pending = set(_normal_states(contract.get("pending_compensation_states") or []))
    now = datetime.now(timezone.utc)
    stale: list[dict[str, Any]] = []
    for idx, row in enumerate(comp.get("records") or []):
        state = _norm(_field_value(row, status_field, mappings))
        dt = _parse_time(_field_value(row, time_field, mappings))
        if state not in pending or not dt:
            continue
        age = max(0, int((now - dt).total_seconds()))
        if age > max_pending:
            marker = _short({"index": idx, "time": dt.isoformat(), "state": state})
            stale.append({"compensation_identity_hash": marker, "state": state, "age_seconds": age})
    observations = [{"result": "executed", "pending_statuses": sorted(pending), "stale_compensation_count": len(stale), "max_pending_seconds": max_pending}]
    if not stale:
        return [], observations
    evidence = {**_base_evidence(source, comp), "compensation_status_field": status_field, "compensation_time_field": time_field, "pending_compensation_states": sorted(pending), "max_pending_seconds": max_pending, "stale_compensations": stale[:30]}
    return [_finding(contract, "stale_compensation", f"补偿长时间未收敛：{contract.get('resource')} 存在超过 SLA 的待补偿记录", "待补偿/重试中的业务必须在配置 SLA 内进入成功、失败或人工处置终态。", f"发现 {len(stale)} 条待补偿记录超过 {max_pending} 秒仍未收敛。", evidence, confidence=0.88, severity="P2", key=stale)], observations


def run_business_saga_compensation_reasoning(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    profile = build_business_saga_compensation_profile(project, root, options)
    section = _section(cfg)
    mode = str(options.get("execution_mode") or cfg.get("business_saga_compensation_execution_mode") or cfg.get("saga_compensation_execution_mode") or "plan_only").lower()
    if mode not in {"plan_only", "safe_live"}:
        mode = "plan_only"
    base_url = str(cfg.get("base_url") or "")
    timeout = max(1, min(int(cfg.get("request_timeout_seconds") or 10), 30))
    max_bytes = max(100_000, min(int(options.get("max_response_bytes") or section.get("max_response_bytes") or 3_000_000), 20_000_000))
    max_pages = max(1, min(int(options.get("max_pages") or section.get("max_pages") or 12), 100))
    max_records = max(10, min(int(options.get("max_records") or section.get("max_records") or 2000), 10000))
    token = _normal_token(cfg, project, root, timeout) if mode == "safe_live" and base_url else None
    cache: dict[str, dict[str, Any]] = {}
    findings: list[dict[str, Any]] = []
    semantic_hypotheses: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []

    def fetch(collection: dict[str, Any] | None) -> dict[str, Any] | None:
        if not collection:
            return None
        key = _hash({"path": collection.get("path"), "query": collection.get("query") or {}, "pagination": collection.get("pagination") or {}})
        if key not in cache:
            cache[key] = _collection_context(base_url, collection, dict(collection.get("query") or {}), dict(collection.get("pagination") or {}), token, timeout, max_bytes, max_pages, max_records)
        return cache[key]

    audit_map = {
        "compensation_coverage": _audit_compensation_coverage,
        "compensation_duplicate": _audit_compensation_duplicate,
        "compensation_amount": _audit_compensation_amount,
        "compensation_orphan": _audit_compensation_orphan,
        "residual_effect_active": _audit_residual_effect,
        "saga_terminal_state": _audit_saga_terminal,
        "compensation_stale": _audit_stale_compensation,
    }
    for contract in profile.get("contracts") or []:
        contract_id = str(contract.get("contract_id") or "")
        if mode != "safe_live" or not base_url:
            executions.append({"contract_id": contract_id, "status": "planned", "reason": "plan_only_or_missing_base_url"})
            continue
        source = fetch(contract.get("source"))
        target_key = "compensation"
        if contract.get("contract_kind") == "residual_effect_active":
            target_key = "residual"
        elif contract.get("contract_kind") == "saga_terminal_state":
            target_key = "saga"
        target = fetch(contract.get(target_key))
        if not source or not target:
            executions.append({"contract_id": contract_id, "status": "skipped", "reason": "source_or_target_path_missing"})
            continue
        if not (source.get("responses") or [{}])[0].get("status_code") or not (target.get("responses") or [{}])[0].get("status_code"):
            executions.append({"contract_id": contract_id, "status": "error", "reason": "collection_fetch_failed", "source_responses": source.get("responses"), "target_responses": target.get("responses")})
            continue
        audit = audit_map.get(str(contract.get("contract_kind") or ""))
        if not audit:
            executions.append({"contract_id": contract_id, "status": "skipped", "reason": "unsupported_contract_kind"})
            continue
        emitted, observations = audit(contract, source, target)
        findings.extend(emitted)

    # --- LLM-powered semantic reasoning (Phase61 moat upgrade) ---
    if mode == "safe_live" and findings:
        try:
            import json as _json
            llm_result = _llm_reason("saga", {
                "prd_text": "", "api_schema": "", "observed_data": _json.dumps(executions[-5:] if "executions" in dir() else [], ensure_ascii=False, default=str)[:4000],
                "heuristic_findings": _json.dumps(findings[:15], ensure_ascii=False, default=str)[:4000],
            })
            semantic_hypotheses.extend(compile_unverified_semantic_hypotheses(
                (llm_result or {}).get("findings"),
                engine="saga",
                type_field="business_saga_type",
            ))
        except Exception:
            pass

        executions.append({
            "contract_id": contract_id,
            "status": "executed",
            "contract_kind": contract.get("contract_kind"),
            "source_complete": source.get("complete"),
            "source_total": source.get("total"),
            "fetched_source_rows": len(source.get("records") or []),
            "target_complete": target.get("complete"),
            "target_total": target.get("total"),
            "fetched_target_rows": len(target.get("records") or []),
            "finding_count": len(emitted),
            "observations": observations,
        })

    output = _output_paths(project, root)
    registry, findings = _update_registry(output["registry"], findings)
    result = {
        "phase": "phase54_business_saga_compensation_reasoning",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "summary": {
            **(profile.get("summary") or {}),
            "execution_mode": mode,
            "executed_contract_count": sum(1 for item in executions if item.get("status") == "executed"),
            "business_saga_compensation_finding_count": len(findings),
            "persistent_business_saga_compensation_count": sum(1 for item in findings if (item.get("evidence_stability") or {}).get("persistent")),
            "memory_fingerprint_count": len((registry or {}).get("entries") or {}),
        },
        "profile": profile,
        "executions": executions,
        "semantic_hypotheses": semantic_hypotheses, "llm_governance": {"status": "unverified_hypothesis_only", "does_not_affect_finding_counts": True, "requires_deterministic_replay": True}, "findings": findings,
        "memory_summary": {"fingerprint_count": len((registry or {}).get("entries") or {}), "updated_at_utc": _now(), "learning_policy": "同一补偿反例跨运行持续出现时提高置信度；仍需人工确认后才进入企业知识回灌。"},
        "governance": {"execution_mode": mode, "live_requests_limited_to_get": True, "write_execution_disabled": True, "compensation_retry_reversal_and_cancel_races_are_sandbox_required": True, "evidence_uses_hashed_identities": True, "raw_business_rows_not_persisted": True, "uses_no_benchmark_answer_files": True},
    }
    result["private_leak_check"] = _private_leak_check(result)
    output["out"].mkdir(parents=True, exist_ok=True)
    output["workspace"].mkdir(parents=True, exist_ok=True)
    _write_json(output["out"] / "business_saga_compensation_run.json", result)
    _write_json(output["workspace"] / "business_saga_compensation_run.json", result)
    (output["out"] / "business_saga_compensation_run_report.html").write_text(render_business_saga_compensation_run_report(result), encoding="utf-8")
    return result


def _render_html(title: str, badge: str, subtitle: str, cards: str, body: str) -> str:
    return f"""<!doctype html><html lang='zh-CN'><meta charset='utf-8'><title>{_html_escape(title)}</title><style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#07111d;color:#eaf2ff;margin:0;padding:28px}}.hero,.panel{{background:#101d2c;border:1px solid #2b4260;border-radius:16px;padding:20px;margin-bottom:16px}}.badge{{display:inline-block;background:#174e52;color:#b6fff4;border-radius:999px;padding:4px 10px;font-size:12px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}}.card{{background:#132638;border:1px solid #2b4260;border-radius:12px;padding:12px}}.card b{{display:block;font-size:24px;margin-top:5px}}table{{width:100%;border-collapse:collapse}}th,td{{padding:10px;border-bottom:1px solid #2b4260;text-align:left;vertical-align:top;word-break:break-word}}th{{color:#9dc4ee}}</style><section class='hero'><span class='badge'>{_html_escape(badge)}</span><h1>{_html_escape(title)}</h1><p>{_html_escape(subtitle)}</p></section><section class='panel'><div class='grid'>{cards}</div></section><section class='panel'>{body}</section></html>"""


def render_business_saga_compensation_profile_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(label)}</span><b>{_html_escape(value)}</b></div>" for label, value in [
        ("补偿契约", summary.get("saga_compensation_contract_count", 0)), ("补偿覆盖", summary.get("compensation_coverage_contract_count", 0)),
        ("残留副作用", summary.get("residual_effect_contract_count", 0)), ("Saga 收敛", summary.get("saga_terminal_contract_count", 0)),
    ])
    rows = "".join(f"<tr><td>{_html_escape(item.get('contract_id'))}</td><td>{_html_escape(item.get('contract_kind'))}</td><td>{_html_escape(item.get('resource'))}</td><td>{_html_escape((item.get('source') or {}).get('path'))}</td><td>{_html_escape((item.get('compensation') or item.get('residual') or item.get('saga') or {}).get('path') or '-')}</td><td>{_html_escape(item.get('discovery'))}</td></tr>" for item in (data.get("contracts") or [])[:180])
    return _render_html("Phase54 Saga 补偿与回滚完整性画像", "GET-only · 反例取证", "验证失败、取消和回滚之后，补偿、释放、状态收敛与金额是否真正完整。", cards, f"<h2>可执行契约</h2><table><thead><tr><th>ID</th><th>类型</th><th>关系</th><th>主实体</th><th>目标集合</th><th>推导来源</th></tr></thead><tbody>{rows or '<tr><td colspan=6>暂无可执行补偿契约；可在 business_saga_compensation_reasoning.contracts 中显式配置。</td></tr>'}</tbody></table>")


def render_business_saga_compensation_run_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(label)}</span><b>{_html_escape(value)}</b></div>" for label, value in [
        ("已执行", summary.get("executed_contract_count", 0)), ("发现问题", summary.get("business_saga_compensation_finding_count", 0)),
        ("稳定复现", summary.get("persistent_business_saga_compensation_count", 0)), ("证据指纹", summary.get("memory_fingerprint_count", 0)),
    ])
    rows = "".join(f"<tr><td>{_html_escape(item.get('severity'))}</td><td>{_html_escape(item.get('business_saga_compensation_type'))}</td><td>{_html_escape(item.get('title'))}</td><td>{_html_escape(item.get('actual'))}</td><td>{_html_escape((item.get('evidence_stability') or {}).get('observations', 1))}</td></tr>" for item in (data.get("findings") or [])[:180])
    return _render_html("Phase54 Saga 补偿与回滚完整性运行报告", str(summary.get("execution_mode") or "plan_only"), "只读验证补偿覆盖、重复补偿、金额守恒、残留副作用、Saga 收敛和超时补偿。", cards, f"<h2>已证伪业务关系</h2><table><thead><tr><th>级别</th><th>类型</th><th>问题</th><th>实际</th><th>观测次数</th></tr></thead><tbody>{rows or '<tr><td colspan=5>未发现已证伪的补偿关系</td></tr>'}</tbody></table>")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase54 saga compensation and rollback integrity")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--mode", default="plan_only", choices=["plan_only", "safe_live"])
    args = parser.parse_args(argv)
    result = run_business_saga_compensation_reasoning(args.project, options={"execution_mode": args.mode})
    print(json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
