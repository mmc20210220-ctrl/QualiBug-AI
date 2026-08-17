"""P0-B: atomic Chinese clause parsing.

Covers the SPEC §8 golden examples (parallel actions with shared conditions,
"未发货" as a condition rather than a prohibition), exception trees, negation
scope, conservative ambiguity on object enumerations, and fail-closed
behaviour on vague text.
"""

from __future__ import annotations

import pytest

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.chinese_clause_parser import (
    CHINESE_CLAUSE_TREE_SCHEMA,
    parse_block_text,
    validate_clause_tree,
)


def _tree(text: str, **kwargs: object) -> dict:
    return parse_block_text(text, source_id="s1", block_id="b1", **kwargs)


def test_spec_8_golden_parallel_actions_with_shared_conditions() -> None:
    # SPEC §8: 非管理员不得修改或删除已发布内容。
    tree = _tree("非管理员不得修改或删除已发布内容。")
    assert tree["modality"]["type"] == "MUST_NOT"
    assert tree["negation_scope"]["type"] == "ACTOR_NEGATION"
    assert tree["negation_scope"]["raw"] == "非管理员"

    clauses = tree["clauses"]
    assert [row["action_mention"] for row in clauses] == ["修改", "删除"]
    assert all(row["modality"] == "MUST_NOT" for row in clauses)
    assert tree["enumeration"]["interpretation"] == "ACTION_SPLIT"
    assert tree["enumeration"]["joiner"] == "或"

    shared = tree["shared_conditions"]
    assert shared["actor_condition"]["kind"] == "ACTOR_NEGATION"
    assert shared["object_condition"]["raw"] == "已发布内容"
    assert shared["object_condition"]["kind"] == "OBJECT_STATE_MODIFIER"
    assert tree["reason_codes"] == []


def test_spec_8_golden_only_if_with_and_condition_tree() -> None:
    # SPEC §8: 只有订单已支付且未发货时，用户才能申请退款。
    tree = _tree("只有订单已支付且未发货时，用户才能申请退款。")
    assert tree["modality"]["type"] == "ONLY_IF"
    assert tree["condition_combinator"] == "AND"
    condition_raws = [row["raw"] for row in tree["conditions"]]
    assert condition_raws == ["订单已支付", "未发货"]
    # 未发货 must be a condition, never an independent prohibition.
    assert tree["negation_scope"]["type"] == "CONDITION_ONLY"
    assert tree["negation_scope"]["raws"] == ["未"]
    assert [row["action_mention"] for row in tree["clauses"]] == ["申请退款"]
    # The 才-subject is recovered as a mention candidate.
    assert tree["actor_mention"]["raw"] == "用户"
    assert tree["reason_codes"] == []


def test_state_negation_is_condition_not_prohibition() -> None:
    tree = _tree("未发货的订单不得删除。")
    assert tree["modality"]["type"] == "MUST_NOT"
    assert tree["negation_scope"]["type"] == "CONDITION_ONLY"
    assert [row["raw"] for row in tree["conditions"]] == ["未发货的订单"]
    assert [row["action_mention"] for row in tree["clauses"]] == ["删除"]


def test_exception_tree_contrast_and_exclusion() -> None:
    tree = _tree("已支付订单不得取消，但经财务确认的异常订单除外。")
    exceptions = tree["exceptions"]
    assert len(exceptions) == 1
    assert exceptions[0]["kind"] == "CONTRAST"
    assert exceptions[0]["raw"] == "经财务确认的异常订单除外"
    assert exceptions[0]["resolution_status"] == "RESOLVED"
    assert [row["raw"] for row in tree["conditions"]] == ["已支付订单"]
    assert [row["action_mention"] for row in tree["clauses"]] == ["取消"]
    assert tree["reason_codes"] == []

    # Standalone exclusion form.
    tree = _tree("除管理员外均不得删除订单。")
    assert tree["exceptions"][0]["kind"] == "EXCLUSION"
    assert tree["exceptions"][0]["raw"] == "管理员"

    # Post-modal 除非 is an UNLESS exception.
    tree = _tree("不得发货，除非已支付。")
    assert tree["exceptions"][0]["kind"] == "UNLESS"
    assert tree["exceptions"][0]["raw"] == "已支付"
    assert [row["action_mention"] for row in tree["clauses"]] == ["发货"]


def test_pre_modal_unless_is_a_negated_condition() -> None:
    tree = _tree("除非已支付，否则不得发货。")
    assert tree["modality"]["type"] == "MUST_NOT"
    assert [row["raw"] for row in tree["conditions"]] == ["已支付"]
    assert tree["negation_scope"]["type"] == "CONDITION_ONLY"
    assert [row["action_mention"] for row in tree["clauses"]] == ["发货"]
    assert tree["exceptions"] == []


def test_object_enumeration_is_ambiguous_never_forced() -> None:
    # 修改订单或发票 could be object enumeration — no state tail, so the
    # action split is AMBIGUOUS and the raw text is preserved.
    tree = _tree("不得修改订单或发票。")
    assert tree["enumeration"]["interpretation"] == "AMBIGUOUS"
    assert "CLAUSE_SEGMENTATION_AMBIGUOUS" in tree["reason_codes"]
    # Candidates are still kept (nothing is lost).
    assert [row["action_mention"] for row in tree["clauses"]] == ["修改订单", "发票"]


def test_list_header_is_a_condition_never_an_action() -> None:
    tree = _tree("已取消订单：", block_type="LIST_ITEM")
    assert tree["enumeration"]["interpretation"] == "HEADER"
    assert [row["raw"] for row in tree["conditions"]] == ["已取消订单"]
    assert tree["clauses"] == []

    child = _tree("1. 不得支付；", block_type="LIST_ITEM")
    assert child["modality"]["type"] == "MUST_NOT"
    assert [row["action_mention"] for row in child["clauses"]] == ["支付"]

    exception_scope = _tree("除管理员外：", block_type="LIST_ITEM")
    assert exception_scope["conditions"] == []
    assert [
        (row["raw"], row["kind"]) for row in exception_scope["exceptions"]
    ] == [("管理员", "EXCLUSION")]


def test_explicit_time_windows_are_typed_without_business_vocabulary() -> None:
    anchored = _tree("在触发后2个工作日内必须完成处理。")
    assert anchored["time_constraints"] == [
        {
            "raw": "触发后2个工作日内",
            "anchor": "触发后",
            "relation": "WITHIN",
            "duration": "2个工作日",
            "source_backed": True,
            "resolution_status": "RESOLVED",
        }
    ]

    list_scope = _tree("事件结束后二十四小时以内：", block_type="LIST_ITEM")
    assert list_scope["time_constraints"] == [
        {
            "raw": "事件结束后二十四小时以内",
            "anchor": "事件结束后",
            "relation": "WITHIN",
            "duration": "二十四小时",
            "source_backed": True,
            "resolution_status": "RESOLVED",
        }
    ]

    combined = _tree("如果条件成立，并且提交之后24小时以内，必须处理。")
    assert [row["raw"] for row in combined["conditions"]] == [
        "条件成立",
        "提交之后24小时以内",
    ]
    assert combined["condition_combinator"] == "AND"
    assert combined["time_constraints"][0]["anchor"] == "提交之后"


def test_vague_text_never_forced_into_facts() -> None:
    # SPEC §18.4 negatives: vague wording must not become a definite rule.
    for vague in (
        "相关人员可以处理相关数据。",
        "原则上应及时完成。",
        "必要时可以调整。",
    ):
        tree = _tree(vague)
        assert validate_clause_tree(tree) == []
        # No definite MUST/MUST_NOT/ONLY_IF is invented from vagueness.
        assert tree["modality"]["type"] in ("ASSERTS", "MAY")


def test_tree_validation_is_fail_closed() -> None:
    tree = _tree("不得删除订单。")
    tree["schema"] = "qualibug.wrong-schema.v1"
    assert "clause_tree_schema_mismatch" in validate_clause_tree(tree)
    tree["schema"] = CHINESE_CLAUSE_TREE_SCHEMA
    tree["modality"]["type"] = "WHATEVER"
    assert any(
        "clause_tree_modality_invalid" in error for error in validate_clause_tree(tree)
    )


def test_parallel_actions_inherit_shared_modality_and_conditions() -> None:
    tree = _tree("已支付订单可以修改或删除。")
    assert tree["modality"]["type"] == "MAY"
    assert [row["action_mention"] for row in tree["clauses"]] == ["修改", "删除"]
    assert all(row["modality"] == "MAY" for row in tree["clauses"])
    assert [row["raw"] for row in tree["conditions"]] == ["已支付订单"]


def test_invalid_tree_raises() -> None:
    with pytest.raises(ValueError, match="chinese_clause_tree_invalid"):
        parse_block_text("", source_id="s", block_id="b")
