"""Final logical gate for Business Behavior IR v1.

Multiple condition slots are not automatically AND.  Unless a source-backed combinator is
present, the behavior remains incomplete and equality differences are not treated as logical
contradictions.  This prevents a table layout from becoming executable Boolean semantics.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .behavior_ir import BEHAVIOR_GATE_SCHEMA
from .behavior_ir_governance import build_governed_business_behavior_ir
from .schema import as_dict, as_list, dedupe_evidence, new_unknown, text


def _explicit_combinator(behavior: dict[str, Any]) -> str:
    value = text(
        behavior.get("condition_combinator")
        or as_dict(behavior.get("trigger")).get("condition_combinator")
    ).upper()
    return value if value in {"AND", "OR"} else ""


def _rebuild_gate(
    rows: list[dict[str, Any]],
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
    traceable = sum(1 for behavior in behaviors if as_list(behavior.get("evidence")))
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
            "unresolved_condition_combinator_count": sum(
                1
                for behavior in behaviors
                if text(behavior.get("condition_combinator")) == "UNRESOLVED"
            ),
            "source_traceability_rate": round(traceable / len(behaviors), 4)
            if behaviors
            else 1.0,
        },
        "quality_claim": "BEHAVIOR_IR_CLOSURE_NOT_RECALL_OR_ACCURACY",
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
    rows, behaviors, conflicts, unknowns, _gate = build_governed_business_behavior_ir(
        asset, facts, operations
    )
    by_id = {text(row.get("behavior_id")): row for row in behaviors}

    for behavior in behaviors:
        conditions = [row for row in as_list(behavior.get("preconditions")) if isinstance(row, dict)]
        explicit = _explicit_combinator(behavior)
        if len(conditions) <= 1:
            behavior["condition_combinator"] = "SINGLE_CONDITION"
            continue
        if explicit:
            behavior["condition_combinator"] = explicit
            continue
        behavior["condition_combinator"] = "UNRESOLVED"
        behavior["unresolved_semantics"] = sorted(
            {
                *[text(value) for value in as_list(behavior.get("unresolved_semantics")) if text(value)],
                "BEHAVIOR_CONDITION_COMBINATOR_UNRESOLVED",
            }
        )
        if text(behavior.get("status")) != "CONFLICTED":
            behavior["status"] = "INCOMPLETE"
            behavior["formal_business_rule"] = False

    kept_conflicts: list[dict[str, Any]] = []
    for conflict in conflicts:
        if not isinstance(conflict, dict):
            continue
        if text(conflict.get("kind")) != "BEHAVIOR_CONDITION_CONTRADICTION":
            kept_conflicts.append(conflict)
            continue
        behavior_refs = [text(value) for value in as_list(conflict.get("behavior_refs")) if text(value)]
        behavior = by_id.get(behavior_refs[0]) if len(behavior_refs) == 1 else None
        if behavior is not None and text(behavior.get("condition_combinator")) != "AND":
            if text(behavior.get("status")) == "CONFLICTED":
                behavior["status"] = "INCOMPLETE"
                behavior["formal_business_rule"] = False
            continue
        kept_conflicts.append(conflict)
    conflicts = kept_conflicts

    preserved_unknowns = [
        row
        for row in unknowns
        if isinstance(row, dict)
        and text(row.get("kind"))
        not in {
            "BUSINESS_BEHAVIOR_INCOMPLETE",
            "BUSINESS_BEHAVIOR_CANDIDATE_UNCONFIRMED",
        }
    ]
    for behavior in behaviors:
        unresolved = [text(value) for value in as_list(behavior.get("unresolved_semantics")) if text(value)]
        if unresolved:
            preserved_unknowns.append(
                new_unknown(
                    "BUSINESS_BEHAVIOR_INCOMPLETE",
                    f"行为“{text(behavior.get('operation_ref')) or text(behavior.get('behavior_id'))}”仍缺少：{'、'.join(sorted(set(unresolved)))}。",
                    related_objects=as_list(behavior.get("object_refs")),
                    related_operations=[behavior.get("operation_ref")],
                    evidence=as_list(behavior.get("evidence")),
                    severity="P1",
                    blocks_formal_understanding=False,
                    reason_code=sorted(set(unresolved))[0],
                    details={
                        "behavior_id": behavior.get("behavior_id"),
                        "unresolved_semantics": sorted(set(unresolved)),
                    },
                )
            )
    candidates = [behavior for behavior in behaviors if text(behavior.get("status")) == "CANDIDATE"]
    if candidates:
        preserved_unknowns.append(
            new_unknown(
                "BUSINESS_BEHAVIOR_CANDIDATE_UNCONFIRMED",
                f"已形成{len(candidates)}条行为候选，但尚无足够来源证据将其升级为正式业务规则。",
                evidence=dedupe_evidence(
                    [evidence for behavior in candidates for evidence in as_list(behavior.get("evidence"))]
                ),
                severity="P1",
                blocks_formal_understanding=False,
                reason_code="BUSINESS_BEHAVIOR_CANDIDATE_UNCONFIRMED",
                details={"behavior_refs": [behavior.get("behavior_id") for behavior in candidates]},
            )
        )
    unknowns = list(
        {
            text(row.get("unknown_id")): row
            for row in preserved_unknowns
            if isinstance(row, dict) and text(row.get("unknown_id"))
        }.values()
    )
    return rows, behaviors, conflicts, unknowns, _rebuild_gate(rows, behaviors, conflicts)


__all__ = ["build_business_behavior_ir_v1"]
