from __future__ import annotations

from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_projection import (
    enrich_asset_with_implicit_rule_projection,
)
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir


def _asset(*, fact_operation_ref: str) -> dict:
    return {
        "field_dictionary": [],
        "data_tables": [],
        "interfaces": [
            {
                "interface_id": "refund_order",
                "operation_id": "refund_order",
                "method": "POST",
                "path": "/orders/{id}/refund",
                "source_id": "openapi-1",
                "summary": "refund one order",
                "request_schema": {},
                "response_schema": {},
            }
        ],
        "permission_matrix": [],
        "state_machines": [],
        "relationships": [],
        "risk_domains": [],
        "oracle_library": [],
        "coverage_gaps": [],
        "rule_library": [],
        "business_fact_ledger": {
            "items": [
                {
                    "fact_id": "fact:idempotency",
                    "fact_type": "BUSINESS_RULE",
                    "status": "ACCEPTED",
                    "raw_statement": "同一订单不得重复成功退款",
                    "subject": {"entity_refs": ["orders"]},
                    "action": {
                        "canonical": "退款",
                        "operation_ref": fact_operation_ref,
                    },
                    "source_spans": [
                        {
                            "source_id": "prd-1",
                            "locator": "prd.md#refund-idempotency",
                            "document_block_id": "refund-idempotency",
                        }
                    ],
                    "confidence": 1.0,
                }
            ]
        },
    }


def _implicit_rule(asset: dict) -> dict:
    return next(
        row
        for row in asset["rule_library"]
        if row.get("derivation") == "implicit_rule_entailment"
    )


def test_exact_operation_identity_reaches_existing_idempotency_obligation() -> None:
    projected = enrich_asset_with_implicit_rule_projection(
        _asset(fact_operation_ref="refund_order")
    )
    rule = _implicit_rule(projected)

    behavior_ir = build_behavior_ir_from_knowledge_asset(
        projected,
        project_id="implicit-idempotency-bridge",
    )
    invariant = next(
        row
        for row in behavior_ir["invariants"]
        if rule["rule_id"] in row.get("source_rule_refs", [])
    )
    operation = next(
        row
        for row in behavior_ir["operations"]
        if "refund_order" in row.get("source_operation_refs", [])
    )

    assert any(
        row.get("operation_ref") == operation["id"]
        and invariant["id"] in {row.get("from_ref"), row.get("to_ref")}
        for row in behavior_ir["relations"]
    )

    compiled = compile_obligations_from_behavior_ir(behavior_ir)
    obligations = [
        row
        for row in compiled["obligations"]
        if row.get("risk_family") == "idempotency"
        and row.get("property", {}).get("invariant_ref") == invariant["id"]
    ]

    assert len(obligations) == 1
    obligation = obligations[0]
    assert obligation["required_operations"] == [operation["id"]]
    assert obligation["property"]["expression"]["kind"] == "idempotency"
    assert obligation["property"]["expression"]["operator"] == (
        "business_effect_count"
    )
    assert obligation["property"]["expression"]["operands"][0][
        "expected_effect_count"
    ] == 1


def test_unresolved_operation_identity_never_falls_back_to_similarity_binding() -> None:
    projected = enrich_asset_with_implicit_rule_projection(
        _asset(fact_operation_ref="missing_operation_identity")
    )
    rule = _implicit_rule(projected)

    behavior_ir = build_behavior_ir_from_knowledge_asset(
        projected,
        project_id="implicit-idempotency-unresolved",
    )
    invariant = next(
        row
        for row in behavior_ir["invariants"]
        if rule["rule_id"] in row.get("source_rule_refs", [])
    )

    assert any(
        row.get("gap_type") == "source_relationship_unresolved"
        and row.get("source_rule_ref") == rule["rule_id"]
        and row.get("source_operation_ref") == "missing_operation_identity"
        for row in behavior_ir["coverage_gaps"]
    )
    assert not any(
        invariant["id"] in {row.get("from_ref"), row.get("to_ref")}
        and row.get("operation_ref")
        for row in behavior_ir["relations"]
    )

    compiled = compile_obligations_from_behavior_ir(behavior_ir)
    assert not any(
        row.get("risk_family") == "idempotency"
        and row.get("property", {}).get("invariant_ref") == invariant["id"]
        for row in compiled["obligations"]
    )
    assert any(
        row.get("code") == "BLOCKED_MISSING_IR_RELATION"
        and row.get("subject_ref") == invariant["id"]
        for row in compiled["coverage_gaps"]
    )
