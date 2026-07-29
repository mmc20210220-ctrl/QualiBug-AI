"""Project existing enterprise-understanding state into Command Center.

This module never builds a second knowledge model or a second readiness authority. It loads the
persisted enterprise knowledge asset and enriches the existing ``knowledge_summary`` field with
its current understanding, Scenario IR, execution-contract and Runtime Plan gate receipts.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _ready(gate: dict[str, Any], *keys: str) -> bool:
    return any(gate.get(key) is True for key in keys)


def _readable_reason(value: Any) -> str:
    code = _text(value)
    labels = {
        "SEMANTIC_UNDERSTANDING_NOT_CLOSED": "企业业务语义尚未闭合",
        "IMPLEMENTATION_BINDING_NOT_CLOSED": "业务场景尚未唯一绑定到系统实现",
        "IMPLEMENTATION_BINDING_CONFLICT": "业务场景与系统实现存在冲突",
        "MODEL_SCHEMA_OR_EVIDENCE_INVALID": "部分理解结论缺少合格结构或来源证据",
        "UNRESOLVED_BUSINESS_FACT_OR_BEHAVIOR_CONFLICTS": "企业资料中的业务事实仍有未解决冲突",
        "OPERATION_OBJECT_UNRESOLVED": "部分业务操作尚未确定唯一作用对象",
        "EXECUTION_CONTRACT_SOURCE_EVIDENCE_MISSING": "部分执行场景缺少原始资料证据",
        "EXECUTION_CONTRACT_AUTHORITATIVE_ACTION_ENTRY_MISSING": "部分业务场景尚未绑定权威执行入口",
        "EXECUTION_CONTRACT_PERMISSION_RESPONSE_OBSERVER_UNRESOLVED": "权限结果缺少可验证的响应观察方式",
        "EXECUTION_CONTRACT_EFFECT_OBSERVER_UNRESOLVED": "业务效果缺少可验证的观察方式",
        "RUNTIME_PLAN_REQUEST_FIELD_LOCATION_UNRESOLVED": "请求字段在接口契约中的位置尚未明确",
        "RUNTIME_PLAN_REQUEST_FIELD_LOCATION_AMBIGUOUS": "同一请求字段在接口契约中存在多个位置",
        "RUNTIME_PLAN_CREDENTIAL_REF_AMBIGUOUS": "同一业务角色对应多个测试凭证引用",
        "RUNTIME_PLAN_ORACLE_TEMPLATE_UNRESOLVED": "运行计划尚未形成可验证的观察模板",
        "RUNTIME_PLAN_CLEANUP_TEMPLATE_UNRESOLVED": "写操作尚未形成安全清理模板",
    }
    return labels.get(code, code.replace("_", " ").strip())


def _row_message(value: Any) -> str:
    row = _record(value)
    details = _record(row.get("details"))
    for candidate in (
        row.get("message"),
        row.get("description"),
        row.get("statement"),
        row.get("raw_statement"),
        details.get("message"),
        details.get("statement"),
        _readable_reason(row.get("reason_code")),
        _readable_reason(row.get("kind")),
    ):
        message = _text(candidate)
        if message:
            return message
    return ""


def _understanding_projection(asset: dict[str, Any]) -> dict[str, Any]:
    summary = _record(asset.get("summary"))
    model = _record(asset.get("enterprise_understanding_model"))
    model_gate = _record(model.get("gate"))
    comprehension_gate = _record(asset.get("enterprise_comprehension_gate"))
    planning_gate = _record(asset.get("scenario_planning_gate"))
    scenario_gate = _record(asset.get("scenario_ir_gate"))
    execution_gate = _record(asset.get("scenario_execution_contract_gate"))
    runtime_plan_gate = _record(asset.get("runtime_plan_gate"))
    model_metrics = _record(model_gate.get("metrics"))
    scenario_metrics = _record(scenario_gate.get("metrics"))
    execution_metrics = _record(execution_gate.get("metrics"))
    runtime_metrics = _record(runtime_plan_gate.get("metrics"))

    understanding_status = (
        _text(summary.get("enterprise_understanding_status"))
        or _text(model_gate.get("status"))
        or _text(comprehension_gate.get("status"))
        or "NOT_BUILT"
    )
    understanding_ready = bool(summary.get("enterprise_understanding_ready")) or _ready(
        model_gate, "entry_allowed"
    ) or _ready(comprehension_gate, "entry_allowed")
    planning_ready = _ready(planning_gate, "scenario_planning_allowed", "entry_allowed")
    scenario_ready = _ready(scenario_gate, "entry_allowed")
    execution_ready = _ready(execution_gate, "execution_contract_ready", "entry_allowed")
    runtime_plan_ready = _ready(runtime_plan_gate, "runtime_plan_ready", "entry_allowed")

    gates = [
        {
            "key": "enterprise_understanding",
            "label": "企业理解",
            "status": understanding_status,
            "ready": understanding_ready,
        },
        {
            "key": "scenario_planning",
            "label": "场景规划",
            "status": _text(planning_gate.get("status")) or "NOT_BUILT",
            "ready": planning_ready,
        },
        {
            "key": "scenario_ir",
            "label": "Scenario IR",
            "status": _text(scenario_gate.get("status")) or "NOT_BUILT",
            "ready": scenario_ready,
        },
        {
            "key": "scenario_execution_contract",
            "label": "执行合同",
            "status": _text(execution_gate.get("status")) or "NOT_BUILT",
            "ready": execution_ready,
        },
        {
            "key": "runtime_plan",
            "label": "Runtime Plan",
            "status": _text(runtime_plan_gate.get("status")) or "NOT_BUILT",
            "ready": runtime_plan_ready,
        },
    ]

    blockers: list[str] = []
    for value in _rows(model_gate.get("critical_unknowns")):
        message = _row_message(value)
        if message:
            blockers.append(message)
    for value in _rows(model_gate.get("unresolved_conflicts")):
        message = _row_message(value)
        if message:
            blockers.append(message)
    for gate in (model_gate, planning_gate, scenario_gate, execution_gate, runtime_plan_gate):
        for value in _rows(gate.get("blocking_reasons")):
            message = _readable_reason(value)
            if message:
                blockers.append(message)
    for value in _rows(asset.get("runtime_plan_unknowns")):
        row = _record(value)
        if not row.get("blocks_runtime_plan"):
            continue
        message = _row_message(row)
        if message:
            blockers.append(message)
    for value in _rows(asset.get("coverage_gaps")):
        row = _record(value)
        kind = _text(row.get("kind"))
        if not any(
            token in kind
            for token in ("UNDERSTANDING", "SCENARIO", "EXECUTION_CONTRACT", "RUNTIME_PLAN")
        ):
            continue
        message = _text(row.get("message")) or _text(row.get("operator_action")) or _readable_reason(kind)
        if message:
            blockers.append(message)

    return {
        "enterprise_understanding_model_id": _text(summary.get("enterprise_understanding_model_id"))
        or _text(model.get("model_id")),
        "enterprise_understanding_status": understanding_status,
        "enterprise_understanding_ready": understanding_ready,
        "understood_business_object_count": _integer(
            summary.get("understood_business_object_count"), len(_rows(model.get("business_objects")))
        ),
        "understood_actor_count": _integer(
            summary.get("understood_actor_count"), len(_rows(model.get("actors")))
        ),
        "understood_operation_count": _integer(
            summary.get("understood_operation_count"), len(_rows(model.get("operations")))
        ),
        "understood_object_relation_count": _integer(
            summary.get("understood_object_relation_count"), len(_rows(model.get("object_relations")))
        ),
        "understood_lifecycle_count": _integer(
            summary.get("understood_lifecycle_count"), len(_rows(model.get("lifecycles")))
        ),
        "understood_process_count": _integer(
            summary.get("understood_process_count"), len(_rows(model.get("processes")))
        ),
        "enterprise_understanding_unknown_count": _integer(
            summary.get("enterprise_understanding_unknown_count"), len(_rows(model.get("unknowns")))
        ),
        "enterprise_understanding_conflict_count": _integer(
            summary.get("enterprise_understanding_conflict_count"), len(_rows(model.get("conflicts")))
        ),
        "source_traceability_rate": _number(model_metrics.get("source_traceability_rate")),
        "operation_object_binding_rate": _number(model_metrics.get("operation_object_binding_rate")),
        "lifecycle_completeness": _number(model_metrics.get("lifecycle_completeness")),
        "scenario_planning_status": _text(planning_gate.get("status")) or "NOT_BUILT",
        "scenario_planning_ready": planning_ready,
        "scenario_ir_status": _text(scenario_gate.get("status")) or "NOT_BUILT",
        "scenario_ir_ready": scenario_ready,
        "scenario_ir_count": _integer(
            summary.get("scenario_ir_count"),
            _integer(scenario_metrics.get("scenario_count"), len(_rows(asset.get("scenario_ir")))),
        ),
        "scenario_ir_plannable_count": _integer(
            summary.get("scenario_ir_plannable_count"),
            _integer(scenario_metrics.get("plannable_scenario_count")),
        ),
        "scenario_ir_incomplete_count": _integer(
            summary.get("scenario_ir_incomplete_count"),
            _integer(scenario_metrics.get("incomplete_scenario_count")),
        ),
        "scenario_ir_unknown_count": _integer(
            summary.get("scenario_ir_unknown_count"), len(_rows(asset.get("scenario_ir_unknowns")))
        ),
        "scenario_execution_contract_status": _text(execution_gate.get("status")) or "NOT_BUILT",
        "scenario_execution_contract_ready": execution_ready,
        "scenario_execution_contract_count": _integer(execution_metrics.get("execution_contract_count")),
        "scenario_execution_contract_incomplete_count": _integer(
            execution_metrics.get("incomplete_execution_contract_count")
        ),
        "scenario_execution_contract_unknown_count": _integer(
            execution_metrics.get("execution_contract_unknown_count"),
            len(_rows(asset.get("scenario_execution_contract_unknowns"))),
        ),
        "runtime_plan_status": _text(runtime_plan_gate.get("status")) or "NOT_BUILT",
        "runtime_plan_ready": runtime_plan_ready,
        "runtime_plan_count": _integer(
            summary.get("runtime_plan_count"),
            _integer(runtime_metrics.get("runtime_plan_count"), len(_rows(asset.get("runtime_plans")))),
        ),
        "runtime_plan_incomplete_count": _integer(
            runtime_metrics.get("incomplete_runtime_plan_count")
        ),
        "runtime_plan_unknown_count": _integer(
            runtime_metrics.get("runtime_plan_unknown_count"), len(_rows(asset.get("runtime_plan_unknowns")))
        ),
        "formal_scenario_chain_ready": all(bool(row.get("ready")) for row in gates),
        "understanding_gates": gates,
        "understanding_blockers": list(dict.fromkeys(blockers))[:8],
        "understanding_projection_contract": "EXISTING_KNOWLEDGE_ASSET_GATE_PROJECTION_NOT_SECOND_AUTHORITY",
        "understanding_source_of_truth": "existing_enterprise_business_knowledge_asset",
    }


def project_existing_understanding_command_center(
    payload: dict[str, Any],
    *,
    project: str,
    root: Path,
) -> dict[str, Any]:
    """Enrich the existing Command Center ``knowledge_summary`` in place."""
    result = dict(payload)
    data_is_enveloped = isinstance(result.get("data"), dict)
    data = dict(_record(result.get("data"))) if data_is_enveloped else result

    try:
        from .enterprise_knowledge_center import load_enterprise_business_knowledge_asset

        asset = load_enterprise_business_knowledge_asset(project, root)
    except Exception:
        asset = None
    if not isinstance(asset, dict) or not asset:
        return result

    existing = dict(_record(data.get("knowledge_summary")))
    data["knowledge_summary"] = {**existing, **_understanding_projection(asset)}
    if data_is_enveloped:
        result["data"] = data
        return result
    return data


class UnderstandingCommandCenterProjectionMixin:
    """Post-process the existing CommandCenterBuilderMixin result without replacing it."""

    def _build_command_center(self, project_id: str, root: Path) -> dict:
        payload = super()._build_command_center(project_id, root)
        if not isinstance(payload, dict):
            return payload
        return project_existing_understanding_command_center(
            payload,
            project=project_id,
            root=root,
        )


__all__ = [
    "UnderstandingCommandCenterProjectionMixin",
    "project_existing_understanding_command_center",
]
