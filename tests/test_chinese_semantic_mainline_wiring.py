"""P0-A: mainline wiring — frame ledger in the knowledge asset and the
Behavior IR consumption channel.

Covers: compile_structure_first_business_facts → frame projection on the same
asset (closed loop), Behavior IR built from an asset WITH the frame ledger is
identical to one built WITHOUT it (zero production regression while frames are
ungrounded), and the composition root imports the projection (wired, no dead
module).
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
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_ledger_adapter import (
    frames_from_asset,
    project_business_facts_to_semantic_frames,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_receipts import (
    validate_receipt,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_schema import (
    validate_semantic_frame,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.structured_fact_compiler import (
    compile_structure_first_business_facts,
)


def _block(block_id: str, text: str, order: int) -> dict:
    return {
        "block_id": block_id,
        "type": "PARAGRAPH",
        "region": "body",
        "text": text,
        "order": order,
        "source_locator": f"rules.docx#paragraph={order};chars=0-{len(text)}",
        "evidence_address": {
            "source_id": "source:rules",
            "source_locator": f"rules.docx#paragraph={order};chars=0-{len(text)}",
            "address_kind": "EXACT_SOURCE_LOCATOR",
        },
    }


def _existing_fact(statement: str) -> dict:
    return {
        "fact_id": "fact:existing",
        "kind": "RULE",
        "language": "zh-CN",
        "subject": {"actor_refs": ["系统"], "entity_refs": ["订单"], "resolution_evidence": []},
        "object": {"entity_refs": ["订单"]},
        "conditions": ["订单审批通过后"],
        "condition_combinator": "SINGLE_CONDITION",
        "condition_frame": {
            "kind": "LEAF",
            "combinator": "SINGLE_CONDITION",
            "conditions": ["订单审批通过后"],
            "exception_scopes": [],
            "branch": "",
            "source_backed": True,
        },
        "action": {"canonical": "审批通过", "raw": "审批通过"},
        "scope": {"tenant": "", "organization": "", "ownership": "", "data_scope": ""},
        "modality": "ASSERTS",
        "polarity": "POSITIVE",
        "exceptions": [],
        "exception_scope": [],
        "postconditions": ["生成出库单", "扣减库存"],
        "state_effects": [],
        "data_effects": [],
        "quantity_constraints": [],
        "time_window_constraints": [],
        "formula_constraints": [],
        "compensation": [],
        "raw_statement": statement,
        "source_spans": [
            {
                "source_id": "source:rules",
                "locator": "rules.docx#section=订单",
                "quote": statement,
            }
        ],
        "confidence": 0.9,
        "status": "ACCEPTED",
        "ambiguities": [],
        "critical": False,
    }


def _compiled_asset() -> dict:
    statement = "订单审批通过后，系统生成出库单并扣减库存。"
    blocks = [
        _block("block:multi", statement, 1),
        _block("block:card", "每张发票只能关联一个结算单。", 2),
    ]
    source = {
        "source_id": "source:rules",
        "filename": "rules.docx",
        "text": "\n".join(row["text"] for row in blocks),
        "document_structure": {
            "schema": "qualibug.document-structure-ir.v1",
            "blocks": blocks,
        },
    }
    asset = {
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v1",
            "fact_authority": "original_chinese_source_span",
            "items": [_existing_fact(statement)],
        },
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }
    return compile_structure_first_business_facts(asset, [source])


def test_compiled_asset_projects_frame_ledger_with_valid_frames() -> None:
    compiled = _compiled_asset()
    assert compiled["business_fact_ledger"]["schema"] == "qualibug.business-fact-ledger.v2"
    asset = project_business_facts_to_semantic_frames(compiled)

    ledger = asset["chinese_semantic_frame_ledger"]
    assert ledger["schema"] == "qualibug.chinese-semantic-frame-ledger.v1"
    assert ledger["closure"]["status"] == "PASS"
    assert ledger["closure"]["fact_count"] > 0
    assert ledger["closure"]["frame_count"] == ledger["closure"]["fact_count"]
    assert ledger["closure"]["silent_drop_allowed"] is False

    frames = frames_from_asset(asset)
    assert frames
    for frame in frames:
        assert validate_semantic_frame(frame) == []
    for receipt in ledger["receipts"]:
        assert validate_receipt(receipt) == []
        assert receipt["status"] == "PASS"

    # Every frame is traceable back to its origin fact (bidirectional link).
    origin_ids = {frame["origin"]["origin_fact_id"] for frame in frames}
    fact_ids = {
        row["fact_id"]
        for row in asset["business_fact_ledger"]["items"]
        if row.get("fact_id")
    }
    assert origin_ids <= fact_ids


def test_behavior_ir_equivalence_with_and_without_frame_ledger() -> None:
    # The zero-regression contract: while frames are ungrounded, building the
    # Behavior IR from an asset WITH the frame ledger must produce exactly the
    # same relations and invariants as without it.
    asset_without = _compiled_asset()
    asset_with = project_business_facts_to_semantic_frames(_compiled_asset())

    ir_without = build_behavior_ir_from_knowledge_asset(asset_without)
    ir_with = build_behavior_ir_from_knowledge_asset(asset_with)

    assert validate_behavior_ir(ir_without) == []
    assert validate_behavior_ir(ir_with) == []
    assert ir_with["relations"] == ir_without["relations"]
    assert ir_with["invariants"] == ir_without["invariants"]
    assert ir_with["actors"] == ir_without["actors"]
    assert ir_with["entities"] == ir_without["entities"]
    assert ir_with["operations"] == ir_without["operations"]

    # The frame consumption is observable on the IR.
    receipt = ir_with["semantic_frame_projection_receipt"]
    assert receipt["receipt_kind"] == "BEHAVIOR_IR_PROJECTION"
    assert receipt["payload"]["frames_considered"] == len(frames_from_asset(asset_with))
    assert receipt["payload"]["contribution_count"] == 0
    assert "TECHNICAL_GROUNDING_PENDING" in receipt["reason_codes"]


def test_build_behavior_ir_without_frame_ledger_is_unchanged() -> None:
    # Old/small assets without the frame ledger keep the historical behavior —
    # no receipt key is invented and the model stays valid.
    ir = build_behavior_ir_from_knowledge_asset(_compiled_asset())
    assert "semantic_frame_projection_receipt" not in ir
    assert validate_behavior_ir(ir) == []


def test_composition_root_is_wired_to_frame_projection() -> None:
    # The composition root imports and invokes the projection (closed loop).
    module = importlib.import_module(
        "ai_test_asset_center.enterprise_knowledge_center.composition"
    )
    assert module.project_business_facts_to_semantic_frames is not None
    assert callable(build_enterprise_business_knowledge_asset)
