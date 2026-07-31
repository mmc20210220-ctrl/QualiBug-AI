from ai_test_asset_center.implicit_rule_runtime_evolution import (
    project_implicit_rule_runtime_evolution,
)


def _behavior_ir():
    return {
        "model_id": "bir:test",
        "source_snapshot_hash": "source-hash",
        "invariants": [
            {
                "id": "bir_inv_1",
                "source_rule_refs": ["implicit_rule_abc"],
            }
        ],
    }


def _obligations():
    return {
        "obligations": [
            {
                "obligation_id": "obl-1",
                "property": {"invariant_ref": "bir_inv_1"},
            },
            {
                "obligation_id": "obl-2",
                "property": {"invariant_ref": "bir_inv_1"},
            },
        ]
    }


def test_runtime_violation_and_conformance_append_evidence_without_mutating_authority():
    receipt = project_implicit_rule_runtime_evolution(
        behavior_ir=_behavior_ir(),
        obligations=_obligations(),
        obligation_attempt_ledger={
            "attempts": [
                {
                    "obligation_id": "obl-1",
                    "attempt_id": "attempt-1",
                    "terminal_status": "DELIVERABLE",
                    "delivery_gate_status": "DELIVERABLE",
                    "reason_code": "CONTRACT_ORACLE_VIOLATED",
                    "finding_ids": ["finding-1"],
                },
                {
                    "obligation_id": "obl-2",
                    "attempt_id": "attempt-2",
                    "terminal_status": "REJECTED",
                    "reason_code": "ORACLE_NOT_VIOLATED",
                },
            ]
        },
    )

    assert receipt["status"] == "OBSERVED"
    assert receipt["authority_mutated"] is False
    assert receipt["rule_count"] == 1
    assert receipt["observation_distribution"] == {
        "OBSERVED_CONFORMANCE": 1,
        "OBSERVED_VIOLATION": 1,
    }
    rule = receipt["rules"][0]
    assert rule["rule_ref"] == "implicit_rule_abc"
    assert rule["authority_transition"] == "UNCHANGED"
    assert rule["legitimate_counterexample_decision_required"] is True


def test_unlinked_attempt_is_visible_not_silently_dropped():
    receipt = project_implicit_rule_runtime_evolution(
        behavior_ir={"invariants": []},
        obligations={"obligations": [{"obligation_id": "obl-x"}]},
        obligation_attempt_ledger={
            "attempts": [
                {
                    "obligation_id": "obl-x",
                    "attempt_id": "attempt-x",
                    "terminal_status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_OBSERVER",
                }
            ]
        },
    )

    assert receipt["status"] == "NO_IMPLICIT_RULE_ATTEMPTS"
    assert receipt["unlinked_attempt_count"] == 1
    assert receipt["unlinked_attempts"][0]["reason_code"] == (
        "IMPLICIT_RULE_IDENTITY_NOT_LINKED"
    )
