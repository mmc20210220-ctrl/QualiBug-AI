from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center._candidate_validation import (
    validate_and_promote_candidates,
)
from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_projection import (
    enrich_asset_with_implicit_rule_projection,
)


def _formal_rule_without_source():
    return {
        "kind": "rule",
        "name": "orders.amount must be non-null",
        "statement": "orders.amount must be non-null",
        "logical_form": "REQUIRED_FIELD",
        "supporting_fact_refs": ["field:orders.amount"],
        "source_refs": [
            {
                "source_id": "",
                "source_locator": "schema.sql#orders.amount",
                "kind": "formal_constraint",
            }
        ],
        "source_authority": "formal_constraint",
        "derivation_basis": ["schema_entailment"],
        "falsifiability": "EVALUABLE",
        "binding_readiness": "READY_FOR_IR_BINDING",
        "scope_status": "NOT_APPLICABLE",
        "exception_status": "NOT_APPLICABLE",
        "counterexample_plan": {"mutate": "null"},
        "antecedents": [{"entity_ref": "table:orders"}],
        "consequent": {
            "field_ref": "field:orders.amount",
            "operator": "not_null",
        },
    }


def _asset():
    return {
        "field_dictionary": [
            {
                "field_id": "field:orders.order_no",
                "field": "order_no",
                "table": "orders",
                "table_id": "table:orders",
                "source_id": "schema.sql",
                "nullable": False,
                "unique": True,
            }
        ],
        "data_tables": [
            {
                "table_id": "table:orders",
                "name": "orders",
                "source_id": "schema.sql",
                "columns": ["order_no"],
                "identity_fields": ["order_no"],
            }
        ],
        "interfaces": [],
        "permission_matrix": [],
        "state_machines": [],
        "relationships": [],
        "risk_domains": [],
        "oracle_library": [],
        "coverage_gaps": [],
        "rule_library": [
            {
                "rule_id": "industry:1",
                "source_id": "industry_inference",
                "source_type": "derived_inference",
                "statement": "industry convention",
                "risk_type": "business_logic",
            }
        ],
    }


def test_rule_locator_without_source_identity_cannot_authorize():
    receipt = validate_and_promote_candidates([_formal_rule_without_source()])
    assert receipt.validated == []
    assert len(receipt.rejected) == 1
    assert receipt.rejected[0]["reason"] == "rule_source_identity_missing"


def test_reprojection_replaces_prior_derived_artifacts_and_keeps_prior_pending():
    once = enrich_asset_with_implicit_rule_projection(deepcopy(_asset()))
    twice = enrich_asset_with_implicit_rule_projection(deepcopy(once))

    once_rule_ids = [
        row["rule_id"]
        for row in once["rule_library"]
        if row.get("derivation") == "implicit_rule_entailment"
    ]
    twice_rule_ids = [
        row["rule_id"]
        for row in twice["rule_library"]
        if row.get("derivation") == "implicit_rule_entailment"
    ]
    assert twice_rule_ids == once_rule_ids
    assert len(twice["relationships"]) == len(once["relationships"])
    assert len(twice["risk_domains"]) == len(once["risk_domains"])
    assert len(twice["oracle_library"]) == len(once["oracle_library"])
    assert len(twice["coverage_gaps"]) == len(once["coverage_gaps"])

    pending = twice["implicit_rule_candidate_validation_receipt"]["pending"]
    assert any(row.get("logical_form") == "PRIOR_HYPOTHESIS" for row in pending)
    assert twice["implicit_rule_projection_gate"]["reprojection_is_idempotent"] is True
