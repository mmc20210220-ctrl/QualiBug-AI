from __future__ import annotations

from ai_test_asset_center.fact_first_loss_ledger import (
    attach_fact_refs_to_planning_artifacts,
    build_fact_first_loss_ledger,
)


def _exp_ledger() -> dict:
    return {
        "schema_version": "qualibug.fact-experimentability-ledger.v1",
        "accepted_fact_count": 2,
        "receipt_count": 2,
        "silent_drop_count": 0,
        "ledger_fingerprint": "fact-ledger-v2",
        "items": [
            {
                "receipt_id": "fer_a",
                "fact_ref": "fact:a",
                "status": "READY",
                "blocker_codes": [],
            },
            {
                "receipt_id": "fer_b",
                "fact_ref": "fact:b",
                "status": "READY",
                "blocker_codes": [],
            },
        ],
    }


def _authority() -> tuple[dict, dict]:
    asset = {
        "business_world_model": {
            "evidence_registry": [
                {"evidence_ref": "evidence:a", "fact_id": "fact:a"}
            ],
            "behavior_nodes": [
                {
                    "node_id": "behavior:a",
                    "evidence_refs": ["evidence:a"],
                    "implementation_binding_refs": ["binding:a"],
                }
            ],
        },
        "enterprise_understanding_model": {
            "business_behaviors": [
                {
                    "behavior_id": "behavior:a",
                    "source_refs": ["fact:a"],
                }
            ],
            "behavior_implementation_bindings": [
                {
                    "binding_id": "binding:a",
                    "behavior_ref": "behavior:a",
                    "api_operation_bindings": [
                        {
                            "binding_id": "api-binding:a",
                            "status": "BOUND",
                            "authoritative": True,
                        }
                    ],
                }
            ],
        },
    }
    behavior_ir = {
        "invariants": [
            {
                "id": "invariant:a",
                "business_behavior_ref": "behavior:a",
                "implementation_binding_refs": ["api-binding:a"],
            }
        ],
        "relations": [],
    }
    return asset, behavior_ir


def test_authoritative_mode_replaces_stale_fact_refs_instead_of_trusting_them() -> None:
    asset, behavior_ir = _authority()
    obligations = [
        {
            "obligation_id": "obl:a",
            "fact_refs": ["fact:b"],
            "property": {"invariant_ref": "invariant:a"},
        }
    ]
    experiments = [
        {
            "obligation_id": "obl:a",
            "experiment_id": "exp:a",
            "fact_refs": ["fact:b"],
        }
    ]

    receipt = attach_fact_refs_to_planning_artifacts(
        obligations=obligations,
        experiments=experiments,
        fact_experimentability_ledger=_exp_ledger(),
        behavior_ir=behavior_ir,
        knowledge_asset=asset,
    )

    assert obligations[0]["fact_refs"] == ["fact:a"]
    assert experiments[0]["fact_refs"] == ["fact:a"]
    assert receipt["preexisting_fact_refs_trusted"] is False
    assert receipt["experiment_lineage_may_widen_obligation_lineage"] is False
    assert receipt["linked_fact_refs"] == ["fact:a"]
    assert receipt["unresolved_fact_refs"] == ["fact:b"]


def test_every_accepted_fact_has_structured_first_loss_diagnostic() -> None:
    asset, behavior_ir = _authority()
    obligations = [
        {
            "obligation_id": "obl:a",
            "property": {"invariant_ref": "invariant:a"},
        }
    ]
    experiments = [
        {"obligation_id": "obl:a", "experiment_id": "exp:a"}
    ]
    receipt = attach_fact_refs_to_planning_artifacts(
        obligations=obligations,
        experiments=experiments,
        fact_experimentability_ledger=_exp_ledger(),
        behavior_ir=behavior_ir,
        knowledge_asset=asset,
    )

    assert receipt["fact_lineage_diagnostic_count"] == 2
    by_fact = {
        row["fact_ref"]: row for row in receipt["fact_lineage_diagnostics"]
    }
    assert by_fact["fact:a"]["status"] == "RESOLVED_LINKED"
    assert by_fact["fact:b"]["status"] == "UNRESOLVED"
    assert by_fact["fact:b"]["break_stage"] == "FACT_LINEAGE_UNRESOLVED"
    assert by_fact["fact:b"]["reason_codes"] == [
        "FACT_WORLD_MODEL_EVIDENCE_MISSING"
    ]

    ledger = build_fact_first_loss_ledger(
        fact_experimentability_ledger=_exp_ledger(),
        obligations=obligations,
        experiments=experiments,
        obligation_attempt_ledger={
            "attempts": [],
            "ledger_fingerprint": "attempt-ledger",
        },
        fact_lineage_receipt=receipt,
    )
    first_loss = {row["fact_ref"]: row for row in ledger["items"]}
    assert first_loss["fact:b"]["first_loss_stage"] == "FACT_LINEAGE_UNRESOLVED"
    assert first_loss["fact:b"]["lineage_reason_codes"] == [
        "FACT_WORLD_MODEL_EVIDENCE_MISSING"
    ]
    assert ledger["conservation"]["fact_lineage_diagnostic_coverage"] is True
