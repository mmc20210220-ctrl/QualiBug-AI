"""Govern Business Behavior IR candidates before they enter enterprise understanding.

The base compiler preserves raw candidate observations.  This stage applies precedence-safe
permission parsing, canonical condition comparison, conflict recalculation, and explicit
candidate-confirmation unknowns.  It never upgrades matrix rows to confirmed rules.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Iterable

from .behavior_ir import (
    BEHAVIOR_GATE_SCHEMA,
    _condition_frame_signature,
    build_business_behavior_ir,
)
from .schema import as_dict, as_list, dedupe_evidence, new_unknown, stable_id, text, unique_text

_DENY_RE = re.compile(r"(?:禁止|不得|不允许|拒绝|不可|不能|deny|forbid)", re.I)
_APPROVAL_RE = re.compile(r"(?:需要|必须|须|需).{0,8}(?:审批|审核)|require.{0,8}approval", re.I)
_CONFIRM_RE = re.compile(r"(?:需要|必须|须|需).{0,8}(?:确认)|require.{0,8}confirmation", re.I)
_ALLOW_RE = re.compile(r"(?:允许|可以|可执行|准许|allow|permit)", re.I)


def _permission(raw: Any) -> str:
    value = text(raw)
    if _DENY_RE.search(value):
        return "DENY"
    if _APPROVAL_RE.search(value):
        return "REQUIRE_APPROVAL"
    if _CONFIRM_RE.search(value):
        return "REQUIRE_CONFIRMATION"
    if _ALLOW_RE.search(value):
        return "ALLOW"
    return "UNSPECIFIED"


def _canonical_value(value: dict[str, Any]) -> dict[str, Any]:
    row = as_dict(value)
    if text(row.get("value_type")) == "NUMBER" and row.get("normalized_value") is not None:
        return {
            "value_type": "NUMBER",
            "normalized_value": row.get("normalized_value"),
            "unit": text(row.get("unit")),
        }
    return {
        "value_type": text(row.get("value_type")) or "TEXT",
        "normalized_value": row.get("normalized_value", text(row.get("raw"))),
        "unit": text(row.get("unit")),
    }


def _condition_signature(conditions: Iterable[dict[str, Any]]) -> str:
    values = sorted(
        [
            {
                "field": text(row.get("field_candidate")),
                "operator": text(row.get("operator_candidate")),
                "value": _canonical_value(as_dict(row.get("value_candidate"))),
            }
            for row in conditions
            if isinstance(row, dict)
        ],
        key=lambda row: json.dumps(row, ensure_ascii=False, sort_keys=True, default=str),
    )
    return json.dumps(values, ensure_ascii=False, sort_keys=True, default=str)


def _reapply_matrix_permissions(
    row_ledger: list[dict[str, Any]], behaviors: list[dict[str, Any]]
) -> None:
    rows_by_id = {text(row.get("row_ledger_id")): row for row in row_ledger}
    for behavior in behaviors:
        if text(behavior.get("source_kind")) != "DECISION_MATRIX_ROW":
            continue
        source_ref = next(
            (text(value) for value in as_list(behavior.get("source_refs")) if text(value)), ""
        )
        row = rows_by_id.get(source_ref)
        if not row:
            continue
        decisions = unique_text(
            _permission(slot.get("raw_value"))
            for slot in as_list(row.get("result_slots"))
            if isinstance(slot, dict) and _permission(slot.get("raw_value")) != "UNSPECIFIED"
        )
        permission = (
            decisions[0]
            if len(decisions) == 1
            else "CONFLICTED"
            if len(decisions) > 1
            else "UNSPECIFIED"
        )
        behavior["permission_decision"] = permission
        unresolved = [
            value
            for value in unique_text(as_list(behavior.get("unresolved_semantics")))
            if value != "BEHAVIOR_RESULT_CONFLICT"
        ]
        if permission == "CONFLICTED":
            unresolved.append("BEHAVIOR_RESULT_CONFLICT")
        behavior["unresolved_semantics"] = unique_text(unresolved)
        if permission == "CONFLICTED":
            behavior["status"] = "CONFLICTED"
        elif unresolved:
            behavior["status"] = "INCOMPLETE"
        else:
            behavior["status"] = "CANDIDATE"
        behavior["candidate_only"] = True
        behavior["formal_business_rule"] = False


def _remerge_governed_behaviors(behaviors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, tuple[str, ...], tuple[str, ...], str, str, str], dict[str, Any]] = {}
    rank = {"CONFIRMED": 4, "CANDIDATE": 3, "INCOMPLETE": 2, "CONFLICTED": 1}
    for raw in behaviors:
        behavior = dict(raw)
        frame = as_dict(behavior.get("condition_frame"))
        key = (
            text(behavior.get("operation_ref")),
            tuple(unique_text(as_list(behavior.get("object_refs")))),
            tuple(unique_text(as_list(behavior.get("actor_refs")))),
            text(behavior.get("permission_decision")),
            _condition_signature(as_list(behavior.get("preconditions"))),
            _condition_frame_signature(frame),
        )
        existing = merged.get(key)
        if existing is None:
            merged[key] = behavior
            continue
        existing["source_refs"] = unique_text(
            [*as_list(existing.get("source_refs")), *as_list(behavior.get("source_refs"))]
        )
        existing["evidence"] = dedupe_evidence(
            [*as_list(existing.get("evidence")), *as_list(behavior.get("evidence"))]
        )
        existing["unresolved_semantics"] = unique_text(
            [
                *as_list(existing.get("unresolved_semantics")),
                *as_list(behavior.get("unresolved_semantics")),
            ]
        )
        existing_frame = as_dict(existing.get("condition_frame"))
        if frame and (
            not existing_frame
            or len(json.dumps(frame, ensure_ascii=False, sort_keys=True, default=str))
            > len(json.dumps(existing_frame, ensure_ascii=False, sort_keys=True, default=str))
        ):
            existing["condition_frame"] = dict(frame)
        if rank.get(text(behavior.get("status")), 0) > rank.get(text(existing.get("status")), 0):
            existing["status"] = behavior.get("status")
            existing["source_kind"] = behavior.get("source_kind")
            existing["candidate_only"] = behavior.get("candidate_only")
            existing["formal_business_rule"] = behavior.get("formal_business_rule")
        existing["corroborated_by_multiple_sources"] = len(as_list(existing.get("source_refs"))) > 1
    return sorted(merged.values(), key=lambda row: text(row.get("behavior_id")))


def _recalculate_conflicts(behaviors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recalculate only conflicts backed by accepted business facts.

    Decision-matrix rows are direct structural interpretations and remain candidate-only
    until corroborated into the business fact ledger. They may expose ambiguity or a
    coverage gap, but they cannot conflict with, downgrade, or block a formal fact-backed
    behavior.
    """
    conflicts: list[dict[str, Any]] = []
    families: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for behavior in behaviors:
        source_kind = text(behavior.get("source_kind"))
        unresolved = unique_text(as_list(behavior.get("unresolved_semantics")))
        is_fact_backed = source_kind == "ACCEPTED_BUSINESS_FACT"

        if is_fact_backed:
            behavior["status"] = "INCOMPLETE" if unresolved else "CONFIRMED"
            behavior["formal_business_rule"] = text(behavior.get("status")) == "CONFIRMED"
        else:
            if text(behavior.get("permission_decision")) == "CONFLICTED":
                unresolved.append("BEHAVIOR_CANDIDATE_RESULT_CONFLICT")
            behavior["unresolved_semantics"] = unique_text(unresolved)
            behavior["status"] = "INCOMPLETE" if unresolved else "CANDIDATE"
            behavior["candidate_only"] = True
            behavior["formal_business_rule"] = False

        if not behavior.get("formal_business_rule"):
            continue

        signature = _condition_signature(as_list(behavior.get("preconditions")))
        families[(text(behavior.get("behavior_family_id")), signature)].append(behavior)

        equal_values: dict[str, set[str]] = defaultdict(set)
        for condition in as_list(behavior.get("preconditions")):
            if (
                not isinstance(condition, dict)
                or text(condition.get("operator_candidate")) != "EQUALS"
            ):
                continue
            field = text(condition.get("field_candidate"))
            if field:
                equal_values[field].add(
                    json.dumps(
                        _canonical_value(as_dict(condition.get("value_candidate"))),
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    )
                )
        contradictions = {
            field: sorted(values) for field, values in equal_values.items() if len(values) > 1
        }
        if contradictions:
            behavior["status"] = "CONFLICTED"
            behavior["formal_business_rule"] = False
            conflicts.append(
                {
                    "conflict_id": stable_id(
                        "behavior_conflict", behavior.get("behavior_id"), contradictions
                    ),
                    "kind": "BEHAVIOR_CONDITION_CONTRADICTION",
                    "status": "UNRESOLVED",
                    "severity": "P0",
                    "behavior_refs": [behavior.get("behavior_id")],
                    "details": {"contradictory_equalities": contradictions},
                    "evidence": as_list(behavior.get("evidence")),
                    "automatic_resolution_allowed": False,
                }
            )

    for (_family, _signature), rows in families.items():
        decisions = {
            text(row.get("permission_decision"))
            for row in rows
            if text(row.get("permission_decision"))
            not in {"", "UNSPECIFIED", "CONFLICTED"}
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


def _governance_unknowns(
    behaviors: list[dict[str, Any]], existing: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {text(row.get("behavior_id")): row for row in behaviors}
    unknowns: list[dict[str, Any]] = []
    for row in existing:
        if not isinstance(row, dict):
            continue
        if text(row.get("kind")) == "BUSINESS_BEHAVIOR_INCOMPLETE":
            behavior = by_id.get(text(as_dict(row.get("details")).get("behavior_id")))
            if behavior is not None and not as_list(behavior.get("unresolved_semantics")):
                continue
        unknowns.append(row)
    candidates = [row for row in behaviors if text(row.get("status")) == "CANDIDATE"]
    if candidates:
        unknowns.append(
            new_unknown(
                "BUSINESS_BEHAVIOR_CANDIDATE_UNCONFIRMED",
                f"已形成{len(candidates)}条决策矩阵行为候选，但尚无足够来源证据将其升级为正式业务规则。",
                evidence=dedupe_evidence(
                    [evidence for row in candidates for evidence in as_list(row.get("evidence"))]
                ),
                severity="P1",
                blocks_formal_understanding=False,
                reason_code="BUSINESS_BEHAVIOR_CANDIDATE_UNCONFIRMED",
                details={"behavior_refs": [row.get("behavior_id") for row in candidates]},
            )
        )
    return list(
        {
            text(row.get("unknown_id")): row
            for row in unknowns
            if isinstance(row, dict) and text(row.get("unknown_id"))
        }.values()
    )


def _gate(
    row_ledger: list[dict[str, Any]],
    behaviors: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
) -> dict[str, Any]:
    counts = defaultdict(int)
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
            "decision_matrix_row_count": len(row_ledger),
            "behavior_count": len(behaviors),
            "confirmed_behavior_count": counts["CONFIRMED"],
            "candidate_behavior_count": counts["CANDIDATE"],
            "incomplete_behavior_count": counts["INCOMPLETE"],
            "conflicted_behavior_count": counts["CONFLICTED"],
            "behavior_conflict_count": len(conflicts),
            "source_traceability_rate": round(traceable / len(behaviors), 4)
            if behaviors
            else 1.0,
        },
        "quality_claim": "BEHAVIOR_IR_CLOSURE_NOT_RECALL_OR_ACCURACY",
        "matrix_rows_require_corroboration": True,
        "automatic_conflict_resolution_allowed": False,
    }


def build_governed_business_behavior_ir(
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
    row_ledger, behaviors, _base_conflicts, unknowns, _base_gate = build_business_behavior_ir(
        asset, facts, operations
    )
    _reapply_matrix_permissions(row_ledger, behaviors)
    behaviors = _remerge_governed_behaviors(behaviors)
    conflicts = _recalculate_conflicts(behaviors)
    unknowns = _governance_unknowns(behaviors, unknowns)
    return row_ledger, behaviors, conflicts, unknowns, _gate(row_ledger, behaviors, conflicts)


__all__ = ["build_governed_business_behavior_ir"]
