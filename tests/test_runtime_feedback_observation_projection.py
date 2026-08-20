from __future__ import annotations

from ai_test_asset_center.runtime_fact_candidate import project_runtime_fact_candidates
from ai_test_asset_center.runtime_feedback_observation_projection import (
    attach_runtime_feedback_observation_evidence,
)


def _blocked_batch() -> dict:
    return {
        "results": [
            {
                "selected_obligation_id": "obl_a",
                "obligation_id": "obl_a",
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "observer_receipts": [
                    {
                        "receipt_id": "observer_readback_1",
                        "observer_id": "entity_readback",
                        "status": "OBSERVED",
                        "evidence": {
                            "observation_path": "/api/resources/{id}",
                            "after_fingerprint": "fp_after_1",
                        },
                    }
                ],
                "contract_evidence_receipts": [
                    {
                        "receipt_id": "contract_1",
                        "kind": "observation",
                        "status": "OBSERVED",
                    }
                ],
                "effect_observation_graph": {
                    "receipt_id": "effect_graph_1",
                    "nodes": [
                        {
                            "kind": "readback",
                            "observation_path": "/api/resources/{id}",
                        }
                    ],
                },
            }
        ],
        "execution_results": {
            "obl_a": {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_MISSING_BINDING",
                "receipt_id": "exec_1",
            }
        },
    }


def test_blocked_governed_observation_survives_execution_projection() -> None:
    projected = attach_runtime_feedback_observation_evidence(_blocked_batch())
    row = projected["execution_results"]["obl_a"]

    assert row["status"] == "BLOCKED"
    assert row["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert row["observer_receipts"][0]["receipt_id"] == "observer_readback_1"
    assert row["effect_observation_graph"]["receipt_id"] == "effect_graph_1"
    assert projected["runtime_feedback_observation_projection_receipt"][
        "execution_status_mutated"
    ] is False
    assert projected["runtime_feedback_observation_projection_receipt"][
        "fact_authority_elevated"
    ] is False


def test_existing_runtime_fact_projector_can_recover_blocked_readback_path() -> None:
    projected = attach_runtime_feedback_observation_evidence(_blocked_batch())
    ledger = project_runtime_fact_candidates(
        execution_results=projected["execution_results"],
        campaign_id="campaign_1",
    )

    candidates = ledger["candidates"]
    assert any(
        row.get("kind") == "runtime_observation_path"
        and row.get("path") == "/api/resources/{id}"
        and row.get("status") == "CANDIDATE"
        for row in candidates
    )


def test_unreceipted_observer_evidence_does_not_cross_projection_boundary() -> None:
    batch = _blocked_batch()
    batch["results"][0]["observer_receipts"] = [
        {
            "observer_id": "entity_readback",
            "status": "OBSERVED",
            "evidence": {"observation_path": "/api/unsealed"},
        }
    ]
    batch["results"][0]["effect_observation_graph"] = {
        "nodes": [{"kind": "readback", "observation_path": "/api/unsealed"}]
    }

    projected = attach_runtime_feedback_observation_evidence(batch)
    row = projected["execution_results"]["obl_a"]
    assert "observer_receipts" not in row
    assert "effect_observation_graph" not in row


def test_existing_projection_evidence_is_not_overwritten() -> None:
    batch = _blocked_batch()
    batch["execution_results"]["obl_a"]["observer_receipts"] = [
        {
            "receipt_id": "existing_observer",
            "observer_id": "existing",
            "status": "OBSERVED",
        }
    ]

    projected = attach_runtime_feedback_observation_evidence(batch)
    assert projected["execution_results"]["obl_a"]["observer_receipts"][0][
        "receipt_id"
    ] == "existing_observer"


def test_projection_does_not_create_missing_execution_result_identity() -> None:
    batch = _blocked_batch()
    batch["execution_results"] = {}
    projected = attach_runtime_feedback_observation_evidence(batch)
    assert projected["execution_results"] == {}
    assert projected["runtime_feedback_observation_projection_receipt"][
        "projected_outcome_count"
    ] == 0
