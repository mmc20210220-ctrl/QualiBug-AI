from __future__ import annotations

from benchmark_evaluator.product_quality.semantic_funnel import (
    SEMANTIC_FUNNEL_QUALITY_CLAIM,
    build_semantic_capture,
    semantic_funnel_for_anchor,
)


QUOTE = "BR-IDEM-001: 相同order_ref的订单不可重复创建，应返回409"
STATE_QUOTE = "BR-STATE-001: 确认（confirm）仅允许从CREATED状态执行"


def _anchor(*surfaces: str, quote: str = QUOTE) -> dict:
    return {
        "anchor_id": "WMS-TEST",
        "exact_quote": quote,
        "expected_surfaces": list(surfaces),
    }


def _outputs(*, obligation: bool = False, design: bool = False) -> dict[str, list[str]]:
    return {
        "requirement_finding_ids": [],
        "test_obligation_ids": ["obligation:1"] if obligation else [],
        "test_design_ids": ["design:1"] if design else [],
    }


def test_semantic_capture_preserves_fact_behavior_and_grounding_truth() -> None:
    asset = {
        "business_fact_ledger": {
            "items": [
                {
                    "fact_id": "fact:idem",
                    "kind": "IDEMPOTENCY",
                    "status": "ACCEPTED",
                    "action": {"raw": "创建订单"},
                    "object": {"entity_refs": ["order"]},
                    "evidence": [{"source_id": "rules", "quote": QUOTE}],
                }
            ]
        },
        "enterprise_understanding_model": {
            "business_behaviors": [
                {
                    "behavior_id": "behavior:idem",
                    "status": "CONFIRMED",
                    "formal_business_rule": True,
                    "candidate_only": False,
                    "operation_ref": "create_order",
                    "object_refs": ["order"],
                    "source_fact_ids": ["fact:idem"],
                    "evidence": [{"source_id": "rules", "quote": QUOTE}],
                }
            ]
        },
    }

    capture = build_semantic_capture(asset)

    assert capture["review_truth_loaded"] is False
    assert capture["fact_count"] == 1
    assert capture["behavior_count"] == 1
    assert capture["lifecycle_transition_count"] == 0
    assert capture["facts"][0]["kind"] == "IDEMPOTENCY"
    assert capture["behaviors"][0]["operation_ref"] == "create_order"
    assert capture["behaviors"][0]["object_refs"] == ["order"]
    assert capture["behaviors"][0]["grounded_for_test_intelligence"] is True


def test_semantic_capture_includes_canonical_lifecycle_path() -> None:
    capture = build_semantic_capture(
        {
            "business_fact_ledger": {
                "items": [
                    {
                        "fact_id": "fact:state",
                        "kind": "STATE_TRANSITION",
                        "evidence": [{"source_id": "rules", "quote": STATE_QUOTE}],
                    }
                ]
            },
            "enterprise_understanding_model": {
                "business_behaviors": [],
                "lifecycles": [
                    {
                        "object_ref": "order",
                        "transitions": [
                            {
                                "transition_id": "transition:confirm",
                                "from_state": "CREATED",
                                "to_state": "CONFIRMED",
                                "transition_kind": "ALLOWED",
                                "completeness": "COMPLETE",
                                "operation_ref": "confirm",
                                "fact_refs": ["fact:state"],
                                "evidence": [
                                    {"source_id": "rules", "quote": STATE_QUOTE}
                                ],
                            }
                        ],
                    }
                ],
            },
        }
    )

    assert capture["lifecycle_transition_count"] == 1
    assert capture["lifecycle_transitions"][0]["transition_id"] == "transition:confirm"
    assert capture["lifecycle_transitions"][0]["eligible_for_test_intelligence"] is True

    result = semantic_funnel_for_anchor(
        _anchor("test_obligation", "test_design", quote=STATE_QUOTE),
        capture,
        _outputs(obligation=True, design=True),
    )
    assert result["first_break_stage"] == "END_TO_END_REACHED"
    assert result["matched_behavior_ids"] == []
    assert result["eligible_lifecycle_transition_ids"] == ["transition:confirm"]


def test_funnel_reports_end_to_end_only_when_every_current_stage_is_present() -> None:
    capture = build_semantic_capture(
        {
            "business_fact_ledger": {
                "items": [
                    {
                        "fact_id": "fact:idem",
                        "kind": "IDEMPOTENCY",
                        "status": "ACCEPTED",
                        "evidence": [{"source_id": "rules", "quote": QUOTE}],
                    }
                ]
            },
            "enterprise_understanding_model": {
                "business_behaviors": [
                    {
                        "behavior_id": "behavior:idem",
                        "status": "CONFIRMED",
                        "formal_business_rule": True,
                        "operation_ref": "create_order",
                        "object_refs": ["order"],
                        "source_fact_ids": ["fact:idem"],
                        "evidence": [{"source_id": "rules", "quote": QUOTE}],
                    }
                ]
            },
        }
    )

    result = semantic_funnel_for_anchor(
        _anchor("test_obligation", "test_design"),
        capture,
        _outputs(obligation=True, design=True),
    )

    assert result["first_break_stage"] == "END_TO_END_REACHED"
    assert result["matched_fact_ids"] == ["fact:idem"]
    assert result["formal_behavior_ids"] == ["behavior:idem"]
    assert result["test_obligation_ids"] == ["obligation:1"]
    assert result["test_design_ids"] == ["design:1"]
    assert result["automatic_quality_verdict"] is False
    assert result["human_review_required"] is True
    assert result["quality_claim"] == SEMANTIC_FUNNEL_QUALITY_CLAIM


def test_funnel_identifies_fact_extraction_as_first_break() -> None:
    result = semantic_funnel_for_anchor(
        _anchor("test_obligation", "test_design"),
        {"facts": [], "behaviors": [], "lifecycle_transitions": []},
        _outputs(),
    )
    assert result["first_break_stage"] == "FACT_EXTRACTION"


def test_funnel_identifies_semantic_unit_projection_as_first_break() -> None:
    capture = {
        "facts": [{"fact_id": "fact:idem", "evidence_quotes": [QUOTE]}],
        "behaviors": [],
        "lifecycle_transitions": [],
    }
    result = semantic_funnel_for_anchor(
        _anchor("test_obligation", "test_design"), capture, _outputs()
    )
    assert result["first_break_stage"] == "SEMANTIC_UNIT_PROJECTION"


def test_funnel_identifies_semantic_grounding_as_first_break() -> None:
    capture = {
        "facts": [{"fact_id": "fact:idem", "evidence_quotes": [QUOTE]}],
        "behaviors": [
            {
                "behavior_id": "behavior:idem",
                "status": "INCOMPLETE",
                "formal_business_rule": False,
                "grounded_for_test_intelligence": False,
                "source_fact_ids": ["fact:idem"],
                "evidence_quotes": [QUOTE],
                "operation_ref": "",
                "object_refs": ["order"],
            }
        ],
        "lifecycle_transitions": [],
    }
    result = semantic_funnel_for_anchor(
        _anchor("test_obligation", "test_design"), capture, _outputs()
    )
    assert result["first_break_stage"] == "SEMANTIC_GROUNDING"


def test_funnel_identifies_formal_semantic_confirmation_as_first_break() -> None:
    capture = {
        "facts": [{"fact_id": "fact:idem", "evidence_quotes": [QUOTE]}],
        "behaviors": [
            {
                "behavior_id": "behavior:idem",
                "status": "CANDIDATE",
                "formal_business_rule": False,
                "candidate_only": True,
                "grounded_for_test_intelligence": True,
                "source_fact_ids": ["fact:idem"],
                "evidence_quotes": [QUOTE],
                "operation_ref": "create_order",
                "object_refs": ["order"],
            }
        ],
        "lifecycle_transitions": [],
    }
    result = semantic_funnel_for_anchor(
        _anchor("test_obligation", "test_design"), capture, _outputs()
    )
    assert result["first_break_stage"] == "FORMAL_SEMANTIC_CONFIRMATION"


def test_funnel_distinguishes_obligation_and_design_projection_breaks() -> None:
    capture = {
        "facts": [{"fact_id": "fact:idem", "evidence_quotes": [QUOTE]}],
        "behaviors": [
            {
                "behavior_id": "behavior:idem",
                "status": "CONFIRMED",
                "formal_business_rule": True,
                "candidate_only": False,
                "grounded_for_test_intelligence": True,
                "source_fact_ids": ["fact:idem"],
                "evidence_quotes": [QUOTE],
                "operation_ref": "create_order",
                "object_refs": ["order"],
            }
        ],
        "lifecycle_transitions": [],
    }

    missing_obligation = semantic_funnel_for_anchor(
        _anchor("test_obligation", "test_design"), capture, _outputs()
    )
    missing_design = semantic_funnel_for_anchor(
        _anchor("test_obligation", "test_design"),
        capture,
        _outputs(obligation=True),
    )

    assert missing_obligation["first_break_stage"] == "TEST_OBLIGATION_PROJECTION"
    assert missing_design["first_break_stage"] == "TEST_DESIGN_PROJECTION"


def test_existing_final_output_prevents_false_intermediate_failure_claim() -> None:
    result = semantic_funnel_for_anchor(
        _anchor("test_obligation"),
        {"facts": [], "behaviors": [], "lifecycle_transitions": []},
        _outputs(obligation=True),
    )
    assert result["first_break_stage"] == "INTERMEDIATE_TRACE_GAP_WITH_PRODUCT_OUTPUT"


def test_requirement_only_anchor_does_not_get_forced_through_test_funnel() -> None:
    result = semantic_funnel_for_anchor(
        _anchor("requirement_finding"),
        {"facts": [], "behaviors": [], "lifecycle_transitions": []},
        {
            "requirement_finding_ids": ["finding:1"],
            "test_obligation_ids": [],
            "test_design_ids": [],
        },
    )
    assert result["first_break_stage"] == "NOT_APPLICABLE_REQUIREMENT_ONLY"


def test_statement_or_title_text_is_not_used_as_source_evidence() -> None:
    capture = build_semantic_capture(
        {
            "business_fact_ledger": {
                "items": [
                    {
                        "fact_id": "fact:false-positive",
                        "statement": QUOTE,
                        "evidence": [{"source_id": "rules", "quote": "另一条规则"}],
                    }
                ]
            }
        }
    )
    result = semantic_funnel_for_anchor(
        _anchor("test_obligation"), capture, _outputs()
    )
    assert result["matched_fact_ids"] == []
    assert result["first_break_stage"] == "FACT_EXTRACTION"
