from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_governance import (
    enrich_asset_with_governed_implicit_rule_projection,
)


IDEMPOTENCY = "同一付款请求不得重复成功扣款；重复提交时业务成功效果最多发生一次。"
RULE_ID = "rule:payment-idempotency"
INTERFACE_ID = "api:POST:/payments"


def _asset() -> dict:
    return {
        "source_inventory": [
            {
                "source_id": "rules.md",
                "status": "active",
                "content_hash": "rules-hash-v1",
                "source_version_id": "rules-version-v1",
                "source_type": "business_rules",
            },
            {
                "source_id": "payment-api.json",
                "status": "active",
                "content_hash": "api-hash-v1",
                "source_version_id": "api-version-v1",
                "source_type": "openapi",
            },
        ],
        "field_dictionary": [],
        "data_tables": [],
        "interfaces": [
            {
                "interface_id": INTERFACE_ID,
                "source_id": "payment-api.json",
                "method": "POST",
                "path": "/payments",
                "operation_id": "submitPayment",
                "source_excerpt": f"提交付款请求\n{IDEMPOTENCY}",
                "source_excerpt_authority": (
                    "OPENAPI_OPERATION_SUMMARY_DESCRIPTION"
                ),
            }
        ],
        "permission_matrix": [],
        "state_machines": [],
        "relationships": [],
        "risk_domains": [],
        "oracle_library": [],
        "coverage_gaps": [],
        "rule_library": [
            {
                "rule_id": RULE_ID,
                "source_id": "rules.md",
                "source_type": "business_rules",
                "source_locator": "rules.md#payment",
                "statement": IDEMPOTENCY,
                "rule_type": "idempotency",
                "risk_type": "idempotency",
                "severity": "P0",
            }
        ],
        "business_fact_ledger": {
            "items": [
                {
                    "fact_id": "fact:payment-idempotency",
                    "fact_type": "BUSINESS_RULE",
                    "status": "ACCEPTED",
                    "raw_statement": IDEMPOTENCY,
                    "subject": {"entity_refs": ["付款请求"]},
                    "action": {"canonical": "付款"},
                    "source_spans": [
                        {
                            "source_id": "rules.md",
                            "locator": "rules.md#payment",
                            "document_block_id": "payment",
                        }
                    ],
                    "confidence": 1.0,
                }
            ]
        },
    }


def _exact_edges(asset: dict) -> list[dict]:
    return [
        row
        for row in asset["relationships"]
        if row.get("from") == RULE_ID
        and row.get("to") == INTERFACE_ID
        and row.get("relation") == "rule_to_interface"
        and row.get("derivation") == "exact_source_section"
        and row.get("status") == "accepted"
    ]


def _oracle(asset: dict) -> dict:
    return next(
        row
        for row in asset["oracle_library"]
        if row.get("rule_id") == RULE_ID
    )


def test_governed_reprojection_preserves_exact_binding_and_typed_oracle():
    once = enrich_asset_with_governed_implicit_rule_projection(_asset())
    twice = enrich_asset_with_governed_implicit_rule_projection(deepcopy(once))

    for projected in (once, twice):
        assert len(projected["rule_library"]) == 1
        rule = projected["rule_library"][0]
        assert rule["rule_id"] == RULE_ID
        assert rule["derivation"] == "implicit_rule_entailment"
        assert rule["logical_form"] == "IDEMPOTENCY"
        assert rule["operator"] == "business_effect_count"
        assert rule["operation_refs"] == [INTERFACE_ID]
        assert rule["downstream_binding_status"] == (
            "READY_AUTHORITATIVE_OPERATION_BOUND"
        )
        assert len(_exact_edges(projected)) == 1
        oracle = _oracle(projected)
        assert oracle["oracle_id"] == f"oracle:{RULE_ID}"
        assert oracle["family"] == "idempotency"
        assert oracle["linked_interfaces"] == [INTERFACE_ID]
        assert projected["implicit_rule_identity_reconciliation_receipt"][
            "exact_binding_refreshed_rule_count"
        ] == 1
        assert projected["implicit_rule_projection_gate"][
            "identity_reconciliation_before_lifecycle"
        ] is True

    assert [row["rule_id"] for row in twice["rule_library"]] == [RULE_ID]
    assert len(twice["risk_domains"]) == 1
    assert len(twice["oracle_library"]) == 1
    assert twice["implicit_rule_lifecycle_ledger"]["active_rule_count"] == 1
    assert twice["implicit_rule_lifecycle_ledger"]["stale_rule_count"] == 0
