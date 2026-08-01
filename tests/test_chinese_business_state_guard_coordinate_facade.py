from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension import (
    analyze_chinese_business_source,
)
from ai_test_asset_center.enterprise_knowledge_center._chinese_business_comprehension.state_guard_coordinates import (
    close_state_guard_coordinates,
    synchronize_rule_library_from_facts,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.structured_fact_compiler import (
    _semantic_signature,
)


def _source(text: str) -> dict:
    return {
        "source_id": "src:ticket-rules",
        "filename": "BUSINESS_RULES.md",
        "source_locator": "BUSINESS_RULES.md",
        "text": text,
    }


def test_facade_closes_state_guard_coordinates_before_structured_deduplication() -> None:
    _coverage, facts, _glossary = analyze_chinese_business_source(
        _source(
            "\n".join(
                [
                    "只有OPEN状态的工单可以被分配。",
                    "只有ASSIGNED状态的工单可以开始处理。",
                    "只有RESOLVED或CLOSED状态的工单可以被重开。",
                ]
            )
        ),
        asset={"business_objects": [{"name": "工单"}]},
    )

    assert len(facts) == 3
    by_action = {row["action"]["canonical"]: row for row in facts}
    assert set(by_action) == {"分配", "开始处理", "重开"}
    assert by_action["分配"]["conditions"] == ["工单.status=OPEN"]
    assert by_action["开始处理"]["conditions"] == ["工单.status=ASSIGNED"]
    assert by_action["重开"]["conditions"] == [
        "工单.status=RESOLVED",
        "工单.status=CLOSED",
    ]
    assert by_action["重开"]["condition_combinator"] == "OR"
    assert len({_semantic_signature(row) for row in facts}) == 3


def test_closure_never_invents_object_or_overwrites_existing_coordinates() -> None:
    unresolved = {
        "fact_id": "fact:unknown",
        "kind": "RULE",
        "raw_statement": "只有OPEN状态的未知对象可以被分配",
        "subject": {"entity_refs": []},
        "object": {"entity_refs": []},
        "action": {},
        "conditions": [],
    }
    parsed = {
        "fact_id": "fact:parsed",
        "kind": "RULE",
        "raw_statement": "只有OPEN状态的工单可以被分配",
        "subject": {"entity_refs": ["工单"]},
        "object": {"entity_refs": ["工单"]},
        "action": {"canonical": "人工动作", "raw": "人工动作"},
        "conditions": ["人工条件"],
    }
    before = deepcopy([unresolved, parsed])

    receipt = close_state_guard_coordinates([unresolved, parsed])

    assert [unresolved, parsed] == before
    assert receipt["normalized_fact_count"] == 0
    assert receipt["object_identity_reuse_failure_ids"] == ["fact:unknown"]
    assert receipt["coordinate_conflict_fact_ids"] == ["fact:parsed"]
    assert receipt["status"] == "BLOCKED_COORDINATE_CONFLICT"


def test_existing_rule_projection_is_refreshed_from_same_fact_authority() -> None:
    fact = {
        "fact_id": "fact:assign",
        "kind": "RULE",
        "raw_statement": "只有OPEN状态的工单可以被分配",
        "subject": {"entity_refs": ["工单"]},
        "object": {"entity_refs": ["工单"]},
        "action": {},
        "conditions": [],
    }
    asset = {
        "rule_library": [
            {
                "rule_id": "zh_business:assign",
                "semantic_contract": fact,
                "action": "",
                "conditions": [],
            }
        ]
    }

    receipt = close_state_guard_coordinates([fact])
    synchronize_rule_library_from_facts(asset, [fact])

    assert receipt["normalized_fact_ids"] == ["fact:assign"]
    assert asset["rule_library"][0]["semantic_contract"] is fact
    assert asset["rule_library"][0]["action"] == "分配"
    assert asset["rule_library"][0]["conditions"] == ["工单.status=OPEN"]
