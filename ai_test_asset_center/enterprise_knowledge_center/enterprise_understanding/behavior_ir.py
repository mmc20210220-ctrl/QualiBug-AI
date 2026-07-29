"""Compile source-backed enterprise facts and decision matrices into Business Behavior IR v1.

This layer is deliberately pre-execution.  It creates candidate or confirmed behavior units,
never tests, findings, probes, or inferred industry rules.  Decision-matrix rows remain
candidates until corroborated by accepted source facts or explicit operator governance.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Iterable

from .schema import (
    as_dict,
    as_list,
    dedupe_evidence,
    evidence_from_fact,
    new_unknown,
    source_evidence,
    stable_id,
    text,
    unique_text,
)

BEHAVIOR_SCHEMA = "qualibug.enterprise-business-behavior.v1"
BEHAVIOR_ROW_LEDGER_SCHEMA = "qualibug.decision-matrix-row-ledger.v1"
BEHAVIOR_GATE_SCHEMA = "qualibug.enterprise-business-behavior-gate.v1"

_PERMISSION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("DENY", re.compile(r"(?:禁止|不得|不允许|拒绝|不可|不能|deny|forbid)", re.I)),
    ("REQUIRE_APPROVAL", re.compile(r"(?:需要|必须|须|需).{0,8}(?:审批|审核)|require.{0,8}approval", re.I)),
    ("REQUIRE_CONFIRMATION", re.compile(r"(?:需要|必须|须|需).{0,8}(?:确认)|require.{0,8}confirmation", re.I)),
    ("ALLOW", re.compile(r"(?:允许|可以|可执行|准许|allow|permit)", re.I)),
)
_OPERATION_PREFIX = re.compile(
    r"^(?:允许|可以|可执行|准许|禁止|不得|不允许|拒绝|不可|不能|需要|必须|须|需)+"
)
_CONDITION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("GREATER_THAN_OR_EQUAL", re.compile(r"^(?P<field>.+?)(?:>=|≥|不少于|至少)(?P<value>.+)$", re.I)),
    ("LESS_THAN_OR_EQUAL", re.compile(r"^(?P<field>.+?)(?:<=|≤|不超过|至多)(?P<value>.+)$", re.I)),
    ("NOT_EQUALS", re.compile(r"^(?P<field>.+?)(?:!=|≠|不等于|不是)(?P<value>.+)$", re.I)),
    ("GREATER_THAN", re.compile(r"^(?P<field>.+?)(?:>|大于|超过|高于)(?P<value>.+)$", re.I)),
    ("LESS_THAN", re.compile(r"^(?P<field>.+?)(?:<|小于|低于)(?P<value>.+)$", re.I)),
    ("EQUALS", re.compile(r"^(?P<field>.+?)(?:=|等于|为)(?P<value>.+)$", re.I)),
)
_NUMBER_RE = re.compile(
    r"^\s*(?P<number>[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(?P<scale>亿|万|千)?\s*(?P<unit>元|万元|亿元|%|％|个|件|天|小时|分钟|秒)?\s*$"
)
_ROLE_HEADER_RE = re.compile(r"(?:角色|人员|用户|岗位|操作者|经办人|actor|role|user)", re.I)
_STATE_HEADER_RE = re.compile(r"(?:状态|阶段|status|state)", re.I)
_GENERIC_HEADER = {"判断", "条件", "输入", "结果", "输出", "动作", "condition", "input", "result", "output", "action"}


def _fact_object_refs(fact: dict[str, Any]) -> list[str]:
    return unique_text(
        [
            *as_list(as_dict(fact.get("subject")).get("entity_refs")),
            *as_list(as_dict(fact.get("object")).get("entity_refs")),
        ]
    )


def _fact_actor_refs(fact: dict[str, Any]) -> list[str]:
    return unique_text(as_list(as_dict(fact.get("subject")).get("actor_refs")))


def _normalize_number(value: str) -> dict[str, Any]:
    match = _NUMBER_RE.match(text(value))
    if not match:
        return {"raw": text(value), "value_type": "TEXT"}
    number = float(match.group("number").replace(",", ""))
    scale = match.group("scale") or ""
    multiplier = {"": 1, "千": 1_000, "万": 10_000, "亿": 100_000_000}[scale]
    normalized = number * multiplier
    if normalized.is_integer():
        normalized = int(normalized)
    return {
        "raw": text(value),
        "value_type": "NUMBER",
        "normalized_value": normalized,
        "scale": scale,
        "unit": match.group("unit") or "",
    }


def _best_header_path(cell: dict[str, Any], fallback: Iterable[Any] = ()) -> list[str]:
    explicit = [text(value) for value in as_list(cell.get("column_header_path")) if text(value)]
    if explicit:
        return explicit
    paths = [row for row in as_list(cell.get("column_header_paths")) if isinstance(row, list)]
    if len(paths) == 1:
        return [text(value) for value in paths[0] if text(value)]
    return [text(value) for value in fallback if text(value)]


def _field_candidate(header_path: list[str]) -> str:
    for value in reversed(header_path):
        if value.lower() not in _GENERIC_HEADER:
            return value
    return header_path[-1] if header_path else ""


def _cell_evidence(
    cell: dict[str, Any], *, source_id: str, filename: str, derivation: str
) -> dict[str, Any]:
    return source_evidence(
        source_id=source_id,
        source_locator=cell.get("source_locator"),
        quote=cell.get("text"),
        asset_ref=cell.get("block_id") or filename,
        derivation=derivation,
    )


def _unique_legend_meaning(cell: dict[str, Any]) -> tuple[str, str]:
    meanings = [
        text(row.get("meaning_text"))
        for row in as_list(cell.get("legend_meaning_candidates"))
        if isinstance(row, dict) and text(row.get("meaning_text"))
    ]
    values = unique_text(meanings)
    return (values[0], "EXPLICIT_UNIQUE_LEGEND") if len(values) == 1 else ("", "")


def _normalize_condition_slot(
    cell: dict[str, Any], *, header_path: list[str], column_index: int
) -> dict[str, Any]:
    raw = text(cell.get("text"))
    field = _field_candidate(header_path)
    slot = {
        "slot_id": stable_id("behavior_condition_slot", cell.get("block_id"), column_index),
        "source_cell_block_id": cell.get("block_id"),
        "column_index": column_index,
        "header_path": header_path,
        "field_candidate": field,
        "raw_value": raw,
        "operator_candidate": "",
        "value_candidate": {},
        "status": "CANDIDATE",
        "candidate_only": True,
    }
    if not raw:
        slot.update(
            {
                "status": "INCOMPLETE",
                "reason_code": "EMPTY_CELL_SEMANTICS_UNRESOLVED",
                "automatic_wildcard_inference_allowed": False,
            }
        )
        return slot
    legend_value, legend_evidence = _unique_legend_meaning(cell)
    candidate_text = legend_value or raw
    for operator, pattern in _CONDITION_PATTERNS:
        match = pattern.match(candidate_text)
        if not match:
            continue
        parsed_field = text(match.group("field"))
        value = text(match.group("value"))
        slot["field_candidate"] = parsed_field or field
        slot["operator_candidate"] = operator
        slot["value_candidate"] = _normalize_number(value)
        slot["normalization_evidence"] = "EXPLICIT_OPERATOR_IN_SOURCE_CELL"
        if legend_evidence:
            slot["legend_evidence"] = legend_evidence
        return slot
    slot["operator_candidate"] = "EQUALS"
    slot["value_candidate"] = _normalize_number(candidate_text)
    slot["normalization_evidence"] = legend_evidence or "CELL_VALUE_WITH_HEADER_FIELD_CANDIDATE"
    return slot


def _permission_decision(raw: str) -> str:
    decisions = [decision for decision, pattern in _PERMISSION_PATTERNS if pattern.search(raw)]
    return decisions[0] if len(set(decisions)) == 1 else "CONFLICTED" if decisions else "UNSPECIFIED"


def _operation_candidate(raw: str) -> str:
    value = _OPERATION_PREFIX.sub("", text(raw)).strip(" ：:，,。.;；")
    value = re.sub(r"^(?:审批|审核|确认)$", lambda match: match.group(0), value)
    return value


def _normalize_result_slot(
    cell: dict[str, Any], *, header_path: list[str], column_index: int
) -> dict[str, Any]:
    raw = text(cell.get("text"))
    slot = {
        "slot_id": stable_id("behavior_result_slot", cell.get("block_id"), column_index),
        "source_cell_block_id": cell.get("block_id"),
        "column_index": column_index,
        "header_path": header_path,
        "raw_value": raw,
        "permission_decision_candidate": "UNSPECIFIED",
        "operation_candidate": "",
        "effect_candidate": raw,
        "status": "CANDIDATE",
        "candidate_only": True,
    }
    if not raw:
        slot.update(
            {
                "status": "INCOMPLETE",
                "reason_code": "EMPTY_RESULT_CELL_SEMANTICS_UNRESOLVED",
            }
        )
        return slot
    legend_value, legend_evidence = _unique_legend_meaning(cell)
    candidate_text = legend_value or raw
    decision = _permission_decision(candidate_text)
    slot["permission_decision_candidate"] = decision
    slot["operation_candidate"] = _operation_candidate(candidate_text)
    if legend_evidence:
        slot["legend_evidence"] = legend_evidence
    if decision == "CONFLICTED":
        slot["status"] = "CONFLICTED"
        slot["reason_code"] = "RESULT_PERMISSION_DECISION_CONFLICT"
    return slot


def _role_headers_by_column(structure: dict[str, Any], table_id: str) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    for row in as_list(structure.get("table_column_role_candidates")):
        if not isinstance(row, dict) or text(row.get("table_block_id")) != table_id:
            continue
        result[int(row.get("column_index") or 0)] = [
            text(value) for value in as_list(row.get("header_path")) if text(value)
        ]
    return result


def build_decision_matrix_row_ledger(
    asset: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ledger: list[dict[str, Any]] = []
    unknowns: list[dict[str, Any]] = []
    structures = as_dict(asset.get("document_structure_assets"))
    for structure in as_list(structures.get("items")):
        if not isinstance(structure, dict):
            continue
        source_id = text(structure.get("source_id"))
        filename = text(structure.get("filename"))
        blocks = [row for row in as_list(structure.get("blocks")) if isinstance(row, dict)]
        tables = {
            text(row.get("block_id")): row
            for row in blocks
            if text(row.get("type")) == "TABLE" and text(row.get("block_id"))
        }
        cells_by_table: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for cell in blocks:
            if text(cell.get("type")) == "TABLE_CELL" and text(cell.get("table_block_id")):
                cells_by_table[text(cell.get("table_block_id"))].append(cell)
        for matrix in as_list(structure.get("decision_matrix_candidates")):
            if not isinstance(matrix, dict):
                continue
            owner_table_id = text(matrix.get("table_block_id"))
            logical_table_id = text(matrix.get("logical_table_id"))
            condition_columns = {int(value) for value in as_list(matrix.get("condition_column_candidates"))}
            result_columns = {int(value) for value in as_list(matrix.get("result_column_candidates"))}
            if not owner_table_id or not condition_columns or not result_columns:
                continue
            table_ids = [owner_table_id]
            if logical_table_id:
                table_ids = [
                    table_id
                    for table_id, table in tables.items()
                    if text(table.get("logical_table_id")) == logical_table_id
                ] or [owner_table_id]
            owner_headers = _role_headers_by_column(structure, owner_table_id)
            for table_id in table_ids:
                table = tables.get(table_id) or {}
                header_depth = int(
                    table.get("semantic_candidate_header_row_count")
                    or table.get("header_row_count")
                    or 0
                )
                rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
                for cell in cells_by_table.get(table_id, []):
                    row_index = int(cell.get("row_index") or 0)
                    if row_index < header_depth or text(cell.get("table_header_role")) == "REPEATED_HEADER":
                        continue
                    rows[row_index].append(cell)
                for row_index, row_cells in sorted(rows.items()):
                    by_column = {
                        int(cell.get("column_index") or 0): cell for cell in row_cells
                    }
                    relevant = condition_columns | result_columns
                    if not any(text(by_column.get(column, {}).get("text")) for column in relevant):
                        continue
                    condition_slots: list[dict[str, Any]] = []
                    result_slots: list[dict[str, Any]] = []
                    evidence: list[dict[str, Any]] = []
                    actor_refs: list[str] = []
                    for column in sorted(condition_columns):
                        cell = by_column.get(column, {})
                        header_path = _best_header_path(cell, owner_headers.get(column, []))
                        slot = _normalize_condition_slot(
                            cell, header_path=header_path, column_index=column
                        )
                        condition_slots.append(slot)
                        if _ROLE_HEADER_RE.search("/".join(header_path)) and text(cell.get("text")):
                            actor_refs.append(text(cell.get("text")))
                        if cell:
                            evidence.append(
                                _cell_evidence(
                                    cell,
                                    source_id=source_id,
                                    filename=filename,
                                    derivation="decision_matrix_condition_cell",
                                )
                            )
                    for column in sorted(result_columns):
                        cell = by_column.get(column, {})
                        header_path = _best_header_path(cell, owner_headers.get(column, []))
                        result_slots.append(
                            _normalize_result_slot(
                                cell, header_path=header_path, column_index=column
                            )
                        )
                        if cell:
                            evidence.append(
                                _cell_evidence(
                                    cell,
                                    source_id=source_id,
                                    filename=filename,
                                    derivation="decision_matrix_result_cell",
                                )
                            )
                    unresolved = unique_text(
                        [
                            slot.get("reason_code")
                            for slot in [*condition_slots, *result_slots]
                            if text(slot.get("status")) in {"INCOMPLETE", "CONFLICTED"}
                        ]
                    )
                    row_id = stable_id(
                        "decision_matrix_row",
                        source_id,
                        logical_table_id or owner_table_id,
                        table_id,
                        row_index,
                    )
                    row = {
                        "schema": BEHAVIOR_ROW_LEDGER_SCHEMA,
                        "row_ledger_id": row_id,
                        "source_id": source_id,
                        "filename": filename,
                        "decision_matrix_candidate_id": matrix.get("candidate_id"),
                        "logical_table_id": logical_table_id,
                        "table_block_id": table_id,
                        "owner_table_block_id": owner_table_id,
                        "page": table.get("page") or (row_cells[0].get("page") if row_cells else 0),
                        "row_index": row_index,
                        "actor_refs_candidate": unique_text(actor_refs),
                        "condition_slots": condition_slots,
                        "result_slots": result_slots,
                        "unresolved_semantics": unresolved,
                        "evidence": dedupe_evidence(evidence),
                        "status": "INCOMPLETE" if unresolved else "CANDIDATE",
                        "candidate_only": True,
                        "formal_business_rule": False,
                    }
                    ledger.append(row)
                    if unresolved:
                        unknowns.append(
                            new_unknown(
                                "DECISION_MATRIX_ROW_INCOMPLETE",
                                f"决策矩阵第{row_index + 1}行仍存在未决单元格语义：{'、'.join(unresolved)}。",
                                evidence=row["evidence"],
                                severity="P1",
                                blocks_formal_understanding=False,
                                reason_code=unresolved[0],
                                details={"row_ledger_id": row_id, "unresolved_semantics": unresolved},
                            )
                        )
    return ledger, unknowns


def _operation_lookup(operations: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        names = unique_text(
            [operation.get("name"), *as_list(operation.get("raw_action_names"))]
        )
        for name in names:
            result[name].append(operation)
    return result


def _fact_permission(fact: dict[str, Any]) -> str:
    modality = text(fact.get("modality")).upper()
    polarity = text(fact.get("polarity")).upper()
    raw = text(fact.get("raw_statement"))
    if modality in {"MUST_NOT", "FORBIDDEN", "DENY"} or polarity in {"NEGATIVE", "NEGATED"}:
        return "DENY"
    if modality in {"MAY", "CAN", "ALLOW", "PERMITTED", "ONLY_IF"}:
        return "ALLOW"
    if modality in {"MUST", "SHALL", "REQUIRED"}:
        if re.search(r"(?:需要|必须|须|需).{0,8}(?:审批|审核)|require.{0,8}approval", raw, re.I):
            return "REQUIRE_APPROVAL"
        if re.search(r"(?:需要|必须|须|需).{0,8}(?:确认)|require.{0,8}confirmation", raw, re.I):
            return "REQUIRE_CONFIRMATION"
        # Source-backed obligation to perform an action is an allow-with-must contract.
        return "ALLOW"
    return "UNSPECIFIED"


def _fact_conditions(fact: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(as_list(fact.get("conditions"))):
        value = text(raw.get("statement") if isinstance(raw, dict) else raw)
        if not value:
            continue
        slot = {
            "slot_id": stable_id("fact_condition_slot", fact.get("fact_id"), index, value),
            "source_kind": "ACCEPTED_BUSINESS_FACT",
            "raw_value": value,
            "field_candidate": "",
            "operator_candidate": "UNPARSED_EXPLICIT_CONDITION",
            "value_candidate": {"raw": value, "value_type": "TEXT"},
            "status": "CONFIRMED_SOURCE_TEXT",
        }
        for operator, pattern in _CONDITION_PATTERNS:
            match = pattern.match(value)
            if not match:
                continue
            slot["field_candidate"] = text(match.group("field"))
            slot["operator_candidate"] = operator
            slot["value_candidate"] = _normalize_number(text(match.group("value")))
            break
        result.append(slot)
    return result


def _behavior_from_fact(fact: dict[str, Any]) -> dict[str, Any] | None:
    if text(fact.get("status")) != "ACCEPTED":
        return None
    action = as_dict(fact.get("action"))
    operation = text(action.get("canonical") or action.get("raw"))
    objects = _fact_object_refs(fact)
    if not operation and text(fact.get("kind")) not in {"RULE", "STATE_TRANSITION"}:
        return None
    evidence = evidence_from_fact(fact)
    conditions = _fact_conditions(fact)
    state_effects = [dict(row) for row in as_list(fact.get("state_effects")) if isinstance(row, dict)]
    data_effects = [
        dict(row) if isinstance(row, dict) else {"statement": text(row)}
        for row in as_list(fact.get("data_effects"))
        if text(row.get("statement") if isinstance(row, dict) else row)
    ]
    missing = unique_text(
        [
            "BEHAVIOR_OPERATION_UNRESOLVED" if not operation else "",
            "BEHAVIOR_OBJECT_UNRESOLVED" if not objects else "",
            "BEHAVIOR_EVIDENCE_MISSING" if not evidence else "",
        ]
    )
    behavior_id = stable_id("business_behavior", "fact", fact.get("fact_id"), operation, objects)
    return {
        "schema": BEHAVIOR_SCHEMA,
        "behavior_id": behavior_id,
        "behavior_family_id": stable_id("behavior_family", operation, objects, _fact_actor_refs(fact)),
        "source_kind": "ACCEPTED_BUSINESS_FACT",
        "source_refs": unique_text([fact.get("fact_id")]),
        "actor_refs": _fact_actor_refs(fact),
        "operation_ref": operation,
        "object_refs": objects,
        "trigger": as_dict(fact.get("trigger")),
        "preconditions": conditions,
        "state_preconditions": [
            slot for slot in conditions if _STATE_HEADER_RE.search(text(slot.get("field_candidate")))
        ],
        "expected_effects": unique_text(
            [
                *as_list(fact.get("postconditions")),
                *[row.get("statement") for row in data_effects],
            ]
        ),
        "state_effects": state_effects,
        "data_effects": data_effects,
        "permission_decision": _fact_permission(fact),
        "exceptions": unique_text(
            [
                *as_list(fact.get("exceptions")),
                *as_list(fact.get("exception_scope")),
            ]
        ),
        "compensations": unique_text(
            [
                *as_list(fact.get("compensations")),
                *as_list(fact.get("compensation")),
            ]
        ),
        "evidence": evidence,
        "unresolved_semantics": missing,
        "status": "INCOMPLETE" if missing else "CONFIRMED",
        "candidate_only": False,
        "formal_business_rule": not bool(missing),
    }


def _matrix_behavior(
    row: dict[str, Any], operation_index: dict[str, list[dict[str, Any]]]
) -> dict[str, Any]:
    result_slots = [slot for slot in as_list(row.get("result_slots")) if isinstance(slot, dict)]
    operations = unique_text(slot.get("operation_candidate") for slot in result_slots)
    decisions = unique_text(
        slot.get("permission_decision_candidate")
        for slot in result_slots
        if text(slot.get("permission_decision_candidate")) not in {"", "UNSPECIFIED"}
    )
    operation = operations[0] if len(operations) == 1 else ""
    matched_operations = operation_index.get(operation, []) if operation else []
    object_refs = unique_text(
        [value for item in matched_operations for value in as_list(item.get("object_refs"))]
    ) if len(matched_operations) == 1 else []
    permission = decisions[0] if len(decisions) == 1 else "CONFLICTED" if len(decisions) > 1 else "UNSPECIFIED"
    unresolved = unique_text(
        [
            *as_list(row.get("unresolved_semantics")),
            "BEHAVIOR_OPERATION_UNRESOLVED" if not operation else "",
            "BEHAVIOR_OPERATION_AMBIGUOUS" if len(operations) > 1 else "",
            "BEHAVIOR_OBJECT_UNRESOLVED" if not object_refs else "",
            "BEHAVIOR_RESULT_CONFLICT" if permission == "CONFLICTED" else "",
        ]
    )
    conditions = [dict(slot) for slot in as_list(row.get("condition_slots")) if isinstance(slot, dict)]
    status = "CONFLICTED" if permission == "CONFLICTED" else "INCOMPLETE" if unresolved else "CANDIDATE"
    behavior_id = stable_id("business_behavior", "matrix", row.get("row_ledger_id"), operation, object_refs)
    return {
        "schema": BEHAVIOR_SCHEMA,
        "behavior_id": behavior_id,
        "behavior_family_id": stable_id(
            "behavior_family", operation, object_refs, row.get("actor_refs_candidate")
        ),
        "source_kind": "DECISION_MATRIX_ROW",
        "source_refs": unique_text([row.get("row_ledger_id")]),
        "actor_refs": unique_text(as_list(row.get("actor_refs_candidate"))),
        "operation_ref": operation,
        "object_refs": object_refs,
        "trigger": {},
        "preconditions": conditions,
        "state_preconditions": [
            slot for slot in conditions if _STATE_HEADER_RE.search(text(slot.get("field_candidate")))
        ],
        "expected_effects": unique_text(slot.get("effect_candidate") for slot in result_slots),
        "state_effects": [],
        "data_effects": [],
        "permission_decision": permission,
        "exceptions": [],
        "compensations": [],
        "evidence": dedupe_evidence(as_list(row.get("evidence"))),
        "unresolved_semantics": unresolved,
        "status": status,
        "candidate_only": True,
        "formal_business_rule": False,
    }


def _condition_signature(conditions: Iterable[dict[str, Any]]) -> str:
    values = [
        {
            "field": text(row.get("field_candidate")),
            "operator": text(row.get("operator_candidate")),
            "value": as_dict(row.get("value_candidate")),
        }
        for row in conditions
        if isinstance(row, dict)
    ]
    return json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)


def _merge_exact_behaviors(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, tuple[str, ...], tuple[str, ...], str, str], dict[str, Any]] = {}
    rank = {"CONFIRMED": 4, "CANDIDATE": 3, "INCOMPLETE": 2, "CONFLICTED": 1}
    for row in rows:
        key = (
            text(row.get("operation_ref")),
            tuple(unique_text(as_list(row.get("object_refs")))),
            tuple(unique_text(as_list(row.get("actor_refs")))),
            text(row.get("permission_decision")),
            _condition_signature(as_list(row.get("preconditions"))),
        )
        existing = merged.get(key)
        if existing is None:
            merged[key] = dict(row)
            continue
        existing["source_refs"] = unique_text(
            [*as_list(existing.get("source_refs")), *as_list(row.get("source_refs"))]
        )
        existing["evidence"] = dedupe_evidence(
            [*as_list(existing.get("evidence")), *as_list(row.get("evidence"))]
        )
        existing["unresolved_semantics"] = unique_text(
            [*as_list(existing.get("unresolved_semantics")), *as_list(row.get("unresolved_semantics"))]
        )
        if rank.get(text(row.get("status")), 0) > rank.get(text(existing.get("status")), 0):
            existing["status"] = row.get("status")
            existing["candidate_only"] = row.get("candidate_only")
            existing["formal_business_rule"] = row.get("formal_business_rule")
        existing["corroborated_by_multiple_sources"] = len(as_list(existing.get("source_refs"))) > 1
    return sorted(merged.values(), key=lambda row: text(row.get("behavior_id")))


def _detect_behavior_conflicts(behaviors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    by_family_and_conditions: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for behavior in behaviors:
        by_family_and_conditions[
            (
                text(behavior.get("behavior_family_id")),
                _condition_signature(as_list(behavior.get("preconditions"))),
            )
        ].append(behavior)
        equal_values: dict[str, set[str]] = defaultdict(set)
        for condition in as_list(behavior.get("preconditions")):
            if not isinstance(condition, dict) or text(condition.get("operator_candidate")) != "EQUALS":
                continue
            field = text(condition.get("field_candidate"))
            value = json.dumps(as_dict(condition.get("value_candidate")), ensure_ascii=False, sort_keys=True)
            if field:
                equal_values[field].add(value)
        contradictory = {field: sorted(values) for field, values in equal_values.items() if len(values) > 1}
        if contradictory:
            behavior["status"] = "CONFLICTED"
            behavior["formal_business_rule"] = False
            conflicts.append(
                {
                    "conflict_id": stable_id("behavior_conflict", behavior.get("behavior_id"), contradictory),
                    "kind": "BEHAVIOR_CONDITION_CONTRADICTION",
                    "status": "UNRESOLVED",
                    "severity": "P0",
                    "behavior_refs": [behavior.get("behavior_id")],
                    "details": {"contradictory_equalities": contradictory},
                    "evidence": as_list(behavior.get("evidence")),
                    "automatic_resolution_allowed": False,
                }
            )
    for (_family, _conditions), rows in by_family_and_conditions.items():
        decisions = {
            text(row.get("permission_decision"))
            for row in rows
            if text(row.get("permission_decision")) not in {"", "UNSPECIFIED", "CONFLICTED"}
        }
        if len(decisions) <= 1:
            continue
        for row in rows:
            row["status"] = "CONFLICTED"
            row["formal_business_rule"] = False
        conflicts.append(
            {
                "conflict_id": stable_id(
                    "behavior_conflict", [row.get("behavior_id") for row in rows], sorted(decisions)
                ),
                "kind": "BEHAVIOR_PERMISSION_DECISION_CONFLICT",
                "status": "UNRESOLVED",
                "severity": "P0",
                "behavior_refs": [row.get("behavior_id") for row in rows],
                "details": {"permission_decisions": sorted(decisions)},
                "evidence": dedupe_evidence(
                    [evidence for row in rows for evidence in as_list(row.get("evidence"))]
                ),
                "automatic_resolution_allowed": False,
            }
        )
    return conflicts


def _behavior_unknowns(behaviors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unknowns: list[dict[str, Any]] = []
    for behavior in behaviors:
        unresolved = unique_text(as_list(behavior.get("unresolved_semantics")))
        if not unresolved:
            continue
        unknowns.append(
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
    return unknowns


def _behavior_gate(
    behaviors: list[dict[str, Any]], conflicts: list[dict[str, Any]], row_ledger: list[dict[str, Any]]
) -> dict[str, Any]:
    traceable = [row for row in behaviors if as_list(row.get("evidence"))]
    confirmed = [row for row in behaviors if text(row.get("status")) == "CONFIRMED"]
    candidate = [row for row in behaviors if text(row.get("status")) == "CANDIDATE"]
    incomplete = [row for row in behaviors if text(row.get("status")) == "INCOMPLETE"]
    conflicted = [row for row in behaviors if text(row.get("status")) == "CONFLICTED"]
    if conflicts or conflicted:
        status = "BLOCKED_BUSINESS_BEHAVIOR_CONFLICT"
    elif incomplete or candidate:
        status = "PARTIAL_BUSINESS_BEHAVIOR_IR"
    elif confirmed:
        status = "PASS"
    else:
        status = "NO_BUSINESS_BEHAVIOR_EVIDENCE"
    denominator = len(behaviors)
    return {
        "schema": BEHAVIOR_GATE_SCHEMA,
        "status": status,
        "entry_allowed": status == "PASS",
        "metrics": {
            "decision_matrix_row_count": len(row_ledger),
            "behavior_count": len(behaviors),
            "confirmed_behavior_count": len(confirmed),
            "candidate_behavior_count": len(candidate),
            "incomplete_behavior_count": len(incomplete),
            "conflicted_behavior_count": len(conflicted),
            "behavior_conflict_count": len(conflicts),
            "source_traceability_rate": round(len(traceable) / denominator, 4) if denominator else 1.0,
        },
        "quality_claim": "BEHAVIOR_IR_CLOSURE_NOT_RECALL_OR_ACCURACY",
        "decision_matrix_rows_are_formal_rules": False,
        "automatic_conflict_resolution_allowed": False,
    }


def build_business_behavior_ir(
    asset: dict[str, Any],
    facts: Iterable[dict[str, Any]],
    operations: Iterable[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return row ledger, behaviors, conflicts, unknowns and a fail-visible behavior gate."""
    row_ledger, ledger_unknowns = build_decision_matrix_row_ledger(asset)
    operation_index = _operation_lookup(operations)
    behaviors = [
        behavior
        for fact in facts
        if isinstance(fact, dict)
        if (behavior := _behavior_from_fact(fact)) is not None
    ]
    behaviors.extend(_matrix_behavior(row, operation_index) for row in row_ledger)
    behaviors = _merge_exact_behaviors(behaviors)
    conflicts = _detect_behavior_conflicts(behaviors)
    unknowns = [*ledger_unknowns, *_behavior_unknowns(behaviors)]
    gate = _behavior_gate(behaviors, conflicts, row_ledger)
    return row_ledger, behaviors, conflicts, unknowns, gate


__all__ = [
    "BEHAVIOR_SCHEMA",
    "BEHAVIOR_ROW_LEDGER_SCHEMA",
    "BEHAVIOR_GATE_SCHEMA",
    "build_decision_matrix_row_ledger",
    "build_business_behavior_ir",
]
