"""Minimum closure checks for enterprise business understanding.

A parsed schema or a list of entities is not equivalent to understanding an
enterprise. This stage prevents empty, field-only, behaviorless, or structurally
incomplete source models from reporting PASS.
"""
from __future__ import annotations

from typing import Any

from .gate import assess_understanding_model
from .schema import as_dict, as_list, new_unknown, text


def apply_minimum_understanding_closure(
    model: dict[str, Any],
    asset: dict[str, Any],
) -> dict[str, Any]:
    ledger = as_dict(asset.get("business_fact_ledger"))
    facts = [row for row in as_list(ledger.get("items")) if isinstance(row, dict)]
    accepted_behavior_facts = [
        row
        for row in facts
        if text(row.get("status")) == "ACCEPTED"
        and text(row.get("kind")) in {"RULE", "STATE_TRANSITION"}
    ]
    pending_facts = [row for row in facts if text(row.get("status")) == "PENDING"]
    active_sources = [
        row
        for row in as_list(asset.get("source_inventory"))
        if isinstance(row, dict) and text(row.get("status") or "active") == "active"
    ]
    unknowns = [row for row in as_list(model.get("unknowns")) if isinstance(row, dict)]

    if active_sources and not accepted_behavior_facts:
        unknowns.append(
            new_unknown(
                "NO_BUSINESS_BEHAVIOR_UNDERSTOOD",
                "已接入企业资料，但尚未形成任何可追溯的业务规则或状态行为事实。字段、表名和对象清单不能替代业务理解。",
                severity="P0",
                blocks_formal_understanding=True,
                reason_code="NO_BUSINESS_BEHAVIOR_UNDERSTOOD",
                details={
                    "active_source_count": len(active_sources),
                    "fact_count": len(facts),
                    "pending_fact_count": len(pending_facts),
                },
            )
        )

    facts_with_actions = [
        row
        for row in accepted_behavior_facts
        if text(as_dict(row.get("action")).get("canonical") or as_dict(row.get("action")).get("raw"))
    ]
    if facts_with_actions and not as_list(model.get("operations")):
        unknowns.append(
            new_unknown(
                "NO_BUSINESS_OPERATION_UNDERSTOOD",
                "资料中存在明确业务动作，但尚未形成任何对象绑定的正式业务操作。",
                severity="P0",
                blocks_formal_understanding=True,
                reason_code="NO_BUSINESS_OPERATION_UNDERSTOOD",
                details={"action_fact_ids": [row.get("fact_id") for row in facts_with_actions]},
            )
        )

    if active_sources and not as_list(model.get("evidence_index")):
        unknowns.append(
            new_unknown(
                "UNDERSTANDING_WITHOUT_SOURCE_EVIDENCE",
                "企业认知模型没有形成任何可追溯来源证据，不能视为理解完成。",
                severity="P0",
                blocks_formal_understanding=True,
                reason_code="UNDERSTANDING_WITHOUT_SOURCE_EVIDENCE",
            )
        )

    model["unknowns"] = list(
        {
            text(row.get("unknown_id")): row
            for row in unknowns
            if isinstance(row, dict) and text(row.get("unknown_id"))
        }.values()
    )
    model["source_summary"] = {
        "active_source_count": len(active_sources),
        "business_fact_count": len(facts),
        "accepted_behavior_fact_count": len(accepted_behavior_facts),
        "pending_fact_count": len(pending_facts),
        "formal_business_object_count": len(as_list(model.get("business_objects"))),
        "formal_operation_count": len(as_list(model.get("operations"))),
        "formal_lifecycle_count": len(as_list(model.get("lifecycles"))),
    }
    gate = assess_understanding_model(
        model,
        upstream_gate=as_dict(asset.get("enterprise_comprehension_gate")),
    )
    model["gate"] = gate
    model["metrics"] = dict(gate.get("metrics") or {})

    # Document-structure completeness is part of the same formal closure.  Keeping
    # it here prevents alternate callers from bypassing structure gaps.
    from .document_structure_gate import apply_document_structure_completeness

    return apply_document_structure_completeness(model, asset)


__all__ = ["apply_minimum_understanding_closure"]
