from copy import deepcopy

from ai_test_asset_center.enterprise_knowledge_center.implicit_rule_governance import (
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
                "content_hash": "prd-hash-v1",
                "source_version_id": "srcv-prd-v1",
            }
        ],
        "field_dictionary": [],
        "data_tables": [],
        "interfaces": [
            {
                "interface_id": "refund_order",
                "operation_id": "refund_order",
                "method": "POST",
                "path": "/orders/{id}/refund",
                "source_id": "openapi-1",
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
                        "operation_ref": "refund_order",
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


def _active_rule(asset):
    return next(
        row
        for row in asset["rule_library"]
        if row.get("derivation") == "implicit_rule_entailment"
    )


def _runtime_receipt(rule_id):
    return project_implicit_rule_runtime_evolution(
        behavior_ir={
            "model_id": "bir:authority-decision",
            "source_snapshot_hash": "source-snapshot",
            "invariants": [
                {
                    "id": "bir_inv_rule",
                    "source_rule_refs": [rule_id],
                }
            ],
        },
        obligations={
            "obligations": [
                {
                    "obligation_id": "obl-rule",
                    "property": {"invariant_ref": "bir_inv_rule"},
                }
            ]
        },
        obligation_attempt_ledger={
            "attempts": [
                {
                    "obligation_id": "obl-rule",
                    "attempt_id": "attempt-rule",
                    "terminal_status": "REJECTED",
                    "reason_code": "ORACLE_NOT_VIOLATED",
                }
            ]
        },
    )


def _decision(rule_id, evidence_id, **overrides):
    row = {
        "decision_id": "decision:counterexample:1",
        "rule_ref": rule_id,
        "decision_type": "MARK_STALE",
        "counterexample_classification": "RULE_COUNTEREXAMPLE",
        "authority_type": "operator_approved",
        "decided_by": {"name": "quality-owner", "role": "business_rule_owner"},
        "reason": "The current approved contract confirms this behavior is no longer a rule.",
        "evidence_refs": [evidence_id, "approved-contract-change:42"],
        "runtime_evidence_refs": [evidence_id],
    }
    row.update(overrides)
    return row


def test_operator_approved_counterexample_marks_rule_stale():
    first = enrich_asset_with_governed_implicit_rule_projection(_asset())
    rule = _active_rule(first)
    receipt = _runtime_receipt(rule["rule_id"])
    evidence_id = receipt["rules"][0]["evidence"][0]["evidence_id"]

    governed = deepcopy(first)
    governed["implicit_rule_runtime_evolution"] = receipt
    governed["implicit_rule_authority_decisions"] = [
        _decision(rule["rule_id"], evidence_id)
    ]
    result = enrich_asset_with_governed_implicit_rule_projection(governed)

    assert not any(
        row.get("rule_id") == rule["rule_id"] for row in result["rule_library"]
    )
    lifecycle = next(
        row
        for row in result["implicit_rule_lifecycle_ledger"]["items"]
        if row["rule_id"] == rule["rule_id"]
    )
    assert lifecycle["status"] == "STALE"
    assert lifecycle["execution_allowed"] is False
    assert lifecycle["reason"] == "AUTHORITY_DECISION_MARKED_RULE_STALE"
    ledger = result["implicit_rule_authority_decision_ledger"]
    assert ledger["applied_count"] == 1
    assert ledger["pending_evidence_count"] == 0
    assert ledger["rejected_count"] == 0
    assert ledger["items"][0]["authority_transition"] == "ACTIVE_TO_STALE"

    # The command input is consumed by the caller; the persisted append-only ledger
    # continues to enforce the same decision on later source rebuilds.
    replay = deepcopy(result)
    replay["implicit_rule_authority_decisions"] = []
    once = enrich_asset_with_governed_implicit_rule_projection(replay)
    twice = enrich_asset_with_governed_implicit_rule_projection(deepcopy(once))
    assert twice["implicit_rule_authority_decision_ledger"]["applied_count"] == 1
    assert not any(
        row.get("rule_id") == rule["rule_id"] for row in twice["rule_library"]
    )
    assert len(twice["implicit_rule_lifecycle_ledger"]["events"]) == len(
        once["implicit_rule_lifecycle_ledger"]["events"]
    )


def test_raw_runtime_observation_cannot_authorize_rule_demotion():
    first = enrich_asset_with_governed_implicit_rule_projection(_asset())
    rule = _active_rule(first)
    receipt = _runtime_receipt(rule["rule_id"])
    evidence_id = receipt["rules"][0]["evidence"][0]["evidence_id"]

    governed = deepcopy(first)
    governed["implicit_rule_runtime_evolution"] = receipt
    governed["implicit_rule_authority_decisions"] = [
        _decision(
            rule["rule_id"],
            evidence_id,
            decision_id="decision:raw-runtime",
            authority_type="runtime_observation",
        )
    ]
    result = enrich_asset_with_governed_implicit_rule_projection(governed)

    assert any(row.get("rule_id") == rule["rule_id"] for row in result["rule_library"])
    ledger = result["implicit_rule_authority_decision_ledger"]
    assert ledger["rejected_count"] == 1
    assert ledger["items"][0]["reason_code"] == "DECISION_AUTHORITY_NOT_APPROVED"
    assert ledger["raw_runtime_observation_can_mutate_authority"] is False
    assert any(
        row.get("kind") == "IMPLICIT_RULE_AUTHORITY_DECISION_REJECTED"
        for row in result["coverage_gaps"]
    )


def test_missing_runtime_receipt_keeps_counterexample_decision_pending():
    first = enrich_asset_with_governed_implicit_rule_projection(_asset())
    rule = _active_rule(first)
    governed = deepcopy(first)
    governed["implicit_rule_authority_decisions"] = [
        _decision(
            rule["rule_id"],
            "implicit_rule_runtime_evidence_not_imported",
            decision_id="decision:pending-evidence",
        )
    ]

    result = enrich_asset_with_governed_implicit_rule_projection(governed)

    assert any(row.get("rule_id") == rule["rule_id"] for row in result["rule_library"])
    ledger = result["implicit_rule_authority_decision_ledger"]
    assert ledger["pending_evidence_count"] == 1
    assert ledger["items"][0]["reason_code"] == (
        "RUNTIME_EVIDENCE_RECEIPT_NOT_IMPORTED"
    )
    assert any(
        row.get("kind") == "IMPLICIT_RULE_AUTHORITY_DECISION_PENDING"
        for row in result["coverage_gaps"]
    )


def test_target_bug_classification_keeps_rule_active():
    first = enrich_asset_with_governed_implicit_rule_projection(_asset())
    rule = _active_rule(first)
    receipt = _runtime_receipt(rule["rule_id"])
    evidence_id = receipt["rules"][0]["evidence"][0]["evidence_id"]
    governed = deepcopy(first)
    governed["implicit_rule_runtime_evolution"] = receipt
    governed["implicit_rule_authority_decisions"] = [
        _decision(
            rule["rule_id"],
            evidence_id,
            decision_id="decision:target-bug",
            decision_type="CONFIRM_ACTIVE",
            counterexample_classification="TARGET_BUG",
            reason="The observed violation is a target defect; the approved rule remains valid.",
        )
    ]

    result = enrich_asset_with_governed_implicit_rule_projection(governed)

    active = next(row for row in result["rule_library"] if row.get("rule_id") == rule["rule_id"])
    assert active["authority_decision_ref"] == "decision:target-bug"
    lifecycle = next(
        row
        for row in result["implicit_rule_lifecycle_ledger"]["items"]
        if row["rule_id"] == rule["rule_id"]
    )
    assert lifecycle["status"] == "ACTIVE"
    assert lifecycle["counterexample_classification"] == "TARGET_BUG"
