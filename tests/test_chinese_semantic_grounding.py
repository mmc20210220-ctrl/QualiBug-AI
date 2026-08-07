"""P0-D: technical grounding engine — evidence chains and receipts.

Covers SPEC §12.2 priorities (actor: permission matrix > roles > UI contract >
concept registry; operation: rule ref > summary > description >
rule_to_interface > UI contract > structural entity+method), the
AMBIGUOUS/UNKNOWN fail-closed paths, state enum grounding, scope OWN
activation, and the receipt completeness contract.
"""

from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_ledger_adapter import (
    project_business_facts_to_semantic_frames,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_grounding import (
    CHINESE_SEMANTIC_GROUNDING_SCHEMA,
    ground_semantic_frames,
)


def _fact(*, fact_id: str, actor: str = "买家", action: str = "查询",
          modality: str = "MAY", ownership: str = "", conditions: list[str] | None = None,
          statement: str = "") -> dict:
    return {
        "fact_id": fact_id,
        "fact_type": "PERMISSION_RULE",
        "kind": "RULE",
        "language": "zh-CN",
        "statement_frame_id": f"statement_frame:{fact_id}",
        "subject": {
            "actor_refs": [actor] if actor else [],
            "entity_refs": ["订单"],
            "resolution_evidence": [],
        },
        "object": {"entity_refs": ["订单"]},
        "predicate": action,
        "action": {"canonical": action, "raw": action},
        "conditions": list(conditions or []),
        "condition_combinator": "",
        "condition_frame": {},
        "scope": {"tenant": "", "organization": "", "ownership": ownership, "data_scope": ""},
        "modality": modality,
        "polarity": "POSITIVE",
        "exceptions": [],
        "exception_scope": [],
        "postconditions": [],
        "state_effects": [],
        "data_effects": [],
        "quantity_constraints": [],
        "time_window_constraints": [],
        "formula_constraints": [],
        "compensation": [],
        "raw_statement": statement or f"{actor}可以{action}订单。",
        "source_spans": [
            {
                "evidence_address": {
                    "source_id": "s1",
                    "locator": "r.docx#section=1#paragraph=1",
                    "document_block_id": "b1",
                    "block_type": "PARAGRAPH",
                },
                "quote": statement or f"{actor}可以{action}订单。",
            }
        ],
        "confidence": 1.0,
        "status": "ACCEPTED",
        "ambiguities": [],
        "critical": True,
        "derivation": "structure_first_explicit_fact_compiler",
    }


def _grounded(fact: dict, **extra: object) -> dict:
    asset = {
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v2",
            "fact_authority": "original_chinese_source_span",
            "items": [fact],
        },
        "enterprise_understanding_model": {
            "actors": [{"actor_id": "business_actor:buyer", "name": "买家"}],
            "business_objects": [
                {"object_id": "business_object:order", "name": "订单", "aliases": ["orders"]}
            ],
        },
    }
    asset.update(extra)
    asset = project_business_facts_to_semantic_frames(asset)
    return ground_semantic_frames(asset)


def _frame(asset: dict) -> dict:
    return asset["chinese_semantic_frame_ledger"]["items"][0]


def test_actor_grounded_via_permission_matrix_role() -> None:
    asset = _grounded(
        _fact(fact_id="f:1"),
        permission_matrix=[
            {"permission_id": "p1", "role": "买家", "resource": "/api/orders",
             "actions": ["get"], "decision": "allow", "scope": "own"}
        ],
    )
    frame = _frame(asset)
    assert frame["actor"]["grounded_actor_refs"] == ["买家"]
    assert frame["actor"]["resolution_status"] == "GROUNDED"
    actor_receipt = next(r for r in frame["grounding_receipts"] if r["grounding_type"] == "ACTOR")
    assert actor_receipt["status"] == "GROUNDED"
    assert actor_receipt["reason_code"] == "PERMISSION_MATRIX_ROLE_EXACT"


def test_actor_ambiguous_when_concept_registry_collides() -> None:
    # Two distinct knowledge-model actors sharing one name → AMBIGUOUS, never
    # a forced pick (SPEC §12.3).
    asset = _grounded(
        _fact(fact_id="f:2"),
        **{
            "enterprise_understanding_model": {
                "actors": [
                    {"actor_id": "business_actor:a1", "name": "买家"},
                    {"actor_id": "business_actor:a2", "name": "买家"},
                ],
                "business_objects": [
                    {"object_id": "business_object:order", "name": "订单"}
                ],
            }
        },
    )
    frame = _frame(asset)
    assert frame["actor"]["grounded_actor_refs"] == []
    actor_receipt = next(r for r in frame["grounding_receipts"] if r["grounding_type"] == "ACTOR")
    assert actor_receipt["status"] == "AMBIGUOUS"
    assert actor_receipt["reason_code"] == "MULTIPLE_ACTOR_CANDIDATES"


def test_duplicate_role_rows_dedup_to_one_candidate() -> None:
    # Several identical role rows name the SAME role — dedup → GROUNDED.
    asset = _grounded(
        _fact(fact_id="f:2b"),
        roles=[
            {"role": "买家", "source_id": "r1"},
            {"role": "买家", "source_id": "r2"},
        ],
    )
    frame = _frame(asset)
    assert frame["actor"]["grounded_actor_refs"] == ["买家"]
    actor_receipt = next(r for r in frame["grounding_receipts"] if r["grounding_type"] == "ACTOR")
    assert actor_receipt["status"] == "GROUNDED"
    assert actor_receipt["reason_code"] == "SOURCE_ROLE_DECLARATION_EXACT"


def test_actor_unknown_without_evidence() -> None:
    asset = _grounded(_fact(fact_id="f:3", actor="神秘角色"))
    frame = _frame(asset)
    assert frame["actor"]["grounded_actor_refs"] == []
    actor_receipt = next(r for r in frame["grounding_receipts"] if r["grounding_type"] == "ACTOR")
    assert actor_receipt["status"] == "UNKNOWN"
    assert actor_receipt["reason_code"] == "GROUNDING_EVIDENCE_INSUFFICIENT"


def test_operation_grounded_via_rule_explicit_ref() -> None:
    fact = _fact(fact_id="f:4")
    asset = _grounded(
        fact,
        rule_library=[
            {
                "rule_id": "zh_business:rule4",
                "fact_id": "f:4",
                "operation_refs": ["api:GET:/api/orders"],
                "statement": fact["raw_statement"],
            }
        ],
        interfaces=[
            {"interface_id": "api:GET:/api/orders", "method": "GET", "path": "/api/orders"}
        ],
    )
    frame = _frame(asset)
    assert frame["action"]["grounded_operation_refs"] == ["GET:/api/orders"]
    op_receipt = next(r for r in frame["grounding_receipts"] if r["grounding_type"] == "OPERATION")
    assert op_receipt["status"] == "GROUNDED"
    assert op_receipt["reason_code"] == "RULE_EXPLICIT_OPERATION_REF"


def test_operation_grounded_via_summary_verbatim() -> None:
    asset = _grounded(
        _fact(fact_id="f:5"),
        interfaces=[
            {"interface_id": "api:GET:/api/orders", "method": "GET", "path": "/api/orders",
             "summary": "查询订单列表", "description": ""},
        ],
    )
    frame = _frame(asset)
    assert frame["action"]["grounded_operation_refs"] == ["GET:/api/orders"]
    op_receipt = next(r for r in frame["grounding_receipts"] if r["grounding_type"] == "OPERATION")
    assert op_receipt["reason_code"] == "SOURCE_SUMMARY_EXACT_MATCH"


def test_operation_ambiguous_on_multiple_summary_matches() -> None:
    asset = _grounded(
        _fact(fact_id="f:6"),
        interfaces=[
            {"interface_id": "api:GET:/api/orders", "method": "GET", "path": "/api/orders",
             "summary": "查询订单", "description": ""},
            {"interface_id": "api:GET:/api/orders_v2", "method": "GET", "path": "/api/orders_v2",
             "summary": "查询订单", "description": ""},
        ],
    )
    frame = _frame(asset)
    assert frame["action"]["grounded_operation_refs"] == []
    op_receipt = next(r for r in frame["grounding_receipts"] if r["grounding_type"] == "OPERATION")
    assert op_receipt["status"] == "AMBIGUOUS"
    assert op_receipt["reason_code"] == "MULTIPLE_OPERATION_CANDIDATES"


def test_operation_grounded_via_rule_to_interface_edge() -> None:
    fact = _fact(fact_id="f:7")
    rule_id = "zh_business:rule7"
    asset = _grounded(
        fact,
        rule_library=[
            {"rule_id": rule_id, "fact_id": "f:7", "statement": fact["raw_statement"]}
        ],
        relationships=[
            {"from": rule_id, "to": "api:GET:/api/orders", "relation": "rule_to_interface",
             "status": "accepted"}
        ],
        interfaces=[
            {"interface_id": "api:GET:/api/orders", "method": "GET", "path": "/api/orders"}
        ],
    )
    frame = _frame(asset)
    assert frame["action"]["grounded_operation_refs"] == ["GET:/api/orders"]
    op_receipt = next(r for r in frame["grounding_receipts"] if r["grounding_type"] == "OPERATION")
    assert op_receipt["reason_code"] == "RULE_TO_INTERFACE_AUTHORITATIVE"


def test_operation_grounded_via_structural_entity_method() -> None:
    asset = _grounded(
        _fact(fact_id="f:8", action="删除", statement="买家可以删除订单。"),
        interfaces=[
            {"interface_id": "api:DELETE:/api/orders/:id", "method": "DELETE",
             "path": "/api/orders/:id", "summary": "", "description": "", "entity_refs": ["orders"]},
        ],
        entities=[{"name": "orders", "kind": "business_object"}],
    )
    frame = _frame(asset)
    # 删除 → DELETE; the entity mention resolves via the declared lexicon.
    assert frame["action"]["grounded_operation_refs"] == ["DELETE:/api/orders/:id"]
    op_receipt = next(r for r in frame["grounding_receipts"] if r["grounding_type"] == "OPERATION")
    assert op_receipt["reason_code"] == "STRUCTURAL_ENTITY_METHOD_MATCH"


def test_operation_unknown_without_evidence() -> None:
    asset = _grounded(_fact(fact_id="f:9"))
    frame = _frame(asset)
    assert frame["action"]["grounded_operation_refs"] == []
    op_receipt = next(r for r in frame["grounding_receipts"] if r["grounding_type"] == "OPERATION")
    assert op_receipt["status"] == "UNKNOWN"


def test_state_grounded_via_field_description_enum() -> None:
    asset = _grounded(
        _fact(fact_id="f:10", conditions=["订单已支付时"], statement="订单已支付时可以取消。"),
        interfaces=[
            {
                "interface_id": "api:POST:/api/orders/:id/cancel",
                "method": "POST",
                "path": "/api/orders/:id/cancel",
                "technical_declarations": [
                    {
                        "property_path": ["Order", "status"],
                        "description": "订单状态：已支付",
                        "constraints": {"enum": ["PAID"]},
                    }
                ],
            }
        ],
    )
    frame = _frame(asset)
    assert frame["technical_grounding"]["state_value_refs"] == ["PAID"]
    state_receipts = [r for r in frame["grounding_receipts"] if r["grounding_type"] == "STATE"]
    assert state_receipts[0]["status"] == "GROUNDED"
    assert state_receipts[0]["reason_code"] == "FIELD_DESCRIPTION_STATE_MATCH"


def test_state_ambiguous_when_enum_has_multiple_values() -> None:
    asset = _grounded(
        _fact(fact_id="f:11", conditions=["订单已支付时"], statement="订单已支付时可以取消。"),
        interfaces=[
            {
                "interface_id": "api:POST:/api/orders/:id/cancel",
                "method": "POST",
                "path": "/api/orders/:id/cancel",
                "technical_declarations": [
                    {
                        "property_path": ["Order", "status"],
                        "description": "订单状态：已支付",
                        "constraints": {"enum": ["PAID", "PAY_SUCCESS"]},
                    }
                ],
            }
        ],
    )
    frame = _frame(asset)
    assert frame["technical_grounding"]["state_value_refs"] == []
    state_receipts = [r for r in frame["grounding_receipts"] if r["grounding_type"] == "STATE"]
    assert state_receipts[0]["status"] == "AMBIGUOUS"
    assert state_receipts[0]["reason_code"] == "MULTIPLE_STATE_VALUE_CANDIDATES"


def test_scope_ownership_activates_own_structure() -> None:
    asset = _grounded(_fact(fact_id="f:12", ownership="只能查询自己的订单"))
    frame = _frame(asset)
    assert frame["scope"]["ownership_relation"]["kind"] == "OWN"
    assert frame["scope"]["ownership_relation"]["target"] == "current_actor"
    assert frame["scope"]["ownership_relation"]["raw"] == "只能查询自己的订单"
    scope_receipt = next(r for r in frame["grounding_receipts"] if r["grounding_type"] == "SCOPE")
    assert scope_receipt["status"] == "GROUNDED"
    assert scope_receipt["reason_code"] == "OWNERSHIP_PHRASE_STRUCTURED"


def test_scope_organization_marker() -> None:
    asset = _grounded(_fact(fact_id="f:13", ownership="只能查看所属门店的订单"))
    frame = _frame(asset)
    assert frame["scope"]["ownership_relation"]["kind"] == "OWN"
    assert frame["scope"]["ownership_relation"]["target"] == "current_actor_organization"


def test_scope_unknown_without_marker() -> None:
    asset = _grounded(_fact(fact_id="f:14", ownership="相关数据"))
    frame = _frame(asset)
    assert frame["scope"]["ownership_relation"] == {"raw": "相关数据"}
    scope_receipt = next(r for r in frame["grounding_receipts"] if r["grounding_type"] == "SCOPE")
    assert scope_receipt["status"] == "UNKNOWN"
    assert scope_receipt["reason_code"] == "OWNERSHIP_RELATION_UNRESOLVED"


def test_every_grounded_binding_has_a_receipt() -> None:
    asset = _grounded(
        _fact(fact_id="f:15", ownership="自己的订单"),
        permission_matrix=[
            {"permission_id": "p1", "role": "买家", "resource": "/api/orders",
             "actions": ["get"], "decision": "allow", "scope": "own"}
        ],
        interfaces=[
            {"interface_id": "api:GET:/api/orders", "method": "GET", "path": "/api/orders",
             "summary": "查询订单列表", "description": ""},
        ],
    )
    frame = _frame(asset)
    for receipt in frame["grounding_receipts"]:
        if receipt["status"] == "GROUNDED":
            assert receipt["reason_code"]
            assert receipt["candidate_ref"]
    assert frame["technical_grounding"]["permission_scope"] == "own"
    ledger = asset["chinese_semantic_grounding_ledger"]
    assert ledger["schema"] == CHINESE_SEMANTIC_GROUNDING_SCHEMA
    assert ledger["closure"]["grounded_frame_count"] == 1
    assert ledger["closure"]["similarity_merge_allowed"] is False
