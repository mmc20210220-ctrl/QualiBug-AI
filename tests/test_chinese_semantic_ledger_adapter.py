"""P0-A: Business Fact Ledger → Chinese Semantic Frame projection adapter.

Covers typed-slot mapping from v1/v2 ledger facts, evidence preservation,
UNKNOWN/OMITTED preservation with reason codes, registry-exact grounding,
zero silent drops, and paraphrase invariance of the projected frame signature.
"""

from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_ledger_adapter import (
    CHINESE_SEMANTIC_FRAME_LEDGER_SCHEMA,
    frames_from_asset,
    project_business_facts_to_semantic_frames,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_schema import (
    semantic_signature,
    validate_semantic_frame,
)


def _v2_permission_fact(*, statement: str = "普通用户可以在订单状态为已支付时查询订单。") -> dict:
    return {
        "fact_id": "fact:perm",
        "fact_type": "PERMISSION_RULE",
        "kind": "RULE",
        "language": "zh-CN",
        "statement_frame_id": "statement_frame:perm",
        "subject": {
            "actor_refs": ["普通用户"],
            "entity_refs": ["订单"],
            "resolution_evidence": [],
        },
        "object": {"entity_refs": ["订单"]},
        "predicate": "查询",
        "action": {"canonical": "查询", "raw": "查询"},
        "conditions": ["订单状态为已支付时"],
        "condition_combinator": "SINGLE_CONDITION",
        "condition_frame": {
            "kind": "LEAF",
            "combinator": "SINGLE_CONDITION",
            "conditions": ["订单状态为已支付时"],
        },
        "scope": {"tenant": "", "organization": "", "ownership": "", "data_scope": ""},
        "modality": "MAY",
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
                    "source_id": "source:rules",
                    "locator": "rules.docx#section=3.2#paragraph=4",
                    "document_block_id": "block-23",
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


def _asset_with(*facts: dict) -> dict:
    return {
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v2",
            "fact_authority": "original_chinese_source_span",
            "items": list(facts),
        }
    }


def test_v2_permission_fact_projects_all_typed_slots() -> None:
    asset = project_business_facts_to_semantic_frames(_asset_with(_v2_permission_fact()))
    ledger = asset["chinese_semantic_frame_ledger"]
    assert ledger["schema"] == CHINESE_SEMANTIC_FRAME_LEDGER_SCHEMA
    assert ledger["closure"]["status"] == "PASS"
    assert ledger["closure"]["fact_count"] == 1
    assert ledger["closure"]["frame_count"] == 1
    assert ledger["closure"]["silent_drop_allowed"] is False

    frame = frames_from_asset(asset)[0]
    assert validate_semantic_frame(frame) == []
    assert frame["frame_type"] == "PERMISSION_RULE"
    assert frame["source_span"]["source_id"] == "source:rules"
    assert frame["source_span"]["document_block_id"] == "block-23"
    assert frame["source_span"]["locator"] == "rules.docx#section=3.2#paragraph=4"
    assert frame["source_span"]["quote"] == "普通用户可以在订单状态为已支付时查询订单。"
    assert frame["source_span"]["quote_hash"]
    assert frame["actor"]["resolution_status"] == "RESOLVED"
    assert frame["actor"]["mentions"] == ["普通用户"]
    assert frame["actor"]["grounded_actor_refs"] == []
    assert frame["action"]["resolution_status"] == "RESOLVED"
    assert frame["action"]["mentions"] == ["查询"]
    assert frame["object"]["resolution_status"] == "RESOLVED"
    assert frame["object"]["mentions"] == ["订单"]
    assert frame["modality"]["type"] == "MAY"
    assert frame["modality"]["resolution_status"] == "RESOLVED"
    assert frame["conditions"][0]["raw"] == "订单状态为已支付时"
    assert frame["conditions"][0]["logic_group"] == "SINGLE_CONDITION"
    assert frame["conditions"][0]["resolution_status"] == "RESOLVED"
    assert frame["scope"]["resolution_status"] == "UNKNOWN"
    assert frame["technical_grounding"]["status"] == "PENDING"
    assert frame["resolution"]["evidence_closure"]["status"] == "PASS"
    assert "TECHNICAL_GROUNDING_PENDING" in frame["resolution"]["reason_codes"]
    assert "OMITTED_ACTOR_UNRESOLVED" not in frame["resolution"]["reason_codes"]
    assert frame["origin"]["origin_fact_id"] == "fact:perm"


def test_legacy_v1_fact_is_classified_by_typed_slots_only() -> None:
    # v1 shape: no fact_type, no evidence_address, no quote_hash — the frame
    # is classified by typed slots (actor_refs + modality), never by wording.
    v1_fact = {
        "fact_id": "fact:legacy",
        "kind": "RULE",
        "language": "zh-CN",
        "subject": {"actor_refs": ["管理员"], "entity_refs": ["订单"], "resolution_evidence": []},
        "object": {"entity_refs": ["订单"]},
        "action": {"canonical": "删除", "raw": "删除"},
        "scope": {"tenant": "", "organization": "", "ownership": "", "data_scope": ""},
        "modality": "MUST_NOT",
        "polarity": "NEGATIVE",
        "raw_statement": "管理员不得删除订单。",
        "source_spans": [{"source_id": "source:rules", "locator": "rules.docx#section=1", "quote": "管理员不得删除订单。"}],
        "status": "ACCEPTED",
        "critical": True,
    }
    asset = project_business_facts_to_semantic_frames(_asset_with(v1_fact))
    frame = frames_from_asset(asset)[0]
    assert frame["frame_type"] == "PERMISSION_RULE"
    assert frame["modality"]["type"] == "MUST_NOT"
    assert frame["actor"]["resolution_status"] == "RESOLVED"
    assert frame["origin"]["origin_fact_type"] == "RULE"


def test_registry_exact_match_grounds_actor_and_entity() -> None:
    asset = _asset_with(_v2_permission_fact())
    asset["enterprise_understanding_model"] = {
        "actors": [
            {"id": "actor:regular", "role_key": "regular", "name": "普通用户"}
        ],
        "business_objects": [
            {"id": "entity:order", "canonical_label": "订单"}
        ],
    }
    asset = project_business_facts_to_semantic_frames(asset)
    frame = frames_from_asset(asset)[0]
    assert frame["actor"]["resolution_status"] == "GROUNDED"
    assert frame["actor"]["grounded_actor_refs"] == ["actor:regular"]
    assert frame["object"]["resolution_status"] == "GROUNDED"
    assert frame["object"]["grounded_entity_refs"] == ["entity:order"]
    assert frame["technical_grounding"]["actor_refs"] == ["actor:regular"]
    assert frame["technical_grounding"]["entity_refs"] == ["entity:order"]


def test_omitted_actor_is_unknown_not_a_guess() -> None:
    fact = _v2_permission_fact()
    fact["subject"]["actor_refs"] = []
    asset = project_business_facts_to_semantic_frames(_asset_with(fact))
    frame = frames_from_asset(asset)[0]
    assert frame["actor"]["resolution_status"] == "OMITTED"
    assert "OMITTED_ACTOR_UNRESOLVED" in frame["resolution"]["reason_codes"]
    assert frame["actor"]["grounded_actor_refs"] == []


def test_ownership_raw_phrase_preserved_but_never_inferred() -> None:
    fact = _v2_permission_fact()
    fact["scope"]["ownership"] = "只能查询自己的订单"
    asset = project_business_facts_to_semantic_frames(_asset_with(fact))
    frame = frames_from_asset(asset)[0]
    assert frame["scope"]["scope_type"] == "OWNERSHIP"
    assert frame["scope"]["ownership_relation"] == {"raw": "只能查询自己的订单"}
    # The relation structure is never inferred (P0-B/C work); the reason code
    # documents that the structured ownership relation is still unresolved.
    assert "OWNERSHIP_RELATION_UNRESOLVED" in frame["resolution"]["reason_codes"]


def test_paraphrase_projection_keeps_semantic_signature() -> None:
    # SPEC §18.2: equivalent paraphrases over the same typed slots project to
    # frames with one semantic signature, no matter the surface wording.
    first_fact = _v2_permission_fact(statement="普通用户可以在订单状态为已支付时查询订单。")
    # Both paraphrases carry the same typed slots — including the ownership
    # scope ("自己的" vs "本人的") — so they are semantically equivalent.
    first_fact["scope"]["ownership"] = "自己的订单"
    second_fact = _v2_permission_fact(statement="普通账号仅可在订单已支付后查看本人的订单信息。")
    second_fact["fact_id"] = "fact:perm2"
    second_fact["subject"]["actor_refs"] = ["普通账号"]
    second_fact["action"] = {"canonical": "查看", "raw": "查看"}
    second_fact["predicate"] = "查看"
    second_fact["scope"]["ownership"] = "本人的订单信息"
    asset = project_business_facts_to_semantic_frames(
        _asset_with(first_fact, second_fact)
    )
    frames = frames_from_asset(asset)
    assert len(frames) == 2
    first, second = frames[0], frames[1]
    assert first["source_span"]["quote"] != second["source_span"]["quote"]
    # Same typed slots → same signature. The raw ownership phrase is evidence,
    # not a typed slot, so it does not split the signature.
    assert (
        first["resolution"]["semantic_signature"]
        == second["resolution"]["semantic_signature"]
        == semantic_signature(first)
        == semantic_signature(second)
    )
    assert first["frame_type"] == second["frame_type"] == "PERMISSION_RULE"
    assert first["modality"]["type"] == second["modality"]["type"] == "MAY"
    assert first["scope"]["scope_type"] == second["scope"]["scope_type"] == "OWNERSHIP"


def test_time_constraint_provenance_never_changes_semantic_signature() -> None:
    first_fact = _v2_permission_fact()
    first_fact["time_window_constraints"] = [
        {
            "raw": "提交后24小时内",
            "anchor": "提交后",
            "relation": "WITHIN",
            "duration": "24小时",
            "window_resolution_status": "UNRESOLVED",
            "window_resolution_reason": "TEMPORAL_WINDOW_UNCOMPILED",
            "source_backed": True,
            "origin": "first_source",
            "evidence": [{"locator": "a.docx#block=1"}],
        }
    ]
    second_fact = _v2_permission_fact()
    second_fact["fact_id"] = "fact:perm2"
    second_fact["time_window_constraints"] = [
        {
            "raw": "在提交后24小时以内",
            "anchor": "提交后",
            "relation": "WITHIN",
            "duration": "24小时",
            "source_backed": True,
            "origin": "second_source",
            "evidence": [{"locator": "b.xlsx#row=2"}],
        }
    ]

    frames = frames_from_asset(
        project_business_facts_to_semantic_frames(
            _asset_with(first_fact, second_fact)
        )
    )
    assert len(frames) == 2
    assert frames[0]["time_constraints"] != frames[1]["time_constraints"]
    assert frames[0]["resolution"]["semantic_signature"] == frames[1][
        "resolution"
    ]["semantic_signature"]


def test_term_alias_is_skipped_with_typed_receipt_never_silent() -> None:
    term_alias = {
        "fact_id": "fact:alias",
        "kind": "TERM_ALIAS",
        "raw_statement": "出库单也称发货单。",
        "source_spans": [{"source_id": "s", "locator": "r#p=1", "quote": "出库单也称发货单。"}],
        "status": "ACCEPTED",
    }
    asset = project_business_facts_to_semantic_frames(_asset_with(term_alias))
    ledger = asset["chinese_semantic_frame_ledger"]
    assert ledger["closure"]["status"] == "PARTIAL"
    assert ledger["closure"]["frame_count"] == 0
    assert ledger["closure"]["skipped_count"] == 1
    assert ledger["closure"]["silent_drop_allowed"] is False
    assert ledger["closure"]["reason_code_counts"]["TERM_ALIAS_NOT_A_FRAME"] == 1
    receipt = ledger["receipts"][0]
    assert receipt["receipt_kind"] == "FACT_PROJECTION"
    assert receipt["status"] == "FAIL"
    assert receipt["reason_codes"] == ["TERM_ALIAS_NOT_A_FRAME"]


def test_degenerate_fact_surfaces_failure_never_drops_silently() -> None:
    degenerate = {
        "fact_id": "fact:degenerate",
        "kind": "RULE",
        "status": "ACCEPTED",
    }
    asset = project_business_facts_to_semantic_frames(_asset_with(degenerate))
    ledger = asset["chinese_semantic_frame_ledger"]
    assert ledger["closure"]["status"] == "FAIL"
    assert ledger["closure"]["failed_count"] == 1
    assert ledger["closure"]["reason_code_counts"].get(
        "chinese_semantic_frame_projection_fact_without_quote", 0
    ) == 1
    assert ledger["receipts"][0]["status"] == "FAIL"


def test_reprojection_is_idempotent_and_traceable() -> None:
    asset = _asset_with(_v2_permission_fact())
    first = project_business_facts_to_semantic_frames(dict(asset))
    second = project_business_facts_to_semantic_frames(dict(asset))
    first_frames = frames_from_asset(first)
    second_frames = frames_from_asset(second)
    assert [f["frame_id"] for f in first_frames] == [f["frame_id"] for f in second_frames]
    assert first_frames[0] == second_frames[0]
