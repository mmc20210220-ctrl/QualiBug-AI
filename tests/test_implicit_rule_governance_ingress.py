from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_governance_entry import (
    enrich_asset_with_governed_implicit_rule_projection,
)
from ai_test_asset_center.implicit_rule_runtime_evolution import (
    project_implicit_rule_runtime_evolution,
)


def _asset():
    return {
        "source_inventory": [
            {
                "source_id": "prd-1",
                "status": "active",
                "content_hash": "prd-v1",
                "source_version_id": "srcv-prd-v1",
            }
        ],
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
        "business_fact_ledger": {
            "items": [
                {
                    "fact_id": "fact:idempotency",
                    "fact_type": "BUSINESS_RULE",
                    "status": "ACCEPTED",
                    "raw_statement": "同一付款请求不得重复成功扣款",
                    "subject": {"entity_refs": ["付款请求"]},
                    "action": {"canonical": "扣款"},
                    "source_spans": [
                        {
                            "source_id": "prd-1",
                            "locator": "prd.md#payment-idempotency",
                        }
                    ],
                    "confidence": 1.0,
                }
            ]
        },
    }


def _runtime(rule_id):
    return project_implicit_rule_runtime_evolution(
        behavior_ir={
            "model_id": "bir:ingress",
            "invariants": [
                {"id": "inv:ingress", "source_rule_refs": [rule_id]}
            ],
        },
        obligations={
            "obligations": [
                {
                    "obligation_id": "obl:ingress",
                    "property": {"invariant_ref": "inv:ingress"},
                }
            ]
        },
        obligation_attempt_ledger={
            "attempts": [
                {
                    "obligation_id": "obl:ingress",
                    "attempt_id": "attempt:ingress",
                    "terminal_status": "REJECTED",
                    "reason_code": "ORACLE_NOT_VIOLATED",
                }
            ]
        },
    )


def _decision(rule_id, evidence_ref):
    return {
        "decision_id": "decision:ingress:1",
        "rule_ref": rule_id,
        "decision_type": "MARK_STALE",
        "counterexample_classification": "RULE_COUNTEREXAMPLE",
        "authority_type": "operator_approved",
        "decided_by": {"name": "rule-owner", "role": "business_rule_owner"},
        "reason": "Approved contract evidence invalidates the prior implicit rule.",
        "evidence_refs": [evidence_ref],
        "runtime_evidence_refs": [evidence_ref],
    }


def _rule_and_receipt():
    first = enrich_asset_with_governed_implicit_rule_projection(_asset())
    rule_id = next(
        row["rule_id"]
        for row in first["rule_library"]
        if row.get("derivation") == "implicit_rule_entailment"
    )
    return first, rule_id, _runtime(rule_id)


def test_exact_decision_command_replay_is_idempotent_not_a_conflict():
    first, rule_id, receipt = _rule_and_receipt()
    evidence_id = receipt["rules"][0]["evidence"][0]["evidence_id"]
    first["implicit_rule_runtime_evolution"] = receipt
    first["implicit_rule_authority_decisions"] = [_decision(rule_id, evidence_id)]

    once = enrich_asset_with_governed_implicit_rule_projection(first)
    twice = enrich_asset_with_governed_implicit_rule_projection(deepcopy(once))

    assert once["implicit_rule_authority_decision_ledger"]["applied_count"] == 1
    assert twice["implicit_rule_authority_decision_ledger"]["applied_count"] == 1
    assert twice["implicit_rule_authority_decision_ledger"]["conflicts"] == []
    assert twice["implicit_rule_governance_ingress_receipt"][
        "exact_replayed_decisions_ignored"
    ] == 1
    assert twice["implicit_rule_runtime_evolution"]["receipt_id"] == receipt[
        "receipt_id"
    ]
    assert not any(row.get("rule_id") == rule_id for row in twice["rule_library"])


def test_derived_decision_id_replay_is_also_idempotent():
    first, rule_id, receipt = _rule_and_receipt()
    evidence_id = receipt["rules"][0]["evidence"][0]["evidence_id"]
    command = _decision(rule_id, evidence_id)
    command.pop("decision_id")
    first["implicit_rule_runtime_evolution"] = receipt
    first["implicit_rule_authority_decisions"] = [command]

    once = enrich_asset_with_governed_implicit_rule_projection(first)
    twice = enrich_asset_with_governed_implicit_rule_projection(deepcopy(once))

    assert once["implicit_rule_authority_decision_ledger"]["applied_count"] == 1
    assert twice["implicit_rule_authority_decision_ledger"]["conflicts"] == []
    assert twice["implicit_rule_governance_ingress_receipt"][
        "exact_replayed_decisions_ignored"
    ] == 1
    assert twice["implicit_rule_governance_ingress_receipt"][
        "derived_decision_identity_used_for_replay_detection"
    ] is True


def test_batch_receipt_id_cannot_replace_per_rule_runtime_evidence_id():
    first, rule_id, receipt = _rule_and_receipt()
    first["implicit_rule_runtime_evolution"] = receipt
    first["implicit_rule_authority_decisions"] = [
        _decision(rule_id, receipt["receipt_id"])
    ]

    result = enrich_asset_with_governed_implicit_rule_projection(first)

    ledger = result["implicit_rule_authority_decision_ledger"]
    assert ledger["pending_evidence_count"] == 1
    assert ledger["items"][0]["reason_code"] == (
        "RUNTIME_EVIDENCE_REFERENCE_UNKNOWN"
    )
    assert result["implicit_rule_governance_ingress_receipt"][
        "batch_receipt_id_is_not_runtime_evidence_id"
    ] is True
    assert result["implicit_rule_governance_ingress_receipt"][
        "runtime_batch_receipt_preserved"
    ] is True
    assert result["implicit_rule_runtime_evolution"]["receipt_id"] == receipt[
        "receipt_id"
    ]
    assert any(row.get("rule_id") == rule_id for row in result["rule_library"])
