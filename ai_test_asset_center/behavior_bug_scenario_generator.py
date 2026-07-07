from __future__ import annotations

"""Generate bug-hunting scenarios from the core behavior-space model.

This is product-core code: it turns modeled business risks into executable or
plan-only defect discovery scenarios. It does not invent business domains; it
uses source-bound risk points, permissions, states, API operations and data
constraints from ``behavior_space_model``.
"""

import hashlib
import re
from typing import Any


_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_READ_METHODS = {"GET", "HEAD"}
_MONEY_RE = re.compile(r"amount|price|payment|refund|balance|金额|支付|退款|余额", re.I)
_TENANT_RE = re.compile(r"tenant|permission|auth|role|own|权限|租户|隔离|越权|本人|自己", re.I)
_DELETE_RE = re.compile(r"delete|deleted|删除", re.I)
_PAY_RE = re.compile(r"pay|payment|paid|支付", re.I)
_REFUND_RE = re.compile(r"refund|refunded|退款", re.I)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _safe(value: Any, limit: int = 260) -> str:
    return str(value or "").strip()[:limit]


def _slug(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_\-]+", "_", str(value or "").strip().lower()).strip("_")
    return text[:80] or "scenario"


def _id(*parts: Any) -> str:
    canonical = "|".join(str(part or "") for part in parts)
    return "BUGSCN_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:18]


def _ops(model: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in _as_list(model.get("api_operations")) if isinstance(row, dict)]


def _path_tokens(path: str) -> set[str]:
    return {token for token in re.split(r"[^A-Za-z0-9]+", path.lower()) if token and token not in {"api", "v1", "v2", "v3", "id"}}


def _entity_tokens(entity: str) -> set[str]:
    value = str(entity or "").lower()
    tokens = {token for token in re.split(r"[^A-Za-z0-9]+", value) if token}
    if value.endswith("y"):
        tokens.add(value[:-1] + "ies")
    elif value and not value.endswith("s"):
        tokens.add(value + "s")
    return tokens


def _matches_entity(operation: dict[str, Any], entity: str) -> bool:
    if not entity:
        return True
    return bool(_path_tokens(_safe(operation.get("path"))) & _entity_tokens(entity))


def _operation_score(operation: dict[str, Any], entity: str, intent: str) -> int:
    method = _safe(operation.get("method")).upper()
    path = _safe(operation.get("path"))
    score = 0
    if _matches_entity(operation, entity):
        score += 4
    if intent == "read" and method in _READ_METHODS:
        score += 5
    if intent == "write" and method in _WRITE_METHODS:
        score += 5
    if intent == "delete_then_read" and method == "DELETE":
        score += 6
    if intent == "pay" and _PAY_RE.search(path):
        score += 5
    if intent == "refund" and _REFUND_RE.search(path):
        score += 5
    if intent == "money" and _MONEY_RE.search(path):
        score += 3
    return score


def _best_op(model: dict[str, Any], *, entity: str = "", intent: str = "read") -> dict[str, Any]:
    operations = _ops(model)
    if not operations:
        return {}
    ranked = sorted(operations, key=lambda row: (_operation_score(row, entity, intent), _safe(row.get("path"))), reverse=True)
    return ranked[0] if _operation_score(ranked[0], entity, intent) > 0 else {}


def _read_after_delete_ops(model: dict[str, Any], entity: str) -> tuple[dict[str, Any], dict[str, Any]]:
    delete_ops = [op for op in _ops(model) if _safe(op.get("method")).upper() == "DELETE" and _matches_entity(op, entity)]
    read_ops = [op for op in _ops(model) if _safe(op.get("method")).upper() == "GET" and _matches_entity(op, entity)]
    if delete_ops and read_ops:
        return delete_ops[0], read_ops[0]
    return {}, {}


def _step(order: int, action: str, operation: dict[str, Any], *, actor: str = "", body: dict[str, Any] | None = None, expected: str = "") -> dict[str, Any]:
    return {
        "order": order,
        "action": action,
        "method": _safe(operation.get("method")).upper(),
        "path": _safe(operation.get("path")),
        "actor": actor,
        "body_template": body or {},
        "expected": expected,
    }


def _scenario(
    *,
    title: str,
    category: str,
    severity: str,
    risk: dict[str, Any],
    oracle: str,
    steps: list[dict[str, Any]],
    expected_outcome: str,
    coverage_tags: list[str],
    evidence_gaps: list[str] | None = None,
) -> dict[str, Any]:
    risk_id = _safe(risk.get("risk_id") or risk.get("permission_id") or risk.get("constraint_id") or title, 120)
    source_refs = _as_list(risk.get("source_refs"))
    gaps = list(evidence_gaps or [])
    if not steps or any(not step.get("path") or not step.get("method") for step in steps):
        gaps.append("SOURCE_BOUND_ROUTE_MISSING")
    if any(step.get("method") in _WRITE_METHODS for step in steps):
        gaps.append("WRITE_APPROVAL_AND_CLEANUP_CONTRACT_REQUIRED")
    execution_policy = "plan_only_requires_runtime_contract" if gaps else "safe_read_only"
    if steps and any(step.get("method") in _WRITE_METHODS for step in steps):
        execution_policy = "approved_sandbox_write_required"
    return {
        "scenario_id": _id(category, risk_id, title, [step.get("method") + " " + step.get("path") for step in steps]),
        "title": title[:260],
        "category": category,
        "severity": severity or _safe(risk.get("severity_hint"), 20) or "P2",
        "risk_id": risk_id,
        "risk_type": _safe(risk.get("risk_type"), 80),
        "oracle_needed": oracle,
        "preconditions": [
            "Use approved test tenant, disposable fixture, or customer-approved sandbox data.",
            "Bind actor identity before runtime execution.",
        ],
        "steps": steps,
        "expected_outcome": expected_outcome,
        "evidence_to_collect": [
            "request method/path and sanitized request body",
            "response status and sanitized response body",
            "actor identity label and tenant/scope label",
            "before/after business state when available",
            "oracle verdict and source_refs used for judgment",
        ],
        "execution_policy": execution_policy,
        "evidence_gaps": sorted(set(gaps)),
        "source_refs": source_refs,
        "coverage_tags": sorted(set(tag for tag in coverage_tags if tag)),
    }


def _permission_scenarios(model: dict[str, Any]) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for rule in _as_list(model.get("permissions")):
        if not isinstance(rule, dict):
            continue
        text = _safe(rule.get("rule"))
        if not text:
            continue
        op = _best_op(model, intent="read")
        scenarios.append(_scenario(
            title=f"Permission boundary: {text}",
            category="permission_boundary",
            severity="P0" if rule.get("mode") == "deny" or _TENANT_RE.search(text) else "P1",
            risk={**rule, "risk_id": rule.get("permission_id"), "risk_type": "permission"},
            oracle="permission_oracle",
            steps=[_step(1, "attempt access across the modeled permission boundary", op, actor="unauthorized_or_cross_tenant_actor", expected="reject_or_hide_forbidden_resource")],
            expected_outcome="The system must reject, hide, or sanitize data outside the actor's allowed scope.",
            coverage_tags=["permission", "tenant_boundary", "negative_path"],
        ))
    return scenarios


def _risk_scenarios(model: dict[str, Any]) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for risk in _as_list(model.get("risk_points")):
        if not isinstance(risk, dict):
            continue
        risk_type = _safe(risk.get("risk_type"))
        title = _safe(risk.get("title"))
        entity = _safe(risk.get("entity"))
        oracle = _safe(risk.get("oracle_needed")) or "business_invariant_oracle"
        if risk_type == "permission":
            continue
        if risk_type == "money" or _MONEY_RE.search(title):
            op = _best_op(model, entity=entity, intent="refund" if _REFUND_RE.search(title) else "money") or _best_op(model, entity=entity, intent="write")
            body = {"amount": "over_limit_or_negative_fixture", "reference": "source_bound_fixture_id"}
            scenarios.append(_scenario(
                title=f"Money invariant violation: {title}",
                category="business_invariant",
                severity=_safe(risk.get("severity_hint"), 20) or "P1",
                risk=risk,
                oracle=oracle if oracle != "" else "business_invariant_oracle",
                steps=[_step(1, "submit amount that violates modeled money invariant", op, actor="approved_business_actor", body=body, expected="reject_or_preserve_consistency")],
                expected_outcome="The system must reject the invalid amount or preserve the source-grounded money invariant.",
                coverage_tags=["money", "business_rule", "negative_path"],
            ))
        elif risk_type == "state" or _DELETE_RE.search(title):
            delete_op, read_op = _read_after_delete_ops(model, entity)
            if delete_op and read_op:
                scenarios.append(_scenario(
                    title=f"State lifecycle check: {title}",
                    category="state_lifecycle",
                    severity=_safe(risk.get("severity_hint"), 20) or "P1",
                    risk=risk,
                    oracle="state_transition_oracle",
                    steps=[
                        _step(1, "move fixture through modeled state change", delete_op, actor="approved_business_actor", expected="state_change_success_or_controlled_reject"),
                        _step(2, "read object after state change", read_op, actor="approved_business_actor", expected="not_found_or_state_consistent_response"),
                    ],
                    expected_outcome="The observable state after the transition must match the modeled lifecycle rule.",
                    coverage_tags=["state", "lifecycle", "read_after_write"],
                ))
            else:
                op = _best_op(model, entity=entity, intent="write") or _best_op(model, entity=entity, intent="read")
                scenarios.append(_scenario(
                    title=f"State transition violation: {title}",
                    category="state_transition",
                    severity=_safe(risk.get("severity_hint"), 20) or "P1",
                    risk=risk,
                    oracle="state_transition_oracle",
                    steps=[_step(1, "attempt modeled forbidden or risky transition", op, actor="approved_business_actor", expected="reject_or_keep_previous_state")],
                    expected_outcome="Forbidden transitions must be rejected; risky transitions must leave state consistent.",
                    coverage_tags=["state", "negative_path"],
                ))
        elif risk_type == "idempotency":
            op = _best_op(model, entity=entity, intent="write")
            scenarios.append(_scenario(
                title=f"Idempotency and duplicate operation: {title}",
                category="idempotency",
                severity=_safe(risk.get("severity_hint"), 20) or "P1",
                risk=risk,
                oracle="idempotency_oracle",
                steps=[
                    _step(1, "submit first operation with idempotency fixture", op, actor="approved_business_actor", body={"idempotency_key": "same_key"}, expected="success_or_controlled_reject"),
                    _step(2, "submit duplicate operation with same idempotency fixture", op, actor="approved_business_actor", body={"idempotency_key": "same_key"}, expected="same_result_without_duplicate_side_effect"),
                ],
                expected_outcome="Duplicate requests must not create duplicate business side effects.",
                coverage_tags=["idempotency", "duplicate", "side_effect"],
            ))
        else:
            op = _best_op(model, entity=entity, intent="read") or _best_op(model, entity=entity, intent="write")
            scenarios.append(_scenario(
                title=f"Business rule probe: {title}",
                category="business_rule",
                severity=_safe(risk.get("severity_hint"), 20) or "P2",
                risk=risk,
                oracle=oracle or "business_invariant_oracle",
                steps=[_step(1, "probe modeled business rule boundary", op, actor="approved_business_actor", expected="source_grounded_rule_preserved")],
                expected_outcome="The observed behavior must preserve the source-grounded business rule.",
                coverage_tags=["business_rule", "boundary"],
            ))
    return scenarios


def _data_constraint_scenarios(model: dict[str, Any]) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for constraint in _as_list(model.get("data_constraints")):
        if not isinstance(constraint, dict):
            continue
        rule = _safe(constraint.get("rule"))
        entity = _safe(constraint.get("table"))
        if not rule:
            continue
        op = _best_op(model, entity=entity, intent="write")
        body: dict[str, Any] = {"constraint_probe": "invalid_value_from_schema_rule"}
        if "NOT NULL" in rule.upper():
            body = {"required_field": None}
        elif "UNIQUE" in rule.upper() or "PRIMARY KEY" in rule.upper():
            body = {"duplicate_key_fixture": "existing_id"}
        elif "CHECK" in rule.upper():
            body = {"checked_field": "out_of_range_or_forbidden_value"}
        scenarios.append(_scenario(
            title=f"Data constraint violation: {rule}",
            category="data_constraint",
            severity="P1",
            risk={**constraint, "risk_id": constraint.get("constraint_id"), "risk_type": "data_constraint"},
            oracle="data_consistency_oracle",
            steps=[_step(1, "submit value that violates source-bound schema constraint", op, actor="approved_business_actor", body=body, expected="reject_or_no_persistence")],
            expected_outcome="The system must reject the invalid value or avoid persisting inconsistent data.",
            coverage_tags=["data_constraint", "schema", "negative_path"],
        ))
    return scenarios


def generate_bug_hunting_scenarios(behavior_space_model: dict[str, Any], max_scenarios: int = 50) -> dict[str, Any]:
    model = _as_dict(behavior_space_model)
    scenarios = _permission_scenarios(model) + _risk_scenarios(model) + _data_constraint_scenarios(model)
    deduped: dict[str, dict[str, Any]] = {}
    for item in scenarios:
        key = item["scenario_id"]
        if key not in deduped:
            deduped[key] = item
    ordered = sorted(
        deduped.values(),
        key=lambda row: (
            {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(str(row.get("severity")), 9),
            str(row.get("category")),
            str(row.get("title")),
        ),
    )[: max(1, int(max_scenarios or 50))]
    gaps: list[dict[str, str]] = []
    if not ordered:
        gaps.append({"kind": "BUG_SCENARIO_GAP", "code": "NO_BUG_HUNTING_SCENARIOS_GENERATED", "detail": "Behavior model did not contain enough source-bound risks or operations."})
    if not _ops(model):
        gaps.append({"kind": "BUG_SCENARIO_GAP", "code": "API_OPERATIONS_MISSING", "detail": "No source-bound API operations are available for executable bug probes."})
    return {
        "schema_version": "behavior-bug-scenarios-v1",
        "purpose": "core_bug_discovery_scenarios",
        "customer_safe": True,
        "scenario_count": len(ordered),
        "scenarios": ordered,
        "coverage_summary": {
            "permission_boundary": sum(1 for item in ordered if item.get("category") == "permission_boundary"),
            "business_invariant": sum(1 for item in ordered if item.get("category") == "business_invariant"),
            "state_transition": sum(1 for item in ordered if item.get("category") in {"state_transition", "state_lifecycle"}),
            "data_constraint": sum(1 for item in ordered if item.get("category") == "data_constraint"),
            "idempotency": sum(1 for item in ordered if item.get("category") == "idempotency"),
            "requires_write_approval": sum(1 for item in ordered if item.get("execution_policy") == "approved_sandbox_write_required"),
            "plan_only": sum(1 for item in ordered if str(item.get("execution_policy") or "").startswith("plan_only")),
        },
        "coverage_gaps": gaps,
    }
