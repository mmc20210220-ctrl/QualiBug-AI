"""P0-B: mainline wiring — envelope → clause trees → frame enrichment on the
knowledge asset, and the zero-regression contract for Behavior IR.

Covers the closed loop (compile → frames → envelope → clause trees →
enrichment) and the equivalence proof: building Behavior IR from an asset
with the P0-B layers produces exactly the same relations/invariants as
without them (frames stay ungrounded, so nothing new is emitted).
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
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_ledger_adapter import (
    project_business_facts_to_semantic_frames,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.structured_fact_compiler import (
    compile_structure_first_business_facts,
)


def _block(block_id: str, text: str, order: int, block_type: str = "PARAGRAPH") -> dict:
    return {
        "block_id": block_id,
        "type": block_type,
        "region": "body",
        "text": text,
        "order": order,
        "source_locator": f"rules.docx#block={order}",
        "evidence_address": {
            "source_id": "source:rules",
            "source_locator": f"rules.docx#block={order}",
            "address_kind": "EXACT_SOURCE_LOCATOR",
        },
    }


def _existing_fact(statement: str, actor_refs: list[str] | None = None) -> dict:
    return {
        "fact_id": "fact:existing",
        "kind": "RULE",
        "language": "zh-CN",
        "subject": {
            "actor_refs": list(actor_refs or []),
            "entity_refs": ["订单"],
            "resolution_evidence": [],
        },
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


def _base_compiled_asset() -> dict:
    blocks = [
        _block("b1", "订单审批通过后，系统生成出库单并扣减库存。", 1),
        _block("b2", "非管理员不得修改或删除已发布内容。", 2),
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
            "items": [_existing_fact("订单审批通过后，系统生成出库单并扣减库存。")],
        },
        # The full composition root aggregates this key; the P0-B stages read
        # it, so the test asset carries it like the composition output does.
        "document_structure_assets": {
            "items": [
                {
                    "source_id": "source:rules",
                    "filename": "rules.docx",
                    "blocks": blocks,
                }
            ]
        },
        "enterprise_comprehension_gate": {"status": "PASS", "entry_allowed": True},
        "coverage_gaps": [],
        "summary": {},
        "governance": {},
    }
    return compile_structure_first_business_facts(asset, [source])


def test_p0b_stages_are_closed_loop_on_the_asset() -> None:
    asset = project_business_facts_to_semantic_frames(_base_compiled_asset())
    asset = build_chinese_semantic_context_envelopes(asset)
    asset = parse_chinese_clause_trees(asset)
    asset = enrich_frames_with_clause_structure(asset)
    asset = resolve_chinese_semantic_context(asset)

    assert asset["chinese_semantic_context_envelopes"]["schema"] == (
        "qualibug.chinese-semantic-context-envelope.v1"
    )
    assert asset["chinese_clause_tree_ledger"]["closure"]["tree_count"] >= 1
    frames = asset["chinese_semantic_frame_ledger"]["items"]
    assert frames
    assert all("clause_structure" in row for row in frames)
    assert all("context_resolution" in row for row in frames)
    receipt = asset["chinese_semantic_frame_ledger"]["enrichment_receipt"]
    assert receipt["enriched_count"] >= 1
    assert asset["chinese_semantic_context_resolution_ledger"]["receipt"][
        "raw_text_never_rewritten"
    ] is True


def test_behavior_ir_equivalence_with_and_without_p0b_layers() -> None:
    # Zero-regression contract: the P0-B/P0-C layers only enrich frames; while
    # frames stay ungrounded the Behavior IR must be bit-identical.
    asset_plain = _base_compiled_asset()
    asset_p0b = _base_compiled_asset()
    asset_p0b = project_business_facts_to_semantic_frames(asset_p0b)
    asset_p0b = build_chinese_semantic_context_envelopes(asset_p0b)
    asset_p0b = parse_chinese_clause_trees(asset_p0b)
    asset_p0b = enrich_frames_with_clause_structure(asset_p0b)
    asset_p0b = resolve_chinese_semantic_context(asset_p0b)

    ir_plain = build_behavior_ir_from_knowledge_asset(asset_plain)
    ir_p0b = build_behavior_ir_from_knowledge_asset(asset_p0b)

    assert validate_behavior_ir(ir_plain) == []
    assert validate_behavior_ir(ir_p0b) == []
    assert ir_p0b["relations"] == ir_plain["relations"]
    assert ir_p0b["invariants"] == ir_plain["invariants"]
    assert ir_p0b["actors"] == ir_plain["actors"]
    assert ir_p0b["entities"] == ir_plain["entities"]
    assert ir_p0b["operations"] == ir_plain["operations"]


def test_composition_root_wires_p0b_stages() -> None:
    module = importlib.import_module(
        "ai_test_asset_center.enterprise_knowledge_center.composition"
    )
    assert callable(module.build_chinese_semantic_context_envelopes)
    assert callable(module.parse_chinese_clause_trees)
    assert callable(module.enrich_frames_with_clause_structure)
    assert callable(module.resolve_chinese_semantic_context)
    assert callable(build_enterprise_business_knowledge_asset)
