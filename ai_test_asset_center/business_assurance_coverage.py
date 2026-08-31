from __future__ import annotations

"""Phase56: Evidence-backed Business Quality Assurance Coverage.

This module makes the product's strongest promise measurable instead of
marketing-only.  It does not claim that software can be proven bug-free.  It
builds an assurance case from the project's PRD, OpenAPI, generated business
Oracles, confirmed-defect regressions and execution evidence:

    requirements / critical operations
        -> required business mutations
        -> compatible Oracle families
        -> planned probes and read-only evidence
        -> residual risks and release decision

The key control is *mutation survivorship*: QualiBug injects a synthetic
business failure model into the assurance model (never into a production
system) and asks whether at least one real Oracle/probe would expose it.  A
surviving mutation is a coverage gap, not a confirmed product defect.

The result is an evidence-backed quality assurance score, explicit residual
risk, and new candidate probes for uncovered critical behaviour.  Write paths
remain sandbox-required; production mode is strictly read-only by default.
"""

import argparse
import hashlib
import html
import importlib
import json
import logging
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable

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
from .universal_defect_mining import _operations, _extract_requirement_rules

_log = logging.getLogger("BusinessAssuranceCoverage")

PHASE = "phase56_business_quality_assurance_coverage"
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# A mutation is a modelled business failure.  To be considered killed, an
# endpoint-relevant probe produced by one of the listed Oracle families must
# exist.  This is deliberately conservative: a planned probe proves coverage
# design, while a completed run proves evidence freshness.
MUTATION_TO_ORACLES: dict[str, set[str]] = {
    "response_contract_break": {"universal_spec_behavior"},
    "required_data_missing": {"universal_spec_behavior", "business_invariant_mining"},
    "filter_semantics_lost": {"business_invariant_mining", "metamorphic_differential_reasoning"},
    "pagination_set_corruption": {"metamorphic_differential_reasoning", "business_invariant_mining"},
    "sort_semantics_lost": {"metamorphic_differential_reasoning"},
    "list_detail_projection_drift": {"counterexample_relation_mining", "business_reconciliation", "multi_source_business_reasoning"},
    "unauthorized_resource_access": {"universal_spec_behavior", "consistency_isolation_reasoning", "multi_source_business_reasoning", "multi_industry_business_reasoning", "enterprise_business_knowledge_asset", "enterprise_testops_permission"},
    "tenant_boundary_leak": {"consistency_isolation_reasoning", "multi_source_business_reasoning", "multi_industry_business_reasoning", "enterprise_business_knowledge_asset", "enterprise_testops_permission"},
    "state_transition_break": {"business_lifecycle_reasoning", "universal_spec_behavior", "multi_industry_business_reasoning", "enterprise_business_knowledge_asset", "enterprise_testops_system_state", "enterprise_testops_journey"},
    "temporal_order_break": {"business_lifecycle_reasoning", "temporal_data_regression_reasoning"},
    "duplicate_business_effect": {"business_causality_conservation", "business_event_chain_reasoning", "business_saga_compensation_reasoning", "universal_spec_behavior", "multi_industry_business_reasoning", "enterprise_business_knowledge_asset", "enterprise_testops_system_state", "enterprise_testops_journey"},
    "missing_business_effect": {"business_causality_conservation", "business_event_chain_reasoning", "business_saga_compensation_reasoning", "multi_industry_business_reasoning", "enterprise_business_knowledge_asset", "enterprise_testops_system_state", "enterprise_testops_journey"},
    "aggregate_or_amount_drift": {"business_reconciliation", "business_outcome_validation", "business_causality_conservation", "multi_industry_business_reasoning", "enterprise_business_knowledge_asset", "enterprise_testops_system_state", "enterprise_testops_journey"},
    "historical_data_regression": {"temporal_data_regression_reasoning", "multi_source_business_reasoning", "enterprise_business_knowledge_asset"},
    "population_constraint_bypass": {"business_population_constraints", "multi_industry_business_reasoning", "enterprise_business_knowledge_asset", "enterprise_testops_system_state"},
    "event_chain_break": {"business_event_chain_reasoning", "multi_industry_business_reasoning", "enterprise_business_knowledge_asset"},
    "compensation_or_rollback_break": {"business_saga_compensation_reasoning", "multi_industry_business_reasoning", "enterprise_business_knowledge_asset"},
    "requirement_rule_unmapped": set(),
}

MUTATION_DESCRIPTIONS = {
    "response_contract_break": "接口成功返回但字段、类型、必填项或枚举违背契约",
    "required_data_missing": "业务关键字段被静默置空、丢失或无法追溯",
    "filter_semantics_lost": "有效筛选条件被服务端忽略、部分忽略或混入无关记录",
    "pagination_set_corruption": "分页重复、漏数、总数漂移或页间集合不闭合",
    "sort_semantics_lost": "排序参数有效但结果不满足单调/稳定关系",
    "list_detail_projection_drift": "列表、详情、统计或导出中的同一业务事实不一致",
    "unauthorized_resource_access": "低权限主体可访问不属于自己的资源",
    "tenant_boundary_leak": "一个租户可见、可读取或可关联到另一个租户的数据",
    "state_transition_break": "业务状态可跳步、回退、终态重入或缺少状态凭证",
    "temporal_order_break": "时间线倒置、失效对象仍有效或历史版本不兼容",
    "duplicate_business_effect": "一次业务动作产生重复付款、扣减、通知、事件或台账",
    "missing_business_effect": "主业务成功但库存、支付、审批、台账、通知等副作用缺失",
    "aggregate_or_amount_drift": "金额、数量、统计或公式不守恒",
    "historical_data_regression": "发布后历史字段、枚举、主键、不可变属性或关键记录漂移",
    "population_constraint_bypass": "多记录组合后突破额度、审批、资源容量或唯一性约束",
    "event_chain_break": "领域事件丢失、重复、乱序、消费者缺失或死信无诊断",
    "compensation_or_rollback_break": "失败/取消后退款、释放、冲正或 Saga 终态未收敛",
    "requirement_rule_unmapped": "PRD 规则尚未被映射为可执行 Oracle",
}

# Lower-risk generic operations still receive contract coverage.  Business
# criticality is raised only by concrete workflow indicators or explicit config.
CRITICAL_KEYWORDS = {
    "p0": ("payment", "pay", "refund", "settlement", "ledger", "balance", "money", "资金", "支付", "退款", "结算", "余额", "账务"),
    "p1": ("order", "inventory", "stock", "tenant", "approval", "invoice", "contract", "shipment", "fulfillment", "订单", "库存", "租户", "审批", "发票", "履约", "发货"),
    "p2": ("customer", "employee", "user", "export", "report", "booking", "reservation", "客户", "员工", "导出", "报表", "预约"),
}

SOURCE_GENERATORS: tuple[tuple[str, str], ...] = (
    ("universal_spec_behavior", "generate_universal_defect_probes"),
    ("counterexample_relation_mining", "generate_counterexample_probes"),
    ("business_outcome_validation", "generate_business_outcome_probes"),
    ("business_reconciliation", "generate_business_reconciliation_probes"),
    ("business_invariant_mining", "generate_business_invariant_probes"),
    ("multi_source_business_reasoning", "generate_multi_source_reasoning_probes"),
    ("business_lifecycle_reasoning", "generate_business_lifecycle_probes"),
    ("consistency_isolation_reasoning", "generate_consistency_isolation_probes"),
    ("metamorphic_differential_reasoning", "generate_metamorphic_differential_probes"),
    ("temporal_data_regression_reasoning", "generate_temporal_data_regression_probes"),
    ("business_causality_conservation", "generate_business_causality_probes"),
    ("business_population_constraints", "generate_business_population_constraint_probes"),
    ("business_event_chain_reasoning", "generate_business_event_chain_probes"),
    ("business_saga_compensation_reasoning", "generate_business_saga_compensation_probes"),
    ("multi_industry_business_reasoning", "generate_multi_industry_business_probes"),
)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8", errors="replace")).hexdigest()


def _short_hash(value: Any, size: int = 16) -> str:
    return _hash(value)[:size]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _path_template(path: Any) -> str:
    value = str(path or "/").strip() or "/"
    value = re.sub(r"https?://[^/]+", "", value).split("?", 1)[0]
    value = re.sub(r"/\d+(?=/|$)", "/{id}", value)
    value = re.sub(r"/[0-9a-f]{8,}(?=/|$)", "/{id}", value, flags=re.I)
    return value if value.startswith("/") else "/" + value


def _path_tokens(path: Any) -> set[str]:
    return {
        _norm(token)
        for token in re.split(r"[/_\-.{}]+", _path_template(path))
        if _norm(token) and _norm(token) not in {"api", "v1", "v2", "id"}
    }


def _resource_key(path: Any) -> str:
    tokens = sorted(_path_tokens(path))
    return "-".join(tokens[:4]) or "root"


def _output_paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "defect_discovery"
    output = root / "platform_outputs" / project / "business_assurance_coverage"
    return {
        "workspace": workspace,
        "output": output,
        "profile": workspace / "business_assurance_coverage.json",
        "run": workspace / "business_assurance_coverage_run.json",
        "gaps": workspace / "business_assurance_coverage_gaps.json",
        "report": output / "business_assurance_coverage_report.html",
    }


def _section(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = cfg.get("business_assurance_coverage") or cfg.get("quality_assurance_coverage") or {}
    return dict(raw) if isinstance(raw, dict) else {}


def _operation_text(op: dict[str, Any]) -> str:
    return " ".join([
        str(op.get("method") or ""),
        str(op.get("path") or ""),
        str(op.get("operation_id") or ""),
        str(op.get("summary") or ""),
        str(op.get("description") or ""),
        " ".join(str(x) for x in (op.get("tags") or [])),
    ]).lower()


def _criticality(op: dict[str, Any], explicit: dict[tuple[str, str], dict[str, Any]]) -> tuple[str, float, list[str]]:
    method = str(op.get("method") or "GET").upper()
    path = _path_template(op.get("path"))
    override = explicit.get((method, path)) or explicit.get(("*", path))
    if override:
        severity = str(override.get("severity") or override.get("criticality") or "P1").upper()
        if severity not in {"P0", "P1", "P2", "P3"}:
            severity = "P1"
        return severity, {"P0": 1.0, "P1": 0.78, "P2": 0.52, "P3": 0.28}[severity], ["explicit_critical_path"]
    text = _operation_text(op)
    if any(word in text for word in CRITICAL_KEYWORDS["p0"]):
        return "P0", 1.0, ["financial_or_settlement_keyword"]
    if any(word in text for word in CRITICAL_KEYWORDS["p1"]):
        return "P1", 0.78, ["core_business_workflow_keyword"]
    if method in WRITE_METHODS:
        return "P1", 0.74, ["state_changing_operation"]
    if any(word in text for word in CRITICAL_KEYWORDS["p2"]):
        return "P2", 0.52, ["business_record_keyword"]
    return "P3", 0.28, ["generic_contract_operation"]


def _has_query(op: dict[str, Any], names: tuple[str, ...]) -> bool:
    for item in op.get("parameters") or []:
        if str(item.get("in") or "").lower() != "query":
            continue
        name = _norm(item.get("name"))
        if any(_norm(word) in name for word in names):
            return True
    return False


def _expected_mutations_for_operation(op: dict[str, Any], severity: str) -> list[str]:
    method = str(op.get("method") or "GET").upper()
    path = _path_template(op.get("path"))
    text = _operation_text(op)
    tokens = _path_tokens(path)
    mutations: list[str] = ["response_contract_break", "required_data_missing"]

    is_collection = method == "GET" and "{" not in path
    is_detail = method == "GET" and "{" in path
    if is_collection:
        mutations += ["pagination_set_corruption"]
        if _has_query(op, ("status", "state", "type", "category", "tenant", "org", "user", "owner", "department", "date", "from", "to", "keyword", "search", "filter")):
            mutations += ["filter_semantics_lost"]
        if _has_query(op, ("sort", "order", "asc", "desc")):
            mutations += ["sort_semantics_lost"]
        if any(word in text for word in ("summary", "report", "dashboard", "stat", "total", "amount", "金额", "统计", "报表", "看板")):
            mutations += ["aggregate_or_amount_drift"]
    if is_detail:
        mutations += ["unauthorized_resource_access", "list_detail_projection_drift"]
    if any(word in text for word in ("tenant", "organization", "org", "租户", "组织")):
        mutations += ["tenant_boundary_leak"]
    if method in WRITE_METHODS:
        mutations += ["duplicate_business_effect", "state_transition_break", "missing_business_effect"]
        if any(word in text for word in ("refund", "cancel", "rollback", "compensat", "退款", "取消", "回滚", "补偿")):
            mutations += ["compensation_or_rollback_break"]
        if any(word in text for word in ("event", "message", "queue", "webhook", "通知", "消息", "事件")):
            mutations += ["event_chain_break"]
    if any(word in text for word in ("payment", "pay", "refund", "invoice", "amount", "price", "balance", "支付", "退款", "金额", "价格", "余额", "账务")):
        mutations += ["aggregate_or_amount_drift", "missing_business_effect"]
    if any(word in text for word in ("order", "inventory", "stock", "booking", "reservation", "approval", "quota", "订单", "库存", "预约", "审批", "额度")):
        mutations += ["population_constraint_bypass"]
    if any(word in text for word in ("created", "updated", "history", "version", "effective", "expire", "历史", "版本", "生效", "失效")):
        mutations += ["temporal_order_break", "historical_data_regression"]
    # A P0 endpoint deserves an explicit authorization check even when its path
    # does not expose an id parameter (for example payment/report endpoints).
    if severity == "P0":
        mutations += ["unauthorized_resource_access"]

    dedup: list[str] = []
    for mutation in mutations:
        if mutation not in dedup:
            dedup.append(mutation)
    return dedup


def _requirements_to_mutations(rule_type: str, statement: str) -> list[str]:
    kind = str(rule_type or "").lower()
    text = str(statement or "").lower()
    mapping = {
        "idempotency": ["duplicate_business_effect"],
        "authorization": ["unauthorized_resource_access", "tenant_boundary_leak"],
        "state_transition": ["state_transition_break"],
        "boundary": ["required_data_missing"],
        "consistency": ["list_detail_projection_drift", "aggregate_or_amount_drift"],
        "negative_constraint": ["response_contract_break"],
    }
    mutations = list(mapping.get(kind) or ["requirement_rule_unmapped"])
    if any(word in text for word in ("金额", "支付", "退款", "amount", "payment", "refund")):
        mutations.append("aggregate_or_amount_drift")
    if any(word in text for word in ("审批", "额度", "预约", "库存", "limit", "quota", "approval", "inventory")):
        mutations.append("population_constraint_bypass")
    if any(word in text for word in ("消息", "事件", "通知", "event", "message")):
        mutations.append("event_chain_break")
    return list(dict.fromkeys(mutations))


def _explicit_paths(section: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    items = section.get("critical_paths") or section.get("critical_operations") or []
    result: dict[tuple[str, str], dict[str, Any]] = {}
    if not isinstance(items, list):
        return result
    for row in items:
        if not isinstance(row, dict):
            continue
        path = _path_template(row.get("path") or row.get("api") or "")
        if path == "/":
            continue
        method = str(row.get("method") or "*").upper()
        result[(method, path)] = dict(row)
    return result


def _configured_control_units(section: dict[str, Any]) -> list[dict[str, Any]]:
    raw = section.get("required_controls") or section.get("critical_oracles") or []
    units: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return units
    for number, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            continue
        mutation = str(item.get("mutation") or item.get("failure_mode") or "").strip()
        if mutation not in MUTATION_TO_ORACLES:
            mutation = "requirement_rule_unmapped"
        path = _path_template(item.get("path") or item.get("api") or "/")
        method = str(item.get("method") or "GET").upper()
        severity = str(item.get("severity") or "P1").upper()
        if severity not in {"P0", "P1", "P2", "P3"}:
            severity = "P1"
        units.append({
            "unit_id": f"CTRL_{number:03d}",
            "kind": "configured_control",
            "title": str(item.get("title") or item.get("name") or mutation)[:240],
            "statement": str(item.get("statement") or item.get("expected") or "")[:500],
            "path": path,
            "method": method,
            "resource_key": _resource_key(path),
            "severity": severity,
            "weight": {"P0": 1.0, "P1": 0.78, "P2": 0.52, "P3": 0.28}[severity],
            "criticality_reasons": ["enterprise_configured_control"],
            "mutations": [mutation],
            "configured_required_oracles": [str(x) for x in (item.get("required_oracle_sources") or item.get("required_oracles") or []) if str(x)],
        })
    return units


# Optional probe generators, imported lazily so the module stays usable in
# limited deployments where an engine has not been packaged.
#
# Table-driven instead of seventeen copy-pasted try/except blocks. A generator
# that fails to import is not "this family has no probes" — it is coverage that
# quietly disappears while the report still reads as complete, and the old form
# made a real import defect indistinguishable from "not packaged". One loop
# means one place where the loss is recorded, and zero copies to drift.
_GENERATOR_SPECS: tuple[tuple[str, str, str], ...] = (
    ("universal_spec_behavior", ".universal_defect_mining", "generate_universal_defect_probes"),
    ("counterexample_relation_mining", ".counterexample_discovery", "generate_counterexample_probes"),
    ("business_outcome_validation", ".business_outcome_validation", "generate_business_outcome_probes"),
    ("business_reconciliation", ".business_reconciliation", "generate_business_reconciliation_probes"),
    ("business_invariant_mining", ".business_invariant_mining", "generate_business_invariant_probes"),
    ("multi_source_business_reasoning", ".multisource_reasoning", "generate_multi_source_reasoning_probes"),
    ("business_lifecycle_reasoning", ".business_lifecycle_reasoning", "generate_business_lifecycle_probes"),
    ("consistency_isolation_reasoning", ".consistency_isolation_reasoning", "generate_consistency_isolation_probes"),
    ("metamorphic_differential_reasoning", ".metamorphic_differential_reasoning", "generate_metamorphic_differential_probes"),
    ("temporal_data_regression_reasoning", ".temporal_data_regression_reasoning", "generate_temporal_data_regression_probes"),
    ("business_causality_conservation", ".business_causality_conservation", "generate_business_causality_probes"),
    ("business_population_constraints", ".business_population_constraints", "generate_business_population_constraint_probes"),
    ("business_event_chain_reasoning", ".business_event_chain_reasoning", "generate_business_event_chain_probes"),
    ("business_saga_compensation_reasoning", ".business_saga_compensation_reasoning", "generate_business_saga_compensation_probes"),
    ("multi_industry_business_reasoning", ".multi_industry_business_reasoning", "generate_multi_industry_business_probes"),
    ("enterprise_business_knowledge_asset", ".enterprise_knowledge_center", "generate_enterprise_business_knowledge_probes"),
    ("enterprise_testops_control_plane", ".enterprise_testops_control_plane", "generate_enterprise_testops_probes"),
)


def _load_generators() -> list[tuple[str, Callable[..., list[dict[str, Any]]]]]:
    """Import every packaged probe generator, reporting the ones that are not.

    Skipping a generator narrows the assurance coverage. Left silent, that
    breadth loss is invisible (AGENTS.md principle 14): the run still reports a
    coverage number that looks complete. Every skip is logged, and an empty
    result is logged at error level because zero generators means the assurance
    case is built on nothing at all.
    """

    rows: list[tuple[str, Callable[..., list[dict[str, Any]]]]] = []
    skipped: list[str] = []
    for family, module_name, attr in _GENERATOR_SPECS:
        try:
            module = importlib.import_module(module_name, package=__package__)
        except Exception as exc:
            skipped.append(f"{family} (import {module_name}: {type(exc).__name__}: {exc})")
            continue
        generator = getattr(module, attr, None)
        if not callable(generator):
            skipped.append(f"{family} ({module_name}.{attr} missing or not callable)")
            continue
        rows.append((family, generator))

    if skipped:
        _log.warning(
            "business assurance coverage: %d of %d probe generators unavailable; "
            "coverage is narrower than the report implies — %s",
            len(skipped), len(_GENERATOR_SPECS), "; ".join(skipped),
        )
    if not rows:
        _log.error(
            "business assurance coverage: no probe generators could be loaded; "
            "the assurance case would be built on zero generators"
        )
    return rows


def _probe_inventory(openapi: dict[str, Any], cfg: dict[str, Any], project: str, root: Path, limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    probes: list[dict[str, Any]] = []
    failures: list[str] = []
    for expected_source, generator in _load_generators():
        try:
            rows = generator(openapi, cfg, project, root, max_count=limit)
            for row in rows or []:
                if isinstance(row, dict):
                    probe = dict(row)
                    probe.setdefault("source", expected_source)
                    probes.append(probe)
        except Exception as exc:
            failures.append(f"{expected_source}: {exc}")
    # Stable de-duplication: same Oracle/source/path can be represented once.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for probe in probes:
        key = (
            str(probe.get("source") or ""),
            str(probe.get("risk_type") or ""),
            str(probe.get("method") or "GET").upper(),
            _path_template(probe.get("path") or "/"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(probe)
    return unique, failures


def _path_match(unit: dict[str, Any], probe: dict[str, Any]) -> float:
    upath = _path_template(unit.get("path") or "/")
    ppath = _path_template(probe.get("path") or "/")
    if upath == ppath and upath != "/":
        return 1.0
    ukey = str(unit.get("resource_key") or _resource_key(upath))
    pkey = _resource_key(ppath)
    common = set(ukey.split("-")) & set(pkey.split("-"))
    if common:
        return min(0.85, 0.45 + 0.16 * len(common))
    # PRD-only rules may legitimately be backed by an Oracle at another
    # endpoint.  They get a lower confidence coverage match.
    if unit.get("kind") == "requirement" and ppath != "/":
        return 0.35
    return 0.0


def _coverage_for_mutation(unit: dict[str, Any], mutation: str, inventory: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], float]:
    allowed = set(unit.get("configured_required_oracles") or []) or MUTATION_TO_ORACLES.get(mutation, set())
    if not allowed:
        return [], 0.0
    matches: list[dict[str, Any]] = []
    best = 0.0
    for probe in inventory:
        source = str(probe.get("source") or "")
        if source not in allowed:
            continue
        score = _path_match(unit, probe)
        if score <= 0:
            continue
        if unit.get("method") not in {"*", "", None} and str(probe.get("method") or "GET").upper() not in {str(unit.get("method")).upper(), "GET"}:
            # Cross-view / side-effect Oracles may use related GET endpoints.
            score *= 0.85
        best = max(best, score)
        matches.append({
            "probe_id": probe.get("probe_id"),
            "contract_id": probe.get("contract_id"),
            "source": source,
            "risk_type": probe.get("risk_type"),
            "path": _path_template(probe.get("path") or "/"),
            "method": str(probe.get("method") or "GET").upper(),
            "execution_policy": probe.get("execution_policy") or "unknown",
            "match_score": round(score, 3),
        })
    matches.sort(key=lambda item: (-float(item.get("match_score") or 0), str(item.get("source") or "")))
    return matches[:12], best


def _unit_from_operation(op: dict[str, Any], number: int, explicit: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    severity, weight, reasons = _criticality(op, explicit)
    path = _path_template(op.get("path"))
    return {
        "unit_id": f"API_{number:03d}",
        "kind": "operation",
        "title": f"{str(op.get('method') or 'GET').upper()} {path}",
        "statement": str(op.get("summary") or op.get("description") or "")[:500],
        "path": path,
        "method": str(op.get("method") or "GET").upper(),
        "resource_key": _resource_key(path),
        "severity": severity,
        "weight": weight,
        "criticality_reasons": reasons,
        "mutations": _expected_mutations_for_operation(op, severity),
    }


def _unit_from_requirement(rule: dict[str, Any], number: int, operations: list[dict[str, Any]]) -> dict[str, Any]:
    statement = str(rule.get("statement") or "")
    keywords = {_norm(item) for item in (rule.get("keywords") or []) if _norm(item)}
    best: dict[str, Any] | None = None
    best_score = 0
    for op in operations:
        text = _operation_text(op)
        score = sum(1 for keyword in keywords if keyword and keyword in _norm(text))
        if score > best_score:
            best, best_score = op, score
    path = _path_template((best or {}).get("path") or "/")
    method = str((best or {}).get("method") or "GET").upper()
    severity = "P1" if str(rule.get("rule_type") or "") in {"authorization", "idempotency", "state_transition", "consistency"} else "P2"
    return {
        "unit_id": str(rule.get("rule_id") or f"REQ_{number:03d}"),
        "kind": "requirement",
        "title": statement[:220] or f"需求规则 {number}",
        "statement": statement[:700],
        "path": path,
        "method": method,
        "resource_key": _resource_key(path),
        "severity": severity,
        "weight": {"P1": 0.78, "P2": 0.52}[severity],
        "criticality_reasons": ["prd_requirement_rule"],
        "mutations": _requirements_to_mutations(str(rule.get("rule_type") or ""), statement),
        "rule_type": rule.get("rule_type"),
    }


def _learned_regression_units(project: str, root: Path) -> list[dict[str, Any]]:
    try:
        from .confirmed_bug_flywheel import load_confirmed_bug_flywheel_profile, build_confirmed_bug_flywheel
        profile = load_confirmed_bug_flywheel_profile(project, root) or build_confirmed_bug_flywheel(project, root)
    except Exception as _flywheel_exc:
        # Returning [] means "no confirmed-bug regressions to replay", which is
        # indistinguishable from the flywheel being broken. Regression coverage
        # would vanish while the report still looked complete.
        _log.warning(
            "confirmed-bug flywheel unavailable for project=%s; regression "
            "coverage is narrower than the report implies (%s): %s",
            project, type(_flywheel_exc).__name__, _flywheel_exc,
        )
        return []
    units: list[dict[str, Any]] = []
    candidates = [row for row in (profile.get("regression_candidates") or []) if isinstance(row, dict)]
    for number, row in enumerate(candidates, start=1):
        candidate = row.get("candidate") or row
        if not isinstance(candidate, dict):
            continue
        path = _path_template(candidate.get("path_template") or candidate.get("path") or "/")
        risk = _norm(candidate.get("risk_type") or "")
        mutation = "requirement_rule_unmapped"
        if any(word in risk for word in ("tenant", "permission", "idor", "authorization")):
            mutation = "unauthorized_resource_access"
        elif any(word in risk for word in ("state", "lifecycle")):
            mutation = "state_transition_break"
        elif any(word in risk for word in ("event", "message")):
            mutation = "event_chain_break"
        elif any(word in risk for word in ("saga", "compensation", "refund")):
            mutation = "compensation_or_rollback_break"
        elif any(word in risk for word in ("amount", "money", "reconciliation", "causality")):
            mutation = "aggregate_or_amount_drift"
        elif any(word in risk for word in ("invariant", "duplicate", "population")):
            mutation = "population_constraint_bypass"
        units.append({
            "unit_id": f"REG_{number:03d}",
            "kind": "confirmed_regression",
            "title": f"已批准确认缺陷回归：{candidate.get('risk_type') or 'business_rule'}",
            "statement": "已确认缺陷必须由至少一个当前 Oracle 持续保护。",
            "path": path,
            "method": str(candidate.get("method") or "GET").upper(),
            "resource_key": _resource_key(path),
            "severity": str(candidate.get("severity") or "P1").upper() if str(candidate.get("severity") or "P1").upper() in {"P0", "P1", "P2", "P3"} else "P1",
            "weight": 1.0,
            "criticality_reasons": ["approved_confirmed_bug_regression"],
            "mutations": [mutation],
            "business_fingerprint": candidate.get("business_fingerprint"),
        })
    return units


def _evaluate_units(units: list[dict[str, Any]], inventory: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assessed: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for unit in units:
        mutation_results: list[dict[str, Any]] = []
        for mutation in unit.get("mutations") or []:
            matches, best_score = _coverage_for_mutation(unit, str(mutation), inventory)
            killed = best_score >= 0.45
            result = {
                "mutation": mutation,
                "description": MUTATION_DESCRIPTIONS.get(mutation, mutation),
                "required_oracle_sources": sorted(set(unit.get("configured_required_oracles") or []) or MUTATION_TO_ORACLES.get(mutation, set())),
                "killed_by_planned_oracle": killed,
                "coverage_match_score": round(best_score, 3),
                "matching_probes": matches,
            }
            mutation_results.append(result)
            if not killed:
                gaps.append({
                    "gap_id": f"GAP_{_short_hash({'unit': unit.get('unit_id'), 'mutation': mutation}, 18)}",
                    "unit_id": unit.get("unit_id"),
                    "unit_kind": unit.get("kind"),
                    "title": f"质量覆盖缺口：{unit.get('title')} · {MUTATION_DESCRIPTIONS.get(mutation, mutation)}",
                    "severity": unit.get("severity"),
                    "path": unit.get("path"),
                    "method": unit.get("method"),
                    "resource_key": unit.get("resource_key"),
                    "mutation": mutation,
                    "expected_oracle_sources": sorted(set(unit.get("configured_required_oracles") or []) or MUTATION_TO_ORACLES.get(mutation, set())),
                    "reason": "没有发现能够覆盖该业务失败模型的同资源 Oracle。",
                    "execution_policy": "safe_read_only" if str(unit.get("method") or "GET").upper() in SAFE_METHODS else "sandbox_required",
                    "status": "quality_assurance_gap",
                })
        total = max(1, len(mutation_results))
        killed_count = sum(1 for item in mutation_results if item.get("killed_by_planned_oracle"))
        assessed.append({
            **unit,
            "mutation_results": mutation_results,
            "planned_mutation_kill_rate": round(killed_count / total, 3),
            "uncovered_mutation_count": total - killed_count,
            "assurance_state": "covered" if killed_count == total else ("partial" if killed_count else "uncovered"),
        })
    return assessed, gaps


def _summary(units: list[dict[str, Any]], gaps: list[dict[str, Any]], inventory: list[dict[str, Any]], generator_failures: list[str], section: dict[str, Any]) -> dict[str, Any]:
    total_weight = sum(float(unit.get("weight") or 0.0) * max(1, len(unit.get("mutation_results") or [])) for unit in units)
    killed_weight = sum(
        float(unit.get("weight") or 0.0) * sum(1 for result in unit.get("mutation_results") or [] if result.get("killed_by_planned_oracle"))
        for unit in units
    )
    mutation_kill_rate = round(killed_weight / total_weight, 3) if total_weight else 0.0
    critical = [unit for unit in units if str(unit.get("severity")) in {"P0", "P1"}]
    critical_fully_covered = [unit for unit in critical if unit.get("assurance_state") == "covered"]
    reqs = [unit for unit in units if unit.get("kind") == "requirement"]
    learned = [unit for unit in units if unit.get("kind") == "confirmed_regression"]
    paths = [unit for unit in units if unit.get("kind") == "operation"]
    structural = round(sum(1 for unit in units if unit.get("assurance_state") != "uncovered") / max(1, len(units)), 3)
    critical_coverage = round(len(critical_fully_covered) / max(1, len(critical)), 3)
    requirement_coverage = round(sum(1 for unit in reqs if unit.get("assurance_state") == "covered") / max(1, len(reqs)), 3) if reqs else 1.0
    regression_coverage = round(sum(1 for unit in learned if unit.get("assurance_state") == "covered") / max(1, len(learned)), 3) if learned else 1.0
    # Score is evidence *design* coverage, not a claim that no other bug exists.
    score = round(100 * (0.34 * mutation_kill_rate + 0.26 * critical_coverage + 0.22 * requirement_coverage + 0.10 * regression_coverage + 0.08 * structural), 1)
    min_score = float(section.get("minimum_assurance_score") or section.get("min_score") or 82.0)
    p0_gaps = [gap for gap in gaps if str(gap.get("severity")) == "P0"]
    p1_gaps = [gap for gap in gaps if str(gap.get("severity")) == "P1"]
    if p0_gaps or mutation_kill_rate < 0.70:
        decision = "not_ready_for_quality_assurance_claim"
    elif score < min_score or p1_gaps:
        decision = "conditional_assurance_requires_closure"
    else:
        decision = "evidence_backed_continuous_assurance"
    return {
        "assurance_score": score,
        "minimum_assurance_score": min_score,
        "modeled_mutation_kill_rate": mutation_kill_rate,
        "critical_unit_coverage_rate": critical_coverage,
        "requirement_rule_coverage_rate": requirement_coverage,
        "confirmed_regression_coverage_rate": regression_coverage,
        "structural_coverage_rate": structural,
        "assurance_decision": decision,
        "assurance_decision_label": {
            "not_ready_for_quality_assurance_claim": "尚不具备质量保障主张条件",
            "conditional_assurance_requires_closure": "具备持续保障能力，但关键覆盖缺口需闭环",
            "evidence_backed_continuous_assurance": "具备基于证据的持续质量保障条件",
        }[decision],
        "operation_unit_count": len(paths),
        "requirement_unit_count": len(reqs),
        "confirmed_regression_unit_count": len(learned),
        "critical_unit_count": len(critical),
        "critical_uncovered_gap_count": len(p0_gaps) + len(p1_gaps),
        "p0_coverage_gap_count": len(p0_gaps),
        "p1_coverage_gap_count": len(p1_gaps),
        "coverage_gap_count": len(gaps),
        "planned_oracle_probe_count": len(inventory),
        "oracle_generator_failure_count": len(generator_failures),
        "claim_guard": {
            "absolute_guarantee_allowed": False,
            "approved_product_language": "基于业务 Oracle、反例搜索、回归证据和覆盖缺口治理的持续质量保障",
            "prohibited_product_language": ["自动保证零缺陷", "覆盖所有业务 Bug", "无需人工复核即可保证生产质量"],
        },
    }


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = _json(data).lower()
    banned = ("private_ground_truth", "ground_truth_bugs", "enabled_bugs", "current_bug_set", "bug_instance_id", "password", "authorization")
    found = [marker for marker in banned if marker in text]
    return {"passed": not found, "markers": found}


def build_business_assurance_coverage_profile(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    paths = config_paths(project, root)
    cfg = load_real_project_config(project, root)
    section = _section(cfg)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    openapi = openapi if isinstance(openapi, dict) else {}
    operations = _operations(openapi)
    explicit = _explicit_paths(section)
    prd = "\n".join(
        _read_text(paths["input_dir"] / name)
        for name in ("prd.md", "requirements.md", "business_rules.md")
    )


    rules = _extract_requirement_rules(prd)
    inventory_limit = max(20, min(int(options.get("inventory_limit") or section.get("inventory_limit") or 120), 360))
    inventory, generator_failures = _probe_inventory(openapi, cfg, project, root, inventory_limit)
    units = [_unit_from_operation(op, number, explicit) for number, op in enumerate(operations, start=1)]
    units += [_unit_from_requirement(rule, number, operations) for number, rule in enumerate(rules, start=1)]
    units += _configured_control_units(section)
    units += _learned_regression_units(project, root)
    assessed, gaps = _evaluate_units(units, inventory)
    summary = _summary(assessed, gaps, inventory, generator_failures, section)
    result = {
        "phase": PHASE,
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "summary": summary,
        "assurance_units": assessed,
        "coverage_gaps": gaps,
        "oracle_inventory": [
            {
                "probe_id": item.get("probe_id"),
                "contract_id": item.get("contract_id"),
                "source": item.get("source"),
                "risk_type": item.get("risk_type"),
                "method": str(item.get("method") or "GET").upper(),
                "path": _path_template(item.get("path") or "/"),
                "execution_policy": item.get("execution_policy") or "unknown",
            }
            for item in inventory
        ],
        "generator_failures": generator_failures[:50],
        "governance": {
            "uses_only_prd_openapi_project_config_and_approved_regressions": True,
            "mutation_testing_is_modelled_not_production_fault_injection": True,
            "safe_live_execution_is_get_only": True,
            "write_path_assurance_requires_sandbox": True,
            "quality_claim_is_evidence_backed_and_non_absolute": True,
        },
    }
    result["private_leak_check"] = _private_leak_check(result)
    output = _output_paths(project, root)
    output["workspace"].mkdir(parents=True, exist_ok=True)
    output["output"].mkdir(parents=True, exist_ok=True)
    _write_json(output["profile"], result)
    _write_json(output["gaps"], {"items": gaps, "summary": summary})
    _write_json(output["output"] / "business_assurance_coverage.json", result)
    _write_json(output["output"] / "business_assurance_coverage_gaps.json", {"items": gaps, "summary": summary})
    output["report"].write_text(render_business_assurance_coverage_report(result), encoding="utf-8")
    return result


def load_business_assurance_coverage_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _load_json(_output_paths(project, root)["profile"], {})
    return data if isinstance(data, dict) and data else None


def generate_business_assurance_coverage_probes(
    openapi: dict[str, Any],
    cfg: dict[str, Any],
    project_id: str = "real_project_demo",
    root: Path | None = None,
    max_count: int | None = None,
) -> list[dict[str, Any]]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    profile = load_business_assurance_coverage_profile(project, root) or build_business_assurance_coverage_profile(project, root)
    limit = int(max_count or max(30, int(cfg.get("max_probe_count") or 100)))
    probes: list[dict[str, Any]] = []
    for number, gap in enumerate(profile.get("coverage_gaps") or [], start=1):
        if len(probes) >= limit:
            break
        path = _path_template(gap.get("path") or "/")
        method = str(gap.get("method") or "GET").upper()
        mutation = str(gap.get("mutation") or "requirement_rule_unmapped")
        policy = gap.get("execution_policy") or ("safe_read_only" if method in SAFE_METHODS else "sandbox_required")
        probes.append({
            "probe_id": f"QA_COVER_{number:04d}",
            "contract_id": gap.get("gap_id"),
            "source": "business_assurance_coverage",
            "risk_type": "assurance_coverage_gap",
            "quality_assurance_mutation": mutation,
            "title": f"覆盖闭环：{gap.get('title')}",
            "path": path,
            "method": method,
            "severity": gap.get("severity") or "P1",
            "expected": f"至少存在并执行覆盖“{MUTATION_DESCRIPTIONS.get(mutation, mutation)}”的 Oracle；当前缺口必须闭环。",
            "bug_signal": "关键业务失败模型没有任何可追溯的测试 Oracle，发布质量无法被证明。",
            "execution_policy": policy,
            "destructive": policy == "sandbox_required",
            "needs_human_review": True,
            "assurance_gap_id": gap.get("gap_id"),
            "required_oracle_sources": gap.get("expected_oracle_sources") or [],
            "claim_guard": "coverage_gap_not_a_confirmed_product_bug",
        })
    return probes


def run_business_assurance_coverage(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Materialize the assurance case and turn unresolved gaps into governance findings.

    No HTTP request is made here.  The engine evaluates test/Oracle coverage and
    mutation survivorship; live execution remains owned by the underlying
    read-only discovery engines.
    """
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    profile = build_business_assurance_coverage_profile(project, root, options)
    findings: list[dict[str, Any]] = []
    for gap in profile.get("coverage_gaps") or []:
        severity = str(gap.get("severity") or "P1")
        findings.append({

            "finding_id": f"QA_FIND_{_short_hash(gap.get('gap_id'), 18)}",
            "issue_id": f"QA_FIND_{_short_hash(gap.get('gap_id'), 18)}",
            "contract_id": gap.get("gap_id"),
            "title": gap.get("title"),
            "risk_type": "assurance_coverage_gap",
            "business_assurance_type": "modelled_mutation_survivor",
            "severity": severity,
            "confidence": 0.99,
            "status": "needs_human_review",
            "expected": "关键业务失败模型应有端到端 Oracle 覆盖并持续提供执行证据。",
            "actual": gap.get("reason"),
            "evidence": {
                "assurance_unit_id": gap.get("unit_id"),
                "mutation": gap.get("mutation"),
                "required_oracle_sources": gap.get("expected_oracle_sources") or [],
                "path_template": gap.get("path"),
                "method": gap.get("method"),
                "redacted": True,
            },
            "execution_policy": gap.get("execution_policy"),
        })
    decision = (profile.get("summary") or {}).get("assurance_decision")
    result = {
        "phase": PHASE,
        "project_id": project,
        "generated_at_utc": _now(),
        "execution_mode": "modelled_coverage_only",
        "summary": profile.get("summary") or {},
        "findings": findings,
        "profile_ref": {
            "assurance_score": (profile.get("summary") or {}).get("assurance_score"),
            "modeled_mutation_kill_rate": (profile.get("summary") or {}).get("modeled_mutation_kill_rate"),
            "assurance_decision": decision,
        },
        "governance": {
            "no_production_fault_injection": True,
            "no_write_requests": True,
            "coverage_gaps_are_release_governance_findings_not_confirmed_product_bugs": True,
        },
    }
    output = _output_paths(project, root)
    _write_json(output["run"], result)
    _write_json(output["output"] / "business_assurance_coverage_run.json", result)
    return result


def render_business_assurance_coverage_report(data: dict[str, Any]) -> str:
    summary = data.get("summary") or {}
    gaps = data.get("coverage_gaps") or []
    score = html.escape(str(summary.get("assurance_score") or 0))
    decision = html.escape(str(summary.get("assurance_decision_label") or summary.get("assurance_decision") or ""))
    cards = (
        f"<div class='card'><b>质量保障分</b><strong>{score}</strong><span>基于可追溯 Oracle 覆盖与模型化故障存活率</span></div>"
        f"<div class='card'><b>模型化故障杀伤率</b><strong>{html.escape(str(summary.get('modeled_mutation_kill_rate') or 0))}</strong><span>未被任一 Oracle 覆盖的失败模型会成为缺口</span></div>"
        f"<div class='card'><b>关键覆盖缺口</b><strong>{html.escape(str(summary.get('critical_uncovered_gap_count') or 0))}</strong><span>P0/P1 业务路径与已确认回归优先闭环</span></div>"
    )
    rows = "".join(
        "<tr>"
        f"<td>{html.escape(str(item.get('severity') or 'P1'))}</td>"
        f"<td>{html.escape(str(item.get('mutation') or ''))}</td>"
        f"<td>{html.escape(str(item.get('path') or ''))}</td>"
        f"<td>{html.escape(', '.join(str(x) for x in (item.get('expected_oracle_sources') or [])))}</td>"
        "</tr>"
        for item in gaps[:100]
    ) or "<tr><td colspan='4'>当前没有未覆盖的模型化失败模式。</td></tr>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>QualiBug 质量保障覆盖</title>
<style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;background:#07111f;color:#e5edf7;margin:0;padding:28px}}.grid{{display:flex;gap:16px;flex-wrap:wrap}}.card{{background:#102033;border:1px solid #244462;border-radius:12px;padding:16px;min-width:220px}}strong{{display:block;font-size:28px;color:#7dd3fc;margin:8px 0}}table{{width:100%;border-collapse:collapse;background:#102033;margin-top:18px}}td,th{{padding:10px;border-bottom:1px solid #244462;text-align:left}}.banner{{padding:16px;border-radius:10px;background:#122c45;margin:18px 0}}</style>
</head><body><h1>业务质量保障覆盖</h1><div class='banner'>{decision}。绝对零缺陷主张被系统禁止；结果只代表当前资料、Oracle、探针与证据所支持的保障范围。</div><div class='grid'>{cards}</div><h2>待闭环的模型化失败模式</h2><table><thead><tr><th>级别</th><th>失败模式</th><th>路径</th><th>建议 Oracle 家族</th></tr></thead><tbody>{rows}</tbody></table></body></html>"""


def _cli() -> int:
    parser = argparse.ArgumentParser(description="QualiBug Phase56 business quality assurance coverage")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--root", default="")
    parser.add_argument("--run", action="store_true")
    args = parser.parse_args()
    root = Path(args.root) if args.root else ROOT
    result = run_business_assurance_coverage(args.project, root) if args.run else build_business_assurance_coverage_profile(args.project, root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
