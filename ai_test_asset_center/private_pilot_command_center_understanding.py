"""Project existing enterprise-understanding state into Command Center.

This module never builds a second knowledge model or a second readiness authority. It loads the
persisted enterprise knowledge asset and enriches the existing ``knowledge_summary`` field with
its current understanding, Scenario IR, execution-contract, Runtime Plan and Runtime
Materialization gate receipts.
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
        "RUNTIME_MATERIALIZATION_ENVIRONMENT_REF_UNRESOLVED": "运行实例尚未唯一绑定测试环境",
        "RUNTIME_MATERIALIZATION_BASE_URL_UNRESOLVED": "运行实例尚未获得测试环境地址",
        "RUNTIME_MATERIALIZATION_ENVIRONMENT_REF_AMBIGUOUS": "运行实例匹配到多个候选测试环境",
        "RUNTIME_MATERIALIZATION_PRODUCTION_WRITE_FORBIDDEN": "生产环境写入被安全策略禁止",
        "RUNTIME_MATERIALIZATION_NON_PRODUCTION_ENVIRONMENT_UNPROVEN": "尚未证明当前环境为非生产测试环境",
        "RUNTIME_MATERIALIZATION_CREDENTIAL_REF_UNRESOLVED": "运行实例尚未绑定对应角色的凭据引用",
        "RUNTIME_MATERIALIZATION_SOURCE_EVIDENCE_MISSING": "运行实例缺少可追溯的企业资料证据",
        "RUNTIME_MATERIALIZATION_TEST_DATA_BINDING_MISSING": "运行实例缺少测试数据绑定",
        "RUNTIME_MATERIALIZATION_TEST_DATA_BINDING_AMBIGUOUS": "运行实例存在多个测试数据候选绑定",
        "RUNTIME_MATERIALIZATION_TEST_DATA_BINDING_NOT_APPROVED": "运行实例引用的测试数据尚未批准",
        "RUNTIME_MATERIALIZATION_SAFE_CLEANUP_CAPABILITY_UNRESOLVED": "写操作尚未绑定可验证的安全清理能力",
    }
    return labels.get(code, code.replace("_", " ").strip())


def _row_message(value: Any) -> str:
    row = _record(value)
    details = _record(row.get("details"))
    for candidate in (
        row.get("message"),
        row.get("description"),
        row.get("question"),
        row.get("reason"),
        row.get("statement"),
        row.get("raw_statement"),
        row.get("resolution_policy"),
        details.get("message"),
        details.get("question"),
        details.get("statement"),
        details.get("reason"),
        _readable_reason(row.get("reason_code")),
        _readable_reason(row.get("kind")),
    ):
        message = _text(candidate)
        if message:
            return message
    return ""


def _source_labels(asset: dict[str, Any]) -> dict[str, str]:
    raw_sources = asset.get("source_inventory") or asset.get("sources") or asset.get("items") or []
    entries = (
        [
            ({"source_id": key, **value} if isinstance(value, dict) else {"source_id": key})
            for key, value in raw_sources.items()
        ]
        if isinstance(raw_sources, dict)
        else _rows(raw_sources)
    )
    labels: dict[str, str] = {}
    for value in entries:
        row = _record(value)
        source_id = _text(row.get("source_id") or row.get("id") or row.get("source_ref"))
        label = _text(
            row.get("display_name")
            or row.get("filename")
            or row.get("original_name")
            or row.get("name")
            or row.get("stored_path")
        )
        if source_id:
            labels[source_id] = label or source_id
    return labels


def _evidence_candidates(value: Any) -> list[dict[str, Any]]:
    row = _record(value)
    details = _record(row.get("details"))
    candidates: list[dict[str, Any]] = []
    for raw in (
        row.get("evidence"),
        row.get("source_evidence"),
        row.get("evidence_refs"),
        # Chinese business fact conflicts carry opposing spans under `facts`.
        row.get("facts"),
        details.get("evidence"),
        details.get("source_evidence"),
        details.get("facts"),
    ):
        if isinstance(raw, dict):
            candidates.append(raw)
        elif isinstance(raw, list):
            candidates.extend(item for item in raw if isinstance(item, dict))
    if any(
        _text(row.get(key))
        for key in (
            "source_id",
            "source_ref",
            "source_locator",
            "locator",
            "quote",
            "verbatim_quote",
            "statement",
        )
    ):
        candidates.append(row)
    for source_ref in _rows(row.get("source_refs")):
        if isinstance(source_ref, dict):
            candidates.append(source_ref)
        elif _text(source_ref):
            candidates.append({"source_id": source_ref})
    return candidates


def _source_receipts(value: Any, labels: dict[str, str]) -> list[dict[str, str]]:
    receipts: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in _evidence_candidates(value):
        row = _record(raw)
        source_id = _text(row.get("source_id") or row.get("source_ref") or row.get("document_id"))
        source_name = _text(
            row.get("source_name")
            or row.get("display_name")
            or row.get("filename")
            or labels.get(source_id)
        )
        locator = _text(
            row.get("source_locator")
            or row.get("locator")
            or row.get("section")
            or row.get("line_range")
            or row.get("page")
            or row.get("asset_ref")
        )
        quote = _text(
            row.get("quote")
            or row.get("verbatim_quote")
            or row.get("source_excerpt")
            or row.get("excerpt")
            or row.get("statement")
            or row.get("raw_statement")
        )[:240]
        fact_id = _text(row.get("fact_id"))
        if not any((source_id, source_name, locator, quote, fact_id)):
            continue
        key = (source_id or source_name, locator, quote, fact_id)
        if key in seen:
            continue
        seen.add(key)
        receipts.append(
            {
                "source_id": source_id,
                "source_name": source_name or source_id,
                "source_locator": locator,
                "quote": quote,
                "fact_id": fact_id,
            }
        )
    return receipts[:4]


def _is_unresolved_conflict(value: Any) -> bool:
    status = _text(_record(value).get("status") or "UNRESOLVED").upper()
    return status not in {"RESOLVED", "SUPERSEDED", "DISMISSED"}


def _is_blocking_unknown(value: Any) -> bool:
    row = _record(value)
    return any(
        row.get(key) is True
        for key in (
            "blocks_formal_understanding",
            "blocks_scenario_planning",
            "blocks_scenario_ir",
            "blocks_execution_contract",
            "blocks_runtime_plan",
            "blocks_runtime_materialization",
            "blocking",
        )
    )


def _blocker_receipts(
    asset: dict[str, Any], model: dict[str, Any], gates: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    del gates  # Receipts come from existing asset unknown/conflict rows, never recomputed gates.
    labels = _source_labels(asset)
    model_gate = _record(model.get("gate"))
    candidates: list[tuple[str, Any]] = []
    candidates.extend(("critical_unknown", row) for row in _rows(model_gate.get("critical_unknowns")))
    candidates.extend(("source_conflict", row) for row in _rows(model_gate.get("unresolved_conflicts")))
    candidates.extend(
        ("enterprise_unknown", row)
        for row in _rows(model.get("unknowns"))
        if _is_blocking_unknown(row)
    )
    candidates.extend(
        ("source_conflict", row)
        for row in _rows(model.get("conflicts"))
        if _is_unresolved_conflict(row)
    )
    candidates.extend(("scenario_ir_unknown", row) for row in _rows(asset.get("scenario_ir_unknowns")))
    candidates.extend(
        ("execution_contract_unknown", row)
        for row in _rows(asset.get("scenario_execution_contract_unknowns"))
    )
    candidates.extend(
        ("runtime_plan_unknown", row)
        for row in _rows(asset.get("runtime_plan_unknowns"))
        if _is_blocking_unknown(row)
    )
    candidates.extend(
        ("runtime_materialization_unknown", row)
        for row in _rows(asset.get("runtime_materialization_unknowns"))
        if _is_blocking_unknown(row)
    )
    candidates.extend(
        ("coverage_gap", row)
        for row in _rows(asset.get("coverage_gaps"))
        if any(
            token in _text(_record(row).get("kind"))
            for token in (
                "UNDERSTANDING",
                "SCENARIO",
                "EXECUTION_CONTRACT",
                "RUNTIME_PLAN",
                "RUNTIME_MATERIALIZATION",
            )
        )
    )

    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for category, value in candidates:
        row = _record(value)
        message = _row_message(row)
        kind = _text(row.get("reason_code") or row.get("kind") or category)
        if not message and not kind:
            continue
        key = (kind, message or _readable_reason(kind))
        receipt = merged.setdefault(
            key,
            {
                "receipt_id": _text(
                    row.get("unknown_id")
                    or row.get("conflict_id")
                    or row.get("gap_id")
                    or row.get("runtime_plan_unknown_id")
                    or row.get("runtime_materialization_unknown_id")
                )
                or f"{category}:{kind}:{message}"[:180],
                "category": category,
                "kind": kind,
                "message": message or _readable_reason(kind),
                "operator_action": _text(
                    row.get("operator_action")
                    or row.get("recommended_action")
                    or row.get("resolution_policy")
                    or _record(row.get("authority_decision")).get("required_operator_action")
                ),
                "blocking": _is_blocking_unknown(row)
                or category
                in {
                    "critical_unknown",
                    "source_conflict",
                    "runtime_plan_unknown",
                    "runtime_materialization_unknown",
                },
                "source_evidence": [],
            },
        )
        evidence = [*_rows(receipt.get("source_evidence")), *_source_receipts(row, labels)]
        deduped: list[dict[str, str]] = []
        seen: set[tuple[str, str, str, str]] = set()
        for item in evidence:
            evidence_row = _record(item)
            evidence_key = (
                _text(evidence_row.get("source_id") or evidence_row.get("source_name")),
                _text(evidence_row.get("source_locator")),
                _text(evidence_row.get("quote")),
                _text(evidence_row.get("fact_id")),
            )
            if evidence_key in seen:
                continue
            seen.add(evidence_key)
            deduped.append(dict(evidence_row))
        receipt["source_evidence"] = deduped[:4]
        receipt["source_backed"] = bool(deduped)

    ordered = list(merged.values())
    ordered.sort(
        key=lambda row: (
            not bool(row.get("blocking")),
            not bool(row.get("source_backed")),
            _text(row.get("message")),
        )
    )
    return ordered[:8]


def _understanding_projection(asset: dict[str, Any]) -> dict[str, Any]:
    summary = _record(asset.get("summary"))
    model = _record(asset.get("enterprise_understanding_model"))
    model_gate = _record(model.get("gate"))
    comprehension_gate = _record(asset.get("enterprise_comprehension_gate"))
    planning_gate = _record(asset.get("scenario_planning_gate"))
    scenario_gate = _record(asset.get("scenario_ir_gate"))
    execution_gate = _record(asset.get("scenario_execution_contract_gate"))
    runtime_plan_gate = _record(asset.get("runtime_plan_gate"))
    materialization_gate = _record(asset.get("runtime_materialization_gate"))
    model_metrics = _record(model_gate.get("metrics"))
    scenario_metrics = _record(scenario_gate.get("metrics"))
    execution_metrics = _record(execution_gate.get("metrics"))
    runtime_metrics = _record(runtime_plan_gate.get("metrics"))
    materialization_metrics = _record(materialization_gate.get("metrics"))

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
    materialization_ready = _ready(
        materialization_gate, "runtime_materialization_ready", "entry_allowed"
    )

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
        {
            "key": "runtime_materialization",
            "label": "运行实例化",
            "status": _text(materialization_gate.get("status")) or "NOT_BUILT",
            "ready": materialization_ready,
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
    for gate in (
        model_gate,
        planning_gate,
        scenario_gate,
        execution_gate,
        runtime_plan_gate,
        materialization_gate,
    ):
        for value in _rows(gate.get("blocking_reasons")):
            message = _readable_reason(value)
            if message:
                blockers.append(message)
    for key, flag in (
        ("runtime_plan_unknowns", "blocks_runtime_plan"),
        ("runtime_materialization_unknowns", "blocks_runtime_materialization"),
    ):
        for value in _rows(asset.get(key)):
            row = _record(value)
            if not row.get(flag):
                continue
            message = _row_message(row)
            if message:
                blockers.append(message)
    for value in _rows(asset.get("coverage_gaps")):
        row = _record(value)
        kind = _text(row.get("kind"))
        if not any(
            token in kind
            for token in (
                "UNDERSTANDING",
                "SCENARIO",
                "EXECUTION_CONTRACT",
                "RUNTIME_PLAN",
                "RUNTIME_MATERIALIZATION",
            )
        ):
            continue
        message = _text(row.get("message")) or _text(row.get("operator_action")) or _readable_reason(kind)
        if message:
            blockers.append(message)

    blocker_receipts = _blocker_receipts(asset, model, gates)
    blockers.extend(
        _text(row.get("message"))
        for row in blocker_receipts
        if _text(row.get("message"))
    )

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
            model_metrics.get("unresolved_conflict_count"),
            len(
                [
                    row
                    for row in _rows(model.get("conflicts"))
                    if str(_record(row).get("status") or "UNRESOLVED").upper()
                    not in {"RESOLVED", "SUPERSEDED", "DISMISSED"}
                ]
            ),
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
        "runtime_materialization_status": _text(materialization_gate.get("status")) or "NOT_BUILT",
        "runtime_materialization_ready": materialization_ready,
        "runtime_materialization_count": _integer(
            summary.get("runtime_materialization_count"),
            _integer(
                materialization_metrics.get("runtime_materialization_count"),
                len(_rows(asset.get("runtime_materializations"))),
            ),
        ),
        "runtime_materialization_incomplete_count": _integer(
            summary.get("runtime_materialization_incomplete_count"),
            _integer(materialization_metrics.get("incomplete_runtime_materialization_count")),
        ),
        "runtime_materialization_unknown_count": _integer(
            summary.get("runtime_materialization_unknown_count"),
            _integer(
                materialization_metrics.get("runtime_materialization_unknown_count"),
                len(_rows(asset.get("runtime_materialization_unknowns"))),
            ),
        ),
        "formal_scenario_chain_ready": all(bool(row.get("ready")) for row in gates),
        "formal_runtime_chain_ready": all(bool(row.get("ready")) for row in gates),
        "understanding_gates": gates,
        "understanding_blockers": list(dict.fromkeys(value for value in blockers if value))[:8],
        "understanding_blocker_receipts": blocker_receipts,
        "understanding_source_receipt_count": sum(
            1 for row in blocker_receipts if bool(row.get("source_backed"))
        ),
        "understanding_projection_contract": "EXISTING_KNOWLEDGE_ASSET_GATE_PROJECTION_NOT_SECOND_AUTHORITY",
        "understanding_source_of_truth": "existing_enterprise_business_knowledge_asset",
    }


def project_existing_understanding_command_center(
    payload: dict[str, Any],
    *,
    project: str,
    root: Path,
    actor: dict[str, Any] | None = None,
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
    if actor is not None:
        from .connector_acl_authority import filter_connector_asset_for_actor

        asset = filter_connector_asset_for_actor(
            project,
            asset,
            actor={**actor, "project_id": project} if actor else actor,
            root=root,
        )

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
        principal_loader = getattr(self, "_principal", None)
        actor = principal_loader() if callable(principal_loader) else None
        return project_existing_understanding_command_center(
            payload,
            project=project_id,
            root=root,
            actor=actor,
        )


__all__ = [
    "UnderstandingCommandCenterProjectionMixin",
    "project_existing_understanding_command_center",
]
