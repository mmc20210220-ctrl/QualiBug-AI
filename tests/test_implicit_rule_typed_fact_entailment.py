from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_fact_entailment import (
    CANONICAL_BEHAVIOR_OWNED_SLOTS,
)
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


def test_cardinality_is_recognized_but_formula_is_the_only_executable_rule():
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
    validation = projected["implicit_rule_candidate_validation_receipt"]
    cardinality = next(
        row
        for row in validation["pending"]
        if row.get("logical_form") == "CARDINALITY"
    )

    assert set(accepted) == {"CONSERVATION_EQUATION"}
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
    assert cardinality["binding_readiness"] == (
        "BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE"
    )
    assert cardinality["execution_capability"] == {
        "status": "BLOCKED_ASSERTION_EVIDENCE_UNPRODUCIBLE",
        "assertion_kind": "cardinality",
        "missing_observation_key": "collection",
        "authority": "assertion_dsl_kind_to_evidence_contract",
    }
    assert "binding_readiness_pass" in cardinality["pending_gates"]
    assert projected["implicit_rule_projection_gate"][
        "typed_fact_candidate_count"
    ] == 2
    assert projected["implicit_rule_projection_gate"]["accepted_rule_count"] == 1
    assert projected["implicit_rule_projection_gate"]["pending_rule_count"] == 1
    assert projected["implicit_rule_projection_gate"]["parallel_rule_ir_created"] is False
    assert any(
        row.get("kind") == "IMPLICIT_RULE_AUTHORITY_INSUFFICIENT"
        and row.get("logical_form") == "CARDINALITY"
        for row in projected["coverage_gaps"]
    )


def test_explicit_idempotency_fact_enters_rule_library_without_guessing():
    asset = _base_asset()
    asset["business_fact_ledger"] = {
        "items": [
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
            }
        ]
    }

    projected = enrich_asset_with_implicit_rule_projection(asset)
    accepted = [
        row
        for row in projected["rule_library"]
        if row.get("derivation") == "implicit_rule_entailment"
    ]

    assert len(accepted) == 1
    rule = accepted[0]
    assert rule["logical_form"] == "IDEMPOTENCY"
    assert rule["kind"] == "idempotency"
    assert rule["operator"] == "business_effect_count"
    assert rule["operation_refs"] == ["refund_order"]
    assert rule["operands"][0]["expected_effect_count"] == 1
    assert rule["counterexample_plan"]["repetitions"] == 2
    assert projected["implicit_rule_projection_gate"][
        "typed_fact_candidate_count"
    ] == 1


def test_condition_state_and_temporal_slots_stay_on_canonical_authorities():
    assert {
        "action",
        "conditions",
        "condition_frame",
        "condition_combinator",
        "state_effects",
        "time_window_constraints",
        "permission_decision",
        "expected_effects",
        "data_effects",
    } == set(CANONICAL_BEHAVIOR_OWNED_SLOTS)

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
                "action": {"canonical": "发货", "operation_ref": "ship_order"},
                "conditions": ["审批通过"],
                "condition_combinator": "AND",
                "permission_decision": "ALLOW",
                "source_spans": _span("state-precondition"),
                "confidence": 1.0,
            },
            {
                "fact_id": "fact:forbidden-transition",
                "fact_type": "STATE_TRANSITION",
                "status": "ACCEPTED",
                "raw_statement": "已取消订单不得转为已发货",
                "subject": {"entity_refs": ["订单"]},
                "action": {"canonical": "发货", "operation_ref": "ship_order"},
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
                "action": {"canonical": "付款", "operation_ref": "pay_order"},
                "time_window_constraints": [
                    {
                        "raw": "订单创建后24小时以内",
                        "anchor": "订单创建",
                        "relation": "后",
                        "duration": "24小时",
                        "source_backed": True,
                    }
                ],
                "source_spans": _span("temporal"),
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


def test_ordinary_action_does_not_invent_idempotency():
    asset = _base_asset()
    asset["business_fact_ledger"] = {
        "items": [
            {
                "fact_id": "fact:ordinary-action",
                "fact_type": "BUSINESS_RULE",
                "status": "ACCEPTED",
                "raw_statement": "订单退款后发送通知",
                "subject": {"entity_refs": ["订单"]},
                "action": {"canonical": "退款"},
                "source_spans": _span("ordinary-action"),
                "confidence": 1.0,
            }
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
