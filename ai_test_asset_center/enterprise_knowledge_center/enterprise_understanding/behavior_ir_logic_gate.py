"""Canonical operation-condition-outcome authority for Business Behavior IR."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Iterable

from .behavior_ir import BEHAVIOR_GATE_SCHEMA
from .behavior_ir_governance import build_governed_business_behavior_ir
from .schema import as_dict, as_list, dedupe_evidence, new_unknown, stable_id, text, unique_text

CONDITION_EXPRESSION_SCHEMA = "qualibug.condition-expression.v1"
OPERATION_CLAUSE_SCHEMA = "qualibug.operation-clause.v1"
OUTCOME_CONTRACT_SCHEMA = "qualibug.outcome-contract.v1"

_LOGIC_CODES = {
    "BEHAVIOR_CONDITION_COMBINATOR_UNRESOLVED",
    "BEHAVIOR_CONDITION_EXPRESSION_UNRESOLVED",
    "BEHAVIOR_OPERATION_CLAUSE_UNRESOLVED",
    "BEHAVIOR_MANDATORY_OUTCOME_UNRESOLVED",
}
_TEMPORAL_SUFFIXES = ("之前", "之后", "以前", "以后", "期间", "以内", "之内", "前", "后", "时")
_FORMAL_PERMISSION_DECISIONS = frozenset(
    {"ALLOW", "DENY", "REQUIRE_APPROVAL", "REQUIRE_CONFIRMATION"}
)


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _explicit_combinator(behavior: dict[str, Any]) -> str:
    frame = as_dict(behavior.get("condition_frame"))
    for candidate in (
        behavior.get("condition_combinator"),
        as_dict(behavior.get("trigger")).get("condition_combinator"),
        frame.get("combinator"),
    ):
        value = text(candidate).upper()
        if value in {"AND", "OR"}:
            return value
    return ""


def _condition_leaf(slot: dict[str, Any]) -> dict[str, Any]:
    raw = text(slot.get("raw_value") or slot.get("statement"))
    field = text(slot.get("field_candidate"))
    operator = text(slot.get("operator_candidate"))
    value = as_dict(slot.get("value_candidate"))
    temporal = next((suffix for suffix in _TEMPORAL_SUFFIXES if raw.endswith(suffix)), "")
    if temporal and not field:
        return {
            "schema": CONDITION_EXPRESSION_SCHEMA,
            "node_type": "TEMPORAL",
            "slot_ref": slot.get("slot_id"),
            "raw_value": raw,
            "relation": temporal,
            "status": "CONFIRMED" if raw else "UNRESOLVED",
            "source_backed": True,
        }
    complete = bool(
        field
        and operator
        and operator != "UNPARSED_EXPLICIT_CONDITION"
        and (value.get("normalized_value") is not None or text(value.get("raw")))
    )
    return {
        "schema": CONDITION_EXPRESSION_SCHEMA,
        "node_type": "PREDICATE",
        "predicate_id": stable_id("condition_predicate", slot.get("slot_id"), field, operator, value),
        "slot_ref": slot.get("slot_id"),
        "raw_value": raw,
        "subject_ref": text(slot.get("subject_candidate")),
        "field_candidate": field,
        "operator_candidate": operator,
        "value_candidate": value,
        "status": "CONFIRMED" if complete else "UNRESOLVED",
        "source_backed": True,
    }


def build_condition_expression(behavior: dict[str, Any]) -> dict[str, Any]:
    existing = as_dict(behavior.get("condition_expression"))
    if existing:
        return existing
    leaves = [_condition_leaf(slot) for slot in _dicts(behavior.get("preconditions"))]
    explicit = _explicit_combinator(behavior)
    frame = as_dict(behavior.get("condition_frame"))
    if not leaves:
        expression: dict[str, Any] = {}
    elif len(leaves) == 1:
        expression = leaves[0]
    elif explicit in {"AND", "OR"}:
        expression = {
            "schema": CONDITION_EXPRESSION_SCHEMA,
            "node_type": "ALL" if explicit == "AND" else "ANY",
            "children": leaves,
            "source_backed": True,
        }
    else:
        expression = {
            "schema": CONDITION_EXPRESSION_SCHEMA,
            "node_type": "UNRESOLVED",
            "children": leaves,
            "reason_code": "CONDITION_COMBINATOR_UNRESOLVED",
            "source_backed": True,
        }

    scopes = unique_text(
        [
            *as_list(frame.get("exception_scopes")),
            *as_list(frame.get("exceptions")),
            *as_list(behavior.get("exceptions")),
        ]
    )
    if expression and scopes:
        expression = {
            "schema": CONDITION_EXPRESSION_SCHEMA,
            "node_type": "EXCEPT",
            "child": expression,
            "exception_scopes": scopes,
            "overlays": as_list(frame.get("overlays")),
            "source_backed": True,
        }
    branch = text(frame.get("branch")).upper()
    if expression and branch in {"THEN", "ELSE", "ELSE_IF"}:
        expression = {
            "schema": CONDITION_EXPRESSION_SCHEMA,
            "node_type": "BRANCH",
            "branch": branch,
            "branch_index": frame.get("branch_index", ""),
            "parent_conditions": unique_text(as_list(frame.get("parent_conditions"))),
            "paired_statement": text(frame.get("paired_statement")),
            "guard": expression,
            "source_backed": True,
        }
    elif expression and text(frame.get("kind")).upper() == "UNRESOLVED":
        expression = {
            "schema": CONDITION_EXPRESSION_SCHEMA,
            "node_type": "UNRESOLVED",
            "children": [expression],
            "reason_code": "CONDITION_FRAME_UNRESOLVED",
            "source_backed": True,
        }
    behavior["condition_expression"] = expression
    return expression


def iter_condition_predicates(expression: dict[str, Any]) -> list[dict[str, Any]]:
    node = as_dict(expression)
    if text(node.get("node_type")).upper() == "PREDICATE":
        return [node]
    rows: list[dict[str, Any]] = []
    for child in _dicts(node.get("children")):
        rows.extend(iter_condition_predicates(child))
    for key in ("child", "guard"):
        child = as_dict(node.get(key))
        if child:
            rows.extend(iter_condition_predicates(child))
    return rows


def condition_expression_combinator(expression: dict[str, Any]) -> str:
    node = as_dict(expression)
    kind = text(node.get("node_type")).upper()
    if kind == "ALL":
        return "AND"
    if kind == "ANY":
        return "OR"
    if kind in {"PREDICATE", "TEMPORAL"}:
        return "SINGLE_CONDITION"
    if kind == "BRANCH":
        return condition_expression_combinator(as_dict(node.get("guard")))
    if kind == "EXCEPT":
        return condition_expression_combinator(as_dict(node.get("child")))
    return "UNRESOLVED" if kind else ""


def condition_expression_complete(expression: dict[str, Any]) -> bool:
    node = as_dict(expression)
    if not node:
        return True
    kind = text(node.get("node_type")).upper()
    if kind == "PREDICATE":
        return text(node.get("status")) == "CONFIRMED"
    if kind == "TEMPORAL":
        return bool(text(node.get("raw_value")) and text(node.get("relation")))
    if kind in {"ALL", "ANY"}:
        children = _dicts(node.get("children"))
        return len(children) >= 2 and all(condition_expression_complete(row) for row in children)
    if kind == "EXCEPT":
        return bool(as_list(node.get("exception_scopes"))) and condition_expression_complete(
            as_dict(node.get("child"))
        )
    if kind == "BRANCH":
        return text(node.get("branch")) in {"THEN", "ELSE", "ELSE_IF"} and condition_expression_complete(
            as_dict(node.get("guard"))
        )
    return False


def _canonical_condition(expression: dict[str, Any]) -> dict[str, Any]:
    node = as_dict(expression)
    kind = text(node.get("node_type")).upper()
    if kind == "PREDICATE":
        return {
            "node_type": kind,
            "subject_ref": text(node.get("subject_ref")),
            "field_candidate": text(node.get("field_candidate")),
            "operator_candidate": text(node.get("operator_candidate")),
            "value_candidate": as_dict(node.get("value_candidate")),
            "status": text(node.get("status")),
        }
    if kind == "TEMPORAL":
        return {"node_type": kind, "raw_value": text(node.get("raw_value")), "relation": text(node.get("relation"))}
    if kind in {"ALL", "ANY", "UNRESOLVED"}:
        children = [_canonical_condition(row) for row in _dicts(node.get("children"))]
        if kind in {"ALL", "ANY"}:
            children.sort(key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))
        return {"node_type": kind, "reason_code": text(node.get("reason_code")), "children": children}
    if kind == "EXCEPT":
        return {
            "node_type": kind,
            "exception_scopes": sorted(unique_text(as_list(node.get("exception_scopes")))),
            "child": _canonical_condition(as_dict(node.get("child"))),
        }
    if kind == "BRANCH":
        return {
            "node_type": kind,
            "branch": text(node.get("branch")),
            "branch_index": node.get("branch_index", ""),
            "parent_conditions": unique_text(as_list(node.get("parent_conditions"))),
            "guard": _canonical_condition(as_dict(node.get("guard"))),
        }
    return {"node_type": kind}


def condition_expression_signature(expression: dict[str, Any]) -> str:
    return json.dumps(_canonical_condition(expression), ensure_ascii=False, sort_keys=True, default=str)


def build_operation_clause(behavior: dict[str, Any]) -> dict[str, Any]:
    existing = as_dict(behavior.get("operation_clause"))
    if existing:
        return existing
    operation = text(behavior.get("operation_ref"))
    objects = unique_text(as_list(behavior.get("object_refs")))
    clause = {
        "schema": OPERATION_CLAUSE_SCHEMA,
        "operation_ref": operation,
        "actor_refs": unique_text(as_list(behavior.get("actor_refs"))),
        "object_refs": objects,
        "source_refs": unique_text(as_list(behavior.get("source_refs"))),
        "evidence": dedupe_evidence(_dicts(behavior.get("evidence"))),
        "status": "CONFIRMED" if operation and objects else "UNRESOLVED",
        "source_backed": True,
    }
    behavior["operation_clause"] = clause
    return clause


def _data_outcome_type(row: dict[str, Any]) -> str:
    action = text(row.get("action")).lower()
    if re.search(r"生成|创建|新建|create|insert", action):
        return "ENTITY_CREATED"
    if re.search(r"删除|移除|delete|remove", action):
        return "ENTITY_DELETED"
    if re.search(r"发送|通知|emit|publish|notify", action):
        return "EVENT_EMITTED"
    # Resource-movement verbs are field deltas: they move a quantity between
    # fields/objects (锁定/冻结/预留/占用/消耗/核销 move quantity the same way
    # 扣减/增加/释放 do), never create or delete an entity.
    if re.search(r"增加|扣减|释放|锁定|冻结|预留|占用|消耗|核销|"
                 r"increase|decrease|increment|decrement|lock|reserve|consume", action):
        return "FIELD_DELTA"
    if re.search(r"更新|写入|修改|update|write|set", action):
        return "FIELD_ASSIGNMENT"
    return "DATA_EFFECT"


def build_outcome_contracts(behavior: dict[str, Any]) -> list[dict[str, Any]]:
    existing = _dicts(behavior.get("outcome_contracts"))
    if existing:
        return existing
    operation = text(build_operation_clause(behavior).get("operation_ref"))
    objects = unique_text(as_list(behavior.get("object_refs")))
    evidence = dedupe_evidence(_dicts(behavior.get("evidence")))
    behavior_id = text(behavior.get("behavior_id"))
    rows: list[dict[str, Any]] = []
    structured: set[str] = set()

    for index, effect in enumerate(_dicts(behavior.get("state_effects"))):
        to_value = text(effect.get("to_state") or effect.get("to_value"))
        field_ref = text(effect.get("field") or effect.get("field_ref"))
        statement = text(effect.get("raw") or effect.get("statement"))
        if statement:
            structured.add(statement)
        row = {
            "schema": OUTCOME_CONTRACT_SCHEMA,
            "outcome_id": stable_id("outcome", behavior_id, "state", index),
            "outcome_type": "STATE_TRANSITION",
            "target_object_refs": objects,
            "field_ref": field_ref,
            "from_value": text(
                effect.get("from_state") or effect.get("from_value")
            ),
            "to_value": to_value,
            "statement": statement,
            "observer_slot_ref": f"state_effect:{index}",
            "mandatory": True,
            "observation_phase": "AFTER",
            "caused_by_operation_ref": operation,
            "status": (
                "CONFIRMED"
                if to_value and objects and field_ref
                else "UNRESOLVED"
            ),
            "evidence": evidence,
        }
        if not field_ref:
            row["reason_code"] = "OUTCOME_STATE_FIELD_UNRESOLVED"
        rows.append(row)

    for index, effect in enumerate(_dicts(behavior.get("data_effects"))):
        statement = text(effect.get("statement") or effect.get("raw") or effect.get("effect"))
        if statement:
            structured.add(statement)
        target = text(effect.get("object") or effect.get("target") or effect.get("entity_ref"))
        field = text(effect.get("field") or effect.get("field_path") or effect.get("name"))
        rows.append(
            {
                "schema": OUTCOME_CONTRACT_SCHEMA,
                "outcome_id": stable_id("outcome", behavior_id, "data", index),
                "outcome_type": _data_outcome_type(effect),
                "target_object_refs": unique_text([target, *objects]),
                "field_ref": field,
                "observer_slot_ref": f"data_effect:{index}",
                "operator": text(effect.get("operator")),
                "value_ref": text(effect.get("value_ref")),
                "statement": statement,
                "mandatory": True,
                "observation_phase": "AFTER",
                "caused_by_operation_ref": operation,
                "status": "CONFIRMED" if statement and (target or field or objects) else "UNRESOLVED",
                "evidence": evidence,
            }
        )

    for index, raw in enumerate(as_list(behavior.get("expected_effects"))):
        statement = text(raw.get("statement") if isinstance(raw, dict) else raw)
        if statement and statement not in structured:
            rows.append(
                {
                    "schema": OUTCOME_CONTRACT_SCHEMA,
                    "outcome_id": stable_id("outcome", behavior_id, "text", index),
                    "outcome_type": "ASSERTION_TEXT",
                    "target_object_refs": objects,
                    "statement": statement,
                    "mandatory": True,
                    "observation_phase": "AFTER",
                    "caused_by_operation_ref": operation,
                    "status": "SOURCE_TEXT_ONLY",
                    "reason_code": "OUTCOME_NOT_STRUCTURED",
                    "evidence": evidence,
                }
            )

    permission = text(behavior.get("permission_decision")).upper()
    if permission not in {"", "UNSPECIFIED", "CONFLICTED"}:
        authorization_status = text(
            behavior.get("authorization_semantics_status")
        ).upper()
        permission_confirmed = bool(
            permission in _FORMAL_PERMISSION_DECISIONS
            and authorization_status != "UNRESOLVED"
        )
        permission_reason = (
            ""
            if permission_confirmed
            else text(behavior.get("authorization_resolution_reason"))
            or "PERMISSION_DECISION_UNRESOLVED"
        )
        row = {
            "schema": OUTCOME_CONTRACT_SCHEMA,
            "outcome_id": stable_id("outcome", behavior_id, "permission"),
            "outcome_type": "PERMISSION_DECISION",
            "target_object_refs": objects,
            "expected_decision": permission,
            "declared_decision": permission,
            "authorization_semantic_kind": text(
                behavior.get("authorization_semantic_kind")
            )
            or "AUTHORIZATION",
            "mandatory": True,
            "observation_phase": "RESPONSE",
            "caused_by_operation_ref": operation,
            "status": "CONFIRMED" if permission_confirmed else "UNRESOLVED",
            "evidence": evidence,
        }
        if permission_reason:
            row["reason_code"] = permission_reason
        rows.append(row)

    for index, raw in enumerate(unique_text(as_list(behavior.get("compensations")))):
        rows.append(
            {
                "schema": OUTCOME_CONTRACT_SCHEMA,
                "outcome_id": stable_id("outcome", behavior_id, "compensation", index),
                "outcome_type": "COMPENSATION",
                "target_object_refs": objects,
                "statement": text(raw),
                "mandatory": False,
                "observation_phase": "COMPENSATION",
                "caused_by_operation_ref": operation,
                "status": "SOURCE_TEXT_ONLY",
                "evidence": evidence,
            }
        )

    contracts = list({text(row.get("outcome_id")): row for row in rows if text(row.get("outcome_id"))}.values())
    behavior["outcome_contracts"] = contracts
    behavior["expected_effects"] = project_outcome_effects(contracts)
    return contracts


def mandatory_outcomes(behavior: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in build_outcome_contracts(behavior) if bool(row.get("mandatory"))]


def outcome_contracts_complete(behavior: dict[str, Any]) -> bool:
    rows = mandatory_outcomes(behavior)
    return bool(rows) and all(text(row.get("status")) == "CONFIRMED" for row in rows)


def project_outcome_effects(outcomes: Iterable[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for row in outcomes:
        if not isinstance(row, dict):
            continue
        statement = text(row.get("statement"))
        if not statement and text(row.get("outcome_type")) == "STATE_TRANSITION":
            statement = f"{text(row.get('field_ref'))}:{text(row.get('from_value'))}->{text(row.get('to_value'))}"
        if not statement and text(row.get("outcome_type")) == "PERMISSION_DECISION":
            statement = text(row.get("expected_decision"))
        if statement:
            values.append(statement)
    return unique_text(values)


def outcome_contracts_signature(behavior: dict[str, Any]) -> str:
    rows = [
        {
            "outcome_type": text(row.get("outcome_type")),
            "target_object_refs": sorted(unique_text(as_list(row.get("target_object_refs")))),
            "field_ref": text(row.get("field_ref")),
            "from_value": text(row.get("from_value")),
            "to_value": text(row.get("to_value")),
            "operator": text(row.get("operator")),
            "value_ref": text(row.get("value_ref")),
            "expected_decision": text(row.get("expected_decision")),
            "statement": text(row.get("statement")),
            "mandatory": bool(row.get("mandatory")),
            "status": text(row.get("status")),
        }
        for row in build_outcome_contracts(behavior)
    ]
    rows.sort(key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))
    return json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)


def ensure_canonical_behavior_semantics(behavior: dict[str, Any]) -> dict[str, Any]:
    expression = build_condition_expression(behavior)
    clause = build_operation_clause(behavior)
    outcomes = build_outcome_contracts(behavior)
    behavior["condition_combinator"] = condition_expression_combinator(expression)
    behavior["expected_effects"] = project_outcome_effects(outcomes)
    behavior["legacy_semantic_fields_are_projections"] = True
    behavior["canonical_semantics_version"] = "operation-condition-outcome.v1"

    unresolved = {
        text(value)
        for value in as_list(behavior.get("unresolved_semantics"))
        if text(value) and text(value) not in _LOGIC_CODES
    }
    if expression and not condition_expression_complete(expression):
        unresolved.add("BEHAVIOR_CONDITION_EXPRESSION_UNRESOLVED")
        inner = expression
        while text(inner.get("node_type")) in {"BRANCH", "EXCEPT"}:
            inner = as_dict(inner.get("guard") or inner.get("child"))
        if (
            text(inner.get("node_type")) == "UNRESOLVED"
            and text(inner.get("reason_code")) == "CONDITION_COMBINATOR_UNRESOLVED"
        ):
            unresolved.add("BEHAVIOR_CONDITION_COMBINATOR_UNRESOLVED")
    if text(clause.get("status")) != "CONFIRMED":
        unresolved.add("BEHAVIOR_OPERATION_CLAUSE_UNRESOLVED")
    # Mandatory structured outcomes are a formal-oracle requirement.  A candidate-only
    # behavior (decision-matrix row awaiting corroboration) carries raw text effects by
    # design and must stay CANDIDATE, not be downgraded to INCOMPLETE for a formal gate
    # it is not yet eligible to enter.
    if (
        text(behavior.get("source_kind")) == "ACCEPTED_BUSINESS_FACT"
        and not outcome_contracts_complete(behavior)
    ):
        unresolved.add("BEHAVIOR_MANDATORY_OUTCOME_UNRESOLVED")
    behavior["unresolved_semantics"] = sorted(unresolved)

    if text(behavior.get("source_kind")) == "ACCEPTED_BUSINESS_FACT":
        if unresolved:
            if text(behavior.get("status")) != "CONFLICTED":
                behavior["status"] = "INCOMPLETE"
            behavior["formal_business_rule"] = False
        elif text(behavior.get("status")) != "CONFLICTED":
            behavior["status"] = "CONFIRMED"
            behavior["formal_business_rule"] = True
            behavior["candidate_only"] = False
    else:
        behavior["candidate_only"] = True
        behavior["formal_business_rule"] = False
        if text(behavior.get("status")) != "CONFLICTED":
            behavior["status"] = "INCOMPLETE" if unresolved else "CANDIDATE"
    return behavior


def _incompatible_equalities(behavior: dict[str, Any]) -> dict[str, list[str]]:
    expression = as_dict(behavior.get("condition_expression"))
    if condition_expression_combinator(expression) != "AND":
        return {}
    values: dict[str, set[str]] = defaultdict(set)
    for predicate in iter_condition_predicates(expression):
        if text(predicate.get("operator_candidate")) != "EQUALS":
            continue
        field = text(predicate.get("field_candidate"))
        if field:
            values[field].add(json.dumps(as_dict(predicate.get("value_candidate")), ensure_ascii=False, sort_keys=True))
    return {field: sorted(rows) for field, rows in values.items() if len(rows) > 1}


def _rebuild_gate(
    rows: list[dict[str, Any]],
    behaviors: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for behavior in behaviors:
        counts[text(behavior.get("status")) or "UNKNOWN"] += 1
    if conflicts or counts["CONFLICTED"]:
        status = "BLOCKED_BUSINESS_BEHAVIOR_CONFLICT"
    elif counts["CANDIDATE"] or counts["INCOMPLETE"]:
        status = "PARTIAL_BUSINESS_BEHAVIOR_IR"
    elif counts["CONFIRMED"]:
        status = "PASS"
    else:
        status = "NO_BUSINESS_BEHAVIOR_EVIDENCE"
    traceable = sum(1 for row in behaviors if as_list(row.get("evidence")))
    return {
        "schema": BEHAVIOR_GATE_SCHEMA,
        "status": status,
        "entry_allowed": status == "PASS",
        "metrics": {
            "decision_matrix_row_count": len(rows),
            "behavior_count": len(behaviors),
            "confirmed_behavior_count": counts["CONFIRMED"],
            "candidate_behavior_count": counts["CANDIDATE"],
            "incomplete_behavior_count": counts["INCOMPLETE"],
            "conflicted_behavior_count": counts["CONFLICTED"],
            "behavior_conflict_count": len(conflicts),
            "unresolved_condition_expression_count": sum(
                1 for row in behaviors if not condition_expression_complete(as_dict(row.get("condition_expression")))
            ),
            "unresolved_condition_combinator_count": sum(
                1
                for row in behaviors
                if condition_expression_combinator(as_dict(row.get("condition_expression"))) == "UNRESOLVED"
            ),
            "unresolved_operation_clause_count": sum(
                1 for row in behaviors if text(as_dict(row.get("operation_clause")).get("status")) != "CONFIRMED"
            ),
            "unresolved_mandatory_outcome_count": sum(
                1 for row in behaviors if not outcome_contracts_complete(row)
            ),
            "source_traceability_rate": round(traceable / len(behaviors), 4) if behaviors else 1.0,
        },
        "quality_claim": "BEHAVIOR_IR_CLOSURE_NOT_RECALL_OR_ACCURACY",
        "condition_expression_is_authoritative": True,
        "operation_clause_is_authoritative": True,
        "outcome_contracts_are_authoritative": True,
        "legacy_semantic_fields_are_projections": True,
        "matrix_rows_require_corroboration": True,
        "multiple_conditions_are_implicitly_and": False,
        "automatic_conflict_resolution_allowed": False,
    }


def build_business_behavior_ir_v1(
    asset: dict[str, Any],
    facts: Iterable[dict[str, Any]],
    operations: Iterable[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    rows, behaviors, conflicts, unknowns, _gate = build_governed_business_behavior_ir(asset, facts, operations)
    by_id = {text(row.get("behavior_id")): row for row in behaviors if text(row.get("behavior_id"))}
    for behavior in behaviors:
        ensure_canonical_behavior_semantics(behavior)

    kept: list[dict[str, Any]] = []
    for conflict in conflicts:
        if not isinstance(conflict, dict):
            continue
        if text(conflict.get("kind")) != "BEHAVIOR_CONDITION_CONTRADICTION":
            kept.append(conflict)
            continue
        refs = [text(value) for value in as_list(conflict.get("behavior_refs")) if text(value)]
        behavior = by_id.get(refs[0]) if len(refs) == 1 else None
        if behavior is not None and not _incompatible_equalities(behavior):
            ensure_canonical_behavior_semantics(behavior)
            continue
        kept.append(conflict)
    conflicts = kept

    preserved = [
        row
        for row in unknowns
        if isinstance(row, dict)
        and text(row.get("kind"))
        not in {"BUSINESS_BEHAVIOR_INCOMPLETE", "BUSINESS_BEHAVIOR_CANDIDATE_UNCONFIRMED"}
    ]
    for behavior in behaviors:
        unresolved = unique_text(as_list(behavior.get("unresolved_semantics")))
        if unresolved:
            preserved.append(
                new_unknown(
                    "BUSINESS_BEHAVIOR_INCOMPLETE",
                    f"行为“{text(behavior.get('operation_ref')) or text(behavior.get('behavior_id'))}”仍缺少：{'、'.join(unresolved)}。",
                    related_objects=as_list(behavior.get("object_refs")),
                    related_operations=[behavior.get("operation_ref")],
                    evidence=as_list(behavior.get("evidence")),
                    severity="P1",
                    blocks_formal_understanding=False,
                    reason_code=unresolved[0],
                    details={"behavior_id": behavior.get("behavior_id"), "unresolved_semantics": unresolved},
                )
            )
    candidates = [row for row in behaviors if text(row.get("status")) == "CANDIDATE"]
    if candidates:
        preserved.append(
            new_unknown(
                "BUSINESS_BEHAVIOR_CANDIDATE_UNCONFIRMED",
                f"已形成{len(candidates)}条行为候选，但尚无足够来源证据将其升级为正式业务规则。",
                evidence=dedupe_evidence(
                    [evidence for row in candidates for evidence in as_list(row.get("evidence"))]
                ),
                severity="P1",
                blocks_formal_understanding=False,
                reason_code="BUSINESS_BEHAVIOR_CANDIDATE_UNCONFIRMED",
                details={"behavior_refs": [row.get("behavior_id") for row in candidates]},
            )
        )
    unknowns = list(
        {
            text(row.get("unknown_id")): row
            for row in preserved
            if isinstance(row, dict) and text(row.get("unknown_id"))
        }.values()
    )
    return rows, behaviors, conflicts, unknowns, _rebuild_gate(rows, behaviors, conflicts)


__all__ = [
    "CONDITION_EXPRESSION_SCHEMA",
    "OPERATION_CLAUSE_SCHEMA",
    "OUTCOME_CONTRACT_SCHEMA",
    "build_condition_expression",
    "iter_condition_predicates",
    "condition_expression_combinator",
    "condition_expression_complete",
    "condition_expression_signature",
    "build_operation_clause",
    "build_outcome_contracts",
    "mandatory_outcomes",
    "outcome_contracts_complete",
    "outcome_contracts_signature",
    "project_outcome_effects",
    "ensure_canonical_behavior_semantics",
    "build_business_behavior_ir_v1",
]
