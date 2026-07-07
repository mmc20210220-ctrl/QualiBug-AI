from __future__ import annotations

"""Core behavior-space model for bug discovery.

This module is the product core: it converts enterprise sources into a compact,
customer-safe model of actors, entities, states, transitions, permissions,
invariants, API operations, data constraints and risk points. The model is meant
for bug-hunting scenario generation, business oracles and coverage-guided
exploration; it is not a sales or delivery artifact.
"""

import re
from typing import Any

from .business_state_graph import BusinessStateGraph, BusinessStateGraphBuilder


_MODAL_RE = re.compile(r"\b(?:must|shall|cannot|must\s+not|only)\b|必须|不得|不允许|不可|只能|禁止", re.I)
_ROLE_RE = re.compile(r"(?:actor|role|用户|角色|参与者)\s*[:：]\s*([A-Za-z0-9_\-\u4e00-\u9fff ,，/、]+)", re.I)
_ROUTE_RE = re.compile(r"\b(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD)\s+(?P<path>/[^\s|`]+)", re.I)
_OPENAPI_PATH_RE = re.compile(r"^\s{0,4}(?P<path>/[^:]+):\s*$")
_OPENAPI_METHOD_RE = re.compile(r"^\s{2,8}(?P<method>get|post|put|patch|delete|head):\s*$", re.I)
_SCHEMA_CONSTRAINT_RE = re.compile(r"\b(?:CHECK|FOREIGN\s+KEY|REFERENCES|NOT\s+NULL|UNIQUE|PRIMARY\s+KEY)\b", re.I)
_RISK_KEYWORDS = {
    "permission": re.compile(r"tenant|permission|role|auth|越权|权限|租户|隔离", re.I),
    "money": re.compile(r"amount|price|payment|refund|balance|金额|支付|退款|余额", re.I),
    "state": re.compile(r"status|state|transition|cancel|delete|状态|流转|取消|删除", re.I),
    "idempotency": re.compile(r"duplicate|idempot|retry|concurrent|重复|幂等|并发", re.I),
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _ref(source_type: str, line_number: int, quote: str) -> dict[str, str]:
    return {"source_type": source_type, "locator": f"line:{line_number}", "quote": str(quote or "")[:500]}


def _safe(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _extract_actors(*texts: str) -> list[dict[str, Any]]:
    names: dict[str, dict[str, Any]] = {}
    generic = {
        "admin": "administrator",
        "管理员": "administrator",
        "buyer": "customer",
        "买家": "customer",
        "seller": "operator",
        "商家": "operator",
        "user": "end_user",
        "用户": "end_user",
        "qa": "tester",
    }
    for source_index, text in enumerate(texts):
        for line_number, raw in enumerate(str(text or "").splitlines(), 1):
            line = raw.strip()
            for match in _ROLE_RE.finditer(line):
                for item in re.split(r"[,，/、]\s*", match.group(1)):
                    name = _safe(item, 80)
                    if name:
                        names.setdefault(name, {"name": name, "role_type": generic.get(name.lower(), "business_actor"), "source_refs": []})["source_refs"].append(_ref("requirement", line_number, line))
            for token, role_type in generic.items():
                if token in line.lower() or token in line:
                    names.setdefault(token, {"name": token, "role_type": role_type, "source_refs": []})["source_refs"].append(_ref("requirement", line_number, line))
    return sorted(names.values(), key=lambda row: row["name"])


def _extract_permissions(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_number, raw in enumerate(str(text or "").splitlines(), 1):
        line = raw.strip()
        if not line or not re.search(r"permission|role|tenant|own|only|cannot|权限|角色|租户|只能|不得|不可|不允许|自己|本人", line, re.I):
            continue
        mode = "deny" if re.search(r"cannot|must\s+not|forbidden|不得|不可|不允许|禁止", line, re.I) else "allow_or_constraint"
        rows.append({
            "permission_id": f"PERM_{len(rows) + 1:03d}",
            "mode": mode,
            "rule": line[:300],
            "source_refs": [_ref("requirement", line_number, line)],
        })
    return rows


def _extract_api_operations(api_text: str) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []
    pending_path = ""
    for line_number, raw in enumerate(str(api_text or "").splitlines(), 1):
        line = raw.rstrip()
        direct = _ROUTE_RE.search(line)
        if direct:
            operations.append({
                "operation_id": f"API_{len(operations) + 1:03d}",
                "method": direct.group("method").upper(),
                "path": direct.group("path"),
                "source_refs": [_ref("api", line_number, line.strip())],
            })
            continue
        path_match = _OPENAPI_PATH_RE.match(line)
        if path_match:
            pending_path = path_match.group("path").strip()
            continue
        method_match = _OPENAPI_METHOD_RE.match(line)
        if method_match and pending_path:
            operations.append({
                "operation_id": f"API_{len(operations) + 1:03d}",
                "method": method_match.group("method").upper(),
                "path": pending_path,
                "source_refs": [_ref("api", line_number, line.strip())],
            })
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for row in operations:
        key = (row["method"], row["path"])
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped


def _extract_data_constraints(schema_text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_table = ""
    for line_number, raw in enumerate(str(schema_text or "").splitlines(), 1):
        line = raw.strip()
        table_match = re.search(r"CREATE\s+TABLE\s+[`\"]?(?P<table>[A-Za-z0-9_\-]+)", line, re.I)
        if table_match:
            current_table = table_match.group("table")
        if _SCHEMA_CONSTRAINT_RE.search(line):
            rows.append({
                "constraint_id": f"DATA_{len(rows) + 1:03d}",
                "table": current_table,
                "rule": line[:300],
                "source_refs": [_ref("db_schema", line_number, line)],
            })
    return rows


def _entity_model(entity: str, graph: BusinessStateGraph) -> dict[str, Any]:
    payload = graph.to_dict()
    return {
        "entity": entity,
        "states": sorted(payload.get("states", {}).keys()),
        "transitions": payload.get("transitions", []),
        "invariants": _unique([
            invariant
            for node in payload.get("states", {}).values()
            if isinstance(node, dict)
            for invariant in node.get("invariants", [])
        ]),
        "dependencies": payload.get("edges", []),
        "stats": payload.get("stats", {}),
        "source_refs": payload.get("source_refs", []),
    }


def _risk_type(text: str) -> str:
    for name, pattern in _RISK_KEYWORDS.items():
        if pattern.search(text):
            return name
    return "business_rule"


def _risk_points(entities: list[dict[str, Any]], permissions: list[dict[str, Any]], data_constraints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    risks: list[dict[str, Any]] = []
    for entity in entities:
        for transition in entity.get("transitions", []):
            if not isinstance(transition, dict):
                continue
            if transition.get("forbidden") or float(transition.get("risk_score") or 0.0) >= 0.5:
                title = f"{entity['entity']} {transition.get('from', '')}->{transition.get('to', '')} via {transition.get('action', '')}".strip()
                risks.append({
                    "risk_id": f"RISK_{len(risks) + 1:03d}",
                    "risk_type": _risk_type(title),
                    "severity_hint": "P0" if transition.get("forbidden") else "P1",
                    "entity": entity["entity"],
                    "title": title,
                    "oracle_needed": "state_transition_oracle",
                    "source_refs": transition.get("source_refs", []),
                })
        for invariant in entity.get("invariants", []):
            risks.append({
                "risk_id": f"RISK_{len(risks) + 1:03d}",
                "risk_type": _risk_type(invariant),
                "severity_hint": "P1",
                "entity": entity["entity"],
                "title": invariant[:240],
                "oracle_needed": "business_invariant_oracle",
                "source_refs": entity.get("source_refs", []),
            })
    for permission in permissions:
        risks.append({
            "risk_id": f"RISK_{len(risks) + 1:03d}",
            "risk_type": "permission",
            "severity_hint": "P0" if permission.get("mode") == "deny" else "P1",
            "entity": "",
            "title": permission.get("rule", ""),
            "oracle_needed": "permission_oracle",
            "source_refs": permission.get("source_refs", []),
        })
    for constraint in data_constraints:
        risks.append({
            "risk_id": f"RISK_{len(risks) + 1:03d}",
            "risk_type": _risk_type(str(constraint.get("rule") or "")),
            "severity_hint": "P1",
            "entity": constraint.get("table", ""),
            "title": constraint.get("rule", ""),
            "oracle_needed": "data_consistency_oracle",
            "source_refs": constraint.get("source_refs", []),
        })
    return risks[:100]


def build_behavior_space_model(prd_text: str = "", api_spec_text: str = "", db_schema_text: str = "") -> dict[str, Any]:
    builder = BusinessStateGraphBuilder()
    graphs = builder.build(prd_text, api_spec_text, db_schema_text)
    behavior_contract = builder.behavior_contract()
    actors = _extract_actors(prd_text, api_spec_text)
    permissions = _extract_permissions(prd_text)
    api_operations = _extract_api_operations(api_spec_text)
    data_constraints = _extract_data_constraints(db_schema_text)
    entities = [_entity_model(name, graph) for name, graph in sorted(graphs.items())]
    risks = _risk_points(entities, permissions, data_constraints)
    coverage_gaps = list(behavior_contract.get("coverage_gaps") or [])
    if not actors:
        coverage_gaps.append({"kind": "BEHAVIOR_MODEL_GAP", "code": "ACTOR_MODEL_MISSING", "detail": "No actor or role was source-bound from PRD/API materials."})
    if not data_constraints:
        coverage_gaps.append({"kind": "BEHAVIOR_MODEL_GAP", "code": "DATA_CONSTRAINTS_MISSING", "detail": "No database constraints were source-bound; data consistency oracles are limited."})
    return {
        "schema_version": "behavior-space-model-v1",
        "purpose": "core_bug_discovery_model",
        "customer_safe": True,
        "actors": actors,
        "entities": entities,
        "permissions": permissions,
        "api_operations": api_operations,
        "data_constraints": data_constraints,
        "risk_points": risks,
        "behavior_slices": behavior_contract.get("slices", []),
        "coverage_gaps": coverage_gaps,
        "coverage_model": {
            "actor_count": len(actors),
            "entity_count": len(entities),
            "api_operation_count": len(api_operations),
            "permission_rule_count": len(permissions),
            "data_constraint_count": len(data_constraints),
            "risk_point_count": len(risks),
            "behavior_slice_count": len(behavior_contract.get("slices", [])),
            "coverage_gap_count": len(coverage_gaps),
        },
    }
