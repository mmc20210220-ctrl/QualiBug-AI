from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import database_relation_observer_experiment_runtime as runtime


def _experiment(pair_id: str = "pair-1") -> dict:
    return {
        "database_relation_observer_execution_drafts": [
            {
                "schema": "qualibug.database-relation-observer-execution-draft.v1",
                "draft_id": "draft:relation:before",
                "relation_pair_id": pair_id,
                "relation_observer_contract_ref": "relation-observer:ledger",
                "root_observer_contract_ref": "observer:accounts",
                "observation_phase": "BEFORE",
                "database_relation_observer_contract": {
                    "schema": "qualibug.database-relation-observer-contract.v1"
                },
                "aggregate_requests": [
                    {
                        "aggregate": "COUNT",
                        "database_field_id": "",
                        "database_field_name": "",
                        "alias": "related_value",
                    }
                ],
                "identity_value_sources": ["request.parameter.id"],
                "database_connection_ref": "",
                "required": True,
            }
        ]
    }


def test_phase_runtime_retains_pair_id_in_receipt_and_aggregate(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        runtime,
        "_runtime_values",
        lambda **_: {"request.parameter.id": "a-1"},
    )

    def fake_execute(*args, **kwargs):
        return {
            "receipt_id": "direct-receipt-1",
            "campaign_id": kwargs["campaign_id"],
            "execution_id": kwargs["execution_id"],
            "observer_id": "approved_database_relation_aggregate",
            "status": "OBSERVED",
            "reason_code": "",
            "evidence": {
                "approved_database_relation_aggregate_snapshot": {
                    "aggregate_fingerprint": "aggregate-fingerprint-1",
                    "oracle_verdict_emitted": False,
                }
            },
        }

    monkeypatch.setattr(
        runtime,
        "execute_database_relation_observer_contract",
        fake_execute,
    )
    experiment = _experiment()
    observations: dict = {}

    summary = runtime.execute_database_relation_observer_phase(
        experiment,
        phase="BEFORE",
        root=tmp_path,
        project="pair-runtime",
        runtime_contract={},
        runtime_bindings={},
        observations=observations,
        steps_out=[],
        campaign_id="campaign-1",
        execution_id="execution-1",
    )

    assert summary["status"] == "OBSERVED"
    receipt = observations["approved_database_relation_phase_receipts"][0]
    assert receipt["relation_pair_id"] == "pair-1"
    assert receipt["phase_receipt_id"]
    assert receipt["oracle_verdict_emitted"] is False

    aggregate = runtime.aggregate_database_relation_phase_receipts(
        {
            "experiment": experiment,
            "observations": observations,
            "campaign_id": "campaign-1",
            "execution_id": "execution-1",
        }
    )
    assert aggregate["status"] == "OBSERVED"
    snapshot = aggregate["evidence"][
        "approved_database_relation_snapshots"
    ][0]
    assert snapshot["relation_pair_id"] == "pair-1"
    assert snapshot["phase_receipt_id"] == receipt["phase_receipt_id"]
    assert aggregate["evidence"]["automatic_receipt_winner_count"] == 0
