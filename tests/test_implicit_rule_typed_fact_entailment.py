from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_projection import (
    enrich_asset_with_implicit_rule_projection,
)


def _base_asset():
    return {
        "field_dictionary": [],
        "data_tables": [],
        "interfaces": [],
        "permission_matrix": [],
        "state_machines": [],
        "relationships": [],
        "risk_domains": [],
        "oracle_library": [],
        "coverage_gaps": [],
        "rule_library": [],
    }


def _span(block_id):
    return [
        {
            "source_id": "prd-1",
            "locator": f"prd.md#block={block_id}",
            "document_block_id": block_id,
        }
    ]


def test_accepted_cardinality_and_formula_facts_enter_existing_rule_library():
    asset = _base_asset()
    asset["business_fact_ledger"] = {
        "items": [
            {
                "fact_id": "fact:cardinality",
                "fact_type": "CARDINALITY_CONSTRAINT",
                "status": "ACCEPTED",
                "raw_statement": "每个订单只能对应一个客户",
                "subject": {"entity_refs": ["订单"]},
                "object": {"entity_refs": ["客户"]},
                "value": {
                    "cardinality": "EXACTLY_ONE",
                    "minimum": 1,
                    "maximum": "1",
                },
                "source_spans": _span("cardinality"),
                "confidence": 1.0,
            },
            {
                "fact_id": "fact:formula",
                "fact_type": "DERIVED_VALUE",
                "status": "ACCEPTED",
                "raw_statement": "订单总额=明细金额之和",
                "subject": {"entity_refs": ["订单总额"]},
                "formula_constraints": [
                    {
                        "raw": "订单总额=明细金额之和",
                        "lhs": "订单总额",
                        "rhs": "明细金额之和",
                    }
                ],
                "source_spans": _span("formula"),
                "confidence": 1.0,
            },
        ]
    }

    projected = enrich_asset_with_implicit_rule_projection(asset)
    accepted = {
        row.get("logical_form"): row
        for row in projected["rule_library"]
        if row.get("derivation") == "implicit_rule_entailment"
    }

    assert {"CARDINALITY", "CONSERVATION_EQUATION"} <= set(accepted)
    assert accepted["CARDINALITY"]["structured_expression"]["consequent"][
        "operator"
    ] == "cardinality"
    assert accepted["CARDINALITY"]["operator"] == "cardinality"
    assert accepted["CARDINALITY"]["kind"] == "cardinality"
    assert accepted["CONSERVATION_EQUATION"]["structured_expression"][
        "consequent"
    ]["operator"] == "equation_holds"
    assert accepted["CONSERVATION_EQUATION"]["kind"] == "conservation"
    assert accepted["CONSERVATION_EQUATION"]["operator"] == "equation_holds"
    assert accepted["CONSERVATION_EQUATION"]["equation"] == {
        "operator": "equation_holds",
        "lhs": "订单总额",
        "rhs": "明细金额之和",
        "raw": "订单总额=明细金额之和",
        "terms": ["订单总额", "明细金额之和"],
    }
    assert projected["implicit_rule_projection_gate"][
        "typed_fact_candidate_count"
    ] == 2
    assert projected["implicit_rule_projection_gate"]["parallel_rule_ir_created"] is False


def test_accepted_state_temporal_and_idempotency_facts_are_governed_candidates():
    asset = _base_asset()
    asset["business_fact_ledger"] = {
        "items": [
            {
                "fact_id": "fact:state-precondition",
                "fact_type": "BUSINESS_RULE",
                "status": "ACCEPTED",
                "raw_statement": "只有审批通过才允许订单发货",
                "subject": {
                    "actor_refs": ["仓库员"],
                    "entity_refs": ["订单"],
                },
                "action": {
                    "canonical": "发货",
                    "operation_ref": "ship_order",
                },
                "conditions": ["审批通过"],
                "condition_combinator": "AND",
                "modality": "ONLY_IF",
                "source_spans": _span("state-precondition"),
                "confidence": 1.0,
            },
            {
                "fact_id": "fact:forbidden-transition",
                "fact_type": "STATE_TRANSITION",
                "status": "ACCEPTED",
                "raw_statement": "已取消订单不得转为已发货",
                "subject": {"entity_refs": ["订单"]},
                "action": {
                    "canonical": "发货",
                    "operation_ref": "ship_order",
                },
                "state_effects": [
                    {
                        "from_state": "已取消",
                        "to_state": "已发货",
                        "raw": "已取消转为已发货",
                    }
                ],
                "modality": "MUST_NOT",
                "polarity": "NEGATIVE",
                "source_spans": _span("forbidden-transition"),
                "confidence": 1.0,
            },
            {
                "fact_id": "fact:temporal",
                "fact_type": "BUSINESS_RULE",
                "status": "ACCEPTED",
                "raw_statement": "订单创建后24小时以内必须付款",
                "subject": {"entity_refs": ["订单"]},
                "action": {
                    "canonical": "付款",
                    "operation_ref": "pay_order",
                },
                "time_window_constraints": [
                    {
                        "raw": "订单创建后24小时以内",
                        "anchor": "订单创建",
                        "relation": "后",
                        "duration": "24小时",
                        "source_backed": True,
                    }
                ],
                "modality": "MUST",
                "source_spans": _span("temporal"),
                "confidence": 1.0,
            },
            {
                "fact_id": "fact:idempotency",
                "fact_type": "BUSINESS_RULE",
                "status": "ACCEPTED",
                "raw_statement": "同一订单不得重复成功退款",
                "subject": {"entity_refs": ["订单"]},
                "action": {
                    "canonical": "退款",
                    "operation_ref": "refund_order",
                },
                "modality": "MUST_NOT",
                "source_spans": _span("idempotency"),
                "confidence": 1.0,
            },
        ]
    }

    projected = enrich_asset_with_implicit_rule_projection(asset)
    accepted = {
        row["logical_form"]: row
        for row in projected["rule_library"]
        if row.get("derivation") == "implicit_rule_entailment"
    }

    assert set(accepted) == {
        "STATE_PRECONDITION",
        "FORBIDDEN_STATE_TRANSITION",
        "TEMPORAL_WINDOW",
        "IDEMPOTENCY",
    }
    assert accepted["STATE_PRECONDITION"]["kind"] == "state_precondition"
    assert accepted["STATE_PRECONDITION"]["operator"] == (
        "operation_allowed_only_when"
    )
    assert accepted["STATE_PRECONDITION"]["operation_refs"] == ["ship_order"]
    assert accepted["FORBIDDEN_STATE_TRANSITION"]["kind"] == (
        "forbidden_state_transition"
    )
    assert accepted["FORBIDDEN_STATE_TRANSITION"]["operator"] == (
        "must_not_transition"
    )
    assert accepted["TEMPORAL_WINDOW"]["kind"] == "temporal_window"
    assert accepted["TEMPORAL_WINDOW"]["operator"] == "within_source_window"
    assert accepted["TEMPORAL_WINDOW"]["operation_refs"] == ["pay_order"]
    assert accepted["IDEMPOTENCY"]["kind"] == "idempotency"
    assert accepted["IDEMPOTENCY"]["operator"] == "business_effect_count"
    assert accepted["IDEMPOTENCY"]["operands"][0]["expected_effect_count"] == 1
    assert projected["implicit_rule_projection_gate"][
        "typed_fact_candidate_count"
    ] == 4


def test_unresolved_condition_logic_and_unmarked_repeat_do_not_invent_rules():
    asset = _base_asset()
    asset["business_fact_ledger"] = {
        "items": [
            {
                "fact_id": "fact:unresolved-condition",
                "fact_type": "BUSINESS_RULE",
                "status": "ACCEPTED",
                "raw_statement": "满足审批或库存条件后允许发货",
                "subject": {"entity_refs": ["订单"]},
                "action": {"canonical": "发货"},
                "conditions": ["审批通过", "库存充足"],
                "condition_combinator": "UNRESOLVED",
                "source_spans": _span("unresolved-condition"),
                "confidence": 1.0,
            },
            {
                "fact_id": "fact:ordinary-action",
                "fact_type": "BUSINESS_RULE",
                "status": "ACCEPTED",
                "raw_statement": "订单退款后发送通知",
                "subject": {"entity_refs": ["订单"]},
                "action": {"canonical": "退款"},
                "source_spans": _span("ordinary-action"),
                "confidence": 1.0,
            },
        ]
    }

    projected = enrich_asset_with_implicit_rule_projection(asset)

    assert [
        row
        for row in projected["rule_library"]
        if row.get("derivation") == "implicit_rule_entailment"
    ] == []
    assert projected["implicit_rule_projection_gate"][
        "typed_fact_candidate_count"
    ] == 0


def test_critical_uncompiled_rule_span_blocks_rule_projection_gate():
    asset = _base_asset()
    asset["business_fact_ledger"] = {"items": []}
    asset["business_fact_candidate_ledger"] = {
        "items": [
            {
                "candidate_id": "fact_candidate:critical",
                "span_id": "span:critical",
                "source_id": "prd-1",
                "block_type": "PARAGRAPH",
                "critical": True,
                "contains_candidate_signal": True,
                "status": "PENDING_WITH_REASON",
                "reason_codes": ["STRUCTURE_BACKED_CANDIDATE_NOT_COMPILED"],
                "evidence_address": {
                    "source_id": "prd-1",
                    "locator": "prd.md#block=critical",
                },
                "quote": "只有审批通过后才允许发货",
            }
        ]
    }

    projected = enrich_asset_with_implicit_rule_projection(asset)

    assert projected["implicit_rule_projection_gate"]["status"] == (
        "BLOCKED_SOURCE_SPAN_COVERAGE"
    )
    assert projected["implicit_rule_projection_gate"]["entry_allowed"] is False
    assert projected["implicit_rule_projection_gate"][
        "critical_uncovered_rule_span_count"
    ] == 1
    assert any(
        row.get("kind") == "IMPLICIT_RULE_SOURCE_SPAN_UNCOVERED"
        for row in projected["coverage_gaps"]
    )


def test_typed_fact_projection_is_idempotent():
    asset = _base_asset()
    asset["business_fact_ledger"] = {
        "items": [
            {
                "fact_id": "fact:formula",
                "fact_type": "DERIVED_VALUE",
                "status": "ACCEPTED",
                "raw_statement": "订单总额=明细金额之和",
                "subject": {"entity_refs": ["订单总额"]},
                "formula_constraints": [
                    {
                        "raw": "订单总额=明细金额之和",
                        "lhs": "订单总额",
                        "rhs": "明细金额之和",
                    }
                ],
                "source_spans": _span("formula"),
                "confidence": 1.0,
            }
        ]
    }

    once = enrich_asset_with_implicit_rule_projection(deepcopy(asset))
    twice = enrich_asset_with_implicit_rule_projection(deepcopy(once))

    once_ids = [
        row["rule_id"]
        for row in once["rule_library"]
        if row.get("derivation") == "implicit_rule_entailment"
    ]
    twice_ids = [
        row["rule_id"]
        for row in twice["rule_library"]
        if row.get("derivation") == "implicit_rule_entailment"
    ]
    assert twice_ids == once_ids
    assert len(twice["risk_domains"]) == len(once["risk_domains"])
    assert len(twice["oracle_library"]) == len(once["oracle_library"])


def test_typed_fact_does_not_duplicate_an_existing_authoritative_rule():
    asset = _base_asset()
    asset["rule_library"] = [
        {
            "rule_id": "rule:explicit-formula",
            "source_id": "prd-1",
            "source_type": "business_requirement",
            "statement": "订单总额=明细金额之和",
            "risk_type": "conservation",
        }
    ]
    asset["business_fact_ledger"] = {
        "items": [
            {
                "fact_id": "fact:formula",
                "fact_type": "DERIVED_VALUE",
                "status": "ACCEPTED",
                "raw_statement": "订单总额 = 明细金额之和",
                "subject": {"entity_refs": ["订单总额"]},
                "formula_constraints": [
                    {
                        "raw": "订单总额 = 明细金额之和",
                        "lhs": "订单总额",
                        "rhs": "明细金额之和",
                    }
                ],
                "source_spans": _span("formula"),
                "confidence": 1.0,
            }
        ]
    }

    projected = enrich_asset_with_implicit_rule_projection(asset)

    assert [row["rule_id"] for row in projected["rule_library"]] == [
        "rule:explicit-formula"
    ]
    assert projected["implicit_rule_projection_gate"][
        "typed_fact_candidate_count"
    ] == 0
