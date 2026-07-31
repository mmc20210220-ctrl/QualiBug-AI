from ai_test_asset_center.behavior_ir import build_behavior_ir_from_knowledge_asset
from ai_test_asset_center.enterprise_knowledge_center._candidate_validation import (
    promote_validated_candidates,
    registered_candidate_kinds,
    validate_and_promote_candidates,
)
from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_projection import (
    enrich_asset_with_implicit_rule_projection,
)


def formal_rule(**overrides):
    row = {
        "kind": "rule",
        "name": "orders.amount must be non-null",
        "statement": "orders.amount must be non-null",
        "logical_form": "REQUIRED_FIELD",
        "supporting_fact_refs": ["field:orders.amount"],
        "source_refs": [
            {
                "source_id": "schema.sql",
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
        "consequent": {"field_ref": "field:orders.amount", "operator": "not_null"},
        "subject_refs": ["table:orders"],
        "field_refs": ["field:orders.amount"],
    }
    row.update(overrides)
    return row


def test_rule_kind_is_registered_and_formal_constraint_promotes():
    assert "rule" in registered_candidate_kinds()
    receipt = validate_and_promote_candidates([formal_rule()])
    assert len(receipt.validated) == 1
    assert receipt.pending == []
    promoted = promote_validated_candidates(receipt.validated, kind="rule")
    assert len(promoted) == 1
    rule = promoted[0]
    assert rule["derivation"] == "implicit_rule_entailment"
    assert rule["semantic_contract"]["status"] == "ACCEPTED"
    assert rule["structured_expression"]["consequent"]["operator"] == "not_null"
    assert rule["kind"] == "validation_required"
    assert rule["operator"] == "not_null"
    assert rule["operands"] == [
        {
            "field_ref": "field:orders.amount",
            "field_id": "field:orders.amount",
            "operator": "not_null",
        }
    ]
    assert rule["entity"] == "table:orders"
    assert rule["source_locator"] == "schema.sql#orders.amount"


def test_promoted_rule_keeps_typed_expression_in_existing_behavior_ir():
    receipt = validate_and_promote_candidates([formal_rule()])
    rule = promote_validated_candidates(receipt.validated, kind="rule")[0]

    behavior_ir = build_behavior_ir_from_knowledge_asset(
        {
            "rule_library": [rule],
            "objects": [{"name": "table:orders", "kind": "entity"}],
        },
        project_id="implicit-rule-runtime-shape",
    )
    invariant = next(
        row
        for row in behavior_ir["invariants"]
        if rule["rule_id"] in row.get("source_rule_refs", [])
    )

    assert invariant["expression"]["kind"] == "validation_required"
    assert invariant["expression"]["operator"] == "not_null"
    assert invariant["expression"]["operands"][0]["field_id"] == (
        "field:orders.amount"
    )
    assert invariant.get("binding_status") != "umbrella_rule_excluded"


def test_industry_prior_cannot_self_authorize():
    candidate = formal_rule(
        source_refs=[{"source_id": "industry_inference", "kind": "industry_prior"}],
        source_authority="industry_prior",
        derivation_basis=["industry_prior"],
    )
    receipt = validate_and_promote_candidates([candidate])
    assert receipt.validated == []
    assert len(receipt.pending) == 1
    assert "not_industry_prior_only" in receipt.pending[0]["pending_gates"]


def test_counterevidence_conflicts_before_promotion():
    receipt = validate_and_promote_candidates(
        [formal_rule(contradicting_fact_refs=["conflict:1"])]
    )
    assert receipt.validated == []
    assert len(receipt.conflicted) == 1
    assert receipt.conflicted[0]["reason"] == "rule_counterevidence_present"


def test_existing_generic_candidate_behavior_is_preserved():
    candidate = {
        "kind": "entity",
        "name": "orders",
        "source_id": "prd",
        "confidence": 0.6,
    }
    receipt = validate_and_promote_candidates(
        [candidate],
        tables=[{"name": "orders", "source_id": "schema"}],
    )
    assert len(receipt.validated) == 1
    assert "cross_ref_table_name" in receipt.validated[0]["promotion_evidence"]


def test_schema_constraints_enter_existing_rule_library_and_industry_prior_is_quarantined():
    asset = {
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
    projected = enrich_asset_with_implicit_rule_projection(asset)
    accepted = [
        row
        for row in projected["rule_library"]
        if row.get("derivation") == "implicit_rule_entailment"
    ]
    assert {row["logical_form"] for row in accepted} == {
        "REQUIRED_FIELD",
        "UNIQUENESS",
    }
    assert all(
        row.get("source_id") != "industry_inference"
        for row in projected["rule_library"]
    )
    pending = projected["implicit_rule_candidate_validation_receipt"]["pending"]
    assert any(row.get("logical_form") == "PRIOR_HYPOTHESIS" for row in pending)
    assert projected["implicit_rule_projection_gate"]["parallel_rule_ir_created"] is False
    assert projected["governance"]["implicit_rules_enter_existing_rule_library"] is True
