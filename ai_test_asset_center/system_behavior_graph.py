from __future__ import annotations

"""System behavior graph contract for command-center payloads.

This builder is intentionally conservative.  It only derives graph nodes from
facts already present in customer materials, findings, evidence, regression
probes, coverage matrices, and command-center payloads.  It does not claim full
system understanding when the sources are incomplete.
"""

from collections import Counter
from typing import Any

GRAPH_VERSION = "system_behavior_graph.v1"

RISK_TO_INVARIANT = {
    "permission_bypass": ("authorization_boundary", "权限边界不变量"),
    "idor": ("object_level_authorization", "对象级授权不变量"),
    "tenant_isolation": ("tenant_isolation", "租户隔离不变量"),
    "security_boundary": ("authorization_boundary", "权限边界不变量"),
    "privacy_compliance": ("privacy_boundary", "隐私边界不变量"),
    "sensitive_field_leak": ("privacy_boundary", "隐私边界不变量"),
    "business_invariant": ("business_rule", "业务规则不变量"),
    "data_integrity": ("data_consistency", "数据一致性不变量"),
    "db_verification": ("data_consistency", "数据一致性不变量"),
    "idempotency": ("idempotency", "幂等不变量"),
    "state_machine": ("state_transition", "状态流转不变量"),
    "concurrency_race_condition": ("concurrency_consistency", "并发一致性不变量"),
    "money_quantity_conservation": ("conservation", "金额/数量守恒不变量"),
    "workflow_approval": ("approval_workflow", "审批流不变量"),
    "api_contract": ("api_contract", "接口契约不变量"),
}

METHOD_TO_ACTION = {
    "GET": ("read", "读取/查询"),
    "POST": ("create", "创建/提交"),
    "PUT": ("update", "更新"),
    "PATCH": ("update", "局部更新"),
    "DELETE": ("delete", "删除/撤销"),
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _norm_key(value: str) -> str:
    return "_".join(part for part in value.lower().replace("-", "_").replace("/", "_").split("_") if part)[:120]


def _is_dynamic_segment(segment: str) -> bool:
    s = segment.strip().strip("{}<>").lower()
    return not s or s in {"id", "uuid", "uid", "key", "no", "code"} or s.startswith(":") or s.endswith("id") or s.isdigit()


def _path_segments(path: str) -> list[str]:
    values: list[str] = []
    for raw in _text(path).split("?")[0].split("/"):
        segment = raw.strip()
        if not segment or segment.lower() in {"api", "v1", "v2", "v3", "openapi"}:
            continue
        if _is_dynamic_segment(segment):
            continue
        values.append(segment)
    return values


def _object_from_path(path: str) -> str:
    segments = _path_segments(path)
    return segments[0] if segments else "unknown_business_object"


def _api_from_record(record: dict[str, Any]) -> tuple[str, str]:
    reproduction = _as_dict(record.get("reproduction"))
    raw = _as_dict(record.get("raw_evidence"))
    req = _as_dict(raw.get("request_raw"))
    method = _text(record.get("repro_method") or record.get("_api_method") or record.get("method") or reproduction.get("method") or req.get("method")).upper()
    path = _text(record.get("repro_path") or record.get("_api_path") or record.get("path") or reproduction.get("path") or req.get("path"))
    return method, path


def _iter_findings(data: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in ("defects", "findings", "real_findings", "clues", "bug_scores"):
        for item in _as_list(data.get(key)):
            if isinstance(item, dict):
                items.append(item)
    return items


def _iter_regression_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    run = _as_dict(data.get("regression_run"))
    items: list[dict[str, Any]] = []
    for key in ("failures", "needs_review", "items"):
        for item in _as_list(run.get(key)):
            if isinstance(item, dict):
                items.append(item)
    board = _as_dict(data.get("test_task_board"))
    for item in _as_list(board.get("slices")):
        if isinstance(item, dict):
            items.append(item)
    return items


def _add_unique(target: list[dict[str, Any]], seen: set[str], item: dict[str, Any], key: str) -> None:
    item_id = _text(item.get(key))
    if not item_id or item_id in seen:
        return
    seen.add(item_id)
    target.append(item)


def build_system_behavior_graph(data: dict[str, Any]) -> dict[str, Any]:
    data = _as_dict(data)
    objects: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    apis: list[dict[str, Any]] = []
    invariants: list[dict[str, Any]] = []
    db_entities: list[dict[str, Any]] = []
    roles: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_objects: set[str] = set()
    seen_actions: set[str] = set()
    seen_apis: set[str] = set()
    seen_invariants: set[str] = set()
    seen_db: set[str] = set()
    seen_roles: set[str] = set()
    seen_states: set[str] = set()
    risk_counter: Counter[str] = Counter()
    evidence_sources: set[str] = set()

    source_records = _iter_findings(data) + _iter_regression_items(data)
    for record in source_records:
        method, path = _api_from_record(record)
        object_label = _text(record.get("source_entity")) or _object_from_path(path)
        object_id = _norm_key(object_label or "unknown_business_object") or "unknown_business_object"
        risk_type = _text(record.get("risk_type") or record.get("defect_family") or record.get("category"))
        if risk_type:
            risk_counter[risk_type] += 1
        if path:
            api_id = f"{method or 'HTTP'} {path}"
            _add_unique(apis, seen_apis, {
                "api_id": api_id,
                "method": method or "HTTP",
                "path": path,
                "business_object_id": object_id,
                "source": "finding_or_regression_evidence",
            }, "api_id")
            edge_id = f"api:{api_id}->object:{object_id}"
            if edge_id not in {edge.get("edge_id") for edge in edges}:
                edges.append({"edge_id": edge_id, "from": api_id, "to": object_id, "type": "acts_on"})
        _add_unique(objects, seen_objects, {
            "object_id": object_id,
            "label": object_label or object_id,
            "source": "finding_or_regression_evidence",
            "evidence_count": 1,
        }, "object_id")
        action_key, action_label = METHOD_TO_ACTION.get(method, ("exercise", "业务动作"))
        if path or method:
            action_id = f"{object_id}.{action_key}"
            _add_unique(actions, seen_actions, {
                "action_id": action_id,
                "label": action_label,
                "method": method or "HTTP",
                "path": path,
                "business_object_id": object_id,
                "source": "api_evidence",
            }, "action_id")
        invariant_id, invariant_label = RISK_TO_INVARIANT.get(risk_type, ("observed_behavior", "已观测行为约束"))
        invariant_key = f"{object_id}.{invariant_id}"
        _add_unique(invariants, seen_invariants, {
            "invariant_id": invariant_key,
            "label": invariant_label,
            "risk_type": risk_type or "unknown",
            "business_object_id": object_id,
            "status": "violated_or_candidate" if record.get("bug_status") in {"reproduced", "suspected"} or record.get("verdict") else "observed",
            "source": "finding_taxonomy",
        }, "invariant_id")
        actor = _text(record.get("actor") or _as_dict(record.get("reproduction")).get("actor"))
        if actor:
            _add_unique(roles, seen_roles, {"role_id": _norm_key(actor), "label": actor, "source": "runtime_evidence"}, "role_id")
        state_text = " ".join(_text(record.get(k)) for k in ("state", "from_state", "to_state", "lifecycle_status") if record.get(k))
        if state_text:
            _add_unique(states, seen_states, {"state_id": _norm_key(f"{object_id}_{state_text}"), "label": state_text, "business_object_id": object_id, "source": "finding_lifecycle"}, "state_id")
        db = _as_dict(record.get("db_evidence")) or _as_dict(_as_dict(record.get("raw_evidence")).get("db_snapshot"))
        table = _text(db.get("table"))
        if table:
            _add_unique(db_entities, seen_db, {
                "entity_id": _norm_key(table),
                "table": table,
                "column": _text(db.get("column")),
                "business_object_id": object_id,
                "source": "db_evidence",
            }, "entity_id")
        for source_key in ("evidence_chain", "raw_evidence", "reproduction", "db_evidence"):
            if record.get(source_key):
                evidence_sources.add(source_key)

    coverage = _as_dict(data.get("coverage_matrix"))
    for row in _as_list(coverage.get("risk_families") or coverage.get("families")):
        item = _as_dict(row)
        risk_type = _text(item.get("risk_family") or item.get("family") or item.get("risk_type"))
        if not risk_type:
            continue
        invariant_id, invariant_label = RISK_TO_INVARIANT.get(risk_type, (_norm_key(risk_type), risk_type))
        _add_unique(invariants, seen_invariants, {
            "invariant_id": f"coverage.{invariant_id}",
            "label": invariant_label,
            "risk_type": risk_type,
            "business_object_id": "coverage_scope",
            "status": _text(item.get("status") or item.get("coverage_status") or "planned_or_observed"),
            "source": "coverage_matrix",
        }, "invariant_id")

    source_count = len(source_records)
    graph_status = "empty" if not source_records and not invariants else "partial"
    if objects and apis and invariants and evidence_sources:
        graph_status = "evidence_backed_partial"

    return {
        "graph_version": GRAPH_VERSION,
        "status": graph_status,
        "business_objects": objects,
        "roles": roles,
        "states": states,
        "actions": actions,
        "apis": apis,
        "db_entities": db_entities,
        "invariants": invariants,
        "edges": edges,
        "summary": {
            "business_object_count": len(objects),
            "role_count": len(roles),
            "state_count": len(states),
            "action_count": len(actions),
            "api_count": len(apis),
            "db_entity_count": len(db_entities),
            "invariant_count": len(invariants),
            "source_record_count": source_count,
            "top_risk_types": dict(risk_counter.most_common(8)),
            "evidence_sources": sorted(evidence_sources),
        },
        "planner_contract": {
            "can_seed_behavior_slices": bool(objects and (apis or actions) and invariants),
            "required_missing_inputs": [
                name for name, present in (
                    ("api_or_ui_paths", bool(apis or actions)),
                    ("business_objects", bool(objects)),
                    ("business_invariants", bool(invariants)),
                    ("runtime_or_db_evidence", bool(evidence_sources)),
                ) if not present
            ],
            "next_step": "Use business_objects × roles × states × actions × apis × db_entities × invariants to generate target-driven behavior slices.",
        },
        "honesty_rule": "This graph is derived only from observed materials, findings, evidence, regression probes, and coverage data. A partial graph must not be presented as full system understanding.",
    }


def inject_system_behavior_graph(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return payload
    graph = build_system_behavior_graph(data)
    data["system_behavior_graph"] = graph
    value_metrics = _as_dict(data.get("value_metrics"))
    value_metrics.update({
        "behavior_graph_status": graph["status"],
        "behavior_graph_object_count": graph["summary"]["business_object_count"],
        "behavior_graph_invariant_count": graph["summary"]["invariant_count"],
        "behavior_graph_api_count": graph["summary"]["api_count"],
    })
    data["value_metrics"] = value_metrics
    contract = _as_dict(data.get("data_contract"))
    contract["system_behavior_graph"] = {
        "display_key": "system_behavior_graph",
        "source": "command-center materials/findings/evidence/regression/coverage normalization",
        "honesty_rule": graph["honesty_rule"],
        "customer_meaning": "Conservative behavior model used to seed target-driven testing; partial means more customer materials or execution evidence are needed.",
    }
    data["data_contract"] = contract
    if isinstance(payload.get("data"), dict):
        payload["data"] = data
    return payload
