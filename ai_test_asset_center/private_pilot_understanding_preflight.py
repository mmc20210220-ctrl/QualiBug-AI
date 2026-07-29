"""Project existing enterprise-understanding gates into the customer preflight.

This module does not build a second model or a second readiness authority. It reads the
persisted enterprise knowledge asset, reuses its existing gates, and appends one actionable
blocker to the existing scan-preflight response when the formal chain is not closed.
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


_REASON_LABELS = {
    "SEMANTIC_UNDERSTANDING_NOT_CLOSED": "企业业务语义尚未闭合",
    "IMPLEMENTATION_BINDING_NOT_CLOSED": "业务场景尚未唯一绑定到系统实现",
    "IMPLEMENTATION_BINDING_CONFLICT": "业务场景与系统实现存在冲突",
    "MODEL_SCHEMA_OR_EVIDENCE_INVALID": "部分理解结论缺少合格的结构或来源证据",
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


def _readable_reason(value: Any) -> str:
    code = _text(value)
    return _REASON_LABELS.get(code, code.replace("_", " ").strip())


def _row_message(value: Any) -> str:
    row = _record(value)
    details = _record(row.get("details"))
    for candidate in (
        row.get("message"),
        row.get("description"),
        row.get("statement"),
        row.get("raw_statement"),
        row.get("reason"),
        row.get("resolution_policy"),
        details.get("message"),
        details.get("statement"),
        details.get("reason"),
        _readable_reason(row.get("reason_code")),
        _readable_reason(row.get("kind")),
    ):
        value_text = _text(candidate)
        if value_text:
            return value_text
    return ""


def _gate_ready(gate: dict[str, Any], *keys: str) -> bool:
    return any(gate.get(key) is True for key in keys)


def _gate_projection(asset: dict[str, Any]) -> dict[str, Any]:
    summary = _record(asset.get("summary"))
    model = _record(asset.get("enterprise_understanding_model"))
    model_gate = _record(model.get("gate"))
    comprehension_gate = _record(asset.get("enterprise_comprehension_gate"))
    planning_gate = _record(asset.get("scenario_planning_gate"))
    scenario_gate = _record(asset.get("scenario_ir_gate"))
    execution_gate = _record(asset.get("scenario_execution_contract_gate"))
    runtime_plan_gate = _record(asset.get("runtime_plan_gate"))
    materialization_gate = _record(asset.get("runtime_materialization_gate"))

    model_status = (
        _text(summary.get("enterprise_understanding_status"))
        or _text(model_gate.get("status"))
        or _text(comprehension_gate.get("status"))
        or "NOT_BUILT"
    )
    model_ready = bool(summary.get("enterprise_understanding_ready")) or _gate_ready(
        model_gate, "entry_allowed"
    ) or _gate_ready(comprehension_gate, "entry_allowed")

    gates = [
        {
            "code": "ENTERPRISE_UNDERSTANDING_BLOCKED",
            "label": "企业理解",
            "status": model_status,
            "ready": model_ready,
            "gate": model_gate or comprehension_gate,
        },
        {
            "code": "SCENARIO_PLANNING_BLOCKED",
            "label": "场景规划",
            "status": _text(planning_gate.get("status")) or "NOT_BUILT",
            "ready": _gate_ready(
                planning_gate, "scenario_planning_allowed", "entry_allowed"
            ),
            "gate": planning_gate,
        },
        {
            "code": "SCENARIO_IR_BLOCKED",
            "label": "Scenario IR",
            "status": _text(scenario_gate.get("status")) or "NOT_BUILT",
            "ready": _gate_ready(scenario_gate, "entry_allowed"),
            "gate": scenario_gate,
        },
        {
            "code": "EXECUTION_CONTRACT_BLOCKED",
            "label": "执行合同",
            "status": _text(execution_gate.get("status")) or "NOT_BUILT",
            "ready": _gate_ready(
                execution_gate, "execution_contract_ready", "entry_allowed"
            ),
            "gate": execution_gate,
        },
        {
            "code": "RUNTIME_PLAN_BLOCKED",
            "label": "Runtime Plan",
            "status": _text(runtime_plan_gate.get("status")) or "NOT_BUILT",
            "ready": _gate_ready(
                runtime_plan_gate, "runtime_plan_ready", "entry_allowed"
            ),
            "gate": runtime_plan_gate,
        },
        {
            "code": "RUNTIME_MATERIALIZATION_BLOCKED",
            "label": "运行实例化",
            "status": _text(materialization_gate.get("status")) or "NOT_BUILT",
            "ready": _gate_ready(
                materialization_gate, "runtime_materialization_ready", "entry_allowed"
            ),
            "gate": materialization_gate,
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
    for gate_row in gates:
        gate = _record(gate_row.get("gate"))
        for value in _rows(gate.get("blocking_reasons")):
            message = _readable_reason(value)
            if message:
                blockers.append(message)
    for value in _rows(asset.get("runtime_plan_unknowns")):
        if not _record(value).get("blocks_runtime_plan"):
            continue
        message = _row_message(value)
        if message:
            blockers.append(message)
    for value in _rows(asset.get("runtime_materialization_unknowns")):
        if not _record(value).get("blocks_runtime_materialization"):
            continue
        message = _row_message(value)
        if message:
            blockers.append(message)
    blockers = list(dict.fromkeys(blockers))[:5]

    return {
        "model_id": _text(summary.get("enterprise_understanding_model_id"))
        or _text(model.get("model_id")),
        "model_status": model_status,
        "model_ready": model_ready,
        "business_object_count": int(
            summary.get("understood_business_object_count")
            or len(_rows(model.get("business_objects")))
        ),
        "actor_count": int(
            summary.get("understood_actor_count")
            or len(_rows(model.get("actors")))
        ),
        "operation_count": int(
            summary.get("understood_operation_count")
            or len(_rows(model.get("operations")))
        ),
        "lifecycle_count": int(
            summary.get("understood_lifecycle_count")
            or len(_rows(model.get("lifecycles")))
        ),
        "process_count": int(
            summary.get("understood_process_count")
            or len(_rows(model.get("processes")))
        ),
        "scenario_count": int(
            summary.get("scenario_ir_count")
            or len(_rows(asset.get("scenario_ir")))
        ),
        "runtime_plan_count": int(
            summary.get("runtime_plan_count")
            or len(_rows(asset.get("runtime_plans")))
        ),
        "runtime_materialization_count": int(
            summary.get("runtime_materialization_count")
            or len(_rows(asset.get("runtime_materializations")))
        ),
        "unknown_count": int(
            summary.get("enterprise_understanding_unknown_count")
            or len(_rows(model.get("unknowns")))
        ),
        "conflict_count": len(_rows(model_gate.get("unresolved_conflicts")))
        if "unresolved_conflicts" in model_gate
        else len(
            [
                row
                for row in _rows(model.get("conflicts"))
                if str(_record(row).get("status") or "UNRESOLVED").upper()
                not in {"RESOLVED", "SUPERSEDED", "DISMISSED"}
            ]
        ),
        "gates": [
            {
                "label": row["label"],
                "status": row["status"],
                "ready": bool(row["ready"]),
            }
            for row in gates
        ],
        "first_blocked_gate": next(
            (row for row in gates if not row["ready"]), None
        ),
        "blockers": blockers,
    }


def project_existing_understanding_preflight(
    payload: dict[str, Any],
    *,
    project: str,
    root: Path,
) -> dict[str, Any]:
    """Append persisted understanding state to the existing preflight payload."""
    result = dict(payload)
    input_checks = dict(_record(result.get("input_checks")))
    source_count = int(_record(input_checks.get("sources")).get("source_count") or 0)
    if source_count <= 0:
        return result

    try:
        from .enterprise_knowledge_center import load_enterprise_business_knowledge_asset

        asset = load_enterprise_business_knowledge_asset(project, root)
    except Exception as exc:
        asset = None
        load_error = f"{type(exc).__name__}: {exc}"[:300]
    else:
        load_error = ""

    reasons = [
        dict(row) for row in _rows(result.get("reasons")) if isinstance(row, dict)
    ]
    if not isinstance(asset, dict) or not asset:
        reasons.append(
            {
                "code": "ENTERPRISE_UNDERSTANDING_NOT_BUILT",
                "message": (
                    "企业资料已经入库，但尚未形成可读取的企业理解资产。"
                    "请等待当前批次理解完成，或补充能说明业务规则、接口和状态流转的原始资料。"
                ),
            }
        )
        result["understanding_summary"] = {
            "status": "NOT_BUILT",
            "ready": False,
            "load_error": load_error,
            "source_of_truth": "existing_enterprise_business_knowledge_asset",
        }
        input_checks["enterprise_understanding"] = {
            "status": "blocked",
            "gate_status": "NOT_BUILT",
        }
    else:
        projection = _gate_projection(asset)
        first_blocked = projection.pop("first_blocked_gate")
        result["understanding_summary"] = {
            **projection,
            "status": projection.get("model_status"),
            "ready": first_blocked is None,
            "source_of_truth": "existing_enterprise_business_knowledge_asset",
        }
        input_checks["enterprise_understanding"] = {
            "status": "passed" if first_blocked is None else "blocked",
            "gate_status": projection.get("model_status"),
            "model_id": projection.get("model_id"),
            "gates": projection.get("gates"),
        }
        if first_blocked is not None:
            blockers = list(projection.get("blockers") or [])
            detail = f"；主要缺口：{'、'.join(blockers)}" if blockers else ""
            reasons.append(
                {
                    "code": _text(first_blocked.get("code")),
                    "message": (
                        f"{_text(first_blocked.get('label'))}尚未放行"
                        f"（{_text(first_blocked.get('status'))}）{detail}。"
                        "请补充相关原始企业资料，或完善测试环境、凭据与测试数据绑定；"
                        "系统不会通过人工确认、常识补全或旧 Probe 回退绕过门禁。"
                    ),
                }
            )

    deduped_reasons: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for row in reasons:
        code = _text(row.get("code"))
        if code and code in seen_codes:
            continue
        if code:
            seen_codes.add(code)
        deduped_reasons.append(row)
    result["reasons"] = deduped_reasons
    result["blocking_codes"] = [
        _text(row.get("code"))
        for row in deduped_reasons
        if _text(row.get("code"))
    ]
    result["ready"] = len(result["blocking_codes"]) == 0
    result["input_checks"] = input_checks
    return result


class UnderstandingPreflightProjectionMixin:
    """Post-process the existing ScanHandlersMixin preflight response in-place."""

    def _handle_scan_preflight(
        self,
        project: str,
        root: Path,
        body: dict[str, Any] | None = None,
    ) -> None:
        captured: dict[str, Any] = {}
        original_json = self._json
        had_instance_json = "_json" in self.__dict__
        previous_instance_json = self.__dict__.get("_json")

        def capture_json(
            payload: Any, status: int = 200, *args: Any, **kwargs: Any
        ) -> None:
            captured["payload"] = payload
            captured["status"] = status
            captured["args"] = args
            captured["kwargs"] = kwargs
            return None

        self._json = capture_json
        try:
            result = super()._handle_scan_preflight(project, root, body)
        finally:
            if had_instance_json:
                self.__dict__["_json"] = previous_instance_json
            else:
                self.__dict__.pop("_json", None)

        if "payload" not in captured:
            return result
        payload = captured.get("payload")
        if not isinstance(payload, dict):
            return original_json(
                payload,
                int(captured.get("status") or 200),
                *captured.get("args", ()),
                **captured.get("kwargs", {}),
            )
        enriched = project_existing_understanding_preflight(
            payload,
            project=project,
            root=root,
        )
        return original_json(
            enriched,
            int(captured.get("status") or 200),
            *captured.get("args", ()),
            **captured.get("kwargs", {}),
        )


__all__ = [
    "UnderstandingPreflightProjectionMixin",
    "project_existing_understanding_preflight",
]
