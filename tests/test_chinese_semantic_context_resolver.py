"""P0-C: Chinese Semantic Frame context resolution.

Covers SPEC §10.2/§10.3 (omitted actor recovery from unique evidence), §10.1
(coreference at mention level), section context population, and the
"无法可靠恢复时输出 UNKNOWN" fail-closed contract.
"""

from __future__ import annotations

import copy

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_clause_parser import (
    parse_chinese_clause_trees,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_context_envelope import (
    build_chinese_semantic_context_envelopes,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_context_resolver import (
    CHINESE_SEMANTIC_CONTEXT_RESOLUTION_SCHEMA,
    resolve_chinese_semantic_context,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_frame_compiler import (
    enrich_frames_with_clause_structure,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_ledger_adapter import (
    project_business_facts_to_semantic_frames,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_semantic_schema import (
    semantic_signature,
    validate_semantic_frame,
)


def _heading(block_id: str, text: str, order: int, parent: str = "") -> dict:
    return {
        "block_id": block_id, "type": "HEADING", "parent_id": parent, "order": order,
        "region": "body", "level": 1, "text": text,
        "source_locator": f"r.docx#block={order}",
    }


def _paragraph(block_id: str, text: str, order: int, parent: str) -> dict:
    return {
        "block_id": block_id, "type": "PARAGRAPH", "parent_id": parent, "order": order,
        "region": "body", "text": text, "source_locator": f"r.docx#block={order}",
    }


def _fact(statement: str, block_id: str, fact_id: str, *, actor_refs: list[str] | None = None,
          conditions: list[str] | None = None, modality: str = "MUST_NOT") -> dict:
    return {
        "fact_id": fact_id,
        "fact_type": "PERMISSION_RULE",
        "kind": "RULE",
        "language": "zh-CN",
        "statement_frame_id": f"statement_frame:{fact_id}",
        "subject": {
            "actor_refs": list(actor_refs or []),
            "entity_refs": ["订单"],
            "resolution_evidence": [],
        },
        "object": {"entity_refs": ["订单"]},
        "predicate": "",
        "action": {"canonical": "", "raw": ""},
        "conditions": list(conditions or []),
        "condition_combinator": "",
        "condition_frame": {},
        "scope": {"tenant": "", "organization": "", "ownership": "", "data_scope": ""},
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


def _resolve(asset: dict) -> dict:
    asset = project_business_facts_to_semantic_frames(asset)
    asset = build_chinese_semantic_context_envelopes(asset)
    asset = parse_chinese_clause_trees(asset)
    asset = enrich_frames_with_clause_structure(asset)
    return resolve_chinese_semantic_context(asset)


def _frames(asset: dict) -> dict[str, dict]:
    return {
        row["origin"]["origin_fact_id"]: row
        for row in asset["chinese_semantic_frame_ledger"]["items"]
    }


def _asset(*, blocks: list[dict], facts: list[dict],
           actors: list[dict] | None = None) -> dict:
    return {
        "document_structure_assets": {
            "items": [{"source_id": "s1", "filename": "r.docx", "blocks": blocks}]
        },
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v2",
            "fact_authority": "original_chinese_source_span",
            "items": facts,
        },
        "enterprise_understanding_model": {
            "actors": list(actors or []),
            "business_objects": [],
        },
    }


def test_spec_10_3_section_heading_recovers_omitted_actor() -> None:
    # SPEC §10.3: 章节「仓库管理员操作规则」+「审批通过后方可出库。」
    h1 = _heading("h1", "仓库管理员操作规则", 1)
    p1 = _paragraph("p1", "审批通过后方可出库。", 2, "h1")
    asset = _resolve(
        _asset(
            blocks=[h1, p1],
            facts=[_fact("审批通过后方可出库。", "p1", "f:ck", modality="MAY")],
            actors=[{"actor_id": "actor:wh", "name": "仓库管理员"}],
        )
    )
    frame = _frames(asset)["f:ck"]
    assert frame["actor"]["resolution_status"] == "RESOLVED"
    assert frame["actor"]["mentions"] == ["仓库管理员"]
    resolution = frame["context_resolution"]["actor_resolution"]
    assert resolution["method"] == "unique_section_heading"
    assert resolution["mention"] == "仓库管理员"
    # The condition and modality survive (方可 → MAY, frame modality matches).
    assert [row["raw"] for row in frame["conditions"]] == ["审批通过后"]
    assert frame["modality"]["type"] == "MAY"
    assert frame["clause_structure"]["modality_cross_check"]["matches"] is True
    assert "OMITTED_ACTOR_UNRESOLVED" not in frame["resolution"]["reason_codes"]


def test_only_if_subject_recovers_omitted_actor() -> None:
    # 只有…才 subject is a same-sentence explicit noun — highest priority.
    h1 = _heading("h1", "退款规则", 1)
    p1 = _paragraph("p1", "只有订单已支付且未发货时，用户才能申请退款。", 2, "h1")
    asset = _resolve(
        _asset(blocks=[h1, p1], facts=[_fact("只有订单已支付且未发货时，用户才能申请退款。", "p1", "f:tk")])
    )
    frame = _frames(asset)["f:tk"]
    assert frame["actor"]["resolution_status"] == "RESOLVED"
    assert frame["actor"]["mentions"] == ["用户"]
    assert frame["context_resolution"]["actor_resolution"]["method"] == "only_if_subject"
    assert [row["raw"] for row in frame["conditions"]] == ["订单已支付", "未发货"]


def test_unique_prior_frame_in_same_section_recovers_actor() -> None:
    h1 = _heading("h1", "审批规则", 1)
    p1 = _paragraph("p1", "审批专员可以处理申请。", 2, "h1")
    p2 = _paragraph("p2", "审批通过后方可出库。", 3, "h1")
    first = _fact("审批专员可以处理申请。", "p1", "f:first", actor_refs=["审批专员"])
    second = _fact("审批通过后方可出库。", "p2", "f:second")
    asset = _resolve(_asset(blocks=[h1, p1, p2], facts=[first, second]))
    frame = _frames(asset)["f:second"]
    assert frame["actor"]["resolution_status"] == "RESOLVED"
    assert "审批专员" in frame["actor"]["mentions"]
    assert frame["context_resolution"]["actor_resolution"]["method"] in (
        "unique_prior_frame_in_same_section",
        "prior_frame_and_section_heading",
    )


def test_prior_frame_never_leaks_across_sections() -> None:
    h1 = _heading("h1", "第一节", 1)
    p1 = _paragraph("p1", "审批专员可以处理申请。", 2, "h1")
    h2 = _heading("h2", "第二节", 3)
    p2 = _paragraph("p2", "审批通过后方可出库。", 4, "h2")
    first = _fact("审批专员可以处理申请。", "p1", "f:first", actor_refs=["审批专员"])
    second = _fact("审批通过后方可出库。", "p2", "f:second")
    asset = _resolve(_asset(blocks=[h1, p1, h2, p2], facts=[first, second]))
    frame = _frames(asset)["f:second"]
    # The prior frame is in another section → no recovery, UNKNOWN stays.
    assert frame["actor"]["resolution_status"] == "OMITTED"
    assert frame["actor"]["mentions"] == []
    assert "OMITTED_ACTOR_UNRESOLVED" in frame["resolution"]["reason_codes"]


def test_ambiguous_heading_candidates_stay_omitted() -> None:
    h1 = _heading("h1", "管理员与审批专员操作规则", 1)
    p1 = _paragraph("p1", "审批通过后方可出库。", 2, "h1")
    asset = _resolve(
        _asset(
            blocks=[h1, p1],
            facts=[_fact("审批通过后方可出库。", "p1", "f:ck")],
            actors=[
                {"actor_id": "actor:admin", "name": "管理员"},
                {"actor_id": "actor:reviewer", "name": "审批专员"},
            ],
        )
    )
    frame = _frames(asset)["f:ck"]
    assert frame["actor"]["resolution_status"] == "OMITTED"
    assert frame["actor"]["mentions"] == []
    assert "MULTIPLE_ACTOR_CANDIDATES" in frame["context_resolution"]["reason_codes"]


def test_coreference_resolves_to_unique_frame_mention() -> None:
    h1 = _heading("h1", "订单规则", 1)
    p1 = _paragraph("p1", "该订单不得取消。", 2, "h1")
    fact = _fact("该订单不得取消。", "p1", "f:co")
    asset = _resolve(_asset(blocks=[h1, p1], facts=[fact]))
    frame = _frames(asset)["f:co"]
    resolutions = frame["context_resolution"]["coreference_resolutions"]
    assert len(resolutions) == 1
    assert resolutions[0]["resolution_status"] == "RESOLVED"
    assert resolutions[0]["resolved_mention_candidate"] == "订单"
    assert resolutions[0]["method"] == "same_sentence_explicit_noun"
    # Raw text is never rewritten.
    assert frame["source_span"]["quote"] == "该订单不得取消。"


def test_unresolvable_coreference_is_unknown() -> None:
    # A bare pronoun (其) with more than one possible referent stays UNKNOWN.
    h1 = _heading("h1", "规则", 1)
    p1 = _paragraph("p1", "其状态不得变更。", 2, "h1")
    fact = _fact("其状态不得变更。", "p1", "f:co2")
    fact["subject"]["entity_refs"] = ["订单", "出库单"]
    fact["object"]["entity_refs"] = ["订单", "出库单"]
    asset = _resolve(_asset(blocks=[h1, p1], facts=[fact]))
    frame = _frames(asset)["f:co2"]
    resolutions = frame["context_resolution"]["coreference_resolutions"]
    assert resolutions[0]["resolution_status"] == "UNKNOWN"
    assert resolutions[0]["candidate_count"] == 2
    assert "COREFERENCE_UNRESOLVED" in frame["context_resolution"]["reason_codes"]


def test_unknown_path_never_force_binds() -> None:
    h1 = _heading("h1", "规则", 1)
    p1 = _paragraph("p1", "不得删除订单。", 2, "h1")
    asset = _resolve(_asset(blocks=[h1, p1], facts=[_fact("不得删除订单。", "p1", "f:plain")]))
    frame = _frames(asset)["f:plain"]
    assert frame["actor"]["resolution_status"] == "OMITTED"
    assert frame["actor"]["mentions"] == []
    assert "OMITTED_ACTOR_UNRESOLVED" in frame["resolution"]["reason_codes"]
    assert frame["context_resolution"]["actor_resolution"] == {}


def test_document_context_is_populated_with_same_section_neighbors() -> None:
    h1 = _heading("h1", "订单管理", 1)
    p1 = _paragraph("p1", "审批专员可以处理申请。", 2, "h1")
    p2 = _paragraph("p2", "只有订单已支付时才能取消。", 3, "h1")
    h2 = _heading("h2", "其他", 4)
    p3 = _paragraph("p3", "不得删除订单。", 5, "h2")
    facts = [
        _fact("审批专员可以处理申请。", "p1", "f:a", actor_refs=["审批专员"]),
        _fact("只有订单已支付时才能取消。", "p2", "f:b"),
        _fact("不得删除订单。", "p3", "f:c"),
    ]
    asset = _resolve(_asset(blocks=[h1, p1, p2, h2, p3], facts=facts))
    frame_b = _frames(asset)["f:b"]
    context = frame_b["document_context"]
    assert context["section_path"] == ["订单管理"]
    assert context["heading"] == "订单管理"
    # The neighbor frame in the same section is linked; the cross-section one
    # (f:c) is not.
    frame_a_id = _frames(asset)["f:a"]["frame_id"]
    assert frame_a_id in context["previous_frame_refs"]
    frame_c_id = _frames(asset)["f:c"]["frame_id"]
    assert frame_c_id not in context["previous_frame_refs"]
    assert frame_c_id not in context["next_frame_refs"]


def test_resolution_is_idempotent_and_signature_stable() -> None:
    h1 = _heading("h1", "仓库管理员操作规则", 1)
    p1 = _paragraph("p1", "审批通过后方可出库。", 2, "h1")
    asset = _resolve(
        _asset(
            blocks=[h1, p1],
            facts=[_fact("审批通过后方可出库。", "p1", "f:ck")],
            actors=[{"actor_id": "actor:wh", "name": "仓库管理员"}],
        )
    )
    before = copy.deepcopy(asset["chinese_semantic_frame_ledger"]["items"])
    frame = _frames(asset)["f:ck"]
    signature_before = frame["resolution"]["semantic_signature"]
    # Actor mentions are NOT part of the signature — resolution keeps it stable.
    assert signature_before == semantic_signature(frame)

    asset = resolve_chinese_semantic_context(asset)
    after = asset["chinese_semantic_frame_ledger"]["items"]
    assert before == after
    for row in after:
        assert validate_semantic_frame(row) == []


def test_resolution_ledger_schema_and_receipt() -> None:
    h1 = _heading("h1", "仓库管理员操作规则", 1)
    p1 = _paragraph("p1", "审批通过后方可出库。", 2, "h1")
    asset = _resolve(
        _asset(
            blocks=[h1, p1],
            facts=[_fact("审批通过后方可出库。", "p1", "f:ck")],
            actors=[{"actor_id": "actor:wh", "name": "仓库管理员"}],
        )
    )
    ledger = asset["chinese_semantic_context_resolution_ledger"]
    assert ledger["schema"] == CHINESE_SEMANTIC_CONTEXT_RESOLUTION_SCHEMA
    assert ledger["receipt"]["actor_resolved_count"] == 1
    assert ledger["receipt"]["raw_text_never_rewritten"] is True
