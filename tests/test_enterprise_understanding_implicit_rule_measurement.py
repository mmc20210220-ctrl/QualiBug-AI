from __future__ import annotations

from copy import deepcopy

import pytest

from benchmark_evaluator.enterprise_understanding.implicit_rules import (
    ANNOTATION_SCOPE,
    IMPLICIT_RULE_GROUND_TRUTH_SCHEMA,
    ImplicitRuleGroundTruthValidationError,
    evaluate_implicit_rules,
    validate_implicit_rule_ground_truth,
)


def _ground_truth():
    return {
        "schema": IMPLICIT_RULE_GROUND_TRUTH_SCHEMA,
        "project_id": "implicit-rule-contract-v1",
        "benchmark_id": "implicit-rule-contract-v1",
        "annotation_scope": ANNOTATION_SCOPE,
        "candidate_universe_complete": True,
        "ground_truth_generated_from_product_output": False,
        "source_snapshot": [
            {
                "source_id": "prd-implicit-v1",
                "source_hash": "sha256:1111111111111111111111111111111111111111111111111111111111111111",
                "source_version_id": "srcv-prd-implicit-v1",
            }
        ],
        "rules": [
            {
                "ground_truth_id": "gt:implicit:idempotency",
                "annotation_status": "CONFIRMED",
                "expected_rule": True,
                "expected_status": "ACTIVE",
                "execution_required": True,
                "criticality": "P0",
                "source_refs": ["prd-implicit-v1"],
                "source_locators": ["rules.md#idempotency"],
                "match": {
                    "logical_form": "IDEMPOTENCY",
                    "operator": "business_effect_count",
                    "operation_refs": ["op:charge"],
                },
            },
            {
                "ground_truth_id": "gt:implicit:cardinality",
                "annotation_status": "CONFIRMED",
                "expected_rule": True,
                "expected_status": "STALE",
                "execution_required": False,
                "criticality": "P1",
                "source_refs": ["prd-implicit-v1"],
                "source_locators": ["rules.md#cardinality"],
                "match": {
                    "logical_form": "CARDINALITY",
                    "operator": "count_eq",
                    "subject_refs": ["Order"],
                },
            },
            {
                "ground_truth_id": "gt:implicit:hard-negative",
                "annotation_status": "CONFIRMED",
                "expected_rule": False,
                "expected_status": "REJECTED",
                "execution_required": False,
                "criticality": "P2",
                "source_refs": ["prd-implicit-v1"],
                "source_locators": ["rules.md#example-only"],
                "match": {
                    "logical_form": "REQUIRED_FIELD",
                    "operator": "not_null",
                    "field_refs": ["field:orders.fake"],
                },
            },
            {
                "ground_truth_id": "gt:implicit:missing-conservation",
                "annotation_status": "CONFIRMED",
                "expected_rule": True,
                "expected_status": "ACTIVE",
                "execution_required": True,
                "criticality": "P0",
                "source_refs": ["prd-implicit-v1"],
                "source_locators": ["rules.md#conservation"],
                "match": {
                    "logical_form": "CONSERVATION_EQUATION",
                    "operator": "equals",
                    "subject_refs": ["Inventory"],
                },
            },
        ],
    }


def _rule(
    rule_id,
    logical_form,
    operator,
    *,
    operation_refs=None,
    subject_refs=None,
    field_refs=None,
):
    return {
        "rule_id": rule_id,
        "candidate_id": f"candidate:{rule_id}",
        "derivation": "implicit_rule_entailment",
        "logical_form": logical_form,
        "operator": operator,
        "operation_refs": list(operation_refs or []),
        "subject_refs": list(subject_refs or []),
        "field_refs": list(field_refs or []),
        "source_version_refs": [
            {
                "source_id": "prd-implicit-v1",
                "source_hash": "1" * 64,
                "source_version_id": "srcv-prd-implicit-v1",
            }
        ],
    }


def _asset():
    idempotency = _rule(
        "implicit_rule_idempotency",
        "IDEMPOTENCY",
        "business_effect_count",
        operation_refs=["op:charge"],
    )
    cardinality = _rule(
        "implicit_rule_cardinality",
        "CARDINALITY",
        "count_eq",
        subject_refs=["Order"],
    )
    hard_negative = _rule(
        "implicit_rule_false_required",
        "REQUIRED_FIELD",
        "not_null",
        field_refs=["field:orders.fake"],
    )
    unmatched = _rule(
        "implicit_rule_unmatched",
        "VALUE_BOUND",
        "range",
        field_refs=["field:orders.universe"],
    )
    candidates = []
    for rule in (idempotency, cardinality, hard_negative):
        candidate = dict(rule)
        candidate["kind"] = "rule"
        candidate["candidate_id"] = rule["candidate_id"]
        candidates.append(candidate)
    return {
        "implicit_rule_candidates": candidates,
        "implicit_rule_candidate_validation_receipt": {
            "validated": [
                {"candidate_id": idempotency["candidate_id"]},
                {"candidate_id": hard_negative["candidate_id"]},
            ],
            "pending": [],
            "conflicted": [],
            "rejected": [],
            "stale": [{"candidate_id": cardinality["candidate_id"]}],
        },
        "rule_library": [idempotency, hard_negative, unmatched],
        "implicit_rule_lifecycle_ledger": {
            "items": [
                {
                    "rule_id": idempotency["rule_id"],
                    "status": "ACTIVE",
                    "source_version_refs": idempotency["source_version_refs"],
                    "rule_snapshot": idempotency,
                },
                {
                    "rule_id": cardinality["rule_id"],
                    "status": "STALE",
                    "source_version_refs": cardinality["source_version_refs"],
                    "rule_snapshot": cardinality,
                },
                {
                    "rule_id": hard_negative["rule_id"],
                    "status": "ACTIVE",
                    "source_version_refs": hard_negative["source_version_refs"],
                    "rule_snapshot": hard_negative,
                },
                {
                    "rule_id": unmatched["rule_id"],
                    "status": "ACTIVE",
                    "source_version_refs": unmatched["source_version_refs"],
                    "rule_snapshot": unmatched,
                },
            ]
        },
        "relationships": [
            {
                "from": idempotency["rule_id"],
                "to": "op:charge",
                "relation": "rule_to_interface",
                "status": "accepted",
            }
        ],
        "oracle_library": [
            {"oracle_id": "oracle:idempotency", "rule_id": idempotency["rule_id"]}
        ],
    }


def test_closed_world_contract_rejects_incomplete_or_product_id_truth():
    incomplete = _ground_truth()
    incomplete["candidate_universe_complete"] = False
    with pytest.raises(ImplicitRuleGroundTruthValidationError):
        validate_implicit_rule_ground_truth(incomplete)

    product_keyed = _ground_truth()
    product_keyed["rules"][0]["rule_id"] = "implicit_rule_from_product"
    with pytest.raises(ImplicitRuleGroundTruthValidationError):
        validate_implicit_rule_ground_truth(product_keyed)


def test_measurement_separates_candidate_promotion_lifecycle_and_execution():
    ground_truth = _ground_truth()
    asset = _asset()
    before = deepcopy(asset)

    measured = evaluate_implicit_rules(ground_truth, asset)

    assert measured["status"] == "MEASURED"
    assert measured["quality_claim_allowed"] is True
    metrics = measured["metrics"]
    assert metrics["candidate_precision"] == 0.6667
    assert metrics["candidate_recall"] == 0.6667
    assert metrics["promotion_precision"] == 0.3333
    assert metrics["promotion_recall"] == 0.5
    assert metrics["overpromotion_rate"] == 0.6667
    assert metrics["lifecycle_accuracy"] == 0.5
    assert metrics["stale_precision"] == 1.0
    assert metrics["stale_recall"] == 1.0
    assert metrics["authoritative_operation_binding_recall"] == 0.5
    assert metrics["oracle_projection_recall"] == 0.5
    assert metrics["executable_projection_recall"] == 0.5
    assert metrics["runtime_observation_recall"] is None
    assert metrics["unmatched_active_rule_ids"] == ["implicit_rule_unmatched"]
    assert measured["next_repair_target"] == "IMPLICIT_RULE_ENTAILMENT_RECALL"
    assert len(measured["false_promotions"]) == 1
    assert len(measured["missed_rules"]) == 1
    assert len(measured["lifecycle_errors"]) == 2
    assert len(measured["execution_bridge_gaps"]) == 1
    assert measured["ground_truth_entered_product_runtime"] is False
    assert measured["fuzzy_or_llm_alignment_used"] is False
    assert measured["model_writeback_allowed"] is False
    assert asset == before


def test_missing_implicit_rule_ground_truth_cannot_claim_quality():
    measured = evaluate_implicit_rules(None, _asset())

    assert measured["status"] == "NOT_MEASURED"
    assert measured["reason_code"] == (
        "EVALUATOR_IMPLICIT_RULE_GROUND_TRUTH_NOT_PROVIDED"
    )
    assert measured["quality_claim_allowed"] is False
