"""P0-D: mainline activation — grounded frames emit Behavior IR relations.

Covers the end-to-end activation of the P0-A channel: evidence-grounded
frames contribute owns/permits/denies relations (canonical node ids, scope
aligned, deduped against legacy), every contribution carries the frame id in
source_refs and a grounding receipt, and assets without grounded evidence
keep the legacy IR bit-identical.
"""

from __future__ import annotations

import importlib

from ai_test_asset_center.behavior_ir_core import (
    build_behavior_ir_from_knowledge_asset,
    validate_behavior_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.composition import (
    build_enterprise_business_knowledge_asset,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_clause_parser import (
    parse_chinese_clause_trees,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_context_envelope import (
    build_chinese_semantic_context_envelopes,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_context_resolver import (
    resolve_chinese_semantic_context,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_frame_compiler import (
    enrich_frames_with_clause_structure,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_grounding import (
    ground_semantic_frames,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_ledger_adapter import (
    project_business_facts_to_semantic_frames,
)
from ai_test_asset_center.obligation_compiler import (
    compile_obligations_from_behavior_ir,
)


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _heading(block_id: str, text: str, order: int) -> dict:
    return {
        "block_id": block_id, "type": "HEADING", "parent_id": "", "order": order,
        "region": "body", "level": 1, "text": text,
        "source_locator": f"r.docx#block={order}",
    }


def _paragraph(block_id: str, text: str, order: int, parent: str) -> dict:
    return {
        "block_id": block_id, "type": "PARAGRAPH", "parent_id": parent, "order": order,
        "region": "body", "text": text, "source_locator": f"r.docx#block={order}",
    }


def _fact(*, fact_id: str, statement: str, block_id: str, actor: str = "买家",
          action: str = "查询", modality: str = "MAY", ownership: str = "") -> dict:
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
        "conditions": [],
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
        "raw_statement": statement,
        "source_spans": [
            {
                "evidence_address": {
                    "source_id": "s1",
                    "locator": f"r.docx#block={block_id}",
                    "document_block_id": block_id,
                    "block_type": "PARAGRAPH",
                },
                "quote": statement,
            }
        ],
        "confidence": 1.0,
        "status": "ACCEPTED",
        "ambiguities": [],
        "critical": True,
        "derivation": "structure_first_explicit_fact_compiler",
    }


def _pipeline_asset(*, facts: list[dict], blocks: list[dict],
                    permission_rows: list[dict] | None = None,
                    interfaces: list[dict] | None = None,
                    entities: list[dict] | None = None) -> dict:
    asset = {
        "document_structure_assets": {
            "items": [{"source_id": "s1", "filename": "r.docx", "blocks": blocks}]
        },
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v2",
            "fact_authority": "original_chinese_source_span",
            "items": facts,
        },
        "enterprise_understanding_model": {
            "actors": [{"actor_id": "business_actor:buyer", "name": "买家"}],
            "business_objects": [
                {"object_id": "business_object:order", "name": "订单", "aliases": ["orders"]}
            ],
        },
    }
    if permission_rows is not None:
        asset["permission_matrix"] = permission_rows
    if interfaces is not None:
        asset["interfaces"] = interfaces
    if entities is not None:
        asset["entities"] = entities
    asset = project_business_facts_to_semantic_frames(asset)
    asset = build_chinese_semantic_context_envelopes(asset)
    asset = parse_chinese_clause_trees(asset)
    asset = enrich_frames_with_clause_structure(asset)
    asset = resolve_chinese_semantic_context(asset)
    return ground_semantic_frames(asset)


def test_grounded_frame_activates_behavior_ir_channel() -> None:
    h1 = _heading("h1", "订单管理", 1)
    p1 = _paragraph("p1", "买家可以查询自己的订单。", 2, "h1")
    asset = _pipeline_asset(
        facts=[_fact(fact_id="f:q", statement="买家可以查询自己的订单。", block_id="p1",
                     ownership="自己的订单")],
        blocks=[h1, p1],
        permission_rows=[
            {"permission_id": "p1", "role": "买家", "resource": "/api/orders",
             "actions": ["get"], "decision": "allow", "scope": "own"}
        ],
        interfaces=[
            {"interface_id": "api:GET:/api/orders", "method": "GET", "path": "/api/orders",
             "summary": "查询订单列表", "description": "", "entity_refs": ["orders"]}
        ],
        entities=[{"name": "orders", "kind": "business_object"}],
    )
    frame = asset["chinese_semantic_frame_ledger"]["items"][0]
    assert frame["technical_grounding"]["status"] == "GROUNDED"

    ir = build_behavior_ir_from_knowledge_asset(asset)
    assert validate_behavior_ir(ir) == []
    assert ir["relations"]
    relation_types = {row["relation_type"] for row in ir["relations"]}
    assert "permits" in relation_types
    assert "owns" in relation_types

    # Every frame-contributed relation carries the frame id in source_refs.
    frame_relations = [
        row
        for row in ir["relations"]
        if any(
            _text(ref.get("kind")) == "chinese_semantic_frame"
            for ref in row.get("source_refs", [])
        )
    ]
    assert frame_relations
    for row in frame_relations:
        frame_refs = [
            _text(ref.get("frame_id"))
            for ref in row.get("source_refs", [])
            if ref.get("kind") == "chinese_semantic_frame"
        ]
        assert frame_refs
        assert frame_refs[0] == frame["frame_id"]

    # The projection receipt is observable on the IR.
    receipt = ir["semantic_frame_projection_receipt"]
    assert receipt["payload"]["frames_considered"] == 1
    assert receipt["payload"]["added_count"] + receipt["payload"]["deduped_count"] >= 1


def test_relation_contributions_are_receipted_and_deduped() -> None:
    h1 = _heading("h1", "订单管理", 1)
    p1 = _paragraph("p1", "买家可以查询自己的订单。", 2, "h1")
    asset = _pipeline_asset(
        facts=[_fact(fact_id="f:q", statement="买家可以查询自己的订单。", block_id="p1",
                     ownership="自己的订单")],
        blocks=[h1, p1],
        permission_rows=[
            {"permission_id": "p1", "role": "买家", "resource": "/api/orders",
             "actions": ["get"], "decision": "allow", "scope": "own"}
        ],
        interfaces=[
            {"interface_id": "api:GET:/api/orders", "method": "GET", "path": "/api/orders",
             "summary": "查询订单列表", "description": "", "entity_refs": ["orders"]}
        ],
        entities=[{"name": "orders", "kind": "business_object"}],
    )
    frame = asset["chinese_semantic_frame_ledger"]["items"][0]
    grounded = [r for r in frame["grounding_receipts"] if r["status"] == "GROUNDED"]
    assert grounded
    for receipt in grounded:
        assert receipt["reason_code"]
        assert receipt["candidate_ref"]

    # The frame contributes exactly the receipted grounded relations; the
    # permits duplicate is deduped against the legacy permission relation.
    ir = build_behavior_ir_from_knowledge_asset(asset)
    receipt = ir["semantic_frame_projection_receipt"]["payload"]
    assert receipt["added_count"] + receipt["deduped_count"] == receipt["contribution_count"]

    # No duplicate permits: exactly one permits per (actor, operation).
    permits = [
        (row["from_ref"], row["to_ref"])
        for row in ir["relations"]
        if row["relation_type"] == "permits"
    ]
    assert len(permits) == len(set(permits))


def test_legacy_ir_unchanged_without_grounded_evidence() -> None:
    # No permission rows / interfaces / entities → nothing grounds → the IR
    # is bit-identical to the legacy-only build.
    h1 = _heading("h1", "订单管理", 1)
    p1 = _paragraph("p1", "买家可以查询自己的订单。", 2, "h1")
    asset = _pipeline_asset(
        facts=[_fact(fact_id="f:q", statement="买家可以查询自己的订单。", block_id="p1",
                     ownership="自己的订单")],
        blocks=[h1, p1],
        permission_rows=[],
        interfaces=[],
        entities=[],
    )
    frame = asset["chinese_semantic_frame_ledger"]["items"][0]
    # The entity mention grounds via the understanding model (PARTIAL), but no
    # IR node exists for it and no operation/actor evidence fires — the IR
    # channel stays inert and the legacy output is bit-identical.
    assert frame["technical_grounding"]["status"] == "PARTIAL"

    ir = build_behavior_ir_from_knowledge_asset(asset)
    asset_plain = dict(asset)
    asset_plain.pop("chinese_semantic_frame_ledger", None)
    ir_plain = build_behavior_ir_from_knowledge_asset(asset_plain)
    assert ir["relations"] == ir_plain["relations"]
    assert ir["invariants"] == ir_plain["invariants"]


def test_composition_root_wires_grounding() -> None:
    module = importlib.import_module(
        "ai_test_asset_center.enterprise_knowledge_center.composition"
    )
    assert callable(module.ground_semantic_frames)
    assert callable(build_enterprise_business_knowledge_asset)


def test_grounded_chinese_time_window_reaches_temporal_obligation() -> None:
    h1 = _heading("h1", "订单管理", 1)
    statement = "提交后24小时内，买家可以查询自己的订单。"
    p1 = _paragraph("p1", statement, 2, "h1")
    asset = _pipeline_asset(
        facts=[
            _fact(
                fact_id="f:timed",
                statement=statement,
                block_id="p1",
                ownership="自己的订单",
            )
        ],
        blocks=[h1, p1],
        permission_rows=[
            {
                "permission_id": "p1",
                "role": "买家",
                "resource": "/api/orders",
                "actions": ["get"],
                "decision": "allow",
                "scope": "own",
            }
        ],
        interfaces=[
            {
                "interface_id": "api:GET:/api/orders",
                "method": "GET",
                "path": "/api/orders",
                "summary": "查询订单列表",
                "description": "",
                "entity_refs": ["orders"],
            }
        ],
        entities=[{"name": "orders", "kind": "business_object"}],
    )
    frame = asset["chinese_semantic_frame_ledger"]["items"][0]
    assert frame["technical_grounding"]["status"] == "GROUNDED"
    assert frame["time_constraints"][0]["window_ms"] == 86_400_000

    ir = build_behavior_ir_from_knowledge_asset(asset)
    assert validate_behavior_ir(ir) == []
    temporal_invariants = [
        row
        for row in ir["invariants"]
        if row.get("frame_family_evidence", {}).get("frame_type")
        == "TIME_WINDOW_CONSTRAINT"
    ]
    assert len(temporal_invariants) == 1
    invariant = temporal_invariants[0]
    assert invariant["expression"]["window_ms"] == 86_400_000
    assert len(invariant["operation_refs"]) == 1
    assert any(
        ref.get("kind") == "chinese_semantic_time_constraint"
        for ref in invariant["source_refs"]
    )

    compiled = compile_obligations_from_behavior_ir(ir)
    temporal = [
        row for row in compiled["obligations"]
        if row.get("risk_family") == "temporal"
    ]
    assert temporal
    assert temporal[0]["property"]["expression"]["window_ms"] == 86_400_000
    assert "temporal_window" in temporal[0]["required_observers"]
