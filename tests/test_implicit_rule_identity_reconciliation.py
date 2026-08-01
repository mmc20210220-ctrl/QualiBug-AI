from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_fact_entailment import (
    derive_rule_candidates_from_business_facts,
)
from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_identity_reconciliation import (
    reconcile_implicit_rule_identities,
)
from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_projection import (
    enrich_asset_with_implicit_rule_projection,
)


FORMULA = "订单总额=明细金额之和"
IDEMPOTENCY = "同一付款请求不得重复成功扣款；重复提交时业务成功效果最多发生一次。"


def _span(source_id: str, locator: str) -> list[dict]:
    return [
        {
            "source_id": source_id,
            "locator": locator,
            "document_block_id": locator,
        }
    ]


def _base_asset() -> dict:
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


def _formula_fact() -> dict:
    return {
        "fact_id": "fact:formula",
        "fact_type": "DERIVED_VALUE",
        "status": "ACCEPTED",
        "raw_statement": FORMULA,
        "subject": {"entity_refs": ["订单总额"]},
        "formula_constraints": [
            {
                "raw": FORMULA,
                "lhs": "订单总额",
                "rhs": "明细金额之和",
            }
        ],
        "source_spans": _span("prd-1", "formula"),
        "confidence": 1.0,
    }


def test_untyped_source_rule_is_upgraded_in_place_without_parallel_rule():
    asset = _base_asset()
    asset["rule_library"] = [
        {
            "rule_id": "rule:source-formula",
            "source_id": "prd-1",
            "source_type": "business_requirement",
            "source_locator": "prd.md#formula",
            "statement": FORMULA,
            "expected": FORMULA,
            "risk_type": "data_conservation",
            "severity": "P0",
        }
    ]
    asset["business_fact_ledger"] = {"items": [_formula_fact()]}

    projected = enrich_asset_with_implicit_rule_projection(asset)
    reconciled = reconcile_implicit_rule_identities(projected)

    assert len(reconciled["rule_library"]) == 1
    rule = reconciled["rule_library"][0]
    assert rule["rule_id"] == "rule:source-formula"
    assert rule["derivation"] == "implicit_rule_entailment"
    assert rule["logical_form"] == "CONSERVATION_EQUATION"
    assert rule["kind"] == "conservation"
    assert rule["operator"] == "equation_holds"
    assert rule["equation"]["lhs"] == "订单总额"
    assert rule["equation"]["rhs"] == "明细金额之和"
    assert rule["source_rule_origin"]["rule_id"] == "rule:source-formula"
    assert rule["authority_upgrade_receipt"]["parallel_rule_row_created"] is False
    assert reconciled["implicit_rule_identity_reconciliation_receipt"]["status"] == "PASS"
    assert reconciled["implicit_rule_identity_reconciliation_receipt"][
        "merged_rule_ids"
    ] == ["rule:source-formula"]
    assert [row["risk_id"] for row in reconciled["risk_domains"]] == [
        "risk:rule:source-formula"
    ]
    assert [row["oracle_id"] for row in reconciled["oracle_library"]] == [
        "oracle:rule:source-formula"
    ]


def test_already_typed_equivalent_source_rule_suppresses_duplicate_candidate():
    asset = _base_asset()
    asset["rule_library"] = [
        {
            "rule_id": "rule:typed-formula",
            "source_id": "prd-1",
            "source_type": "business_requirement",
            "statement": FORMULA,
            "logical_form": "CONSERVATION_EQUATION",
            "operator": "equation_holds",
            "kind": "conservation",
            "structured_expression": {
                "logical_form": "CONSERVATION_EQUATION",
                "consequent": {
                    "operator": "equation_holds",
                    "lhs": "订单总额",
                    "rhs": "明细金额之和",
                },
            },
        }
    ]
    asset["business_fact_ledger"] = {"items": [_formula_fact()]}

    candidates = derive_rule_candidates_from_business_facts(asset)
    projected = enrich_asset_with_implicit_rule_projection(asset)
    reconciled = reconcile_implicit_rule_identities(projected)

    assert candidates == []
    assert [row["rule_id"] for row in reconciled["rule_library"]] == [
        "rule:typed-formula"
    ]
    assert reconciled["implicit_rule_projection_gate"][
        "typed_fact_candidate_count"
    ] == 0
    assert reconciled["implicit_rule_identity_reconciliation_receipt"]["status"] == (
        "NO_TYPED_SOURCE_RULE_UPGRADES"
    )


def test_second_projection_reuses_typed_rule_id_without_duplicate_risk_or_oracle():
    asset = _base_asset()
    asset["rule_library"] = [
        {
            "rule_id": "rule:source-formula",
            "source_id": "prd-1",
            "source_type": "business_requirement",
            "statement": FORMULA,
            "risk_type": "data_conservation",
        }
    ]
    asset["business_fact_ledger"] = {"items": [_formula_fact()]}

    once = reconcile_implicit_rule_identities(
        enrich_asset_with_implicit_rule_projection(deepcopy(asset))
    )
    twice_projected = enrich_asset_with_implicit_rule_projection(deepcopy(once))
    twice = reconcile_implicit_rule_identities(twice_projected)

    assert [row["rule_id"] for row in once["rule_library"]] == [
        "rule:source-formula"
    ]
    assert [row["rule_id"] for row in twice["rule_library"]] == [
        "rule:source-formula"
    ]
    candidate = next(
        row
        for row in twice["implicit_rule_candidates"]
        if row.get("logical_form") == "CONSERVATION_EQUATION"
    )
    assert candidate["rule_id"] == "rule:source-formula"
    assert candidate["authority_upgrade_target"]["match_kind"] == (
        "PREVIOUS_TYPED_PROJECTION_IDENTITY"
    )
    assert len(twice["risk_domains"]) == 1
    assert len(twice["oracle_library"]) == 1


def test_exact_interface_relation_and_typed_oracle_share_source_rule_id():
    asset = _base_asset()
    asset["interfaces"] = [
        {
            "interface_id": "api:POST:/payments",
            "source_id": "openapi-1",
            "method": "POST",
            "path": "/payments",
            "source_excerpt": f"提交付款请求\n{IDEMPOTENCY}",
        }
    ]
    asset["rule_library"] = [
        {
            "rule_id": "rule:payment-idempotency",
            "source_id": "prd-1",
            "source_type": "business_requirement",
            "statement": IDEMPOTENCY,
            "risk_type": "idempotency",
        }
    ]
    asset["business_fact_ledger"] = {
        "items": [
            {
                "fact_id": "fact:idempotency",
                "fact_type": "BUSINESS_RULE",
                "status": "ACCEPTED",
                "raw_statement": IDEMPOTENCY,
                "subject": {"entity_refs": ["付款请求"]},
                "action": {"canonical": "付款"},
                "source_spans": _span("prd-1", "payment-idempotency"),
                "confidence": 1.0,
            }
        ]
    }

    reconciled = reconcile_implicit_rule_identities(
        enrich_asset_with_implicit_rule_projection(asset)
    )

    assert len(reconciled["rule_library"]) == 1
    rule = reconciled["rule_library"][0]
    assert rule["rule_id"] == "rule:payment-idempotency"
    assert rule["logical_form"] == "IDEMPOTENCY"
    assert rule["operation_refs"] == ["api:POST:/payments"]
    exact_edges = [
        row
        for row in reconciled["relationships"]
        if row.get("from") == "rule:payment-idempotency"
        and row.get("to") == "api:POST:/payments"
        and row.get("derivation") == "exact_source_section"
    ]
    assert len(exact_edges) == 1
    oracle = next(
        row
        for row in reconciled["oracle_library"]
        if row.get("rule_id") == "rule:payment-idempotency"
    )
    assert oracle["linked_interfaces"] == ["api:POST:/payments"]
    assert oracle["family"] == "idempotency"


def test_missing_upgrade_target_fails_closed_before_behavior_ir():
    asset = _base_asset()
    asset["implicit_rule_candidate_validation_receipt"] = {
        "validated": [
            {
                "candidate_id": "candidate:missing-target",
                "kind": "rule",
                "statement": FORMULA,
                "logical_form": "CONSERVATION_EQUATION",
                "consequent": {
                    "operator": "equation_holds",
                    "lhs": "订单总额",
                    "rhs": "明细金额之和",
                },
                "supporting_fact_refs": ["fact:formula"],
                "supporting_source_ids": ["prd-1"],
                "source_refs": [
                    {
                        "source_id": "prd-1",
                        "source_locator": "prd.md#formula",
                    }
                ],
                "source_authority": "formal_constraint",
                "falsifiability": "EVALUABLE",
                "binding_readiness": "READY_FOR_IR_BINDING",
                "scope_status": "NOT_APPLICABLE",
                "exception_status": "NOT_APPLICABLE",
                "counterexample_plan": {"action": "change_operand"},
                "risk_type": "conservation",
                "authority_upgrade_target": {
                    "rule_id": "rule:missing",
                    "match_kind": "SOURCE_RULE_TYPED_SEMANTIC_UPGRADE",
                },
            }
        ]
    }
    asset["implicit_rule_projection_gate"] = {
        "status": "PASS",
        "entry_allowed": True,
    }

    reconciled = reconcile_implicit_rule_identities(asset)

    receipt = reconciled["implicit_rule_identity_reconciliation_receipt"]
    assert receipt["status"] == "BLOCKED"
    assert receipt["missing_targets"][0]["reason_code"] == (
        "SOURCE_RULE_UPGRADE_TARGET_MISSING"
    )
    assert reconciled["implicit_rule_projection_gate"]["status"] == (
        "BLOCKED_UPGRADE_TARGET_MISSING"
    )
    assert reconciled["implicit_rule_projection_gate"]["entry_allowed"] is False


def test_source_chinese_rule_relationship_is_refreshed_before_understanding() -> None:
    statement = "后台管理员删除商品记录"
    asset = _base_asset()
    asset["interfaces"] = [
        {
            "interface_id": "api:DELETE:/products/:id",
            "source_id": "api-doc",
            "method": "DELETE",
            "path": "/products/:id",
            "source_excerpt": f"DELETE /products/:id\n{statement}",
        }
    ]
    asset["rule_library"] = [
        {
            "rule_id": "zh_business:delete-product",
            "source_id": "api-doc",
            "source_type": "chinese_business_semantic_contract",
            "source_locator": "api.md#delete-product",
            "statement": statement,
            "derivation": "chinese_first_business_comprehension",
            "semantic_contract": {"status": "ACCEPTED"},
        }
    ]
    asset["business_fact_ledger"] = {"items": []}

    reconciled = reconcile_implicit_rule_identities(asset)

    edge = next(
        row
        for row in reconciled["relationships"]
        if row.get("from") == "zh_business:delete-product"
        and row.get("to") == "api:DELETE:/products/:id"
    )
    assert edge["status"] == "accepted"
    assert edge["derivation"] == "exact_source_section"
    assert reconciled["implicit_rule_identity_reconciliation_receipt"][
        "exact_binding_refreshed_rule_count"
    ] == 1
