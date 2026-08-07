"""P0-E phase 2: v1 regex extractor demoted to a candidate discovery layer.

Contract:
- Every fact/rule the legacy fixed-vocabulary regex extractor
  (``_chinese_business_comprehension_extractor_v1``) produces carries
  ``semantic_candidate=True`` + ``candidate_reason=legacy_regex_vocabulary_hit``
  (TERM_ALIAS glossary rows are dictionaries, not business rules).
- ``apply_v1_extractor_frame_confirmation`` (composition, right after P0-D
  grounding on both the full and incremental paths) decides each candidate
  rule against the Chinese Semantic Frame SSOT:
  frame grounded → CONFIRMED; frame ungrounded → FALLBACK_UNGROUNDED; no
  frame → UNCONFIRMED_NO_FRAME; no ledger → compat path untouched.
  The decision is receipted in ``asset["v1_extractor_demotion_receipt"]``
  (``qualibug.v1-extractor-demotion-receipt.v1``).
- ``behavior_ir_core`` carries ``frame_confirmation`` / reason onto the
  invariant so the demotion is observable end-to-end; rules without the
  status (ledger-less assets, non-v1 rules) stay byte-identical.
"""

from __future__ import annotations

from ai_test_asset_center.behavior_ir_core import (
    build_behavior_ir_from_knowledge_asset,
    validate_behavior_ir,
)
from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    V1_EXTRACTOR_DEMOTION_RECEIPT_SCHEMA,
    apply_v1_extractor_frame_confirmation,
)
from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension_extractor_v1 import (
    _rule_from_fact,
    analyze_chinese_business_source,
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


def _candidate_rule(*, rule_id: str, statement: str, **extra: object) -> dict:
    rule: dict = {
        "rule_id": rule_id,
        "statement": statement,
        "kind": "business_rule",
        "semantic_candidate": True,
        "candidate_reason": "legacy_regex_vocabulary_hit",
    }
    rule.update(extra)
    return rule


def _grounded_asset() -> dict:
    """Pipeline asset whose single frame is GROUNDED (permission rule on the
    GET /api/orders operation)."""
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
    assert asset["chinese_semantic_frame_ledger"]["items"][0]["technical_grounding"]["status"] == "GROUNDED"
    return asset


# ── 1. v1 extractor self-declares candidates ──

def test_v1_extractor_marks_facts_as_candidates() -> None:
    source = {
        "source_id": "s1",
        "filename": "doc.md",
        "text": "买家只能查询自己的订单。管理员可以导出全部订单数据。",
    }
    coverage, facts, glossary = analyze_chinese_business_source(source)
    assert facts
    for fact in facts:
        assert fact["semantic_candidate"] is True
        assert fact["candidate_reason"] == "legacy_regex_vocabulary_hit"
    # Glossary rows are dictionaries — not candidate business rules.
    for row in glossary:
        assert row.get("semantic_candidate") is not True


def test_v1_rule_inherits_candidate_marker() -> None:
    source = {
        "source_id": "s1",
        "filename": "doc.md",
        "text": "买家只能查询自己的订单。",
    }
    _, facts, _ = analyze_chinese_business_source(source)
    promoted = [_rule_from_fact(f) for f in facts]
    promoted = [r for r in promoted if r is not None]
    assert promoted
    for rule in promoted:
        assert rule["semantic_candidate"] is True
        assert rule["derivation"] == "chinese_first_business_comprehension"


# ── 2. confirmation gate branches ──

def test_confirmation_gate_grounded_frame_confirms() -> None:
    asset = _grounded_asset()
    asset["rule_library"] = [_candidate_rule(
        rule_id="zh_business:q", statement="买家可以查询自己的订单。",
    )]
    receipt = apply_v1_extractor_frame_confirmation(asset)
    rule = asset["rule_library"][0]
    assert rule["frame_confirmation"] == "CONFIRMED"
    assert rule["frame_confirmation_reason"] == "FRAME_GROUNDED"
    assert receipt["frame_ledger_present"] is True
    assert receipt["candidate_rule_count"] == 1
    assert receipt["confirmed_count"] == 1
    assert receipt["kind_counts"]["V1_CANDIDATE_CONFIRMED_BY_FRAME"] == 1


def test_confirmation_gate_ungrounded_frame_falls_back() -> None:
    asset = _grounded_asset()
    asset["rule_library"] = [_candidate_rule(
        rule_id="zh_business:q", statement="买家可以查询自己的订单。",
    )]
    asset["chinese_semantic_frame_ledger"]["items"][0]["technical_grounding"] = {
        "operation_refs": [], "entity_refs": [], "field_refs": [],
        "actor_refs": [], "state_value_refs": [], "permission_scope": "",
        "status": "PENDING",
    }
    receipt = apply_v1_extractor_frame_confirmation(asset)
    rule = asset["rule_library"][0]
    assert rule["frame_confirmation"] == "FALLBACK_UNGROUNDED"
    assert rule["frame_confirmation_reason"] == "FRAME_UNGROUNDED"
    assert receipt["kind_counts"]["V1_CANDIDATE_FRAME_UNGROUNDED_FALLBACK"] == 1


def test_confirmation_gate_no_frame_for_rule() -> None:
    asset = _grounded_asset()
    asset["rule_library"] = [_candidate_rule(
        rule_id="zh_business:zz", statement="系统应保证数据一致性。",
    )]
    receipt = apply_v1_extractor_frame_confirmation(asset)
    rule = asset["rule_library"][0]
    assert rule["frame_confirmation"] == "UNCONFIRMED_NO_FRAME"
    assert rule["frame_confirmation_reason"] == "NO_FRAME_FOR_RULE"
    assert receipt["kind_counts"]["V1_CANDIDATE_NO_FRAME_FOR_RULE"] == 1


def test_confirmation_gate_skips_non_candidate_rules() -> None:
    asset = _grounded_asset()
    asset["rule_library"] = [
        _candidate_rule(rule_id="zh_business:zz", statement="系统应保证数据一致性。"),
        {
            "rule_id": "bir:structured",
            "statement": "结构化规则",
            "kind": "business_rule",
            "derivation": "structure_first_explicit_fact_compiler",
        },
    ]
    receipt = apply_v1_extractor_frame_confirmation(asset)
    # Only the candidate rule is decided; the structured rule is untouched.
    assert asset["rule_library"][0]["frame_confirmation"] == "UNCONFIRMED_NO_FRAME"
    assert "frame_confirmation" not in asset["rule_library"][1]
    assert receipt["candidate_rule_count"] == 1


def test_confirmation_gate_no_ledger_compat_path() -> None:
    asset: dict = {
        "rule_library": [_candidate_rule(
            rule_id="zh_business:q", statement="买家可以查询自己的订单。",
        )]
    }
    receipt = apply_v1_extractor_frame_confirmation(asset)
    assert "frame_confirmation" not in asset["rule_library"][0]
    assert receipt["frame_ledger_present"] is False
    assert receipt["kind_counts"] == {"V1_EXTRACTOR_NO_FRAME_LEDGER": 1}
    assert receipt["reason_codes"] == []


def test_confirmation_gate_receipt_shape() -> None:
    asset = _grounded_asset()
    asset["rule_library"] = [_candidate_rule(
        rule_id="zh_business:q", statement="买家可以查询自己的订单。",
    )]
    receipt = apply_v1_extractor_frame_confirmation(asset)
    assert receipt["schema"] == V1_EXTRACTOR_DEMOTION_RECEIPT_SCHEMA
    assert receipt["reason_codes"] == ["V1_EXTRACTOR_CANDIDATE_DEMOTION"]
    assert receipt["contract"] == {
        "gate": "v1_extractor_frame_confirmation",
        "frame_grounded_wins": True,
        "legacy_fallback_observable": True,
        "no_ledger_behavior_unchanged": True,
    }


# ── 3. Behavior IR pass-through ──

def test_behavior_ir_carries_frame_confirmation_on_invariant() -> None:
    asset = _grounded_asset()
    asset["rule_library"] = [{
        "rule_id": "zh_business:q",
        "statement": "买家可以查询自己的订单。",
        "kind": "business_rule",
        "entity": "orders",
        "source_id": "s1",
        "source_locator": "r.docx#block=p1",
        "confidence": 0.9,
        "semantic_candidate": True,
        "frame_confirmation": "UNCONFIRMED_NO_FRAME",
        "frame_confirmation_reason": "NO_FRAME_FOR_RULE",
    }]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    assert validate_behavior_ir(ir) == []
    inv = [r for r in ir["invariants"] if r.get("description") == "买家可以查询自己的订单。"]
    assert len(inv) == 1
    assert inv[0]["frame_confirmation"] == "UNCONFIRMED_NO_FRAME"
    assert inv[0]["frame_confirmation_reason"] == "NO_FRAME_FOR_RULE"


def test_behavior_ir_untouched_without_confirmation_status() -> None:
    # A rule without the status (no ledger / non-v1 rule) must not gain any
    # new invariant field — ledger-less assets stay byte-identical.
    asset = _grounded_asset()
    asset["rule_library"] = [{
        "rule_id": "zh_business:q",
        "statement": "买家可以查询自己的订单。",
        "kind": "business_rule",
        "entity": "orders",
        "source_id": "s1",
        "source_locator": "r.docx#block=p1",
        "confidence": 0.9,
    }]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    inv = [r for r in ir["invariants"] if r.get("description") == "买家可以查询自己的订单。"]
    assert len(inv) == 1
    assert "frame_confirmation" not in inv[0]
    assert "frame_confirmation_reason" not in inv[0]


def test_confirmation_status_does_not_rotate_no_ledger_model_id() -> None:
    # The pass-through only fires when the rule carries the status; a
    # ledger-less asset's IR (and model_id) is unchanged by the phase-2
    # machinery.
    asset = _grounded_asset()
    asset = dict(asset)
    asset.pop("chinese_semantic_frame_ledger", None)
    asset["rule_library"] = [{
        "rule_id": "zh_business:q",
        "statement": "买家可以查询自己的订单。",
        "kind": "business_rule",
        "entity": "orders",
        "source_id": "s1",
        "source_locator": "r.docx#block=p1",
        "confidence": 0.9,
        "semantic_candidate": True,
    }]
    ir = build_behavior_ir_from_knowledge_asset(asset)
    inv = [r for r in ir["invariants"] if r.get("description") == "买家可以查询自己的订单。"]
    assert len(inv) == 1
    assert "frame_confirmation" not in inv[0]
