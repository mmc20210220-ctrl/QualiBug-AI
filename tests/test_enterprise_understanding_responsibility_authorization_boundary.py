"""Business responsibility must never be promoted into authorization semantics."""
from __future__ import annotations

import hashlib

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.behavior_ir import (
    build_business_behavior_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.scenario_ir import (
    _scenario_type,
)


def _fact(
    statement: str,
    *,
    modality: str,
    scope: dict[str, str] | None = None,
    authorization_semantics: dict[str, str] | None = None,
) -> dict:
    quote_hash = hashlib.sha256(statement.encode("utf-8")).hexdigest()
    fact = {
        "fact_id": f"fact:{quote_hash[:16]}",
        "kind": "RULE",
        "status": "ACCEPTED",
        "raw_statement": statement,
        "subject": {
            "actor_refs": ["仓库员"],
            "entity_refs": ["入库单"],
        },
        "object": {"entity_refs": ["入库单"]},
        "action": {"canonical": "登记", "raw": "登记"},
        "conditions": [],
        "condition_combinator": "",
        "modality": modality,
        "polarity": "NEGATIVE" if modality == "MUST_NOT" else "POSITIVE",
        "scope": scope or {},
        "postconditions": ["形成入库记录"],
        "source_spans": [
            {
                "source_id": "src:responsibility-auth-boundary",
                "locator": "line:1",
                "quote": statement,
                "quote_hash": quote_hash,
            }
        ],
    }
    if authorization_semantics is not None:
        fact["authorization_semantics"] = authorization_semantics
    return fact


def _behavior(fact: dict) -> tuple[dict, dict]:
    _rows, behaviors, conflicts, unknowns, gate = build_business_behavior_ir(
        {}, [fact], []
    )
    assert conflicts == []
    assert unknowns == []
    assert len(behaviors) == 1
    return behaviors[0], gate


def test_required_business_action_is_responsibility_not_allow() -> None:
    behavior, gate = _behavior(
        _fact("仓库员必须登记入库单", modality="MUST")
    )

    assert behavior["status"] == "CONFIRMED"
    assert behavior["business_modality"] == "MUST"
    assert behavior["permission_decision"] == "UNSPECIFIED"
    assert behavior["authorization_semantics_explicit"] is False
    assert gate["metrics"]["responsibility_behavior_count"] == 1
    assert gate["metrics"]["authorization_behavior_count"] == 0
    assert _scenario_type(behavior) == ("POSITIVE", ["POSITIVE"])


def test_generic_business_prohibition_is_rejection_not_unauthorized() -> None:
    behavior, gate = _behavior(
        _fact("仓库员不得重复登记入库单", modality="MUST_NOT")
    )

    assert behavior["permission_decision"] == "UNSPECIFIED"
    assert behavior["authorization_semantics_explicit"] is False
    assert gate["metrics"]["authorization_behavior_count"] == 0
    scenario_type, dimensions = _scenario_type(behavior)
    assert scenario_type == "REJECTION"
    assert "REJECTION" in dimensions
    assert "AUTHORIZATION" not in dimensions


def test_explicit_source_authority_still_creates_unauthorized() -> None:
    behavior, gate = _behavior(
        _fact("仓库员无权删除入库单", modality="MUST_NOT")
    )

    assert behavior["permission_decision"] == "DENY"
    assert behavior["authorization_semantics_explicit"] is True
    assert gate["metrics"]["authorization_behavior_count"] == 1
    scenario_type, dimensions = _scenario_type(behavior)
    assert scenario_type == "UNAUTHORIZED"
    assert "AUTHORIZATION" in dimensions
    assert "REJECTION" in dimensions


def test_explicit_authorization_semantics_is_fact_authority() -> None:
    behavior, _gate = _behavior(
        _fact(
            "仓库员执行受控登记",
            modality="ASSERTS",
            authorization_semantics={
                "decision": "ALLOW",
                "source_backed": True,
            },
        )
    )

    assert behavior["permission_decision"] == "ALLOW"
    assert behavior["authorization_semantics_explicit"] is True


def test_scoped_actor_boundary_remains_authorization() -> None:
    behavior, _gate = _behavior(
        _fact(
            "仓库员不得登记其他仓库的入库单",
            modality="MUST_NOT",
            scope={"organization": "本仓库", "data_scope": "本仓库"},
        )
    )

    assert behavior["permission_decision"] == "DENY"
    assert behavior["authorization_semantics_explicit"] is True
    scenario_type, dimensions = _scenario_type(behavior)
    assert scenario_type == "UNAUTHORIZED"
    assert "AUTHORIZATION" in dimensions
