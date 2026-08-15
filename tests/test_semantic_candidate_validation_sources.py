from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._candidate_validation import (
    candidates_to_behavior_ir_entries,
    validate_and_promote_candidates,
)


def _candidate(*, name: str = "订单", kind: str = "entity", source_id: str) -> dict:
    return {
        "name": name,
        "kind": kind,
        "source_id": source_id,
        "source_locator": f"{source_id}.md#chars=0-20",
        "verbatim_quote": f"{name}业务说明",
        "confidence": 0.7,
        "status": "CANDIDATE",
    }


def test_repetition_inside_one_document_is_not_multi_source_evidence() -> None:
    candidate = _candidate(source_id="prd-1")

    receipt = validate_and_promote_candidates([candidate, dict(candidate)])

    assert receipt.validated == []
    assert len(receipt.pending) == 2
    assert all(
        item["pending_reason"] == "no_independent_source_evidence"
        for item in receipt.pending
    )


def test_two_distinct_sources_can_validate_same_typed_candidate() -> None:
    receipt = validate_and_promote_candidates(
        [
            _candidate(source_id="prd-1"),
            _candidate(source_id="api-1"),
        ]
    )

    assert len(receipt.validated) == 2
    assert receipt.pending == []
    assert all(
        "multi_source_consistency" in item["promotion_evidence"]
        for item in receipt.validated
    )
    assert receipt.validated[0]["promotion_evidence_sources"][
        "multi_source_consistency"
    ] == ["api-1"]


def test_same_source_api_text_does_not_self_validate_candidate() -> None:
    receipt = validate_and_promote_candidates(
        [_candidate(source_id="prd-1")],
        interfaces=[
            {
                "interface_id": "interface:GET:/orders",
                "source_id": "prd-1",
                "path": "/订单",
                "summary": "查看订单",
            }
        ],
    )

    assert receipt.validated == []
    assert len(receipt.pending) == 1


def test_independent_api_source_is_recorded_as_promotion_evidence() -> None:
    receipt = validate_and_promote_candidates(
        [_candidate(source_id="prd-1")],
        interfaces=[
            {
                "interface_id": "interface:GET:/orders",
                "source_id": "api-1",
                "path": "/订单",
                "summary": "查看订单",
            }
        ],
    )

    assert len(receipt.validated) == 1
    promoted = receipt.validated[0]
    assert promoted["promotion_evidence_sources"]["cross_ref_api_path"] == [
        "api-1"
    ]


def test_only_validated_entity_candidate_can_enter_entity_space() -> None:
    entries = candidates_to_behavior_ir_entries(
        [
            _candidate(name="订单", kind="entity", source_id="prd-1"),
            _candidate(name="金额", kind="field", source_id="prd-1"),
        ],
        [_candidate(name="管理员", kind="actor", source_id="prd-1")],
    )

    assert entries[0]["object"] == "订单"
    assert entries[0]["semantic_candidate_kind"] == "entity"
    assert "object" not in entries[1]
    assert entries[1]["semantic_candidate_kind"] == "field"
    assert entries[1]["_cannot_enter_entity_space"] is True
    assert "object" not in entries[2]
    assert entries[2]["behavior_ir_promotion_status"] == "PENDING_DIAGNOSTIC_ONLY"


def test_incremental_projection_recomputes_multi_source_entity_validation() -> None:
    from ai_test_asset_center.enterprise_knowledge_center.composition import (
        _incremental_refresh_semantic_candidate_projection,
    )

    asset = {
        "semantic_candidates": [
            _candidate(name="陌生业务对象", source_id="prd-1"),
            _candidate(name="陌生业务对象", source_id="api-1"),
        ],
        "interfaces": [],
        "data_tables": [],
        "rule_library": [],
        "state_machines": [],
        "business_objects": [
            {
                "object": "过期候选",
                "source": "semantic_extraction_validated",
            }
        ],
    }

    added = _incremental_refresh_semantic_candidate_projection(asset)

    assert added == 1
    assert [row["object"] for row in asset["business_objects"]] == [
        "陌生业务对象"
    ]
    receipt = asset["candidate_validation_receipt"]
    assert receipt["validated_count"] == 2
    assert receipt["pending_count"] == 0
