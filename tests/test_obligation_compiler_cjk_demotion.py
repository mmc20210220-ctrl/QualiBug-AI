"""P0-E phase-3: obligation compiler family CJK token demotion.

Contract:
- A grounded frame's structured frame_type is the SSOT risk-family signal
  (``_FRAME_TYPE_FAMILY``); when it is present the legacy kind-token family
  detection (including CJK tokens 库存/金额/隐私/过期/可见/状态/因果/后置)
  does not run.
- Without grounded frame family evidence, the legacy detection runs and CJK
  token hits are counted as ``CJK_FAMILY_TOKEN_FALLBACK`` on the P0-E
  ``legacy_semantic_fallback_receipt`` — but ONLY when a frame ledger exists
  (no ledger → no counts, plain legacy semantics).
- Operation-level CJK ownership language markers (自己的/本人/归属/只能查询)
  and CJK privacy policy markers (from _ABSENT/_MASK_MARKERS Chinese items)
  are counted candidate hints (OWNERSHIP_LANGUAGE_CJK_CANDIDATE /
  PRIVACY_POLICY_CJK_CANDIDATE) under the same ledger gate.
"""

from __future__ import annotations

from ai_test_asset_center.behavior_ir_core import (
    build_behavior_ir_from_knowledge_asset,
    validate_behavior_ir,
)
from ai_test_asset_center.obligation_compiler import (
    compile_obligations_from_behavior_ir,
)
from ai_test_asset_center.obligation_compiler_base import _FRAME_TYPE_FAMILY
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


def _base_asset(*, with_ledger: bool = True) -> dict:
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
             "summary": "查询订单列表", "description": "买家只能查询自己的订单。",
             "entity_refs": ["orders"]}
        ],
        entities=[{"name": "orders", "kind": "business_object"}],
    )
    if not with_ledger:
        asset.pop("chinese_semantic_frame_ledger", None)
    return asset


def _rule(statement: str, *, rule_id: str = "zh_business:q", kind: str = "business_rule") -> dict:
    return {
        "rule_id": rule_id,
        "statement": statement,
        "kind": kind,
        "entity": "orders",
        "source_id": "s1",
        "source_locator": "r.docx#block=p1",
        "confidence": 0.9,
    }


def _invariant(ir: dict, statement: str) -> dict:
    matches = [r for r in ir["invariants"] if r.get("description") == statement]
    assert len(matches) == 1, f"expected one invariant, got {len(matches)}"
    return matches[0]


def _compile(ir: dict) -> dict:
    # Bind every invariant to the corpus operation so family detection is
    # observable (conservation etc. fall back to observes obligations).
    op_id = ir["operations"][0]["id"]
    for inv in ir["invariants"]:
        inv["operation_refs"] = [op_id]
    return compile_obligations_from_behavior_ir(ir)


# ── 1. frame_family_evidence on the invariant ──

def test_grounded_rule_invariant_carries_frame_family_evidence() -> None:
    asset = _base_asset()
    asset["rule_library"] = [_rule("买家可以查询自己的订单。")]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    assert validate_behavior_ir(ir) == []
    inv = _invariant(ir, "买家可以查询自己的订单。")
    evidence = inv["frame_family_evidence"]
    assert evidence["grounded"] is True
    assert evidence["frame_type"]
    assert evidence["frame_id"]


def test_no_frame_rule_has_no_family_evidence() -> None:
    asset = _base_asset()
    asset["rule_library"] = [_rule("库存数量必须准确。", rule_id="zh_business:zz")]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    inv = _invariant(ir, "库存数量必须准确。")
    assert "frame_family_evidence" not in inv


def test_no_ledger_rule_has_no_family_evidence() -> None:
    asset = _base_asset(with_ledger=False)
    asset["rule_library"] = [_rule("买家可以查询自己的订单。")]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    inv = _invariant(ir, "买家可以查询自己的订单。")
    assert "frame_family_evidence" not in inv


# ── 2. family detection: SSOT frame_type wins ──

def test_frame_type_family_mapping_is_structured() -> None:
    # The mapping is the schema-typed SSOT signal (industry-neutral).
    assert _FRAME_TYPE_FAMILY["PERMISSION_RULE"] == "visibility"
    assert _FRAME_TYPE_FAMILY["QUANTITY_CONSTRAINT"] == "conservation"
    assert _FRAME_TYPE_FAMILY["TIME_WINDOW_CONSTRAINT"] == "temporal"
    assert _FRAME_TYPE_FAMILY["VALIDATION_RULE"] == "validation"
    assert _FRAME_TYPE_FAMILY["STATE_TRANSITION"] == "state"


def test_frame_type_decides_family_over_cjk_kind_tokens() -> None:
    asset = _base_asset()
    asset["rule_library"] = [_rule("买家可以查询自己的订单。")]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    inv = _invariant(ir, "买家可以查询自己的订单。")
    # Grounded PERMISSION_RULE frame → visibility family (SSOT), even though
    # the expression kind carries no family token.
    inv["frame_family_evidence"] = {
        "frame_id": "fr-x", "frame_type": "PERMISSION_RULE", "grounded": True,
    }
    out = _compile(ir)
    gap_families = {g.get("risk_family") for g in out.get("coverage_gaps", [])}
    # Single-actor privacy/visibility obligations demote to
    # BLOCKED_MISSING_ACTOR_PAIR gaps with the family retained.
    assert "visibility" in gap_families


def test_ssot_family_suppresses_cjk_family_counting() -> None:
    asset = _base_asset()
    asset["rule_library"] = [_rule("买家可以查询自己的订单。")]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    inv = _invariant(ir, "买家可以查询自己的订单。")
    # Grounded frame with a mapped frame_type + CJK kind token → SSOT wins,
    # no CJK fallback counting.
    inv["frame_family_evidence"] = {
        "frame_id": "fr-x", "frame_type": "TIME_WINDOW_CONSTRAINT", "grounded": True,
    }
    inv["expression"] = dict(inv.get("expression", {}))
    inv["expression"]["kind"] = "库存规则"
    _compile(ir)
    kinds = ir["legacy_semantic_fallback_receipt"]["kind_counts"]
    assert "CJK_FAMILY_TOKEN_FALLBACK" not in kinds


def test_cjk_kind_token_falls_back_and_is_counted() -> None:
    asset = _base_asset()
    asset["rule_library"] = [_rule("库存数量必须准确。", rule_id="zh_business:zz")]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    inv = _invariant(ir, "库存数量必须准确。")
    inv["expression"] = dict(inv.get("expression", {}))
    inv["expression"]["kind"] = "库存规则"
    out = _compile(ir)
    families = {o.get("risk_family") for o in out.get("obligations", [])}
    assert "conservation" in families
    kinds = ir["legacy_semantic_fallback_receipt"]["kind_counts"]
    assert kinds.get("CJK_FAMILY_TOKEN_FALLBACK", 0) >= 1


# ── 3. counting gated on the frame ledger ──

def test_cjk_counts_are_gated_on_frame_ledger() -> None:
    asset = _base_asset(with_ledger=False)
    asset["rule_library"] = [_rule("库存数量必须准确。", rule_id="zh_business:zz")]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    inv = _invariant(ir, "库存数量必须准确。")
    inv["expression"] = dict(inv.get("expression", {}))
    inv["expression"]["kind"] = "库存规则"
    _compile(ir)
    kinds = ir["legacy_semantic_fallback_receipt"]["kind_counts"]
    assert "CJK_FAMILY_TOKEN_FALLBACK" not in kinds
    assert "OWNERSHIP_LANGUAGE_CJK_CANDIDATE" not in kinds
    assert "PRIVACY_POLICY_CJK_CANDIDATE" not in kinds


def test_ir_without_receipt_keeps_plain_legacy_semantics() -> None:
    # An IR built before P0-E (no receipt) is treated as no-ledger: no CJK
    # counting and the family detection stays the legacy token detection.
    asset = _base_asset(with_ledger=False)
    asset["rule_library"] = [_rule("库存数量必须准确。", rule_id="zh_business:zz")]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    ir.pop("legacy_semantic_fallback_receipt", None)
    inv = _invariant(ir, "库存数量必须准确。")
    inv["expression"] = dict(inv.get("expression", {}))
    inv["expression"]["kind"] = "库存规则"
    out = _compile(ir)
    families = {o.get("risk_family") for o in out.get("obligations", [])}
    assert "conservation" in families


# ── 4. ownership + privacy CJK marker counting ──

def test_ownership_language_cjk_marker_is_counted() -> None:
    asset = _base_asset()
    asset["rule_library"] = [_rule("买家可以查询自己的订单。")]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    _compile(ir)
    kinds = ir["legacy_semantic_fallback_receipt"]["kind_counts"]
    # The operation's description carries 只能查询自己的订单 → CJK ownership
    # language marker hits.
    assert kinds.get("OWNERSHIP_LANGUAGE_CJK_CANDIDATE", 0) >= 1


def test_privacy_policy_cjk_marker_is_counted() -> None:
    asset = _base_asset()
    asset["rule_library"] = [_rule("买家可以查询自己的订单。")]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    inv = _invariant(ir, "买家可以查询自己的订单。")
    inv["expression"] = dict(inv.get("expression", {}))
    inv["expression"]["raw"] = "响应不得暴露用户手机号，需脱敏。"
    _compile(ir)
    kinds = ir["legacy_semantic_fallback_receipt"]["kind_counts"]
    assert kinds.get("PRIVACY_POLICY_CJK_CANDIDATE", 0) >= 1


def test_receipt_kind_counts_remain_deterministic() -> None:
    asset = _base_asset()
    asset["rule_library"] = [_rule("买家可以查询自己的订单。")]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    _compile(ir)
    receipt = ir["legacy_semantic_fallback_receipt"]
    assert receipt["frame_ledger_present"] is True
    assert isinstance(receipt["kind_counts"], dict)
    assert receipt["kind_counts"]
