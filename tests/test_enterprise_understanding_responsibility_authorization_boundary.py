"""Business responsibility must never be promoted into authorization semantics."""
from __future__ import annotations

import hashlib

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.authorization_semantics import (
    resolve_fact_authorization,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.behavior_ir import (
    build_business_behavior_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.builder import (
    build_enterprise_understanding_model,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.implementation_binding_governance import (
    _behavior_semantic_ready,
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


def _understanding_model(fact: dict) -> dict:
    return build_enterprise_understanding_model(
        {
            "asset_id": "asset:responsibility-authorization-boundary",
            "business_fact_ledger": {"items": [fact]},
        }
    )


def _only_actor(model: dict) -> dict:
    actors = model["actors"]
    assert len(actors) == 1
    assert actors[0]["name"] == "仓库员"
    return actors[0]


def test_required_business_action_is_responsibility_not_allow() -> None:
    behavior, gate = _behavior(
        _fact("仓库员必须登记入库单", modality="MUST")
    )

    assert behavior["status"] == "CONFIRMED"
    assert behavior["business_modality"] == "MUST"
    assert behavior["permission_decision"] == "UNSPECIFIED"
    assert behavior["authorization_semantics_explicit"] is False
    assert behavior["authorization_semantic_kind"] == "NONE"
    assert gate["metrics"]["responsibility_behavior_count"] == 1
    assert gate["metrics"]["authorization_behavior_count"] == 0
    assert _scenario_type(behavior) == ("POSITIVE", ["POSITIVE"])


def test_generic_business_prohibition_is_rejection_not_unauthorized() -> None:
    behavior, gate = _behavior(
        _fact("仓库员不得重复登记入库单", modality="MUST_NOT")
    )

    assert behavior["permission_decision"] == "UNSPECIFIED"
    assert behavior["authorization_semantics_explicit"] is False
    assert behavior["authorization_semantic_kind"] == "NONE"
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
    assert behavior["authorization_semantics_status"] == "RESOLVED"
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
    assert behavior["authorization_derivation"] == "explicit_authorization_semantics"


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


def test_explicit_unknown_is_fail_closed_without_text_fallback() -> None:
    fact = _fact(
        "仓库员允许执行受控登记",
        modality="MAY",
        authorization_semantics={
            "decision": "UNKNOWN",
            "source_backed": True,
        },
    )

    resolution = resolve_fact_authorization(fact)
    assert resolution["decision"] == "UNKNOWN"
    assert resolution["semantic_kind"] == "AUTHORIZATION"
    assert resolution["authority_declared"] is True
    assert resolution["resolution_status"] == "UNRESOLVED"
    assert resolution["reason_code"] == "FACT_AUTHORIZATION_DECISION_UNRESOLVED"
    assert resolution["text_fallback_used"] is False

    _rows, behaviors, conflicts, unknowns, gate = build_business_behavior_ir(
        {}, [fact], []
    )
    assert conflicts == []
    assert len(behaviors) == 1
    behavior = behaviors[0]
    assert behavior["permission_decision"] == "UNKNOWN"
    assert behavior["authorization_semantics_explicit"] is True
    assert behavior["authorization_semantics_status"] == "UNRESOLVED"
    assert behavior["authorization_text_fallback_used"] is False
    assert behavior["status"] == "INCOMPLETE"
    assert behavior["formal_business_rule"] is False
    assert "BEHAVIOR_AUTHORIZATION_DECISION_UNRESOLVED" in behavior["unresolved_semantics"]
    assert _behavior_semantic_ready(behavior) is False
    assert len(unknowns) == 1
    assert unknowns[0]["reason_code"] == "BEHAVIOR_AUTHORIZATION_DECISION_UNRESOLVED"
    assert gate["entry_allowed"] is False
    assert gate["metrics"]["authorization_behavior_count"] == 0
    assert gate["metrics"]["unresolved_authorization_behavior_count"] == 1
    assert gate["explicit_unknown_authorization_can_fallback_to_text"] is False


def test_approval_is_governance_not_actor_authorization() -> None:
    fact = _fact("仓库员登记入库单前必须经过主管审批", modality="MUST")
    behavior, gate = _behavior(fact)

    assert behavior["permission_decision"] == "REQUIRE_APPROVAL"
    assert behavior["authorization_semantic_kind"] == "GOVERNANCE"
    assert behavior["authorization_semantics_explicit"] is False
    assert gate["metrics"]["authorization_behavior_count"] == 0
    assert gate["metrics"]["governance_decision_behavior_count"] == 1

    actor = _only_actor(_understanding_model(fact))
    assert actor["authorization_contracts"] == []
    assert actor["permissions"] == []
    assert actor["restrictions"] == []
    assert actor["permission_unknowns"] == []
    assert actor["authorization_status"] == "NOT_DECLARED"
    assert actor["responsibility_operation_refs"]


def test_actor_model_keeps_business_prohibition_out_of_restrictions() -> None:
    model = _understanding_model(
        _fact("仓库员不得重复登记入库单", modality="MUST_NOT")
    )
    actor = _only_actor(model)

    assert actor["authorization_contracts"] == []
    assert actor["permissions"] == []
    assert actor["restrictions"] == []
    assert actor["permission_unknowns"] == []
    assert actor["authorization_status"] == "NOT_DECLARED"
    assert actor["responsibility_operation_refs"]
    assert model["authorization_unknowns"] == []
    assert model["authorization_model"]["responsibility_is_permission"] is False
    assert model["authorization_model"]["workflow_governance_is_actor_permission"] is False


def test_actor_model_preserves_explicit_unknown_only_as_unknown() -> None:
    fact = _fact(
        "仓库员允许执行受控登记",
        modality="MAY",
        authorization_semantics={
            "decision": "UNKNOWN",
            "source_backed": True,
        },
    )
    model = _understanding_model(fact)
    actor = _only_actor(model)

    assert actor["permissions"] == []
    assert actor["restrictions"] == []
    assert len(actor["authorization_contracts"]) == 1
    assert len(actor["permission_unknowns"]) == 1
    contract = actor["permission_unknowns"][0]
    assert contract["decision"] == "UNKNOWN"
    assert contract["declared_decision"] == "UNKNOWN"
    assert contract["derivation"] == "explicit_authorization_semantics"
    assert actor["authorization_status"] == "UNRESOLVED"
    assert actor["status"] == "PARTIAL"
    assert len(model["authorization_unknowns"]) == 1
    assert model["authorization_unknowns"][0]["reason_code"] == "FACT_AUTHORIZATION_DECISION_UNRESOLVED"
    assert model["authorization_model"]["unknown_is_allow"] is False
    assert model["authorization_model"]["unknown_is_deny"] is False
    assert model["authorization_model"]["explicit_unknown_can_fallback_to_text"] is False


def test_non_identity_scope_remains_business_rule() -> None:
    fact = _fact(
        "仓库员不得在夜间登记入库单",
        modality="MUST_NOT",
        scope={"time_window": "夜间"},
    )

    resolution = resolve_fact_authorization(fact)
    assert resolution["decision"] == "UNSPECIFIED"
    assert resolution["semantic_kind"] == "NONE"
    assert resolution["authority_declared"] is False

    behavior, gate = _behavior(fact)
    assert behavior["permission_decision"] == "UNSPECIFIED"
    assert behavior["authorization_semantics_explicit"] is False
    assert _scenario_type(behavior) == ("REJECTION", ["REJECTION"])
    assert gate["metrics"]["authorization_behavior_count"] == 0

    actor = _only_actor(_understanding_model(fact))
    assert actor["authorization_contracts"] == []
    assert actor["restrictions"] == []
    assert actor["authorization_status"] == "NOT_DECLARED"


def test_empty_explicit_authorization_declaration_is_unknown() -> None:
    fact = _fact(
        "仓库员允许执行受控登记",
        modality="MAY",
        authorization_semantics={},
    )

    resolution = resolve_fact_authorization(fact)
    assert resolution["decision"] == "UNKNOWN"
    assert resolution["declared_decision"] == "UNKNOWN"
    assert resolution["resolution_status"] == "UNRESOLVED"
    assert resolution["text_fallback_used"] is False

    _rows, behaviors, conflicts, unknowns, gate = build_business_behavior_ir(
        {}, [fact], []
    )
    assert conflicts == []
    assert len(behaviors) == 1
    assert behaviors[0]["permission_decision"] == "UNKNOWN"
    assert behaviors[0]["status"] == "INCOMPLETE"
    assert len(unknowns) == 1
    assert gate["entry_allowed"] is False


def test_incomplete_coordinate_preserves_declared_authorization() -> None:
    fact = _fact(
        "仓库员无权执行受控登记",
        modality="MUST_NOT",
        authorization_semantics={
            "decision": "DENY",
            "source_backed": True,
        },
    )
    fact["subject"]["entity_refs"] = []
    fact["object"]["entity_refs"] = []

    model = _understanding_model(fact)
    actor = _only_actor(model)
    assert actor["permissions"] == []
    assert actor["restrictions"] == []
    assert len(actor["permission_unknowns"]) == 1
    contract = actor["permission_unknowns"][0]
    assert contract["decision"] == "UNKNOWN"
    assert contract["declared_decision"] == "DENY"
    assert contract["coordinate_complete"] is False
    assert contract["resolution_reason"] == "ACTOR_AUTHORIZATION_COORDINATE_INCOMPLETE"
    assert actor["authorization_status"] == "UNRESOLVED"
    assert len(model["authorization_unknowns"]) == 1
    assert model["authorization_unknowns"][0]["reason_code"] == "ACTOR_AUTHORIZATION_COORDINATE_INCOMPLETE"
